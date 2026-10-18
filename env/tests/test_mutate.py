"""Mutator (SPEC 9.6, P1-10): POST /mutate and scar_env.mutate."""

import json
import os
import random
import re
import subprocess
import sys
import threading
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest
from openai import OpenAI
from psycopg.types.json import Jsonb
from scar_schemas.ids import new_id
from scar_schemas.models import FailureCause, FailureRecord, MutateRequest, Origin, RunResult, Split, Task

from scar_env.config import get_settings
from scar_env.mutate import PARAPHRASE_INSTRUCTIONS, ParaphraseLM, mutate, paraphrase_lm
from scar_env.routes.loop import get_paraphrase_lm
from scar_env.store import get_task, store_task
from scar_env.tasks import sample_task
from scar_env.templates import TEMPLATES, load_company

TRAIN = {"company-901", "company-902", "company-904", "company-905"}  # 904 and 905 are clones of 901 (`world`)
NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


# ---------- rows other services write (built with the shared models; production code only reads them) ----------


def run_result(task: Task, **overrides) -> RunResult:
    fields = {
        "run_id": new_id("run"),
        "task_id": task.task_id,
        "company_id": task.company_id,
        "split": task.split,
        "family": task.family,
        "model_version": "base",
        "endpoint_model_id": "base-model",
        "prompt_version": "system_v1",
        "tools_version": "tools_v1",
        "arm": "D",
        "seed": 0,
        "generation": 1,
        "purpose": "traffic",
        "origin": task.origin,
        "attempt_index": 0,
        "temperature": 0.0,
        "messages": [],
        "final_answer": None,
        "stop_reason": "no_submit",
        "passed": False,
        "verify_detail": "no answer submitted",
        "steps": 1,
        "input_tokens": 0,
        "output_tokens": 0,
        "latency_ms": 0,
        "sentry_trace_id": None,
        "created_at": NOW,
    }
    return RunResult(**(fields | overrides))


def insert_run(conn, run: RunResult) -> None:
    """The row the agent's Store.insert_run writes (app.runs belongs to Person 2)."""
    conn.execute(
        "INSERT INTO app.runs (run_id, task_id, model_version, prompt_version, arm, seed, generation, purpose, origin,"
        " split, family, passed, stop_reason, body, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            run.run_id,
            run.task_id,
            run.model_version,
            run.prompt_version,
            run.arm,
            run.seed,
            run.generation,
            run.purpose,
            run.origin.value,
            run.split.value,
            run.family.value,
            run.passed,
            run.stop_reason,
            Jsonb(run.model_dump(mode="json")),
            run.created_at,
        ),
    )


def add_failure(conn, task: Task, failure_id: str | None = None, included: bool = True) -> str:
    """A failed traffic run on `task` and its FailureRecord, as Person 2's miner stores them."""
    run = run_result(task)
    insert_run(conn, run)
    f = FailureRecord(
        failure_id=failure_id or new_id("fail"),
        run_id=run.run_id,
        task_id=task.task_id,
        arm="D",
        seed=0,
        generation=1,
        model_version="base",
        origin=task.origin,
        cause=FailureCause.reasoning_error,
        cause_confidence=0.9,
        cause_probabilities={"reasoning_error": 0.9, "other": 0.1},
        learnable_p=0.8,
        included=included,
        exclusion_reason=None if included else "over_cap",
        jev_model="jev-1.14.0",
        created_at=NOW,
    )
    conn.execute(
        "INSERT INTO app.failures (failure_id, run_id, arm, seed, generation, included, body, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            f.failure_id,
            f.run_id,
            f.arm,
            f.seed,
            f.generation,
            f.included,
            Jsonb(f.model_dump(mode="json")),
            f.created_at,
        ),
    )
    return f.failure_id


# ---------- helpers ----------


def clone_company(conn, company_id: str, *, transactions: bool = True) -> None:
    """A train company with company-901's accounts, vendors and (optionally) transactions under new IDs."""
    args = {"src": "company-901", "new": company_id}
    conn.execute(
        "INSERT INTO ledger.companies SELECT %(new)s, name || ' ' || %(new)s, industry, 'train'"
        " FROM ledger.companies WHERE company_id = %(src)s",
        args,
    )
    conn.execute(
        "INSERT INTO ledger.accounts SELECT replace(account_id, %(src)s, %(new)s), %(new)s, name, account_type"
        " FROM ledger.accounts WHERE company_id = %(src)s",
        args,
    )
    conn.execute(
        "INSERT INTO ledger.vendors SELECT replace(vendor_id, %(src)s, %(new)s), %(new)s, name"
        " FROM ledger.vendors WHERE company_id = %(src)s",
        args,
    )
    if transactions:
        conn.execute(
            "INSERT INTO ledger.transactions SELECT replace(txn_id, %(src)s, %(new)s), %(new)s, txn_date, amount,"
            " replace(account_id, %(src)s, %(new)s), replace(vendor_id, %(src)s, %(new)s), description, txn_type"
            " FROM ledger.transactions WHERE company_id = %(src)s",
            args,
        )


@pytest.fixture
def world(conn, ledger):
    """The synthetic ledger (train 901 and 902, eval 903) plus train companies 904 and 905."""
    clone_company(conn, "company-904")
    clone_company(conn, "company-905")
    return ledger


@pytest.fixture
def api(client):
    """The app with paraphrasing off, whatever the developer's .env says."""
    client.app.dependency_overrides[get_paraphrase_lm] = lambda: None
    return client


def original_task(
    conn, template_id: str = "agg.total_by_account_period", company_id: str = "company-901", difficulty: int = 1
) -> Task:
    task = sample_task(
        conn, template_id, company_id, rng=random.Random(3), difficulty=difficulty, origin=Origin.generated
    )
    store_task(conn, task)
    return task


def post(api, failure_ids: list[str], n: int = 10, seed: int = 0):
    return api.post("/mutate", json={"failure_ids": failure_ids, "variants_per_failure": n, "seed": seed})


def variants(api, failure_ids: list[str], n: int = 10, seed: int = 0) -> list[Task]:
    resp = post(api, failure_ids, n, seed)
    assert resp.status_code == 200, resp.text
    return [Task.model_validate(t) for t in resp.json()]


def variant_count(conn) -> int:
    return conn.execute("SELECT count(*) FROM app.tasks WHERE origin = 'variant'").fetchone()[0]


def template_prompts(conn, task: Task) -> list[str]:
    template, company = TEMPLATES[task.template_id], load_company(conn, task.company_id)
    return [template.render(company, task.params, p) for p in template.phrasings]


# ---------- variants ----------


def test_variants_are_stored_train_tasks_of_the_same_template(api, conn, world):
    original = original_task(conn)
    fid = add_failure(conn, original)
    tasks = variants(api, [fid])
    assert len(tasks) == 10 and len({t.task_id for t in tasks}) == 10
    for t in tasks:
        assert (t.template_id, t.family) == (original.template_id, original.family)
        assert (t.origin, t.split, t.parent_failure_id) == (Origin.variant, Split.train, fid)
        assert get_task(conn, t.task_id) == t
        assert t.prompt in template_prompts(conn, t)
        answer = TEMPLATES[t.template_id].answer(conn, load_company(conn, t.company_id), t.params)
        assert answer.expected == t.expected and not answer.degenerate  # the template's SQL, never an LLM (R5)
    assert len({json.dumps(t.params, sort_keys=True) for t in tasks}) == 10


def test_ten_variants_span_the_original_and_the_other_train_companies(api, conn, world):
    fid = add_failure(conn, original_task(conn))
    companies = [t.company_id for t in variants(api, [fid])]
    assert companies[:2] == ["company-901", "company-901"]
    others = companies[2:5]
    assert set(others) == TRAIN - {"company-901"}
    assert companies[2:] == (others * 3)[:8]  # round robin in a seeded order
    assert "company-903" not in companies  # the eval company is never used (R6)


def test_two_variants_both_use_the_original_company(api, conn, world):
    fid = add_failure(conn, original_task(conn, company_id="company-902"))
    assert [t.company_id for t in variants(api, [fid], n=2)] == ["company-902", "company-902"]


def test_a_company_that_cannot_support_the_template_is_skipped(api, conn, world):
    clone_company(conn, "company-906", transactions=False)  # a train company with no transactions
    fid = add_failure(conn, original_task(conn))
    counts = Counter(t.company_id for t in variants(api, [fid]))
    assert "company-906" not in counts
    assert set(counts) == TRAIN and counts["company-901"] >= 2 and sum(counts.values()) == 10


@pytest.mark.parametrize(
    ("template_id", "difficulty", "n", "expected"),
    [
        ("agg.total_by_account_period", 1, 10, [1] * 7 + [2] * 3),
        ("agg.total_by_account_period", 2, 10, [2] * 7 + [3] * 3),
        ("agg.total_by_account_period", 3, 10, [3] * 10),  # capped at 3
        ("agg.top_vendor_by_spend", 2, 10, [2] * 7 + [3] * 3),
        ("anom.duplicate_payments", 2, 10, [2] * 10),  # the template supports only difficulty 2
        ("agg.total_by_account_period", 1, 2, [1, 2]),  # round(0.3 * 2) = 1
        ("agg.total_by_account_period", 1, 1, [1]),
    ],
)
def test_difficulty_rises_for_the_last_thirty_percent(api, conn, world, template_id, difficulty, n, expected):
    fid = add_failure(conn, original_task(conn, template_id, difficulty=difficulty))
    assert [t.difficulty for t in variants(api, [fid], n=n)] == expected


def test_difficulty_stays_when_no_company_can_make_the_harder_variant(api, conn, world):
    conn.execute("DELETE FROM ledger.transactions WHERE txn_date > '2026-02-28'")  # 59 days: too short for 93+
    fid = add_failure(conn, original_task(conn, difficulty=2))
    assert [t.difficulty for t in variants(api, [fid])] == [2] * 10


DETERMINISM_SCRIPT = """
import json, sys
import psycopg
from scar_schemas.models import MutateRequest
from scar_env.mutate import mutate

url, failure_id, seed = sys.argv[1], sys.argv[2], int(sys.argv[3])
with psycopg.connect(url) as conn:
    tasks = mutate(conn, MutateRequest(failure_ids=[failure_id], variants_per_failure=10, seed=seed))
    conn.rollback()  # every call starts from the same database
print(json.dumps([t.model_dump(mode="json", exclude={"task_id", "created_at"}) for t in tasks]))
"""


def mutate_in_new_process(db_url: str, failure_id: str, seed: int, hash_seed: int) -> list[dict]:
    out = subprocess.run(
        [sys.executable, "-c", DETERMINISM_SCRIPT, db_url, failure_id, str(seed)],
        env=os.environ | {"PYTHONHASHSEED": str(hash_seed)},
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_same_seed_gives_the_same_variants_in_separate_processes(db_url, conn, world):
    fid = add_failure(conn, original_task(conn), failure_id="fail_fixed")
    first = mutate_in_new_process(db_url, fid, seed=5, hash_seed=1)
    again = mutate_in_new_process(db_url, fid, seed=5, hash_seed=2)  # str hashes differ, variants must not
    other = mutate_in_new_process(db_url, fid, seed=6, hash_seed=1)
    assert len(first) == 10 and first == again
    assert [t["params"] for t in other] != [t["params"] for t in first]
    assert variant_count(conn) == 0


# ---------- paraphrasing ----------


class FakeOpenAI:
    """Stands in for openai.OpenAI: records chat.completions.create calls and replies with reword(question)."""

    def __init__(self, reword):
        self.reword, self.calls = reword, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.reword(kwargs["messages"][-1]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def with_lm(api, reword) -> FakeOpenAI:
    fake = FakeOpenAI(reword)
    api.app.dependency_overrides[get_paraphrase_lm] = lambda: ParaphraseLM(fake, "fake-model")
    return fake


def test_a_paraphrase_that_keeps_every_value_replaces_the_phrasing(api, conn, world):
    fake = with_lm(api, lambda q: f"Quick question for the books: {q}")
    fid = add_failure(conn, original_task(conn))
    tasks = variants(api, [fid], n=3)
    calls = {call["messages"][-1]["content"]: call for call in fake.calls}  # LM calls run in parallel
    assert len(fake.calls) == len(calls) == 3
    for t in tasks:
        question = t.prompt.removeprefix("Quick question for the books: ")
        assert question != t.prompt and question in template_prompts(conn, t)
        assert get_task(conn, t.task_id).prompt == t.prompt
        call = calls[question]
        assert call["model"] == "fake-model" and call["temperature"] == 0
        assert call["messages"] == [
            {"role": "system", "content": PARAPHRASE_INSTRUCTIONS},
            {"role": "user", "content": question},
        ]
        assert str(t.expected.number) not in json.dumps(call)  # the LM never sees the answer


def test_a_paraphrase_that_drops_a_value_is_rejected(api, conn, world):
    fake = with_lm(api, lambda q: re.sub(r"\d{4}-\d{2}-\d{2}", "the first day", q, count=1))
    fid = add_failure(conn, original_task(conn))
    tasks = variants(api, [fid], n=3)
    assert sorted(t.prompt for t in tasks) == sorted(call["messages"][-1]["content"] for call in fake.calls)
    for t in tasks:
        assert t.prompt in template_prompts(conn, t)


def test_a_failing_paraphrase_lm_is_a_503_and_stores_nothing(api, conn, world):
    def down(_):
        raise ConnectionError("connection refused")

    with_lm(api, down)
    fid = add_failure(conn, original_task(conn))
    resp = post(api, [fid])
    assert resp.status_code == 503 and resp.json()["error"] == "paraphrase_lm_unavailable"
    assert variant_count(conn) == 0


@pytest.mark.parametrize("missing", ["paraphrase_lm_base_url", "paraphrase_lm_api_key", "paraphrase_lm_model"])
def test_paraphrasing_is_off_unless_all_three_settings_are_set(missing):
    full = {"paraphrase_lm_base_url": "http://lm.test/v1", "paraphrase_lm_api_key": "k", "paraphrase_lm_model": "m"}
    assert paraphrase_lm(replace(get_settings(), **(full | {missing: None}))) is None


def test_paraphrasing_uses_an_openai_client_from_the_settings():
    full = {"paraphrase_lm_base_url": "http://lm.test/v1", "paraphrase_lm_api_key": "k", "paraphrase_lm_model": "m"}
    lm = paraphrase_lm(replace(get_settings(), **full))  # builds the client only; nothing is sent
    assert isinstance(lm.client, OpenAI) and lm.model == "m"
    assert str(lm.client.base_url) == "http://lm.test/v1/" and lm.client.api_key == "k"


# ---------- request handling and guards ----------


def test_mutate_is_idempotent_per_failure(api, conn, world):
    fid = add_failure(conn, original_task(conn))
    first = post(api, [fid]).json()
    again = post(api, [fid], n=4, seed=9)  # a retry, or learn-step C asking again with other arguments
    assert again.status_code == 200 and again.json() == first
    assert variant_count(conn) == 10


def test_a_concurrent_call_waits_and_returns_the_same_variants(db_url, conn, world):
    fid = add_failure(conn, original_task(conn))
    req = MutateRequest(failure_ids=[fid], variants_per_failure=3, seed=0)
    with psycopg.connect(db_url) as first, psycopg.connect(db_url) as second:
        made = mutate(first, req)  # not committed yet
        result = {}
        retry = threading.Thread(target=lambda: result.update(tasks=mutate(second, req)))
        retry.start()
        retry.join(timeout=1)
        assert retry.is_alive()  # waiting on the first call's lock
        first.commit()
        retry.join(timeout=10)
        assert [t.task_id for t in result["tasks"]] == [t.task_id for t in made]
    assert variant_count(conn) == 3


def test_variants_are_flattened_in_request_order(api, conn, world):
    f1 = add_failure(conn, original_task(conn))
    f2 = add_failure(conn, original_task(conn, "agg.total_by_vendor_period", "company-902"))
    tasks = variants(api, [f2, f1], n=3)
    assert [t.parent_failure_id for t in tasks] == [f2] * 3 + [f1] * 3
    assert [t.template_id for t in tasks] == ["agg.total_by_vendor_period"] * 3 + ["agg.total_by_account_period"] * 3


def test_an_unknown_failure_is_a_404_and_stores_nothing(api, conn, world):
    fid = add_failure(conn, original_task(conn))
    resp = post(api, [fid, "fail_nope"])
    assert resp.status_code == 404
    assert resp.json() == {"error": "not_found", "detail": "unknown failure: fail_nope"}
    assert variant_count(conn) == 0


def test_a_failure_on_an_eval_task_is_refused(api, conn, world):
    fid = add_failure(conn, original_task(conn, company_id="company-903"))
    resp = post(api, [fid])
    assert resp.status_code == 409 and resp.json()["error"] == "eval_failure"
    assert variant_count(conn) == 0


def test_an_excluded_failure_is_refused(api, conn, world):
    fid = add_failure(conn, original_task(conn), included=False)
    resp = post(api, [fid])
    assert resp.status_code == 409 and resp.json()["error"] == "failure_not_included"
    assert "over_cap" in resp.json()["detail"] and variant_count(conn) == 0


def test_variants_per_failure_must_be_positive(api, conn, world):
    fid = add_failure(conn, original_task(conn))
    resp = post(api, [fid], n=0)
    assert resp.status_code == 422 and resp.json()["error"] == "invalid_variants_per_failure"


def test_a_non_train_company_is_caught_before_anything_is_stored(conn, world, monkeypatch):
    fid = add_failure(conn, original_task(conn))
    monkeypatch.setattr("scar_env.mutate.train_companies", lambda conn: ["company-901", "company-903"])
    with pytest.raises(RuntimeError, match="eval leakage"):
        mutate(conn, MutateRequest(failure_ids=[fid], variants_per_failure=3, seed=0))
    assert variant_count(conn) == 0


def test_stored_variants_pass_the_shared_train_only_guard(conn, world, monkeypatch):
    seen = []
    monkeypatch.setattr("scar_env.mutate.assert_train_only", lambda task_ids, conn: seen.append(task_ids))
    fid = add_failure(conn, original_task(conn))
    tasks = mutate(conn, MutateRequest(failure_ids=[fid], variants_per_failure=3, seed=0))
    assert seen == [[t.task_id for t in tasks]]
