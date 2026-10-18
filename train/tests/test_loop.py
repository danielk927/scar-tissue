import dataclasses
import json

import pytest
from psycopg.types.json import Jsonb
from scar_schemas.constants import K_SAMPLES, VARIANTS_PER_FAILURE
from scar_schemas.models import EvalSet, GenStatus, LearnResult, Origin, RunBatchRequest, RunRequest, SimulateResult

from scar_train import data_builder, gate, loop, registry
from scar_train.config import get_settings
from scar_train.data_builder import Snapshot
from scar_train.errors import ApiError
from scar_train.gate import GateResult
from scar_train.trainer import Deployment, FakeDeployer, FakeTrainer, TrainedAdapter

from .factories import FakeAgent, failure, insert_run, insert_task, model_version, run_result, task

# Calls each arm-D step makes, in order (the fakes below record them by these names).
D_STEPS = [
    (GenStatus.collecting, ["simulate"]),
    (GenStatus.mining, ["mine"]),
    (GenStatus.mutating, ["mutate"]),
    (GenStatus.building_data, ["build_snapshot"]),
    (GenStatus.training, ["lineage_examples", "export_jsonl", "train"]),
    (GenStatus.deploying, ["deploy", "tool_check"]),
    (GenStatus.gating, ["eval_set", "run_gate"]),
]
BC_STEPS = [
    *D_STEPS[:3],
    (GenStatus.learning_prompt, ["learn"]),
    (GenStatus.gating, ["eval_set", "run_gate"]),
]
G0_CALLS = ["eval_set", "eval_score"]


def calls_from(steps, start: GenStatus | None = None) -> list[str]:
    names = [s for s, _ in steps]
    return [c for _, cs in steps[names.index(start) if start else 0 :] for c in cs]


class Killed(BaseException):
    """Stands in for the process being killed mid-step (not an Exception, so nothing marks the record failed)."""


class Fakes:
    """Every dependency of a generation. `calls` records the order; `fail[name]` is raised once by that call."""

    def __init__(self):
        self.calls: list[str] = []
        self.args: dict[str, list[dict]] = {}
        self.fail: dict[str, BaseException] = {}
        self.traffic = SimulateResult(run_ids=["run_t1", "run_t2", "run_t3"], passed=1, failed=2)
        self.failures = [
            failure("fail_1", "run_t1", "task_a", learnable_p=0.7),
            failure("fail_2", "run_t2", "task_b", learnable_p=0.9),
        ]
        self.snapshot = Snapshot("snap_1", 3, 2)
        self.examples = ["ex"] * 5
        self.eval_ids = [f"task_eval_{i:02d}" for i in range(39, -1, -1)]  # unsorted on purpose
        self.base_score = 0.4
        self.gate = GateResult(0.5, 1.0, 1.0, True)

    def call(self, name: str, **kw):
        self.calls.append(name)
        self.args.setdefault(name, []).append(kw)
        if name in self.fail:
            raise self.fail.pop(name)

    # env
    def simulate(self, req):
        self.call("simulate", req=req)
        return self.traffic

    def mutate(self, req):
        self.call("mutate", req=req)
        return [task(f"task_var_{f}_{i}") for f in req.failure_ids for i in range(req.variants_per_failure)]

    def learn(self, req):
        self.call("learn", req=req)
        return LearnResult(prompt_version=f"{req.arm}-s{req.seed}-g{req.generation}", notes="2 lessons")

    def eval_set(self, eval_set_id):
        self.call("eval_set")
        return EvalSet(eval_set_id=eval_set_id, task_ids=self.eval_ids, sha256="x")

    # agent
    def mine(self, req):
        self.call("mine", req=req)
        return self.failures

    # trainer, deployer, tool check
    def train(self, **kw):
        self.call("train", **kw)
        return TrainedAdapter(adapter_uri=f"veri://{kw['version']}", training_job_id=f"job_{kw['version']}")

    def deploy(self, **kw):
        self.call("deploy", **kw)
        return Deployment(endpoint_base_url="http://models.local/v1", endpoint_model_id=kw["version"])

    def tool_check(self, url, model_id, key):
        self.call("tool_check", url=url, model_id=model_id, key=key)

    # data builder
    def build_snapshot(self, conn, agent, **kw):
        self.call("build_snapshot", **kw)
        return self.snapshot

    def lineage_examples(self, conn, lineage):
        self.call("lineage_examples", lineage=lineage)
        return self.examples

    def export_jsonl(self, examples, path):
        self.call("export_jsonl", path=path)
        path.write_text("{}\n" * len(examples))
        return len(examples)

    # gate
    def eval_score(self, conn, agent, **kw):
        self.call("eval_score", **kw)
        return self.base_score

    def run_gate(self, conn, agent, **kw):
        self.call("run_gate", **kw)
        return self.gate


def context(f, run_dir, **kw) -> loop.LoopContext:
    fields = dict(
        env=f,
        agent=f,
        trainer=f,
        deployer=f,
        tool_check=f.tool_check,
        build_snapshot=f.build_snapshot,
        lineage_examples=f.lineage_examples,
        export_jsonl=f.export_jsonl,
        eval_score=f.eval_score,
        run_gate=f.run_gate,
        settings=dataclasses.replace(get_settings(), model_api_key="key"),
        run_dir=run_dir,
    )
    fields.update(kw)
    return loop.LoopContext(**fields)


@pytest.fixture
def f():
    return Fakes()


@pytest.fixture
def ctx(f, tmp_path):
    return context(f, tmp_path)


@pytest.fixture
def base(conn):
    registry.put_model_version(conn, model_version())


def run(conn, ctx, arm="D", g=1, **kw):
    return loop.run_generation(conn, ctx, arm, 0, g, **kw)


def test_arm_d_runs_every_step_in_order(conn, ctx, f, base, tmp_path):
    rec = run(conn, ctx)
    assert f.calls == G0_CALLS + calls_from(D_STEPS)
    g0 = registry.get_generation(conn, "D-s0-g0")
    assert (g0.status, g0.eval_score, g0.parent_eval_score, g0.current_model_version) == ("promoted", 0.4, 0.4, "base")

    sim = f.args["simulate"][0]["req"]
    assert (sim.model_version, sim.prompt_version, sim.n_tasks, sim.generation) == ("base", "system_v1", 200, 1)
    mut = f.args["mutate"][0]["req"]
    assert (mut.failure_ids, mut.variants_per_failure, mut.seed) == (["fail_2", "fail_1"], VARIANTS_PER_FAILURE, 0)
    snap = f.args["build_snapshot"][0]
    variants = [f"task_var_fail_{i}_{j}" for i in (1, 2) for j in range(VARIANTS_PER_FAILURE)]
    assert snap["task_ids"] == sorted(variants + ["task_a", "task_b"])
    assert (snap["model_version"], snap["k_samples"], snap["generation"]) == ("base", K_SAMPLES, 1)
    assert f.args["export_jsonl"][0]["path"] == tmp_path / "D-s0-g1" / "dataset.jsonl"
    assert {k: f.args["train"][0][k] for k in ("version", "n_examples", "base_model_hf_id", "seed")} == {
        "version": "D-s0-g1",
        "n_examples": 5,
        "base_model_hf_id": "Qwen/Example-4B",
        "seed": 0,
    }
    assert f.args["tool_check"][0] == {"url": "http://models.local/v1", "model_id": "D-s0-g1", "key": "key"}
    gate_args = f.args["run_gate"][0]
    assert gate_args["eval_task_ids"] == sorted(f.eval_ids)
    assert (gate_args["candidate_model_version"], gate_args["candidate_prompt_version"]) == ("D-s0-g1", "system_v1")
    assert (gate_args["parent_model_version"], gate_args["parent_eval_score"]) == ("base", 0.4)

    assert rec == registry.get_generation(conn, "D-s0-g1")
    assert (rec.traffic_runs, rec.failures_mined, rec.failures_included, rec.variants_created, rec.examples_built) == (
        3,
        2,
        2,
        2 * VARIANTS_PER_FAILURE,
        5,
    )
    mv = registry.get_model_version(conn, "D-s0-g1")
    assert (mv.status, mv.parent_version, mv.snapshot_id, mv.adapter_uri, mv.training_job_id) == (
        "promoted",
        "base",
        "snap_1",
        "veri://D-s0-g1",
        "job_D-s0-g1",
    )
    assert (mv.endpoint_model_id, mv.arm, mv.seed, mv.generation) == ("D-s0-g1", "D", 0, 1)
    assert json.loads((tmp_path / "D-s0-g1" / "state.json").read_text())["step"] == "promoted"


@pytest.mark.parametrize("arm", ["B", "C"])
def test_arms_b_and_c_learn_a_prompt_on_the_parent_weights(conn, ctx, f, base, arm):
    rec = run(conn, ctx, arm)
    assert f.calls == G0_CALLS + calls_from(BC_STEPS)
    learn = f.args["learn"][0]["req"]
    assert (learn.arm, learn.model_version, learn.current_prompt_version, learn.failure_ids) == (
        arm,
        "base",
        "system_v1",
        ["fail_2", "fail_1"],
    )
    gate_args = f.args["run_gate"][0]
    assert (gate_args["candidate_model_version"], gate_args["candidate_prompt_version"]) == ("base", f"{arm}-s0-g1")
    assert (rec.status, rec.candidate_model_version, rec.candidate_prompt_version) == ("promoted", None, f"{arm}-s0-g1")
    assert (rec.current_model_version, rec.current_prompt_version) == ("base", f"{arm}-s0-g1")
    assert "2 lessons" in rec.notes
    assert [m.version for m in registry.list_model_versions(conn)] == ["base"]


def test_promotion_and_rejection_bookkeeping(conn, ctx, f, base):
    g1 = run(conn, ctx, g=1)
    assert (g1.status, g1.eval_score, g1.parent_eval_score) == ("promoted", 0.5, 0.4)
    assert (g1.regression_score, g1.parent_regression_score) == (1.0, 1.0)
    assert (g1.current_model_version, g1.current_prompt_version, g1.finished_at is not None) == (
        "D-s0-g1",
        "system_v1",
        True,
    )
    assert "gate: eval 0.500 vs parent 0.400, regression 1.000 vs parent 1.000" in g1.notes

    f.gate = GateResult(0.45, 1.0, 1.0, False)
    g2 = run(conn, ctx, g=2)
    assert f.args["simulate"][-1]["req"].model_version == "D-s0-g1"
    assert (f.args["run_gate"][-1]["parent_model_version"], f.args["run_gate"][-1]["parent_eval_score"]) == (
        "D-s0-g1",
        0.5,
    )
    assert (g2.status, g2.parent_model_version, g2.candidate_model_version, g2.current_model_version) == (
        "rejected",
        "D-s0-g1",
        "D-s0-g2",
        "D-s0-g1",
    )
    assert registry.get_model_version(conn, "D-s0-g2").status == "rejected"
    assert registry.get_model_version(conn, "D-s0-g1").status == "promoted"
    assert registry.current(conn, "D", 0) == ("D-s0-g1", "system_v1")

    f.gate = GateResult(0.6, 1.0, 1.0, True)
    g3 = run(conn, ctx, g=3)
    assert (g3.parent_model_version, g3.parent_eval_score, g3.current_model_version) == ("D-s0-g1", 0.5, "D-s0-g3")


def test_generation_zero_is_the_base_eval_once_per_lineage(conn, ctx, f, base):
    rec = run(conn, ctx, "A", 0)
    assert f.calls == G0_CALLS
    assert f.args["eval_score"][0] == {
        "model_version": "base",
        "prompt_version": "system_v1",
        "arm": "A",
        "seed": 0,
        "generation": 0,
        "eval_task_ids": sorted(f.eval_ids),
    }
    assert (rec.generation_id, rec.status, rec.eval_score, rec.parent_eval_score) == ("A-s0-g0", "promoted", 0.4, 0.4)
    assert (rec.parent_model_version, rec.current_model_version, rec.current_prompt_version) == (
        "base",
        "base",
        "system_v1",
    )
    assert run(conn, ctx, "A", 0) == rec and f.calls == G0_CALLS  # finished: nothing runs again
    run(conn, ctx, "D", 1)
    assert [g.generation_id for g in registry.list_generations(conn)] == ["A-s0-g0", "D-s0-g0", "D-s0-g1"]


def test_a_generation_without_included_failures_is_rejected(conn, ctx, f, base):
    f.failures = [failure("fail_1", "run_t1", "task_a", included=False), failure("fail_2", "run_t2", "task_b")]
    f.failures[1] = f.failures[1].model_copy(update={"included": False, "exclusion_reason": "over_cap"})
    rec = run(conn, ctx)
    assert f.calls == G0_CALLS + ["simulate", "mine"]
    assert (rec.status, rec.failures_mined, rec.failures_included, rec.candidate_model_version) == (
        "rejected",
        2,
        0,
        None,
    )
    assert (rec.current_model_version, rec.eval_score) == ("base", None)
    assert "rejected: no included failures" in rec.notes


def test_an_empty_cumulative_dataset_is_rejected_but_older_examples_still_train(conn, ctx, f, base):
    f.snapshot, f.examples = Snapshot(None, 0, 0, ["task_a"]), []
    rec = run(conn, ctx)
    assert f.calls[-2:] == ["build_snapshot", "lineage_examples"]
    assert (rec.status, rec.examples_built, rec.current_model_version) == ("rejected", 0, "base")
    assert "rejected: the cumulative training dataset of D-s0 is empty" in rec.notes

    f.examples = ["ex"] * 4  # nothing kept this generation, but earlier generations left examples
    rec = run(conn, ctx, g=2)
    assert (rec.status, registry.get_model_version(conn, "D-s0-g2").snapshot_id) == ("promoted", None)


@pytest.mark.parametrize(
    ("arm", "failing", "step"),
    [
        ("D", "simulate", GenStatus.collecting),
        ("D", "mine", GenStatus.mining),
        ("D", "mutate", GenStatus.mutating),
        ("D", "build_snapshot", GenStatus.building_data),
        ("D", "train", GenStatus.training),
        ("D", "tool_check", GenStatus.deploying),
        ("D", "run_gate", GenStatus.gating),
        ("B", "learn", GenStatus.learning_prompt),
        ("C", "run_gate", GenStatus.gating),
    ],
)
def test_a_failed_step_resumes_without_redoing_completed_steps(conn, ctx, f, base, arm, failing, step, tmp_path):
    steps = D_STEPS if arm == "D" else BC_STEPS
    f.fail[failing] = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        run(conn, ctx, arm)
    failed = registry.get_generation(conn, f"{arm}-s0-g1")
    assert failed.status == GenStatus.failed and failed.finished_at is not None
    assert f"failed at {step.value}: RuntimeError: boom" in failed.notes
    assert (failed.current_model_version, failed.current_prompt_version) == ("base", "system_v1")
    assert json.loads((tmp_path / f"{arm}-s0-g1" / "state.json").read_text())["step"] == step.value
    if step == GenStatus.deploying:
        assert registry.get_model_version(conn, "D-s0-g1") is None  # the check failed: nothing registered

    f.calls.clear()
    rec = run(conn, ctx, arm)
    assert f.calls == calls_from(steps, step)
    assert (rec.status, rec.finished_at is not None) == ("promoted", True)
    assert (rec.traffic_runs, rec.failures_included, rec.variants_created) == (3, 2, 2 * VARIANTS_PER_FAILURE)
    assert rec.examples_built == (5 if arm == "D" else 0)
    assert "failed at" not in rec.notes  # the step ran again, so the failure note is stale


def test_a_killed_runner_resumes_at_the_interrupted_step(conn, ctx, f, base):
    f.fail["train"] = Killed()
    with pytest.raises(Killed):
        run(conn, ctx)
    assert registry.get_generation(conn, "D-s0-g1").status == GenStatus.training
    f.calls.clear()
    assert run(conn, ctx).status == "promoted"
    assert f.calls == calls_from(D_STEPS, GenStatus.training)


@pytest.mark.parametrize("killed_at", [GenStatus.deploying, GenStatus.promoted])
def test_a_kill_between_the_state_file_and_the_database_never_redoes_a_step(conn, ctx, f, base, monkeypatch, killed_at):
    put = registry.put_generation

    def killed_put(c, rec):
        if rec.generation_id == "D-s0-g1" and rec.status == killed_at:
            raise Killed()
        put(c, rec)

    monkeypatch.setattr(registry, "put_generation", killed_put)
    with pytest.raises(Killed):
        run(conn, ctx)
    monkeypatch.setattr(registry, "put_generation", put)
    assert registry.get_generation(conn, "D-s0-g1").status != killed_at  # the database is one write behind
    f.calls.clear()
    rec = run(conn, ctx)
    assert f.calls == ([] if killed_at == GenStatus.promoted else calls_from(D_STEPS, GenStatus.deploying))
    assert rec == registry.get_generation(conn, "D-s0-g1") and rec.status == "promoted"
    assert (rec.traffic_runs, rec.examples_built) == (3, 5)
    assert registry.get_model_version(conn, "D-s0-g1").status == "promoted"


def test_tiny_mode_sizes(conn, ctx, f, base):
    p = [0.9, 0.8, 0.95, 0.7, 0.65, 0.99]
    f.failures = [failure(f"fail_{i}", f"run_t{i}", f"task_{i}", learnable_p=x) for i, x in enumerate(p)]
    f.failures += [
        failure("fail_judge", "run_j", "task_j", origin="judge", learnable_p=0.61),
        failure("fail_out", "run_o", "task_o", included=False),
    ]
    f.traffic = SimulateResult(run_ids=[f"run_t{i}" for i in range(6)] + ["run_o"], passed=0, failed=7)
    rec = run(conn, ctx, tiny=True)
    assert f.args["eval_score"][0]["eval_task_ids"] == sorted(f.eval_ids)[:30]
    assert f.args["simulate"][0]["req"].n_tasks == 20
    mut = f.args["mutate"][0]["req"]
    assert (mut.failure_ids, mut.variants_per_failure) == (["fail_judge", "fail_5", "fail_2", "fail_0", "fail_1"], 2)
    assert f.args["build_snapshot"][0]["k_samples"] == 2
    assert f.args["run_gate"][0]["eval_task_ids"] == sorted(f.eval_ids)[:30]
    assert (rec.failures_mined, rec.failures_included, rec.variants_created) == (8, 5, 10)
    assert rec.notes.startswith(loop.TINY_MARK) and loop.is_tiny(registry.get_generation(conn, "D-s0-g0"))


def test_tiny_and_full_records_never_mix_until_reset(conn, ctx, f, base):
    run(conn, ctx, tiny=True)
    with pytest.raises(ApiError) as e:
        run(conn, ctx, g=2)
    assert (e.value.status, e.value.error) == (409, "mode_conflict")
    run(conn, ctx, "C", 0)
    with pytest.raises(ApiError) as e:
        run(conn, ctx, "C", 1, tiny=True)
    assert e.value.status == 409

    registry.put_model_version(conn, model_version("D-s1-g1", status="candidate", arm="D", seed=1, generation=1))
    for ex_id, lin in [("ex_1", "D-s0"), ("ex_2", "D-s0"), ("ex_3", "D-s1")]:
        conn.execute(
            "INSERT INTO app.training_examples (example_id, snapshot_id, lineage, task_id, source, body, created_at)"
            " VALUES (%s, 'snap_x', %s, 'task_a', 'self', %s, now())",
            (ex_id, lin, Jsonb({})),
        )
    counts = loop.reset_lineage(conn, "D", 0)
    assert counts == {"generations": 2, "model_versions": 1, "training_examples": 2}
    assert sorted(m.version for m in registry.list_model_versions(conn)) == ["D-s1-g1", "base"]
    assert [g.generation_id for g in registry.list_generations(conn)] == ["C-s0-g0"]
    assert conn.execute("SELECT example_id FROM app.training_examples").fetchall() == [("ex_3",)]
    loop.check_run(conn, "D", 0, [1], tiny=False)


def test_reset_lineage_command(db_url, conn, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db_url)
    registry.put_model_version(conn, model_version())
    registry.put_model_version(conn, model_version("D-s0-g1", arm="D", seed=0, generation=1))
    assert loop.main(["reset-lineage", "D", "0"]) == 0
    assert [m.version for m in registry.list_model_versions(conn)] == ["base"]
    assert "restart the agent" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("arm", "generations", "status", "match"),
    [
        ("E", [1], 422, "not implemented \\(SPEC D-11: first to cut\\)"),
        ("A", [1], 422, "generation 0 only"),
        ("A", [0, 1], 422, "generation 0 only"),
        ("D", [], 422, "ascending"),
        ("D", [2, 1], 422, "ascending"),
        ("D", [-1], 422, "ascending"),
        ("D", [2], 409, "D-s0-g1 has not ended"),
    ],
)
def test_check_run_refusals(conn, base, arm, generations, status, match):
    with pytest.raises(ApiError, match=match) as e:
        loop.check_run(conn, arm, 0, generations, tiny=False)
    assert e.value.status == status


def test_check_run_needs_base_and_allows_queued_predecessors(conn, ctx):
    with pytest.raises(ApiError) as e:
        loop.check_run(conn, "D", 0, [1], tiny=False)
    assert (e.value.status, e.value.error) == (503, "base_not_registered")
    registry.put_model_version(conn, model_version())
    loop.check_run(conn, "D", 0, [1, 2, 3], tiny=False)
    with pytest.raises(ApiError, match="not implemented"):
        run(conn, ctx, "E", 1)


def test_stale_failure_records_stop_mining(conn, ctx, f, base):
    f.traffic = SimulateResult(run_ids=["run_t1", "run_t2", "run_t3", "run_t4"], passed=1, failed=3)
    with pytest.raises(RuntimeError, match="1 failed traffic runs of D-s0-g1 have no failure record"):
        run(conn, ctx)
    assert registry.get_generation(conn, "D-s0-g1").status == GenStatus.failed
    assert "mutate" not in f.calls


def test_only_this_traffic_and_judge_failures_count(conn, ctx, f, base):
    f.failures += [
        failure("fail_old", "run_before_reset", "task_x", learnable_p=0.99),
        failure("fail_judge", "run_judge", "task_j", origin="judge", learnable_p=0.61),
    ]
    rec = run(conn, ctx)
    assert f.args["mutate"][0]["req"].failure_ids == ["fail_judge", "fail_2", "fail_1"]
    assert (rec.failures_mined, rec.failures_included) == (3, 3)


def test_runs_of_the_candidate_version_at_another_endpoint_stop_deploying(conn, ctx, f, base):
    def old_run(run_id, endpoint_model_id):
        insert_run(
            conn,
            run_result(
                run_id, "task_eval_00", model_version="D-s0-g1", endpoint_model_id=endpoint_model_id, purpose="eval"
            ),
        )

    old_run("run_old_1", "D-s0-g1")  # same endpoint (a repeated fake-trainer tiny run): reusable
    old_run("run_old_2", "base")  # another endpoint, left from before a reset
    with pytest.raises(RuntimeError, match="runs of D-s0-g1 made at endpoint model base, not D-s0-g1"):
        run(conn, ctx)
    assert "tool_check" not in f.calls and registry.get_model_version(conn, "D-s0-g1") is None
    conn.execute("DELETE FROM app.runs WHERE run_id = 'run_old_2'")
    assert run(conn, ctx).status == "promoted"


def test_default_context_picks_the_trainer_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOP_RUN_DIR", str(tmp_path))
    monkeypatch.delenv("TRAINER", raising=False)
    ctx = loop.default_context()
    assert isinstance(ctx.trainer, FakeTrainer) and isinstance(ctx.deployer, FakeDeployer)
    assert ctx.run_dir == tmp_path and ctx.build_snapshot is data_builder.build_snapshot
    monkeypatch.setenv("TRAINER", "veri")
    monkeypatch.setenv("VERI_API_KEY", "vk_test")
    monkeypatch.delenv("VERI_DEPLOYMENT_ID", raising=False)
    with pytest.raises(ValueError, match="VERI_DEPLOYMENT_ID"):  # the Veri backend itself: tests/test_veri.py
        loop.default_context()
    monkeypatch.setenv("TRAINER", "gpu")
    with pytest.raises(ValueError):
        loop.default_context()


# ---- the IC-2 shape end to end: tiny generation 1 of D-s0 with the real data builder and gate ----


class SimEnv:
    """env stand-in over FakeAgent: /simulate runs pool tasks as traffic, /mutate stores variant tasks."""

    def __init__(self, conn, agent):
        self.conn, self.agent = conn, agent
        self.pool = [f"task_pool_{i:02d}" for i in range(40)]
        for t in self.pool:
            insert_task(conn, task(t))

    def simulate(self, req):
        reqs = [
            RunRequest(
                task_id=t,
                model_version=req.model_version,
                prompt_version=req.prompt_version,
                arm=req.arm,
                seed=req.seed,
                generation=req.generation,
                purpose="traffic",
            )
            for t in self.pool[: req.n_tasks]
        ]
        runs = self.agent.run_batch(RunBatchRequest(requests=reqs))
        passed = sum(r.passed for r in runs)
        return SimulateResult(run_ids=[r.run_id for r in runs], passed=passed, failed=len(runs) - passed)

    def mutate(self, req):
        out = []
        for fid in req.failure_ids:
            for i in range(req.variants_per_failure):
                t = task(f"task_var_{fid}_{i}").model_copy(update={"origin": Origin.variant, "parent_failure_id": fid})
                insert_task(self.conn, t)
                out.append(t)
        return out

    def eval_set(self, eval_set_id):
        return EvalSet(eval_set_id=eval_set_id, task_ids=[f"task_eval_{i:02d}" for i in range(40)], sha256="x")


class MiningAgent(FakeAgent):
    def mine(self, req):
        rows = self.conn.execute(
            "SELECT run_id, task_id FROM app.runs WHERE arm = %s AND seed = %s AND generation = %s"
            " AND purpose = 'traffic' AND NOT passed ORDER BY run_id",
            (req.arm, req.seed, req.generation),
        ).fetchall()
        return [failure(f"fail_{i:02d}", r, t, learnable_p=0.9 - i / 100) for i, (r, t) in enumerate(rows)]


def outcome(req, attempt):
    """Base passes even pool tasks in traffic and 10 of the first 30 eval tasks; every candidate passes all."""
    if req.purpose == "traffic":
        return {"passed": int(req.task_id[-2:]) % 2 == 0, "origin": "generated"}
    if req.purpose == "eval":
        return {"passed": req.model_version != "base" or int(req.task_id[-2:]) < 10, "split": "eval", "origin": "eval"}
    return {"passed": True}


def test_tiny_generation_end_to_end_with_the_real_builder_and_gate(conn, base, tmp_path):
    agent = MiningAgent(conn, outcome)
    checked = []
    ctx = loop.LoopContext(
        env=SimEnv(conn, agent),
        agent=agent,
        trainer=FakeTrainer(),
        deployer=FakeDeployer(),
        tool_check=lambda *a: checked.append(a),
        build_snapshot=data_builder.build_snapshot,
        lineage_examples=data_builder.lineage_examples,
        export_jsonl=data_builder.export_jsonl,
        eval_score=gate.eval_score,
        run_gate=gate.run_gate,
        settings=get_settings(),
        run_dir=tmp_path,
    )
    rec = loop.run_generation(conn, ctx, "D", 0, 1, tiny=True)
    assert (rec.status, rec.traffic_runs, rec.failures_mined, rec.failures_included, rec.variants_created) == (
        "promoted",
        20,
        10,
        5,
        10,
    )
    assert rec.examples_built == 15 + 10  # 5 originals + 10 variants pass once each; 10 passed traffic tasks replay
    assert (rec.eval_score, round(rec.parent_eval_score, 4), rec.regression_score) == (1.0, 0.3333, 1.0)
    assert rec.parent_regression_score is not None
    assert rec.notes.startswith("[tiny]; fake trainer") and rec.current_model_version == "D-s0-g1"
    mv = registry.get_model_version(conn, "D-s0-g1")
    assert (mv.status, mv.endpoint_model_id, mv.adapter_uri) == ("promoted", "base", "fake://D-s0-g1")
    assert checked == [("http://models.local/v1", "base", get_settings().model_api_key)]
    assert len((tmp_path / "D-s0-g1" / "dataset.jsonl").read_text().splitlines()) == 25
    sampling = [r for r in agent.requests if r.purpose == "sampling"]
    assert {r.n for r in sampling} == {2} and len(sampling) == 15
