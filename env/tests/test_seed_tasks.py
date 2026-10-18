import random
from collections import Counter

import pytest
from scar_schemas.models import Origin

from scar_env import seed_tasks
from scar_env.load_ledger import assign_splits
from scar_env.store import get_task
from scar_env.tasks import make_task
from scar_env.templates import TEMPLATES

REAL_SPLITS = assign_splits([f"company-{i:03d}" for i in range(1, 101)])


def test_seeds_cover_every_family_on_train_companies():
    assert len(seed_tasks.SEEDS) == 30
    families = Counter(TEMPLATES[t].family.value for t, *_ in seed_tasks.SEEDS)
    assert families == {"aggregation": 10, "anomaly": 10, "bookkeeping": 10}
    assert {t for t, *_ in seed_tasks.SEEDS} == set(TEMPLATES)
    for template_id, company_id, difficulty, params, prompt in seed_tasks.SEEDS:
        assert REAL_SPLITS[company_id] == "train", company_id
        assert difficulty in TEMPLATES[template_id].difficulties
        assert all(v.startswith(f"{company_id}:") for k, v in params.items() if k.endswith("_id")), params
        assert "{" not in prompt and len(prompt) > 40


def test_fixture_matches_seeds():
    tasks = seed_tasks.read_fixture()
    assert len({t.task_id for t in tasks}) == 30
    got = [(t.template_id, t.company_id, t.difficulty, t.params, t.prompt) for t in tasks]
    assert got == [(t, c, d, p, prompt) for t, c, d, p, prompt in seed_tasks.SEEDS]
    assert {t.origin for t in tasks} == {Origin.seed} and {t.split.value for t in tasks} == {"train"}


def _synthetic_seed(conn, company_id="company-901"):
    return make_task(
        conn,
        "agg.total_by_account_period",
        company_id,
        {"account_id": f"{company_id}:acct-rent", "start_date": "2026-01-01", "end_date": "2026-03-31"},
        origin=Origin.seed,
        difficulty=2,
        rng=random.Random(0),
        prompt="How much rent did we pay in the first quarter of 2026?",
    )


def test_load_is_idempotent_and_round_trips(conn, ledger, tmp_path):
    tasks = [_synthetic_seed(conn), _synthetic_seed(conn, "company-902")]
    path = tmp_path / "seeds.jsonl"
    seed_tasks.write_fixture(tasks, path)
    assert seed_tasks.read_fixture(path) == tasks
    assert seed_tasks.load_tasks(conn, tasks) == 2
    assert seed_tasks.load_tasks(conn, tasks) == 0
    assert get_task(conn, tasks[0].task_id) == tasks[0]


def test_load_refuses_drifted_answers_and_eval_companies(conn, ledger):
    drifted = _synthetic_seed(conn)
    drifted.expected.number += 1
    with pytest.raises(RuntimeError, match="drifted"):
        seed_tasks.load_tasks(conn, [_synthetic_seed(conn, "company-902"), drifted])
    with pytest.raises(RuntimeError, match="eval-split"):
        seed_tasks.load_tasks(conn, [_synthetic_seed(conn, "company-903")])
    assert conn.execute("SELECT count(*) FROM app.tasks").fetchone()[0] == 0
