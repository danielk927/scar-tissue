import random
from datetime import timedelta

import pytest
from scar_schemas.constants import EVAL_TEMPERATURE, REGRESSION_MIN_PASS, REGRESSION_SET_SIZE

from scar_train.clients import DependencyError
from scar_train.gate import GateResult, eval_score, promotion_rule, regression_task_ids, run_gate

from .factories import NOW, FakeAgent, insert_run, run_result

BASE = dict(model_version="base", prompt_version="system_v1")
EVAL_IDS = [f"task_e{i}" for i in range(1, 7)]


def stored(conn, run_id, task_id, minute=0, **kw):
    insert_run(conn, run_result(run_id, task_id, created_at=NOW + timedelta(minutes=minute), **kw))


def eval_run(conn, run_id, task_id, minute=0, **kw):
    fields = dict(purpose="eval", split="eval", temperature=EVAL_TEMPERATURE, arm="A", generation=0)
    stored(conn, run_id, task_id, minute, **{**fields, **kw})


def traffic(conn, run_id, task_id, generation, passed=True, **kw):
    stored(
        conn,
        run_id,
        task_id,
        generation,
        purpose="traffic",
        temperature=0.0,
        generation=generation,
        passed=passed,
        **kw,
    )


def agent_passing(conn, failing=()):
    return FakeAgent(conn, lambda req, i: {"passed": req.task_id not in failing})


def test_promotion_rule_edges():
    assert REGRESSION_MIN_PASS == 0.95
    assert promotion_rule(0.51, 0.50, 0.95)
    assert promotion_rule(0.51, 0.50, 95 / 100) and promotion_rule(0.51, 0.50, 19 / 20)
    assert not promotion_rule(0.50, 0.50, 1.0)  # equal eval does not promote
    assert not promotion_rule(0.49, 0.50, 1.0)
    assert not promotion_rule(0.90, 0.50, 94 / 100)


def test_eval_score_reuses_runs_across_lineages_and_resumes(conn):
    eval_run(conn, "run_1", "task_e1", passed=True)  # arm A, generation 0: reused by every lineage
    eval_run(conn, "run_2a", "task_e2", 0, arm="D", passed=False)  # the earliest run counts
    eval_run(conn, "run_2b", "task_e2", 5, arm="D", passed=True)
    eval_run(conn, "run_3", "task_e3", stop_reason="model_error")  # infrastructure failure: run again
    eval_run(conn, "run_4", "task_e4", passed=True, temperature=0.8)  # another temperature
    eval_run(conn, "run_5", "task_e5", passed=True, prompt_version="B-s0-g1")  # another prompt
    eval_run(conn, "run_6", "task_e6", passed=True, purpose="regression")  # another purpose
    agent = agent_passing(conn)

    score = eval_score(conn, agent, **BASE, arm="D", seed=1, generation=0, eval_task_ids=EVAL_IDS)

    assert score == 5 / 6
    assert [r.task_id for r in agent.requests] == ["task_e3", "task_e4", "task_e5", "task_e6"]
    assert agent.batches[0].max_concurrency == 16
    for r in agent.requests:
        assert (r.purpose, r.temperature, r.n) == ("eval", EVAL_TEMPERATURE, 1)
        assert (r.model_version, r.prompt_version, r.arm, r.seed, r.generation) == ("base", "system_v1", "D", 1, 0)

    # Another lineage (and a resumed gate) reuses everything.
    other = agent_passing(conn)
    assert eval_score(conn, other, **BASE, arm="C", seed=2, generation=0, eval_task_ids=EVAL_IDS) == 5 / 6
    assert other.batches == []


def test_eval_outage_raises_and_retry_runs_the_same_tasks(conn):
    down = FakeAgent(conn, lambda req, i: {"stop_reason": "timeout"})
    with pytest.raises(DependencyError):
        eval_score(conn, down, **BASE, arm="D", seed=0, generation=0, eval_task_ids=EVAL_IDS[:2])
    agent = agent_passing(conn, failing={"task_e2"})
    assert eval_score(conn, agent, **BASE, arm="D", seed=0, generation=0, eval_task_ids=EVAL_IDS[:2]) == 0.5
    assert [r.task_id for r in agent.requests] == EVAL_IDS[:2]


def test_regression_selection_is_seeded_sorted_and_from_the_latest_traffic(conn):
    parent = dict(parent_model_version="base", parent_prompt_version="system_v1")
    assert regression_task_ids(conn, arm="D", seed=0, generation=2, **parent) == []

    passed = [f"task_t{i:03d}" for i in range(120)]
    for i, t in enumerate(passed):
        traffic(conn, f"run_t{i:03d}", t, 2)
    traffic(conn, "run_f", "task_f", 2, passed=False)
    traffic(conn, "run_e", "task_e", 2, split="eval")
    traffic(conn, "run_old", "task_old", 1)  # an older generation of the parent
    for i, other in enumerate(
        [{"arm": "C"}, {"seed": 1}, {"model_version": "D-s0-g1"}, {"prompt_version": "B-s0-g1"}, {"purpose": "eval"}]
    ):
        stored(conn, f"run_o{i}", f"task_o{i}", 2, generation=2, passed=True, **{"purpose": "traffic", **other})
    traffic(conn, "run_late1", "task_late1", 3)  # after the gated generation
    traffic(conn, "run_late2", "task_late2", 3)

    chosen = regression_task_ids(conn, arm="D", seed=0, generation=2, **parent)
    assert len(chosen) == REGRESSION_SET_SIZE
    assert chosen == sorted(random.Random(0 * 100 + 2).sample(passed, REGRESSION_SET_SIZE))
    assert regression_task_ids(conn, arm="D", seed=0, generation=2, **parent) == chosen
    # Generation 3's most recent traffic has fewer passed tasks than the set size: take them all.
    assert regression_task_ids(conn, arm="D", seed=0, generation=3, **parent) == ["task_late1", "task_late2"]


def test_run_gate_sends_the_right_purposes_and_temperatures_and_resumes(conn):
    for t in ("task_r1", "task_r2", "task_r3", "task_r4"):
        traffic(conn, f"run_{t}", t, 1)
    traffic(conn, "run_rf", "task_rf", 1, passed=False)
    cand = dict(model_version="D-s0-g1", prompt_version="system_v1", arm="D", seed=0, generation=1)
    stored(conn, "run_reg1", "task_r1", purpose="regression", temperature=0.0, **cand)  # failed; reused
    stored(conn, "run_reg2", "task_r2", passed=True, purpose="regression", temperature=0.0, **{**cand, "generation": 2})
    eval_ids = ["task_e1", "task_e2", "task_e3", "task_e4"]
    agent = agent_passing(conn, failing={"task_e4"})
    gate = dict(
        arm="D",
        seed=0,
        generation=1,
        candidate_model_version="D-s0-g1",
        candidate_prompt_version="system_v1",
        parent_model_version="base",
        parent_prompt_version="system_v1",
        eval_task_ids=eval_ids,
    )

    result = run_gate(conn, agent, **gate, parent_eval_score=0.5)

    assert result == GateResult(eval_score=0.75, regression_score=0.75, promote=False)
    sent = [(r.purpose, r.temperature, r.task_id) for r in agent.requests]
    assert sent == [("eval", 0.0, t) for t in eval_ids] + [
        ("regression", 0.0, t) for t in ("task_r2", "task_r3", "task_r4")
    ]
    for r in agent.requests:
        assert (r.model_version, r.prompt_version, r.arm, r.seed, r.generation, r.n) == (
            "D-s0-g1",
            "system_v1",
            "D",
            0,
            1,
            1,
        )

    # A resumed gate reuses every stored run.
    again = agent_passing(conn)
    assert run_gate(conn, again, **gate, parent_eval_score=0.5) == result
    assert again.batches == []


def test_empty_regression_set_scores_one(conn):
    traffic(conn, "run_f", "task_f", 1, passed=False)  # the parent passed nothing
    agent = agent_passing(conn)
    result = run_gate(
        conn,
        agent,
        arm="B",
        seed=0,
        generation=1,
        candidate_model_version="base",
        candidate_prompt_version="B-s0-g1",
        parent_model_version="base",
        parent_prompt_version="system_v1",
        parent_eval_score=0.5,
        eval_task_ids=["task_e1", "task_e2"],
    )
    assert result == GateResult(eval_score=1.0, regression_score=1.0, promote=True)
    assert {r.purpose for r in agent.requests} == {"eval"}
    assert {r.prompt_version for r in agent.requests} == {"B-s0-g1"}


def test_infrastructure_failures_are_retried_before_they_count(conn):
    attempts: dict[str, int] = {}

    def flaky(req, i):  # task_e1 times out once then passes; task_e2 always times out; others pass at once
        attempts[req.task_id] = attempts.get(req.task_id, 0) + 1
        if req.task_id == "task_e2" or (req.task_id == "task_e1" and attempts["task_e1"] == 1):
            return {"stop_reason": "timeout", "passed": False}
        return {"passed": True}

    agent = FakeAgent(conn, flaky)
    score = eval_score(conn, agent, **BASE, arm="D", seed=0, generation=0, eval_task_ids=EVAL_IDS[:3])
    assert score == 2 / 3  # task_e1 passes on its retry; task_e2 counts as failed after 1 + 2 tries
    assert attempts == {"task_e1": 2, "task_e2": 3, "task_e3": 1}
    assert [len(b.requests) for b in agent.batches] == [3, 2, 1]
