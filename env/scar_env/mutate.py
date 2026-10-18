"""Mutator (SPEC 9.6, P1-10): new train-split tasks with the same template as an included failure's task (R6).

For failure f, request seed s and variant i, all sampling uses random.Random(f"{f}:{s}:{i}") and the company order
random.Random(f"{f}:{s}:companies"). SPEC 9.6 writes hash((f, s, i)), but str hashes are salted per process, which
would break R8. Per failure, with n = variants_per_failure:
- Template: the failed task's template_id. Answers come only from the template's SQL via tasks.sample_task, which also
  resamples degenerate answers (R5).
- Companies: variants 0 and 1 use the original company; variant i >= 2 takes the other train companies round robin in
  the seeded order. A company that cannot support a variant (sample_task gives None) passes it to the next company in
  that ring. So n >= 4 spans at least three companies when three train companies support the template; n = 2 uses
  only the original company.
- Difficulty: the last round(0.3 n) variants (half up) use difficulty + 1 when the template supports it; the others
  keep the failed task's difficulty. A variant no train company can make at difficulty + 1 keeps the difficulty.
- Prompt: sample_task's seeded template phrasing. With a paraphrase LM, its rewording replaces the phrasing only if
  every parameter value shown in the phrasing appears in it verbatim. The LM sees only the question, never the answer.
- Only included failures on train tasks are mutated (409 otherwise).
- Idempotent per failure: if variants with this parent_failure_id exist, they are returned in (created_at, task_id)
  order and nothing is created, whatever the seed or n.
"""

import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import psycopg
from openai import OpenAI
from scar_schemas.guards import assert_train_only
from scar_schemas.models import FailureRecord, MutateRequest, Origin, Split, Task

from .config import Settings
from .errors import ApiError, not_found
from .store import store_task
from .tasks import sample_task
from .templates import Template, get_template, load_company

ORIGINAL_COMPANY_VARIANTS = 2
PARAPHRASE_TIMEOUT_S = 30.0
PARAPHRASE_INSTRUCTIONS = (
    "Reword the user's accounting question so that it asks exactly the same thing in different words. Keep every"
    " name, date and amount exactly as written. Do not answer the question. Reply with the reworded question only."
)


@dataclass(frozen=True)
class ParaphraseLM:
    """The PARAPHRASE_LM_* chat model. `client` is an openai.OpenAI (or a test fake with chat.completions.create)."""

    client: Any
    model: str

    def paraphrase(self, question: str) -> str:
        """The reworded question. Any SDK error or malformed reply is a 503 (SPEC 7.4: dependency unavailable)."""
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": PARAPHRASE_INSTRUCTIONS},
                    {"role": "user", "content": question},
                ],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            raise ApiError(503, "paraphrase_lm_unavailable", f"paraphrase LM failed: {exc!r}") from exc


def paraphrase_lm(settings: Settings) -> ParaphraseLM | None:
    """The configured paraphrase LM, or None (no paraphrasing) unless all three PARAPHRASE_LM_* are set."""
    if not (settings.paraphrase_lm_base_url and settings.paraphrase_lm_api_key and settings.paraphrase_lm_model):
        return None
    client = OpenAI(
        base_url=settings.paraphrase_lm_base_url, api_key=settings.paraphrase_lm_api_key, timeout=PARAPHRASE_TIMEOUT_S
    )
    return ParaphraseLM(client, settings.paraphrase_lm_model)


def mutate(conn: psycopg.Connection, req: MutateRequest, lm: ParaphraseLM | None = None) -> list[Task]:
    """Stores and returns the variants of every failure, flattened in request order. Run it in one transaction."""
    # A concurrent call for the same failure (a retry) waits, then returns these variants instead of making a second
    # set. Locks are taken in sorted order so calls that list the same failures in another order cannot deadlock.
    for failure_id in sorted(set(req.failure_ids)):
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"mutate:{failure_id}",))
    return [
        task
        for failure_id in req.failure_ids
        for task in _mutate_failure(conn, failure_id, n=req.variants_per_failure, seed=req.seed, lm=lm)
    ]


def _mutate_failure(
    conn: psycopg.Connection, failure_id: str, *, n: int, seed: int, lm: ParaphraseLM | None
) -> list[Task]:
    row = conn.execute(
        "SELECT f.body, t.body FROM app.failures f JOIN app.tasks t ON t.task_id = f.body->>'task_id'"
        " WHERE f.failure_id = %s",
        (failure_id,),
    ).fetchone()
    if row is None:
        raise not_found("failure", failure_id)
    failure, original = FailureRecord.model_validate(row[0]), Task.model_validate(row[1])
    if not failure.included:
        raise ApiError(
            409, "failure_not_included", f"failure {failure_id} is excluded ({failure.exclusion_reason}); not mutated"
        )
    if original.split != Split.train:
        raise ApiError(
            409,
            "eval_failure",
            f"failure {failure_id} is on {original.split.value}-split task {original.task_id};"
            " variants are made only from train tasks (R6)",
        )
    existing = conn.execute(
        "SELECT body FROM app.tasks WHERE origin = %s AND parent_failure_id = %s ORDER BY created_at, task_id",
        (Origin.variant.value, failure_id),
    ).fetchall()
    if existing:
        return [Task.model_validate(body) for (body,) in existing]

    template = get_template(original.template_id)
    others = [c for c in train_companies(conn) if c != original.company_id]
    random.Random(f"{failure_id}:{seed}:companies").shuffle(others)
    ring = [original.company_id, *others]
    up = original.difficulty + 1  # never in `difficulties` above 3, so the cap is implied
    n_up = (3 * n + 5) // 10 if up in template.difficulties else 0
    variants = []
    for i in range(n):
        start = 1 + (i - ORIGINAL_COMPANY_VARIANTS) % len(others) if i >= ORIGINAL_COMPANY_VARIANTS and others else 0
        companies = ring[start:] + ring[:start]
        difficulties = [up, original.difficulty] if i >= n - n_up else [original.difficulty]
        task = _sample_variant(
            conn,
            template,
            companies,
            difficulties,
            rng=random.Random(f"{failure_id}:{seed}:{i}"),
            failure_id=failure_id,
        )
        if task is None:
            raise ApiError(422, "cannot_mutate", f"no train company supports {template.template_id}")
        variants.append(task)
    if lm is not None:
        variants = _paraphrased(conn, lm, template, variants)

    if any(t.split != Split.train for t in variants):
        raise RuntimeError(f"eval leakage: a variant of {failure_id} uses a non-train company")
    for task in variants:
        store_task(conn, task)
    assert_train_only([t.task_id for t in variants], conn)
    return variants


def _sample_variant(
    conn: psycopg.Connection,
    template: Template,
    companies: list[str],
    difficulties: list[int],
    *,
    rng: random.Random,
    failure_id: str,
) -> Task | None:
    """The first task sample_task makes, trying each difficulty in turn over the companies in order."""
    for difficulty in difficulties:
        for company_id in companies:
            task = sample_task(
                conn,
                template.template_id,
                company_id,
                rng=rng,
                difficulty=difficulty,
                origin=Origin.variant,
                parent_failure_id=failure_id,
            )
            if task is not None:
                return task
    return None


def train_companies(conn: psycopg.Connection) -> list[str]:
    return [c for (c,) in conn.execute("SELECT company_id FROM ledger.companies WHERE split = 'train' ORDER BY 1")]


def _paraphrased(conn: psycopg.Connection, lm: ParaphraseLM, template: Template, tasks: list[Task]) -> list[Task]:
    """Each task with the LM's wording if that keeps every parameter value its phrasing shows. Calls run in parallel."""
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        wordings = list(pool.map(lm.paraphrase, [t.prompt for t in tasks]))
    out = []
    for task, wording in zip(tasks, wordings, strict=True):
        shown = template.placeholders(load_company(conn, task.company_id), task.params).values()
        keep = wording and all(value in wording for value in shown if value in task.prompt)
        out.append(task.model_copy(update={"prompt": wording}) if keep else task)
    return out
