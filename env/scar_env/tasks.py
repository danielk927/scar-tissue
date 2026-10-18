"""Task construction and generation (SPEC 9.5) on top of the template catalog in scar_env.templates.

Internal API (the mutator and judge flow build on it). Nothing here stores; callers use scar_env.store.store_task.
- make_task(conn, template_id, company_id, params, *, origin, difficulty, rng, prompt=None, parent_failure_id=None)
  -> Task: validates params (ParamError on bad template, company or params), computes `expected` with the template's
  SQL, picks a phrasing with `rng` unless `prompt` is given, and takes `split` from ledger.companies.
- sample_task(conn, template_id, company_id, *, rng, difficulty, origin, parent_failure_id=None) -> Task | None:
  samples params at `difficulty` and resamples degenerate answers, up to MAX_TRIES times; None means give up on this
  company (it has no possible match, or every try was degenerate).
- generate_tasks(conn, *, split, n, seed, origin, family=None) -> list[Task]: the stratified batch behind
  POST /tasks/generate (algorithm in docs/TEMPLATES.md).
- eval_set(task_ids) -> EvalSet, interpretation(conn, task) -> str.
`difficulty` must be in the template's `difficulties` (ValueError otherwise). The same inputs and rng state give the
same Task except `task_id` and `created_at` (R8).
"""

import hashlib
import random
from datetime import datetime, timezone
from typing import Any

import psycopg
from scar_schemas.constants import EVAL_SET_ID
from scar_schemas.ids import new_id
from scar_schemas.models import EvalSet, ExpectedAnswer, Origin, Split, Task, TaskFamily

from .templates import TEMPLATES, Company, ParamError, Template, get_template, load_company

MAX_TRIES = 20


class GenerationError(RuntimeError):
    """generate_tasks found no company in the split that supports a slot's family and difficulty."""


def _company(conn: psycopg.Connection, company_id: str) -> Company:
    company = load_company(conn, company_id)
    if company is None:
        raise ParamError(f"unknown company_id: {company_id}")
    return company


def _check_difficulty(template: Template, difficulty: int) -> None:
    if difficulty not in template.difficulties:
        raise ValueError(f"{template.template_id} supports difficulties {template.difficulties}, not {difficulty}")


def _build(
    template: Template,
    company: Company,
    params: dict[str, Any],
    expected: ExpectedAnswer,
    *,
    origin: Origin,
    difficulty: int,
    rng: random.Random,
    prompt: str | None,
    parent_failure_id: str | None,
) -> Task:
    if prompt is None:
        prompt = template.render(company, params, rng.choice(template.phrasings))
    return Task(
        task_id=new_id("task"),
        company_id=company.company_id,
        split=company.split,
        family=template.family,
        template_id=template.template_id,
        params=params,
        prompt=prompt,
        expected=expected,
        difficulty=difficulty,
        origin=origin,
        parent_failure_id=parent_failure_id,
        created_at=datetime.now(timezone.utc),
    )


def make_task(
    conn: psycopg.Connection,
    template_id: str,
    company_id: str,
    params: dict[str, Any],
    *,
    origin: Origin,
    difficulty: int,
    rng: random.Random,
    prompt: str | None = None,
    parent_failure_id: str | None = None,
) -> Task:
    template = get_template(template_id)
    _check_difficulty(template, difficulty)
    company = _company(conn, company_id)
    clean = template.validate(conn, company, params)
    expected = template.answer(conn, company, clean).expected
    return _build(
        template,
        company,
        clean,
        expected,
        origin=origin,
        difficulty=difficulty,
        rng=rng,
        prompt=prompt,
        parent_failure_id=parent_failure_id,
    )


def sample_task(
    conn: psycopg.Connection,
    template_id: str,
    company_id: str,
    *,
    rng: random.Random,
    difficulty: int,
    origin: Origin,
    parent_failure_id: str | None = None,
) -> Task | None:
    template = get_template(template_id)
    _check_difficulty(template, difficulty)
    company = _company(conn, company_id)
    for _ in range(MAX_TRIES):
        params = template.sample(conn, company, rng, difficulty)
        if params is None:
            return None
        params = template.validate(conn, company, params)  # a sampler bug raises here instead of storing bad tasks
        try:
            answer = template.answer(conn, company, params)
        except ParamError:
            continue  # undefined answer (e.g. no positive expense total): resample like a degenerate one
        if not answer.degenerate:
            return _build(
                template,
                company,
                params,
                answer.expected,
                origin=origin,
                difficulty=difficulty,
                rng=rng,
                prompt=None,
                parent_failure_id=parent_failure_id,
            )
    return None


def generate_tasks(
    conn: psycopg.Connection, *, split: Split, n: int, seed: int, origin: Origin, family: TaskFamily | None = None
) -> list[Task]:
    """n tasks from `split` companies, interleaved by family (or only `family`). Within a family, slot k has
    difficulty 1 + k % 3 and uses the templates supporting that difficulty in turn; companies are spread evenly by a
    seeded shuffle. A slot whose company cannot support it tries the other companies in a seeded random order (so the
    extra load spreads over the companies that can), then the next template."""
    families = [family] if family else list(TaskFamily)
    companies = [
        cid
        for (cid,) in conn.execute(
            "SELECT company_id FROM ledger.companies WHERE split = %s ORDER BY company_id", (split.value,)
        )
    ]
    if not companies:
        raise GenerationError(f"no {split.value}-split companies")
    by_family = []
    for i, fam in enumerate(families):
        n_family = n // len(families) + (i < n % len(families))
        assigned = (companies * (n_family // len(companies) + 1))[:n_family]
        random.Random(f"{seed}:{fam.value}:companies").shuffle(assigned)
        by_family.append(
            [_generate_slot(conn, fam, k, assigned[k], companies, seed=seed, origin=origin) for k in range(n_family)]
        )
    return [tasks[k] for k in range(len(by_family[0])) for tasks in by_family if k < len(tasks)]


def _generate_slot(
    conn: psycopg.Connection,
    family: TaskFamily,
    k: int,
    company_id: str,
    companies: list[str],
    *,
    seed: int,
    origin: Origin,
) -> Task:
    difficulty = 1 + k % 3
    templates = [t for t in TEMPLATES.values() if t.family == family and difficulty in t.difficulties]
    r = (k // 3) % len(templates)
    rng = random.Random(f"{seed}:{family.value}:{k}")
    fallbacks = [cid for cid in companies if cid != company_id]
    rng.shuffle(fallbacks)
    for template in templates[r:] + templates[:r]:
        for cid in [company_id, *fallbacks]:
            task = sample_task(conn, template.template_id, cid, rng=rng, difficulty=difficulty, origin=origin)
            if task is not None:
                return task
    raise GenerationError(f"no company supports a {family.value} task at difficulty {difficulty}")


def eval_set(task_ids: list[str]) -> EvalSet:
    ids = sorted(task_ids)
    return EvalSet(eval_set_id=EVAL_SET_ID, task_ids=ids, sha256=hashlib.sha256("\n".join(ids).encode()).hexdigest())


def interpretation(conn: psycopg.Connection, task: Task) -> str:
    return get_template(task.template_id).interpretation(_company(conn, task.company_id), task.params)
