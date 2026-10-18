"""Loop runner (SPEC 6.3, P3-10): a resumable state machine over GenStatus for one (arm, seed, generation).

Steps (each recorded before it runs, so a crash or a failure resumes at that step):
- D:    collecting, mining, mutating, building_data, training, deploying, gating, then promoted or rejected.
- B, C: collecting, mining, mutating, learning_prompt, gating, then promoted or rejected.
- Generation 0 of every lineage is one gating step: the eval score of base + system_v1 (stored eval runs make it
  computed once for all lineages). Any later generation first ensures it exists. Arm A has only generation 0; arm E
  is not implemented (SPEC D-11: first to cut).
A generation with no included failures, or (arm D) an empty cumulative dataset, ends rejected with a note. Any error
marks it failed with current_* = parent; running it again resumes at the failed step.

State: {run_dir}/{generation_id}/state.json holds the step to run, the IDs later steps need (traffic runs, included
failures, variants, snapshot, adapter) and the GenerationRecord; dataset.jsonl sits next to it. Every change is
written there first and then to app.generations, so after a crash the state file is never behind the database and a
completed step never runs again. The env and agent endpoints are idempotent and reuse stored runs, so an interrupted
step is safe to re-run (a Trainer must make re-training the same version reuse its job).

Tiny mode (SPEC 10, IC-2) shrinks every size. Tiny records start their notes with TINY_MARK and are never reported;
a lineage never mixes tiny and full records (409: reset_lineage first).

    uv run python -m scar_train.loop reset-lineage D 0
"""

import argparse
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from pydantic import BaseModel
from scar_schemas.constants import (
    BASE_PROMPT_VERSION,
    EVAL_SET_ID,
    K_SAMPLES,
    TRAFFIC_PER_GEN,
    VARIANTS_PER_FAILURE,
)
from scar_schemas.models import (
    FailureRecord,
    GenerationRecord,
    GenStatus,
    LearnRequest,
    MineRequest,
    ModelVersion,
    MutateRequest,
    Origin,
    SimulateRequest,
    TrainingExample,
)

from . import data_builder, gate, registry
from .checks import tool_call_check
from .clients import AgentClient, EnvClient
from .config import REPO_ROOT, Settings, get_settings
from .errors import ApiError
from .log import get_logger
from .trainer import Deployer, FakeDeployer, FakeTrainer, TrainedAdapter, Trainer

log = get_logger(__name__)

TINY_MARK = "[tiny]"
# One base eval at a time in this process, so lineages starting together reuse each other's stored eval runs and
# share one generation-0 score (SPEC 9.9).
_BASE_EVAL_LOCK = threading.Lock()
BASE = registry.BASE_VERSION
DONE = (GenStatus.promoted, GenStatus.rejected)


@dataclass(frozen=True)
class Sizes:
    traffic: int
    failure_cap: int | None  # None: only the agent's own FAILURE_CAP_PER_GEN
    variants: int
    k_samples: int
    eval_tasks: int | None  # None: all of eval_v1


FULL = Sizes(
    traffic=TRAFFIC_PER_GEN, failure_cap=None, variants=VARIANTS_PER_FAILURE, k_samples=K_SAMPLES, eval_tasks=None
)
TINY = Sizes(traffic=20, failure_cap=5, variants=2, k_samples=2, eval_tasks=30)


@dataclass(frozen=True)
class LoopContext:
    """Everything a generation talks to, so tests inject fakes. default_context() builds the production one."""

    env: EnvClient
    agent: AgentClient
    trainer: Trainer
    deployer: Deployer
    tool_check: Callable[[str, str, str | None], None]
    build_snapshot: Callable[..., data_builder.Snapshot]
    lineage_examples: Callable[[psycopg.Connection, str], list[TrainingExample]]
    export_jsonl: Callable[[list[TrainingExample], Path], int]
    eval_score: Callable[..., float]
    run_gate: Callable[..., gate.GateResult]
    settings: Settings
    run_dir: Path


def default_context(settings: Settings | None = None) -> LoopContext:
    """Production context. TRAINER picks the training backend (default fake); LOOP_RUN_DIR the run directory."""
    settings = settings or get_settings()
    backend = os.environ.get("TRAINER", "fake")
    if backend == "fake":
        log.warning("TRAINER=fake: arm-D candidates serve the base weights; for tests and tiny smoke runs only")
        trainer, deployer = FakeTrainer(), FakeDeployer()
    elif backend == "veri":
        from . import veri  # imported only when selected: it loads the Veri SDK

        trainer, deployer = veri.make_backends(settings)
    else:
        raise ValueError(f"unknown TRAINER={backend!r}; use fake or veri")
    return LoopContext(
        env=EnvClient(settings.env_service_url),
        agent=AgentClient(settings.agent_service_url),
        trainer=trainer,
        deployer=deployer,
        tool_check=tool_call_check,
        build_snapshot=data_builder.build_snapshot,
        lineage_examples=data_builder.lineage_examples,
        export_jsonl=data_builder.export_jsonl,
        eval_score=gate.eval_score,
        run_gate=gate.run_gate,
        settings=settings,
        run_dir=Path(os.environ.get("LOOP_RUN_DIR") or REPO_ROOT / "runs"),
    )


class StepState(BaseModel):
    """The step to run next, the record as last written, and what later steps need from earlier ones."""

    step: GenStatus
    record: GenerationRecord
    traffic_run_ids: list[str] = []
    traffic_failed: int = 0
    included_failure_ids: list[str] = []
    failed_task_ids: list[str] = []
    variant_task_ids: list[str] = []
    snapshot_id: str | None = None
    adapter_uri: str | None = None
    training_job_id: str | None = None


def is_tiny(rec: GenerationRecord) -> bool:
    return rec.notes.startswith(TINY_MARK)


def current_score(rec: GenerationRecord) -> float | None:
    """Eval score of the version in use after a finished generation."""
    return rec.eval_score if rec.status == GenStatus.promoted else rec.parent_eval_score


def select_included(records: list[FailureRecord], cap: int | None) -> list[FailureRecord]:
    """Included failures in the SPEC 9.4 cap order (judge first, then highest learnable_p, then run_id), cut to cap."""
    included = sorted(
        (r for r in records if r.included), key=lambda r: (r.origin != Origin.judge, -r.learnable_p, r.run_id)
    )
    return included if cap is None else included[:cap]


def check_run(conn: psycopg.Connection, arm: str, seed: int, generations: list[int], tiny: bool) -> None:
    """Raise ApiError unless these generations of the lineage can run, in this order and mode."""
    lin = registry.lineage(arm, seed)
    if arm == "E":
        raise ApiError(422, "not_implemented", "arm E: not implemented (SPEC D-11: first to cut)")
    if not generations or generations != sorted(set(generations)) or generations[0] < 0:
        raise ApiError(422, "invalid_generations", "generations must be distinct, ascending and >= 0")
    if arm == "A" and generations != [0]:
        raise ApiError(422, "invalid_generations", "arm A is the frozen base: generation 0 only")
    if registry.get_model_version(conn, BASE) is None:
        raise ApiError(503, "base_not_registered", "model version 'base' is not registered (scar_train.register_base)")
    records = registry.list_generations(conn, arm, seed)
    if any(is_tiny(r) != tiny for r in records):
        other = "full" if tiny else "tiny"
        raise ApiError(409, "mode_conflict", f"lineage {lin} has {other}-mode records; reset_lineage before switching")
    done = {r.generation for r in records if r.status in DONE}
    for g in generations:
        if g >= 2 and g - 1 not in done and g - 1 not in generations:
            prev = registry.generation_id(arm, seed, g - 1)
            raise ApiError(409, "previous_generation_unfinished", f"{prev} has not ended promoted or rejected")


def run_generation(
    conn: psycopg.Connection,
    ctx: LoopContext,
    arm: str,
    seed: int,
    generation: int,
    *,
    tiny: bool = False,
    on_log: Callable[[str], None] | None = None,
) -> GenerationRecord:
    """Run or resume one generation and return its finished record (promoted or rejected). A failed step marks the
    record failed and re-raises. `conn` must be autocommit, so every record write commits at once."""
    check_run(conn, arm, seed, [generation], tiny)
    return _Generation(conn, ctx, arm, seed, generation, tiny, on_log).run()


class _Generation:
    def __init__(self, conn, ctx: LoopContext, arm: str, seed: int, generation: int, tiny: bool, on_log):
        self.conn, self.ctx, self.arm, self.seed, self.generation, self.tiny = conn, ctx, arm, seed, generation, tiny
        self.on_log = on_log
        self.sizes = TINY if tiny else FULL
        self.gid = registry.generation_id(arm, seed, generation)
        self.dir = ctx.run_dir / self.gid
        self.state_path = self.dir / "state.json"
        self.state: StepState

    def run(self) -> GenerationRecord:
        if self.generation > 0:
            _Generation(self.conn, self.ctx, self.arm, self.seed, 0, self.tiny, self.on_log).run()
        rec = registry.get_generation(self.conn, self.gid)
        if rec is not None and rec.status in DONE:
            self._log(f"{self.gid} already {rec.status.value}")
            return rec
        if rec is None:
            self.state = self._new_state()
            self._commit()
        else:
            if not self.state_path.exists():
                raise RuntimeError(f"{self.state_path} is missing, so {self.gid} cannot resume; restore it or reset")
            self.state = StepState.model_validate_json(self.state_path.read_text())  # never behind the database
        while True:
            step = self.state.step
            try:
                if step in DONE:  # finished in the state file, interrupted before the database
                    return self._finish(step)
                if self.rec.status != step:  # resuming a failed or interrupted generation
                    self._update(status=step, finished_at=None, notes=self._notes_without_failure(step))
                self._log(f"{self.gid} {step.value}")
                nxt = getattr(self, f"_{step.value}")()
                if nxt in DONE:
                    return self._finish(nxt)
                self.state.step = nxt
                self._update(status=nxt)
            except Exception as e:
                self._fail(step, e)
                raise

    # ---- bookkeeping ----

    @property
    def rec(self) -> GenerationRecord:
        return self.state.record

    def _log(self, msg: str) -> None:
        log.info(msg, extra={"arm": self.arm, "seed": self.seed, "generation": self.generation})
        if self.on_log:
            self.on_log(msg)

    def _new_state(self) -> StepState:
        if self.generation == 0:
            step, parent, parent_score = GenStatus.gating, (BASE, BASE_PROMPT_VERSION), None
        else:
            step, parent = GenStatus.collecting, registry.current(self.conn, self.arm, self.seed)
            prev = registry.get_generation(self.conn, registry.generation_id(self.arm, self.seed, self.generation - 1))
            parent_score = current_score(prev)
        record = GenerationRecord(
            generation_id=self.gid,
            arm=self.arm,
            seed=self.seed,
            generation=self.generation,
            status=step,
            parent_model_version=parent[0],
            parent_prompt_version=parent[1],
            candidate_model_version=None,
            candidate_prompt_version=None,
            current_model_version=parent[0],
            current_prompt_version=parent[1],
            parent_eval_score=parent_score,
            notes=TINY_MARK if self.tiny else "",
            started_at=datetime.now(timezone.utc),
        )
        return StepState(step=step, record=record)

    def _set(self, **fields) -> None:
        self.state.record = self.rec.model_copy(update=fields)

    def _update(self, **fields) -> None:
        self._set(**fields)
        self._commit()

    def _commit(self) -> None:
        self._write_state()
        registry.put_generation(self.conn, self.rec)

    def _write_state(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(self.state.model_dump_json(indent=2))
        tmp.replace(self.state_path)

    def _note(self, text: str) -> None:
        if text and text not in self.rec.notes:
            self._set(notes="; ".join(n for n in (self.rec.notes, text) if n))

    def _notes_without_failure(self, step: GenStatus) -> str:
        """The notes without the failure note _fail appended last, now that the failed step runs again."""
        notes = self.rec.notes
        cut = notes.rfind(f"failed at {step.value}: ") if self.rec.status == GenStatus.failed else -1
        return notes if cut < 0 else notes[:cut].removesuffix("; ")

    def _fail(self, step: GenStatus, e: Exception) -> None:
        log.error("%s failed at %s", self.gid, step.value, exc_info=e)
        if self.on_log:
            self.on_log(f"{self.gid} failed at {step.value}: {type(e).__name__}: {e}")
        self._note(f"failed at {step.value}: {type(e).__name__}: {e}")
        self._update(
            status=GenStatus.failed,
            current_model_version=self.rec.parent_model_version,
            current_prompt_version=self.rec.parent_prompt_version,
            finished_at=datetime.now(timezone.utc),
        )

    def _finish(self, status: GenStatus) -> GenerationRecord:
        rec = self.rec
        if status == GenStatus.promoted:
            current = (
                rec.candidate_model_version or rec.parent_model_version,
                rec.candidate_prompt_version or rec.parent_prompt_version,
            )
        else:
            current = (rec.parent_model_version, rec.parent_prompt_version)
        self.state.step = status
        self._set(
            status=status,
            current_model_version=current[0],
            current_prompt_version=current[1],
            finished_at=datetime.now(timezone.utc),
        )
        self._write_state()
        with self.conn.transaction():
            if rec.candidate_model_version:
                registry.set_model_status(self.conn, rec.candidate_model_version, status.value)
            registry.put_generation(self.conn, self.rec)
        self._log(f"{self.gid} {status.value}; current {current[0]} + {current[1]}")
        return self.rec

    def _eval_task_ids(self) -> list[str]:
        ids = sorted(self.ctx.env.eval_set(EVAL_SET_ID).task_ids)
        return ids if self.sizes.eval_tasks is None else ids[: self.sizes.eval_tasks]

    def _base(self) -> ModelVersion:
        base = registry.get_model_version(self.conn, BASE)
        assert base is not None  # check_run refuses to start without it
        return base

    # ---- steps: each does its work and returns the next status ----

    def _collecting(self) -> GenStatus:
        res = self.ctx.env.simulate(
            SimulateRequest(
                arm=self.arm,
                seed=self.seed,
                generation=self.generation,
                model_version=self.rec.parent_model_version,
                prompt_version=self.rec.parent_prompt_version,
                n_tasks=self.sizes.traffic,
            )
        )
        self.state.traffic_run_ids, self.state.traffic_failed = res.run_ids, res.failed
        self._set(traffic_runs=len(res.run_ids))
        return GenStatus.mining

    def _mining(self) -> GenStatus:
        mined = self.ctx.agent.mine(
            MineRequest(
                arm=self.arm,
                seed=self.seed,
                generation=self.generation,
                model_version=self.rec.parent_model_version,
                prompt_version=self.rec.parent_prompt_version,
            )
        )
        # This generation's failures: its own traffic plus judge runs (records of other traffic predate a reset).
        traffic = set(self.state.traffic_run_ids)
        records = [r for r in mined if r.run_id in traffic or r.origin == Origin.judge]
        untriaged = self.state.traffic_failed - len({r.run_id for r in records} & traffic)
        if untriaged > 0:
            # /failures/mine is idempotent per (arm, seed, generation): records made earlier with other traffic
            # (for example a tiny run of this generation) would silently hide this traffic's failures.
            raise RuntimeError(
                f"{untriaged} failed traffic runs of {self.gid} have no failure record because this generation was"
                " mined before with other traffic (a tiny run?); delete its app.failures and app.runs rows, then resume"
            )
        included = select_included(records, self.sizes.failure_cap)
        self.state.included_failure_ids = [f.failure_id for f in included]
        self.state.failed_task_ids = list(dict.fromkeys(f.task_id for f in included))
        self._set(failures_mined=len(records), failures_included=len(included))
        if not included:
            self._note("rejected: no included failures in this generation")
            return GenStatus.rejected
        return GenStatus.mutating

    def _mutating(self) -> GenStatus:
        tasks = self.ctx.env.mutate(
            MutateRequest(
                failure_ids=self.state.included_failure_ids, variants_per_failure=self.sizes.variants, seed=self.seed
            )
        )
        self.state.variant_task_ids = [t.task_id for t in tasks]
        self._set(variants_created=len(tasks))
        return GenStatus.building_data if self.arm == "D" else GenStatus.learning_prompt

    def _building_data(self) -> GenStatus:
        snap = self.ctx.build_snapshot(
            self.conn,
            self.ctx.agent,
            arm=self.arm,
            seed=self.seed,
            generation=self.generation,
            model_version=self.rec.parent_model_version,
            task_ids=sorted(set(self.state.variant_task_ids) | set(self.state.failed_task_ids)),
            k_samples=self.sizes.k_samples,
        )
        self.state.snapshot_id = snap.snapshot_id
        self._set(examples_built=snap.n_self + snap.n_replay)
        if snap.zero_pass_task_ids:
            self._log(f"{self.gid}: {len(snap.zero_pass_task_ids)} tasks had no passing sample")
        return GenStatus.training

    def _training(self) -> GenStatus:
        lin = registry.lineage(self.arm, self.seed)
        # Cumulative (SPEC 9.7): a generation that kept no examples (snapshot_id None) still trains on earlier ones.
        examples = self.ctx.lineage_examples(self.conn, lin)
        if not examples:
            self._note(f"rejected: the cumulative training dataset of {lin} is empty")
            return GenStatus.rejected
        path = self.dir / "dataset.jsonl"
        n = self.ctx.export_jsonl(examples, path)
        if isinstance(self.ctx.trainer, FakeTrainer):
            log.warning("FAKE TRAINER: %s will serve the base weights; never report it", self.gid)
            self._note("fake trainer: the candidate serves the base weights")
        self._log(f"{self.gid}: training on {n} examples of {lin}")
        adapter = self.ctx.trainer.train(
            version=self.gid,
            dataset_path=path,
            n_examples=n,
            base_model_hf_id=self._base().base_model_hf_id,
            seed=self.seed,
        )
        self.state.adapter_uri, self.state.training_job_id = adapter.adapter_uri, adapter.training_job_id
        return GenStatus.deploying

    def _deploying(self) -> GenStatus:
        version = self.gid
        if registry.get_model_version(self.conn, version) is None:
            base = self._base()
            adapter = TrainedAdapter(adapter_uri=self.state.adapter_uri, training_job_id=self.state.training_job_id)
            dep = self.ctx.deployer.deploy(version=version, adapter=adapter, base=base)
            # Runs left from before a reset_lineage, made at another endpoint: the gate would reuse them and the agent
            # may still cache that endpoint for this version.
            stale = self.conn.execute(
                "SELECT DISTINCT body->>'endpoint_model_id' FROM app.runs WHERE model_version = %s"
                " AND body->>'endpoint_model_id' <> %s ORDER BY 1",
                (version, dep.endpoint_model_id),
            ).fetchall()
            if stale:
                raise RuntimeError(
                    f"app.runs has runs of {version} made at endpoint model {stale[0][0]}, not {dep.endpoint_model_id}"
                    " (left from before a reset); delete those runs and restart the agent, then resume"
                )
            self.ctx.tool_check(dep.endpoint_base_url, dep.endpoint_model_id, self.ctx.settings.model_api_key)
            registry.put_model_version(
                self.conn,
                ModelVersion(
                    version=version,
                    arm=self.arm,
                    seed=self.seed,
                    generation=self.generation,
                    parent_version=self.rec.parent_model_version,
                    base_model_hf_id=base.base_model_hf_id,
                    endpoint_base_url=dep.endpoint_base_url,
                    endpoint_model_id=dep.endpoint_model_id,
                    adapter_uri=adapter.adapter_uri,
                    training_job_id=adapter.training_job_id,
                    snapshot_id=self.state.snapshot_id,
                    status="candidate",
                    created_at=datetime.now(timezone.utc),
                ),
            )
        self._set(candidate_model_version=version)
        return GenStatus.gating

    def _learning_prompt(self) -> GenStatus:
        res = self.ctx.env.learn(
            LearnRequest(
                arm=self.arm,
                seed=self.seed,
                generation=self.generation,
                model_version=self.rec.parent_model_version,
                current_prompt_version=self.rec.parent_prompt_version,
                failure_ids=self.state.included_failure_ids,
            )
        )
        self._set(candidate_prompt_version=res.prompt_version)
        self._note(res.notes)
        return GenStatus.gating

    def _gating(self) -> GenStatus:
        if self.generation == 0:
            with _BASE_EVAL_LOCK:
                score = self.ctx.eval_score(
                    self.conn,
                    self.ctx.agent,
                    model_version=BASE,
                    prompt_version=BASE_PROMPT_VERSION,
                    arm=self.arm,
                    seed=self.seed,
                    generation=0,
                    eval_task_ids=self._eval_task_ids(),
                )
            self._set(eval_score=score, parent_eval_score=score)
            return GenStatus.promoted
        rec = self.rec
        res = self.ctx.run_gate(
            self.conn,
            self.ctx.agent,
            arm=self.arm,
            seed=self.seed,
            generation=self.generation,
            candidate_model_version=rec.candidate_model_version or rec.parent_model_version,
            candidate_prompt_version=rec.candidate_prompt_version or rec.parent_prompt_version,
            parent_model_version=rec.parent_model_version,
            parent_prompt_version=rec.parent_prompt_version,
            parent_eval_score=rec.parent_eval_score,
            eval_task_ids=self._eval_task_ids(),
        )
        self._set(
            eval_score=res.eval_score,
            regression_score=res.regression_score,
            parent_regression_score=res.parent_regression_score,
        )
        self._note(
            f"gate: eval {res.eval_score:.3f} vs parent {rec.parent_eval_score:.3f},"
            f" regression {res.regression_score:.3f} vs parent {res.parent_regression_score:.3f}"
        )
        return GenStatus.promoted if res.promote else GenStatus.rejected


def reset_lineage(conn: psycopg.Connection, arm: str, seed: int) -> dict[str, int]:
    """Delete lineage {arm}-s{seed} from Person 3's tables: its generations, its model versions (never base) and its
    training examples. For use before full-size runs; never while a job runs on the lineage. Runs, failures, variants
    and prompts in the other owners' tables stay (the loop refuses to build on stale ones), and the agent keeps
    cached endpoints of deleted model versions until it restarts."""
    with conn.transaction():
        gens = conn.execute("DELETE FROM app.generations WHERE arm = %s AND seed = %s", (arm, seed)).rowcount
        models = conn.execute(
            "DELETE FROM app.model_versions WHERE version <> %s AND body->>'arm' = %s AND (body->>'seed')::int = %s",
            (BASE, arm, seed),
        ).rowcount
        examples = conn.execute(
            "DELETE FROM app.training_examples WHERE lineage = %s", (registry.lineage(arm, seed),)
        ).rowcount
    return {"generations": gens, "model_versions": models, "training_examples": examples}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scar_train.loop", description="Loop runner maintenance.")
    parser.add_argument("command", choices=["reset-lineage"])
    parser.add_argument("arm", choices=["A", "B", "C", "D", "E"])
    parser.add_argument("seed", type=int)
    args = parser.parse_args(argv)
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        counts = reset_lineage(conn, args.arm, args.seed)
    print(f"reset {registry.lineage(args.arm, args.seed)}: deleted {counts}")
    print("restart the agent service before these model versions are registered again: it caches their endpoints")
    return 0


if __name__ == "__main__":
    sys.exit(main())
