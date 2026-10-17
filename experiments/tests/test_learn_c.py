import hashlib
from collections import Counter

import gepa
import pytest
from scar_schemas.constants import GEPA_MAX_METRIC_CALLS, GEPA_TRAIN_EXTRA, GEPA_VAL_SIZE, VARIANTS_PER_FAILURE
from scar_schemas.models import LearnRequest, MutateRequest, PromptVersion

from scar_experiments.errors import NotFoundError
from scar_experiments.learn_c import AgentPromptAdapter, gepa_sets, learn_c
from scar_experiments.prompts import candidate_version_id, get_prompt_version, store_prompt_version

from .factories import NOW, SYSTEM_V1, FakeAgent, FakeEnv, FakeLM, add_failure, insert_pool, insert_task, make_task

HINT = "Always check the date range twice."
# gepa's default proposal template (strategies/instruction_proposal.py) says this right before it ends.
REFLECTION_ASK = "Provide the new instructions within ``` blocks."
# gepa 0.1.4 can finish one iteration past the budget: a parent and a child minibatch (3 each) plus a full valset.
MAX_OVERSHOOT = 2 * 3 + GEPA_VAL_SIZE


def _req(failures, generation=1, seed=0, current="system_v1") -> LearnRequest:
    return LearnRequest(
        arm="C",
        seed=seed,
        generation=generation,
        model_version="base",
        current_prompt_version=current,
        failure_ids=[f.failure_id for f in failures],
    )


def _hinting_lm() -> FakeLM:
    def reply(prompt: str) -> str:
        assert REFLECTION_ASK in prompt
        return f"Here is the improved instruction:\n```\n{SYSTEM_V1}\n{HINT}\n```"

    return FakeLM(reply)


def _candidates(conn):
    rows = conn.execute(
        "SELECT prompt_version, body FROM app.prompt_versions WHERE prompt_version LIKE 'C-cand-%%' ORDER BY 1"
    ).fetchall()
    return {v: PromptVersion.model_validate(b) for v, b in rows}


def test_gepa_mutates_the_prompt_and_stores_the_best(conn):
    """Guards the silent no-op (SPEC F17): the reflection LM's proposal must reach the agent and win."""
    failures = [add_failure(conn, arm="C") for _ in range(2)]
    insert_pool(conn, 120)
    env = FakeEnv(conn)
    agent = FakeAgent(conn, passes=lambda text, _: HINT in text)
    lm = _hinting_lm()

    result = learn_c(conn, _req(failures), lm, agent, env, SYSTEM_V1)

    assert result.prompt_version == "C-s0-g1"
    best = get_prompt_version(conn, "C-s0-g1")
    assert HINT in best.text and best.text != SYSTEM_V1
    assert (best.arm, best.seed, best.generation, best.is_candidate) == ("C", 0, 1, False)
    assert best.parent_prompt_version == "system_v1"
    assert lm.prompts, "the reflection LM was never called"

    candidates = _candidates(conn)
    assert set(candidates) == {candidate_version_id(SYSTEM_V1), candidate_version_id(best.text)}
    assert all(pv.is_candidate and pv.arm == "C" for pv in candidates.values())
    assert {r.prompt_version for r in agent.requests} == set(candidates)
    assert {(r.purpose, r.arm, r.seed, r.generation, r.model_version, r.temperature, r.n) for r in agent.requests} == {
        ("gepa", "C", 0, 1, "base", 0.0, 1)
    }
    assert env.calls == [
        MutateRequest(
            failure_ids=sorted(f.failure_id for f in failures), variants_per_failure=VARIANTS_PER_FAILURE, seed=0
        )
    ]
    assert "2 candidates, best valset score 1.000 (seed prompt 0.000)" in result.notes
    assert f"{len(agent.requests)} agent runs" in result.notes

    # Re-calling returns the stored result without any agent, LM or env call.
    again_agent, again_lm, again_env = FakeAgent(conn, lambda *_: True), _hinting_lm(), FakeEnv(conn)
    again = learn_c(conn, _req(failures), again_lm, again_agent, again_env, SYSTEM_V1)
    assert again.prompt_version == "C-s0-g1"
    assert (again_agent.requests, again_lm.prompts, again_env.calls) == ([], [], [])


def test_adapter_subclasses_gepa_adapter():
    assert gepa.GEPAAdapter in AgentPromptAdapter.__mro__
    assert AgentPromptAdapter.propose_new_texts is None


def test_gepa_stays_within_metric_call_budget(conn):
    failures = [add_failure(conn, arm="C") for _ in range(3)]
    insert_pool(conn, 120)
    counter = iter(range(10_000))
    lm = FakeLM(lambda p: f"```\nPrompt variant {next(counter)}.\n```")

    def coin(text, task):  # deterministic pseudo-random pass/fail, so some proposals win and trigger valset runs
        return int(hashlib.sha256(f"{text}|{task.task_id}".encode()).hexdigest(), 16) % 2 == 0

    agent = FakeAgent(conn, passes=coin)
    result = learn_c(conn, _req(failures), lm, agent, FakeEnv(conn), SYSTEM_V1)

    runs = len(agent.requests)
    assert GEPA_MAX_METRIC_CALLS <= runs <= GEPA_MAX_METRIC_CALLS + MAX_OVERSHOOT
    assert f"{runs} agent runs" in result.notes
    # The expensive path ran: at least one proposal won and was scored on the whole valset.
    per_version = Counter(r.prompt_version for r in agent.requests)
    assert sum(1 for n in per_version.values() if n >= GEPA_VAL_SIZE) >= 2


def test_gepa_sets_are_deterministic_disjoint_and_train_only(conn):
    failures = [add_failure(conn, arm="C") for _ in range(2)]
    variants = [
        insert_task(conn, make_task(origin="variant", parent_failure_id=f.failure_id))
        for f in failures
        for _ in range(3)
    ]
    other_failure = add_failure(conn, arm="C")
    insert_task(conn, make_task(origin="variant", parent_failure_id=other_failure.failure_id))
    pool_ids = {t.task_id for t in insert_pool(conn, 130)}
    env = FakeEnv(conn)
    fids = [f.failure_id for f in failures]

    trainset, valset = gepa_sets(conn, _req(failures), fids, env)
    again = gepa_sets(conn, _req(failures), fids, env)

    assert env.calls == []
    assert (trainset, valset) == again
    assert [t.task_id for t in trainset[: len(variants)]] == sorted(v.task_id for v in variants)
    extra = trainset[len(variants) :]
    assert len(extra) == GEPA_TRAIN_EXTRA and len(valset) == GEPA_VAL_SIZE
    assert {t.task_id for t in extra + valset} <= pool_ids
    assert not {t.task_id for t in trainset} & {t.task_id for t in valset}
    assert all(t.split.value == "train" for t in trainset + valset)

    other_generation = gepa_sets(conn, _req(failures, generation=2), fids, env)
    assert [t.task_id for t in other_generation[1]] != [t.task_id for t in valset]


def test_mutate_only_for_failures_without_variants(conn):
    with_variants, without = add_failure(conn, arm="C"), add_failure(conn, arm="C")
    insert_task(conn, make_task(origin="variant", parent_failure_id=with_variants.failure_id))
    insert_pool(conn, GEPA_TRAIN_EXTRA + GEPA_VAL_SIZE)
    env = FakeEnv(conn)
    trainset, _ = gepa_sets(conn, _req([with_variants, without]), [with_variants.failure_id, without.failure_id], env)
    assert env.calls == [MutateRequest(failure_ids=[without.failure_id], variants_per_failure=10, seed=0)]
    assert len(trainset) == 1 + VARIANTS_PER_FAILURE + GEPA_TRAIN_EXTRA


def test_eval_tasks_are_rejected_before_any_write(conn):
    failures = [add_failure(conn, arm="C")]
    pool = insert_pool(conn, GEPA_TRAIN_EXTRA + GEPA_VAL_SIZE - 1)
    leaked = insert_task(conn, make_task(split="eval"))
    conn.execute("INSERT INTO app.train_pool VALUES (%s, %s)", (len(pool), leaked.task_id))
    agent, lm = FakeAgent(conn, lambda *_: True), _hinting_lm()
    with pytest.raises(RuntimeError, match="eval leakage"):
        learn_c(conn, _req(failures), lm, agent, FakeEnv(conn), SYSTEM_V1)
    assert (agent.requests, lm.prompts) == ([], [])
    assert conn.execute("SELECT count(*) FROM app.prompt_versions").fetchone()[0] == 0

    eval_failure = add_failure(conn, arm="C", split="eval")
    env = FakeEnv(conn)
    with pytest.raises(RuntimeError, match="eval leakage"):
        learn_c(conn, _req([eval_failure]), lm, agent, env, SYSTEM_V1)
    assert env.calls == []


def test_adapter_evaluate_and_reflective_dataset(conn):
    tasks = [insert_task(conn, make_task()) for _ in range(2)]
    agent = FakeAgent(conn, passes=lambda _, task: task.task_id == tasks[0].task_id)
    adapter = AgentPromptAdapter(conn, agent, _req([], generation=3, seed=2))
    candidate = {"system_prompt": "Be careful."}

    plain = adapter.evaluate(tasks, candidate)
    assert plain.scores == [1.0, 0.0] and plain.trajectories is None
    traced = adapter.evaluate(tasks, candidate, capture_traces=True)
    dataset = adapter.make_reflective_dataset(candidate, traced, ["system_prompt"])

    assert list(dataset) == ["system_prompt"]
    passed, failed = dataset["system_prompt"]
    assert set(failed) == {"Inputs", "Generated Outputs", "Feedback"}
    assert failed["Inputs"] == {"question": tasks[1].prompt}
    assert failed["Generated Outputs"] == 'final answer: {"number":7600}; stop reason: submitted'
    assert failed["Feedback"].startswith(
        "Failed. Verifier: number: expected 7500.00, got 7600\nSteps:\n1. get_transactions"
    )
    assert passed["Feedback"].startswith("Passed.")

    # The candidate is stored once as C-cand-<sha16> and reused across evaluations.
    (pv,) = _candidates(conn).values()
    assert pv.prompt_version == "C-cand-" + hashlib.sha256(b"Be careful.").hexdigest()[:16]
    assert (pv.text, pv.is_candidate, pv.seed, pv.generation) == ("Be careful.", True, 2, 3)
    assert adapter.agent_runs == 4


def test_seed_candidate_is_the_current_prompt_version(conn):
    store_prompt_version(
        conn,
        PromptVersion(
            prompt_version="C-s0-g1",
            arm="C",
            seed=0,
            generation=1,
            text="Generation one prompt.",
            parent_prompt_version="system_v1",
            created_at=NOW,
        ),
    )
    failures = [add_failure(conn, arm="C", generation=2)]
    insert_pool(conn, GEPA_TRAIN_EXTRA + GEPA_VAL_SIZE)
    agent = FakeAgent(conn, lambda *_: True)
    result = learn_c(conn, _req(failures, generation=2, current="C-s0-g1"), _hinting_lm(), agent, FakeEnv(conn), "")
    assert agent.batches[0].requests[0].prompt_version == candidate_version_id("Generation one prompt.")
    assert get_prompt_version(conn, result.prompt_version).parent_prompt_version == "C-s0-g1"

    with pytest.raises(NotFoundError, match="unknown prompt version: C-s0-g9"):
        learn_c(conn, _req(failures, generation=3, current="C-s0-g9"), _hinting_lm(), agent, FakeEnv(conn), "")
