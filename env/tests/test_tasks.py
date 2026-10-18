"""Task construction (scar_env.tasks) and the task routes (SPEC 8.5): templates, generate, from_params, reads."""

import hashlib
import random
import re
from collections import Counter
from decimal import Decimal

import pytest
from scar_schemas.models import AnswerType, FromParamsResult, Origin, Split, Task, TaskFamily

from scar_env.tasks import generate_tasks, make_task, sample_task
from scar_env.templates import TEMPLATES, ParamError, load_company
from scar_env.templates.base import money

RENT_Q1 = {"account_id": "company-901:acct-rent", "start_date": "2026-01-01", "end_date": "2026-03-31"}
JUDGE = {
    "template_id": "agg.total_by_account_period",
    "company_id": "company-901",
    "params": RENT_Q1,
    "origin": "judge",
}


def strip(task: Task | dict) -> dict:
    """A task without the two fields that may differ between runs with the same seed."""
    body = task.model_dump(mode="json") if isinstance(task, Task) else task
    return {k: v for k, v in body.items() if k not in ("task_id", "created_at")}


def task_count(conn) -> int:
    return conn.execute("SELECT count(*) FROM app.tasks").fetchone()[0]


def generate(client, **overrides):
    return client.post(
        "/tasks/generate", json={"split": "train", "n": 30, "seed": 42, "origin": "generated"} | overrides
    )


# ---------- make_task / sample_task ----------


def test_make_task_validates_computes_and_takes_split_from_the_ledger(conn, ledger):
    params = {"account_id": "company-903:acct-rent", "start_date": "2026-01-01", "end_date": "2026-03-31"}
    a = make_task(
        conn,
        "agg.total_by_account_period",
        "company-903",
        params,
        origin=Origin.eval,
        difficulty=1,
        rng=random.Random(5),
    )
    b = make_task(
        conn,
        "agg.total_by_account_period",
        "company-903",
        params,
        origin=Origin.eval,
        difficulty=1,
        rng=random.Random(5),
    )
    assert strip(a) == strip(b) and a.task_id != b.task_id and a.task_id.startswith("task_")
    assert a.split == Split.eval and a.family == TaskFamily.aggregation and a.origin == Origin.eval
    assert a.expected.number == Decimal("6000.00")  # three 2000.00 rent bills
    template, company = TEMPLATES["agg.total_by_account_period"], load_company(conn, "company-903")
    assert a.prompt in [template.render(company, a.params, p) for p in template.phrasings]
    assert a.created_at.tzinfo is not None


def test_make_task_uses_a_given_prompt_and_normalizes_params(conn, ledger):
    params = {
        "expense_account_id": "company-902:acct-utilities",
        "liability_account_id": "company-902:acct-accounts-payable",
        "amount": 500,
        "entry_date": "2026-03-31",
    }
    task = make_task(
        conn,
        "book.accrual",
        "company-902",
        params,
        origin=Origin.variant,
        difficulty=1,
        rng=random.Random(0),
        prompt="Accrue 500 of utilities.",
        parent_failure_id="fail_x",
    )
    assert task.prompt == "Accrue 500 of utilities." and task.parent_failure_id == "fail_x"
    assert task.params["amount"] == "500.00" and task.split == Split.train
    assert task.expected.journal_entries[0].lines[0].debit == Decimal("500.00")


def test_make_task_rejects_bad_input(conn, ledger):
    kw = {"origin": Origin.judge, "difficulty": 1, "rng": random.Random(0)}
    with pytest.raises(ParamError, match="unknown company_id"):
        make_task(conn, "agg.total_by_account_period", "company-999", RENT_Q1, **kw)
    with pytest.raises(ParamError, match="unknown template_id"):
        make_task(conn, "agg.nope", "company-901", RENT_Q1, **kw)
    with pytest.raises(ParamError, match="end_date: Field required"):
        make_task(conn, "agg.total_by_account_period", "company-901", {"account_id": "company-901:acct-rent"}, **kw)
    with pytest.raises(ValueError, match=r"supports difficulties \(3,\), not 1"):
        make_task(conn, "agg.share_of_expense", "company-901", RENT_Q1, **kw)


def test_sample_task_is_deterministic_for_a_seed(conn, ledger):
    for template_id, template in TEMPLATES.items():
        for difficulty in template.difficulties:
            runs = [
                sample_task(
                    conn,
                    template_id,
                    "company-902",
                    rng=random.Random(7),
                    difficulty=difficulty,
                    origin=Origin.generated,
                )
                for _ in range(2)
            ]
            assert strip(runs[0]) == strip(runs[1]) and runs[0].task_id != runs[1].task_id


# ---------- simple reads ----------


def test_list_templates_returns_the_catalog_in_order(client):
    body = client.get("/templates").json()
    assert body == [t.spec.model_dump(mode="json") for t in TEMPLATES.values()]
    assert [s["template_id"] for s in body][:2] == ["agg.total_by_account_period", "agg.total_by_vendor_period"]


def test_get_task_and_unknown_ids(client, conn, ledger):
    stored = client.post("/tasks/from_params", json=JUDGE).json()["task"]
    assert client.get(f"/tasks/{stored['task_id']}").json() == stored
    r = client.get("/tasks/task_missing")
    assert r.status_code == 404 and r.json() == {"error": "not_found", "detail": "unknown task: task_missing"}
    r = client.get("/eval_sets/eval_missing")
    assert r.status_code == 404 and r.json()["error"] == "not_found"


# ---------- POST /tasks/generate ----------


def test_generate_train_pool_is_stratified_and_fills_positions(client, conn, ledger):
    r = generate(client, n=90)
    assert r.status_code == 200, r.text
    tasks = [Task.model_validate(t) for t in r.json()]
    assert len(tasks) == 90 and task_count(conn) == 90
    assert [t.family for t in tasks[:6]] == list(TaskFamily) * 2  # interleaved by family
    assert {(t.origin, t.split) for t in tasks} == {(Origin.generated, Split.train)}
    for family in TaskFamily:
        mine = [t for t in tasks if t.family == family]
        assert len(mine) == 30
        assert Counter(t.difficulty for t in mine) == {1: 10, 2: 10, 3: 10}
        assert Counter(t.company_id for t in mine) == {"company-901": 15, "company-902": 15}
        assert {t.template_id for t in mine} == {tid for tid, t in TEMPLATES.items() if t.family == family}
        for t in mine:
            assert t.difficulty in TEMPLATES[t.template_id].difficulties
    pool = conn.execute("SELECT position, task_id FROM app.train_pool ORDER BY position").fetchall()
    assert pool == [(i, t.task_id) for i, t in enumerate(tasks)]
    assert client.get(f"/tasks/{tasks[5].task_id}").json() == r.json()[5]


def test_generate_is_deterministic_for_the_same_request(client, conn, ledger):
    first = generate(client, n=24).json()
    conn.execute("TRUNCATE app.train_pool, app.tasks")
    second = generate(client, n=24).json()
    assert [strip(t) for t in first] == [strip(t) for t in second]
    assert {t["task_id"] for t in first}.isdisjoint(t["task_id"] for t in second)
    conn.execute("TRUNCATE app.train_pool, app.tasks")
    other_seed = generate(client, n=24, seed=43).json()
    assert [strip(t) for t in other_seed] != [strip(t) for t in first]


def test_generate_family_filter_and_uneven_n(client, conn, ledger):
    tasks = generate(client, n=7, family="bookkeeping").json()
    assert [t["family"] for t in tasks] == ["bookkeeping"] * 7
    assert [t["template_id"] for t in tasks[:3]] == ["book.accrual", "book.prepaid_amortization", "book.reclassify"]
    conn.execute("TRUNCATE app.train_pool, app.tasks")
    assert [t["family"] for t in generate(client, n=4).json()] == [
        "aggregation",
        "anomaly",
        "bookkeeping",
        "aggregation",
    ]


def test_generate_refuses_a_second_train_pool(client, conn, ledger):
    assert generate(client, n=3).status_code == 200
    r = generate(client, n=3, seed=1)
    assert r.status_code == 409 and r.json()["error"] == "train_pool_exists"
    assert task_count(conn) == 3


def test_generate_eval_set_once(client, conn, ledger):
    r = generate(client, split="eval", origin="eval", n=30, seed=1234)
    assert r.status_code == 200, r.text
    tasks = [Task.model_validate(t) for t in r.json()]
    assert {(t.company_id, t.split, t.origin) for t in tasks} == {("company-903", Split.eval, Origin.eval)}
    for family in TaskFamily:
        assert Counter(t.difficulty for t in tasks if t.family == family) == {1: 4, 2: 3, 3: 3}
    ids = sorted(t.task_id for t in tasks)
    assert client.get("/eval_sets/eval_v1").json() == {
        "eval_set_id": "eval_v1",
        "task_ids": ids,
        "sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
    }
    again = generate(client, split="eval", origin="eval", n=30, seed=1234)
    assert again.status_code == 409 and again.json()["error"] == "eval_set_exists"
    assert task_count(conn) == 30
    assert conn.execute("SELECT count(*) FROM app.train_pool").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"split": "eval"}, "split_origin_mismatch"),
        ({"origin": "eval"}, "split_origin_mismatch"),
        ({"origin": "variant"}, "invalid_origin"),
        ({"origin": "judge"}, "invalid_origin"),
        ({"split": "eval", "origin": "eval", "n": 31}, "invalid_eval_request"),
        ({"split": "eval", "origin": "eval", "n": 30, "family": "anomaly"}, "invalid_eval_request"),
        ({"n": 0}, "invalid_n"),
        ({"split": "bogus"}, "invalid_input"),
    ],
)
def test_generate_rejects_bad_requests(client, conn, ledger, overrides, error):
    r = generate(client, **overrides)
    assert r.status_code == 422 and r.json()["error"] == error
    assert task_count(conn) == 0


def test_generate_moves_to_the_next_company_when_one_cannot_support_a_slot(conn, ledger):
    conn.execute("DELETE FROM ledger.transactions WHERE company_id = 'company-902' AND amount = 2950.37")  # no outlier
    tasks = generate_tasks(conn, split=Split.train, n=12, seed=0, origin=Origin.generated, family=TaskFamily.anomaly)
    outliers = [t for t in tasks if t.difficulty == 3]
    assert len(outliers) == 4 and {(t.template_id, t.company_id) for t in outliers} == {
        ("anom.vendor_outliers", "company-901")
    }


def test_generate_reports_a_slot_no_company_supports(client, conn, ledger):
    conn.execute("DELETE FROM ledger.transactions WHERE amount = 2950.37")  # no vendor outliers anywhere
    r = generate(client, n=3, family="anomaly")
    assert r.status_code == 422 and r.json()["error"] == "cannot_generate"
    assert task_count(conn) == 0 and conn.execute("SELECT count(*) FROM app.train_pool").fetchone()[0] == 0


# ---------- POST /tasks/from_params ----------


def test_from_params_stores_a_judge_task_with_the_judges_wording(client, conn, ledger):
    r = client.post("/tasks/from_params", json=JUDGE | {"prompt": "How much rent did we pay in Q1 2026?"})
    body = FromParamsResult.model_validate(r.json())
    assert body.valid and body.error is None
    task = body.task
    assert (task.origin, task.split, task.difficulty) == (Origin.judge, Split.train, 1)
    assert task.prompt == "How much rent did we pay in Q1 2026?" and task.expected.number == Decimal("7500.00")
    assert body.interpretation == (
        "Total of all Alpha Supply Co transactions in Rent and Lease from 2026-01-01 to 2026-03-31, dates inclusive."
    )
    assert client.get(f"/tasks/{task.task_id}").json() == r.json()["task"]


def test_from_params_without_a_prompt_uses_a_template_phrasing(client, conn, ledger):
    first = client.post("/tasks/from_params", json=JUDGE).json()["task"]
    second = client.post("/tasks/from_params", json=JUDGE).json()["task"]
    template, company = TEMPLATES[JUDGE["template_id"]], load_company(conn, "company-901")
    assert first["prompt"] == second["prompt"]
    assert first["prompt"] in [template.render(company, first["params"], p) for p in template.phrasings]
    assert task_count(conn) == 2


def test_from_params_dry_run_validates_without_storing(client, conn, ledger):
    body = client.post("/tasks/from_params", json=JUDGE | {"dry_run": True}).json()
    assert body["valid"] is True and body["task"] is None and body["error"] is None
    assert body["interpretation"].startswith("Total of all Alpha Supply Co transactions in Rent and Lease")
    assert task_count(conn) == 0


def test_from_params_rejects_eval_companies(client, conn, ledger):
    params = {"account_id": "company-903:acct-rent", "start_date": "2026-01-01", "end_date": "2026-03-31"}
    for dry_run in (True, False):
        body = client.post(
            "/tasks/from_params", json=JUDGE | {"company_id": "company-903", "params": params, "dry_run": dry_run}
        ).json()
        assert body["valid"] is False and body["task"] is None and "eval-split company" in body["error"]
    assert task_count(conn) == 0


def test_from_params_requires_origin_judge(client, conn, ledger):
    r = client.post("/tasks/from_params", json=JUDGE | {"origin": "generated"})
    assert r.status_code == 422 and r.json()["error"] == "invalid_origin"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"template_id": "agg.nope"}, "unknown template_id: agg.nope"),
        ({"company_id": "company-999"}, "unknown company_id: company-999"),
        ({"params": RENT_Q1 | {"account_id": "company-902:acct-rent"}}, "is not an account of company-901"),
        (
            {
                "template_id": "agg.total_by_vendor_period",
                "params": {**RENT_Q1, "vendor_id": "company-902:vend-insurer"},
            },
            "account_id: Extra inputs are not permitted",
        ),
        (
            {
                "template_id": "agg.total_by_vendor_period",
                "params": {
                    "vendor_id": "company-902:vend-insurer",
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-31",
                },
            },
            "is not a vendor of company-901",
        ),
        (
            {
                "template_id": "book.reclassify",
                "params": {"txn_id": "company-902:bill-00001:1", "to_account_id": "company-901:acct-rent"},
            },
            "is not a transaction of company-901",
        ),
        ({"params": RENT_Q1 | {"start_date": "2026-02-30"}}, "start_date: 2026-02-30 is not a valid date"),
        ({"params": RENT_Q1 | {"start_date": "01/01/2026"}}, "start_date: String should match pattern"),
        ({"params": RENT_Q1 | {"start_date": "2026-04-01"}}, "start_date 2026-04-01 is after end_date 2026-03-31"),
        ({"params": RENT_Q1 | {"start_date": "2027-01-01", "end_date": "2027-03-31"}}, "is outside company-901's data"),
        (
            {
                "template_id": "agg.mom_change_account",
                "params": {"account_id": "company-901:acct-rent", "month": "2026-01"},
            },
            "month before",
        ),
        ({"params": {"account_id": "company-901:acct-rent", "start_date": "2026-01-01"}}, "end_date: Field required"),
        ({"params": RENT_Q1 | {"note": "x"}}, "note: Extra inputs are not permitted"),
        ({"params": RENT_Q1 | {"account_id": 5}}, "account_id: Input should be a valid string"),
    ],
)
def test_from_params_rejects_invalid_params_with_clear_errors(client, conn, ledger, overrides, message):
    r = client.post("/tasks/from_params", json=JUDGE | overrides)
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False and body["task"] is None and body["interpretation"] == ""
    assert message in body["error"], body["error"]
    assert task_count(conn) == 0


def answer_leaks(text: str, expected, params: dict) -> list[str]:
    """Pieces of the expected answer that appear in an interpretation."""
    if expected.answer_type == AnswerType.number:
        numbers = re.findall(r"(?<![\w.,-])\d[\d,]*(?:\.\d+)?(?![\w-])", text)
        return [n for n in numbers if Decimal(n.replace(",", "")) == expected.number]
    if expected.answer_type == AnswerType.id_set:
        return [i for i in expected.id_set if i in text]
    given = {Decimal(params[k]) for k in ("amount", "total") if k in params}  # amounts the question itself states
    leaks = []
    for entry in expected.journal_entries:
        if "txn_id" in params and entry.entry_date in text:  # reclassify: the date comes from the ledger
            leaks.append(entry.entry_date)
        for line in entry.lines:
            leaks += [line.account_id] if line.account_id in text else []
            amount = line.debit or line.credit
            if amount not in given and (money(amount) in text or str(amount) in text):
                leaks.append(str(amount))
    return leaks


def test_interpretations_never_contain_the_answer(client, conn, ledger):
    for template_id, template in TEMPLATES.items():
        for cid in ("company-901", "company-902"):
            company = load_company(conn, cid)
            for seed in range(5):
                params = template.sample(conn, company, random.Random(seed), template.difficulty)
                body = client.post(
                    "/tasks/from_params",
                    json={
                        "template_id": template_id,
                        "company_id": cid,
                        "params": params,
                        "origin": "judge",
                        "dry_run": True,
                    },
                ).json()
                assert body["valid"], body
                expected = template.answer(conn, company, template.validate(conn, company, params)).expected
                assert company.name in body["interpretation"]
                assert answer_leaks(body["interpretation"], expected, params) == [], (
                    template_id,
                    body["interpretation"],
                )
    assert task_count(conn) == 0
