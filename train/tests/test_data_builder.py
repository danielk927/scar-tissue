import json
import random
from datetime import timedelta

import pytest
from psycopg.types.json import Jsonb
from scar_schemas.constants import MAX_EXAMPLES_PER_TASK, REPLAY_RATIO, SAMPLE_TEMPERATURE
from scar_schemas.models import ChatMessage, ToolCall, TrainingExample
from scar_schemas.openai_format import from_openai_message, load_tools, to_openai_messages

from scar_train.clients import DependencyError
from scar_train.data_builder import build_snapshot, export_jsonl, lineage_examples

from .factories import NOW, FakeAgent, insert_run, insert_task, run_result, task

KEY = dict(arm="D", seed=0, model_version="base")


def stored(conn, run_id, task_id, minute=0, **kw):
    r = run_result(run_id, task_id, created_at=NOW + timedelta(minutes=minute), **kw)
    insert_run(conn, r)
    return r


def train_tasks(conn, *task_ids):
    for t in task_ids:
        insert_task(conn, task(t))


def examples(conn, snapshot_id=None):
    rows = conn.execute(
        "SELECT body FROM app.training_examples WHERE %(s)s::text IS NULL OR snapshot_id = %(s)s"
        ' ORDER BY example_id COLLATE "C"',
        {"s": snapshot_id},
    ).fetchall()
    return [TrainingExample.model_validate(b) for (b,) in rows]


def origins(exs, source=None):
    return sorted(e.origin_run_id for e in exs if source in (None, e.source))


def test_keep_rules_cap_fewer_steps_run_id_ties_and_duplicates(conn):
    assert MAX_EXAMPLES_PER_TASK == 2
    train_tasks(conn, "task_a", "task_b", "task_c")
    sql_y = [("sql_query", {"sql": "select y"})]
    # task_a: fewest steps win; run_a3 duplicates run_a2 (same calls and answer), so run_a4 takes the second slot.
    stored(conn, "run_a1", "task_a", 0, passed=True, calls=[("get_accounts", {}), ("sql_query", {"sql": "x"})])
    stored(conn, "run_a2", "task_a", 1, passed=True, calls=sql_y)
    stored(conn, "run_a3", "task_a", 2, passed=True, calls=sql_y)
    stored(conn, "run_a4", "task_a", 3, passed=True, calls=[("get_vendors", {"name_contains": "café"})])
    # task_b: equal steps break ties by run_id, not by creation order; failed attempts are never kept.
    stored(conn, "run_b2", "task_b", 0, passed=True, calls=[("get_accounts", {})])
    stored(conn, "run_b1", "task_b", 1, passed=True, calls=[("get_vendors", {})])
    stored(conn, "run_b3", "task_b", 2, passed=True, calls=[("get_transactions", {})])
    stored(conn, "run_b0", "task_b", 3, passed=False, calls=[], steps=1)
    # task_c: same calls as run_a2 but another task, and same calls with another final answer: neither is a duplicate.
    stored(conn, "run_c1", "task_c", 0, passed=True, calls=sql_y)
    stored(conn, "run_c2", "task_c", 1, passed=True, calls=sql_y, answer={"number": 1234.505})
    stored(conn, "run_c3", "task_c", 2)
    stored(conn, "run_c4", "task_c", 3)
    agent = FakeAgent(conn)

    snap = build_snapshot(conn, agent, **KEY, generation=1, task_ids=["task_a", "task_b", "task_c"], k_samples=4)

    assert agent.batches == []  # all 4 attempts per task were stored already
    assert (snap.n_self, snap.n_replay, snap.zero_pass_task_ids) == (6, 0, [])
    kept = examples(conn, snap.snapshot_id)
    assert origins(kept) == ["run_a2", "run_a4", "run_b1", "run_b2", "run_c1", "run_c2"]
    assert {e.source for e in kept} == {"self"}


def test_zero_pass_tasks_are_reported_and_nothing_passing_writes_nothing(conn):
    train_tasks(conn, "task_1", "task_2")
    agent = FakeAgent(conn)  # every attempt fails

    snap = build_snapshot(conn, agent, **KEY, generation=1, task_ids=["task_1", "task_2"], k_samples=2)
    assert (snap.snapshot_id, snap.n_self, snap.n_replay, snap.zero_pass_task_ids) == (None, 0, 0, ["task_1", "task_2"])
    assert examples(conn) == []
    assert [(r.task_id, r.n) for r in agent.requests] == [("task_1", 2), ("task_2", 2)]
    # Calling again neither resamples nor writes.
    assert build_snapshot(conn, agent, **KEY, generation=1, task_ids=["task_1", "task_2"], k_samples=2) == snap
    assert len(agent.batches) == 1 and examples(conn) == []

    agent = FakeAgent(conn, lambda req, i: {"passed": req.task_id == "task_1"})
    snap = build_snapshot(conn, agent, **KEY, generation=2, task_ids=["task_1", "task_2"], k_samples=2)
    assert snap.snapshot_id and snap.zero_pass_task_ids == ["task_2"]
    assert {e.task_id for e in examples(conn, snap.snapshot_id)} == {"task_1"}


def _replay_world(conn):
    """Self: task_s1..s3 pass once each (generation 2, k=1). Traffic candidates for D-s0 at g <= 2: task_p1..p4."""
    train_tasks(conn, "task_s1", "task_s2", "task_s3", *[f"task_p{i}" for i in range(1, 13)])
    for i in (1, 2, 3):
        stored(conn, f"run_s{i}", f"task_s{i}", generation=2, passed=True)
    traffic = dict(purpose="traffic", passed=True, temperature=0.0)
    stored(conn, "run_t1", "task_p1", 0, generation=1, **traffic)
    stored(conn, "run_t1b", "task_p1", 9, generation=2, **traffic)  # same task later: the earliest run represents it
    stored(conn, "run_t2", "task_p2", 1, generation=2, **traffic)
    stored(conn, "run_t3", "task_p3", 2, generation=1, **traffic)
    stored(conn, "run_t4", "task_p4", 3, generation=2, **traffic)
    # Not candidates: failed, other arm, other seed, other prompt, eval split, later generation, a task already in
    # the snapshot, and a run the lineage already trained on.
    stored(conn, "run_x6", "task_p6", generation=1, **{**traffic, "passed": False})
    stored(conn, "run_x7", "task_p7", generation=1, arm="C", **traffic)
    stored(conn, "run_x8", "task_p8", generation=1, seed=1, **traffic)
    stored(conn, "run_x9", "task_p9", generation=1, prompt_version="B-s0-g1", **traffic)
    stored(conn, "run_x10", "task_p10", generation=1, split="eval", **traffic)
    stored(conn, "run_x11", "task_p11", generation=3, **traffic)
    stored(conn, "run_xs1", "task_s1", generation=1, **traffic)
    used = stored(conn, "run_x12", "task_p12", generation=1, **traffic)
    _insert_example(conn, "ex_old", "snap_old", "D-s0", used.run_id, "replay", NOW)


def _insert_example(conn, example_id, snapshot_id, lineage, origin_run_id, source, created_at):
    r = run_result(origin_run_id, "task_old")
    ex = TrainingExample(
        example_id=example_id,
        snapshot_id=snapshot_id,
        lineage=lineage,
        task_id=r.task_id,
        source=source,
        origin_run_id=origin_run_id,
        prompt_version=r.prompt_version,
        tools_version=r.tools_version,
        messages=r.messages,
        created_at=created_at,
    )
    conn.execute(
        "INSERT INTO app.training_examples (example_id, snapshot_id, lineage, task_id, source, body, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (example_id, snapshot_id, lineage, ex.task_id, source, Jsonb(ex.model_dump(mode="json")), created_at),
    )
    return ex


def test_replay_count_selection_and_determinism(conn):
    _replay_world(conn)
    agent = FakeAgent(conn)
    tasks = ["task_s1", "task_s2", "task_s3"]

    snap = build_snapshot(conn, agent, **KEY, generation=2, task_ids=tasks, k_samples=1)

    assert agent.batches == []
    assert (snap.n_self, snap.n_replay) == (3, round(REPLAY_RATIO * 3))
    candidates = ["run_t1", "run_t2", "run_t3", "run_t4"]  # sorted by task_id
    expected = sorted(random.Random(0 * 100 + 2).sample(candidates, 3))
    built = examples(conn, snap.snapshot_id)
    assert origins(built, "replay") == expected
    assert origins(built, "self") == ["run_s1", "run_s2", "run_s3"]
    assert {e.lineage for e in built} == {"D-s0"}

    # Same inputs, same replay choice.
    conn.execute("DELETE FROM app.training_examples WHERE snapshot_id = %s", (snap.snapshot_id,))
    again = build_snapshot(conn, agent, **KEY, generation=2, task_ids=tasks, k_samples=1)
    assert again.snapshot_id != snap.snapshot_id
    assert origins(examples(conn, again.snapshot_id), "replay") == expected

    # Fewer candidates than wanted: take them all (D-s1 has one, task_p8).
    for i in (1, 2):
        stored(conn, f"run_s1_{i}", f"task_s{i}", seed=1, generation=2, passed=True)
    snap = build_snapshot(
        conn, agent, arm="D", seed=1, model_version="base", generation=2, task_ids=tasks[:2], k_samples=1
    )
    assert (snap.n_self, snap.n_replay) == (2, 1)
    assert origins(examples(conn, snap.snapshot_id), "replay") == ["run_x8"]


def test_eval_tasks_never_enter_a_snapshot(conn):
    train_tasks(conn, "task_1")
    insert_task(conn, task("task_eval", split="eval"))
    agent = FakeAgent(conn, lambda req, i: {"passed": True})
    with pytest.raises(RuntimeError, match="eval leakage"):
        build_snapshot(conn, agent, **KEY, generation=1, task_ids=["task_1", "task_eval"], k_samples=2)
    assert agent.batches == [] and examples(conn) == []

    # The check also covers replay picks, right before writing (here app.runs disagrees with app.tasks).
    insert_task(conn, task("task_eval2", split="eval"))
    stored(conn, "run_t", "task_eval2", purpose="traffic", passed=True)
    with pytest.raises(RuntimeError, match="eval leakage: 1 non-train tasks, e.g. task_eval2"):
        build_snapshot(conn, agent, **KEY, generation=1, task_ids=["task_1"], k_samples=2)
    assert examples(conn) == []


def test_resume_requests_only_missing_attempts(conn):
    train_tasks(conn, "task_1", "task_2", "task_3")
    # task_1: two real attempts and two infrastructure failures (not attempts).
    stored(conn, "run_11", "task_1", 0, passed=True)
    stored(conn, "run_12", "task_1", 1, passed=False)
    stored(conn, "run_13", "task_1", 2, stop_reason="model_error")
    stored(conn, "run_14", "task_1", 3, stop_reason="timeout")
    # Runs with another key do not count either.
    for i, other in enumerate(
        [
            {"model_version": "D-s0-g1"},
            {"generation": 2},
            {"prompt_version": "B-s0-g1"},
            {"purpose": "traffic"},
            {"arm": "C"},
            {"seed": 1},
        ]
    ):
        stored(conn, f"run_o{i}", "task_1", 4, passed=True, **other)
    for i in range(4):  # task_2: complete
        stored(conn, f"run_2{i}", "task_2", i)

    down = FakeAgent(conn, lambda req, i: {"stop_reason": "tool_error"})
    with pytest.raises(DependencyError, match="all 6 runs ended in model_error/tool_error/timeout"):
        build_snapshot(conn, down, **KEY, generation=1, task_ids=["task_1", "task_2", "task_3"], k_samples=4)
    assert examples(conn) == []

    agent = FakeAgent(conn, lambda req, i: {"passed": True, "calls": [("sql_query", {"sql": f"select {i}"})]})
    snap = build_snapshot(conn, agent, **KEY, generation=1, task_ids=["task_1", "task_2", "task_3"], k_samples=4)

    for got in (down, agent):
        assert len(got.batches) == 1 and got.batches[0].max_concurrency == 16
        assert [(r.task_id, r.n) for r in got.requests] == [("task_1", 2), ("task_3", 4)]
    for r in agent.requests:
        assert (r.model_version, r.prompt_version, r.arm, r.seed, r.generation) == ("base", "system_v1", "D", 0, 1)
        assert (r.purpose, r.temperature) == ("sampling", SAMPLE_TEMPERATURE)
    assert snap.zero_pass_task_ids == ["task_2"]
    assert {e.task_id for e in examples(conn, snap.snapshot_id) if e.source == "self"} == {"task_1", "task_3"}


def test_idempotent_per_lineage_and_generation_and_cumulative(conn):
    train_tasks(conn, "task_1", "task_2")
    agent = FakeAgent(conn, lambda req, i: {"passed": True, "calls": [("sql_query", {"sql": f"select {i}"})]})
    first = build_snapshot(conn, agent, **KEY, generation=1, task_ids=["task_1", "task_2"], k_samples=2)
    assert (first.n_self, first.n_replay) == (4, 0)

    again = build_snapshot(conn, FakeAgent(conn), **KEY, generation=1, task_ids=["task_1", "task_2"], k_samples=2)
    assert again == first
    assert len(examples(conn)) == 4

    second = build_snapshot(conn, agent, **KEY, generation=2, task_ids=["task_1"], k_samples=2)
    assert second.snapshot_id not in (None, first.snapshot_id)
    assert agent.requests[-1].generation == 2
    cumulative = lineage_examples(conn, "D-s0")
    assert len(cumulative) == first.n_self + second.n_self
    assert {e.snapshot_id for e in cumulative} == {first.snapshot_id, second.snapshot_id}
    assert lineage_examples(conn, "D-s1") == []


def test_lineage_examples_dedup_by_origin_run_in_created_then_id_order(conn):
    later = NOW + timedelta(hours=1)
    _insert_example(conn, "ex_b", "snap_1", "D-s0", "run_1", "self", NOW)
    _insert_example(conn, "ex_a", "snap_1", "D-s0", "run_2", "replay", NOW)
    _insert_example(conn, "ex_0", "snap_2", "D-s0", "run_1", "replay", later)  # same origin run: dropped
    _insert_example(conn, "ex_c", "snap_2", "D-s0", "run_3", "self", later)
    _insert_example(conn, "ex_z", "snap_3", "D-s1", "run_4", "self", NOW)
    assert [e.example_id for e in lineage_examples(conn, "D-s0")] == ["ex_a", "ex_b", "ex_c"]


def _rich_messages():
    return [
        ChatMessage(role="system", content="You are a careful bookkeeping assistant."),
        ChatMessage(role="user", content="Company: Café Ünited\n\nTotal spend in March 2026?"),
        ChatMessage(
            role="assistant",
            content="Let me look.",
            tool_calls=[
                ToolCall(id="c1", name="sql_query", arguments={"sql": "SELECT 1", "b": [1, 2.5]}),
                ToolCall(id="c2", name="get_vendors", arguments={"__raw__": '{"name_contains": "caf'}),
            ],
        ),
        ChatMessage(role="tool", tool_call_id="c1", name="sql_query", content='{"rows":[["Café",1]]}'),
        ChatMessage(
            role="tool", tool_call_id="c2", name="get_vendors", content='{"error":"arguments were not valid JSON"}'
        ),
        ChatMessage(role="assistant", tool_calls=[ToolCall(id="c3", name="submit_answer", arguments={"number": 12.5})]),
        ChatMessage(role="tool", tool_call_id="c3", name="submit_answer", content='{"ok":true}'),
    ]


def test_examples_are_verbatim_runs(conn):
    train_tasks(conn, "task_1")
    run = stored(conn, "run_1", "task_1", passed=True, messages=_rich_messages(), steps=2)

    snap = build_snapshot(conn, FakeAgent(conn), **KEY, generation=1, task_ids=["task_1"], k_samples=1)

    row = conn.execute(
        "SELECT example_id, snapshot_id, lineage, task_id, source, body, created_at FROM app.training_examples"
    ).fetchone()
    run_body = conn.execute("SELECT body FROM app.runs WHERE run_id = 'run_1'").fetchone()[0]
    ex = TrainingExample.model_validate(row[5])
    assert row[5]["messages"] == run_body["messages"]
    assert ex.messages == run.messages
    assert (ex.origin_run_id, ex.prompt_version, ex.tools_version, ex.source) == (
        "run_1",
        "system_v1",
        "tools_v1",
        "self",
    )
    assert row[:5] == (ex.example_id, snap.snapshot_id, "D-s0", "task_1", "self")
    assert ex.example_id.startswith("ex_") and ex.snapshot_id.startswith("snap_")
    assert row[6] == ex.created_at and ex.created_at.utcoffset() == timedelta(0)


def test_export_round_trips_and_is_deterministic(tmp_path):
    r1 = run_result("run_1", "task_1", passed=True, messages=_rich_messages())
    r2 = run_result("run_2", "task_2", passed=True)
    exs = [
        TrainingExample(
            example_id=f"ex_{i}",
            snapshot_id="snap_1",
            lineage="D-s0",
            task_id=r.task_id,
            source="self",
            origin_run_id=r.run_id,
            prompt_version=r.prompt_version,
            tools_version=r.tools_version,
            messages=r.messages,
            created_at=NOW,
        )
        for i, r in enumerate([r1, r2])
    ]
    path = tmp_path / "data.jsonl"
    assert export_jsonl(exs, path) == 2
    text = path.read_text(encoding="utf-8")
    lines = [json.loads(s) for s in text.splitlines()]
    assert len(lines) == 2 and "Café" in text  # ensure_ascii=False
    for line, ex in zip(lines, exs, strict=True):
        assert set(line) == {"messages", "tools"}
        assert line["messages"] == to_openai_messages(ex.messages)
        assert [from_openai_message(m) for m in line["messages"]] == ex.messages
        # Same tools, keys in the same order the agent sends them (chat templates render them in order).
        assert json.dumps(line["tools"]) == json.dumps(load_tools("tools_v1"))

    export_jsonl(exs, tmp_path / "again.jsonl")
    assert (tmp_path / "again.jsonl").read_bytes() == path.read_bytes()
