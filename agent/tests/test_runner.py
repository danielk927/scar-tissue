"""/agent/run and /agent/run_batch semantics (P2-02, P2-05)."""

import asyncio

from conftest import FakeEnv, FakeLLM, assistant, fake_llm_factory, make_task, tool_call
from scar_schemas.models import RunRequest

from scar_agent.registry import Registry, load_system_v1
from scar_agent.runner import Runner


class SlowLLM(FakeLLM):
    """Always submits; records the peak number of concurrent calls."""

    def __init__(self):
        super().__init__([])
        self.active = self.peak = 0

    async def _create(self, **kwargs):
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        self.script = [assistant(calls=[tool_call("submit_answer", {"number": 100.0})])]
        return await super()._create(**kwargs)


def make_runner(store, llm, env=None):
    env = env or FakeEnv()
    return Runner(store, Registry(store), env, llm_factory=fake_llm_factory(llm)), env


def req(task_id, n=1, **kw):
    return RunRequest(task_id=task_id, model_version="base", purpose="traffic", n=n, **kw)


async def test_run_n_attempts_stored_with_indices(store):
    runner, _ = make_runner(store, SlowLLM())
    results = await runner.run(req("task_a", n=3, temperature=0.8))
    assert [r.attempt_index for r in results] == [0, 1, 2]
    assert len({r.run_id for r in results}) == 3
    assert len(store.runs) == 3 and all(r.passed for r in results)
    assert results[0].messages[0].content == load_system_v1()


async def test_batch_order_and_concurrency_limit(store):
    llm = SlowLLM()
    env = FakeEnv()
    for t in ("t0", "t1", "t2", "t3"):
        env.tasks[t] = make_task(t)
    runner, _ = make_runner(store, llm, env)
    reqs = [req("t0", n=2), req("t1", n=1), req("t2", n=3), req("t3", n=2)]
    results = await runner.run_batch(reqs, max_concurrency=3)
    assert [(r.task_id, r.attempt_index) for r in results] == [
        ("t0", 0), ("t0", 1), ("t1", 0), ("t2", 0), ("t2", 1), ("t2", 2), ("t3", 0), ("t3", 1)]
    assert llm.peak <= 3


async def test_batch_200_runs_at_16(store):
    llm = SlowLLM()
    runner, _ = make_runner(store, llm)
    results = await runner.run_batch([req(f"t{i}") for i in range(200)], max_concurrency=16)
    assert len(results) == 200 and [r.task_id for r in results] == [f"t{i}" for i in range(200)]
    assert llm.peak == 16


def test_system_v1_matches_spec():
    text = load_system_v1()
    assert text.startswith("You are a careful bookkeeping assistant")
    assert text.endswith("The task only ends when you call submit_answer.")
