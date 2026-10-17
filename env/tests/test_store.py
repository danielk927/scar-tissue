from datetime import datetime, timezone
from decimal import Decimal

from scar_schemas.ids import new_id
from scar_schemas.models import ExpectedAnswer, Task

from scar_env.store import get_company, get_task, store_task


def test_store_and_get_task(conn, ledger):
    task = Task(
        task_id=new_id("task"),
        company_id="company-901",
        split="train",
        family="aggregation",
        template_id="agg.total_by_account_period",
        params={"account_id": "company-901:acct-rent"},
        prompt="How much rent?",
        expected=ExpectedAnswer(answer_type="number", number=Decimal("2500.00")),
        difficulty=1,
        origin="seed",
        created_at=datetime.now(timezone.utc),
    )
    store_task(conn, task)
    assert get_task(conn, task.task_id) == task
    assert get_task(conn, "task_missing") is None
    row = conn.execute("SELECT split, family, origin FROM app.tasks").fetchone()
    assert row == ("train", "aggregation", "seed")


def test_get_company(conn, ledger):
    assert get_company(conn, "company-903")["split"] == "eval"
    assert get_company(conn, "nope") is None
