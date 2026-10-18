import json

import pytest
from scar_schemas.models import ChatMessage, TrainingExample

from scar_train.config import REPO_ROOT
from scar_train.fixtures import fixture_examples, main, read_runs, validate_fixture
from scar_train.veri import veri_rows

from .factories import run_result


def passed(run_id, task_id="task_1", **kwargs):
    return run_result(run_id, task_id, passed=True, purpose="traffic", stop_reason="submitted", **kwargs)


def test_fixture_excludes_failed_eval_dev_and_other_arm_runs():
    runs = [
        passed("run_keep"),
        run_result("run_fail", "task_2", passed=False),
        passed("run_eval", "task_3", split="eval"),
        passed("run_dev", "task_4", model_version="dev"),
        passed("run_other_arm", "task_5", arm="C"),
        passed("run_other_prompt", "task_6", prompt_version="B-s0-g1"),
    ]
    examples = fixture_examples(runs)
    assert [example.origin_run_id for example in examples] == ["run_keep"]
    assert examples[0].messages == runs[0].messages
    validate_fixture(runs, examples)
    examples[0].messages = [ChatMessage(role="assistant", content="edited")]
    with pytest.raises(ValueError, match="transcript or provenance"):
        validate_fixture(runs, examples)


def test_fixture_deduplicates_and_caps_and_refuses_mixed_lineages():
    runs = [passed("run_1"), passed("run_2"), passed("run_3", calls=[("get_vendors", {})])]
    examples = fixture_examples(runs)
    assert [example.origin_run_id for example in examples] == ["run_1", "run_3"]
    with pytest.raises(ValueError, match="exactly one lineage"):
        fixture_examples([passed("run_1"), passed("run_2", seed=1)])
    with pytest.raises(ValueError, match="no passed"):
        fixture_examples([])


def test_build_validate_and_export_cli(tmp_path):
    source, output, export = [tmp_path / name for name in ("runs.jsonl", "examples.jsonl", "sft.jsonl")]
    run = passed("run_1")
    source.write_text(run.model_dump_json() + "\n")
    args = ["--runs", str(source), "--examples", str(output), "--export", str(export)]
    assert main(["build", *args]) == 0
    assert main(["validate", *args]) == 0
    assert json.loads(export.read_text())["messages"][-1]["role"] == run.messages[-1].role
    output.write_text("")
    assert main(["validate", *args]) == 1


def test_shipped_fixture_matches_actual_passed_runs():
    runs = read_runs(REPO_ROOT / "fixtures/runs_sample.jsonl")
    examples = [
        TrainingExample.model_validate_json(line)
        for line in (REPO_ROOT / "fixtures/training_examples_sample.jsonl").read_text().splitlines()
    ]
    validate_fixture(runs, examples)


def test_real_fixture_exports_as_veri_prompt_completion_rows(tmp_path):
    export = tmp_path / "dataset.jsonl"
    assert main(["validate", "--export", str(export)]) == 0
    rows = [row for line in export.read_text().splitlines() for row in veri_rows(json.loads(line))]
    assert rows
    for row in rows:
        assert row["completion"][0]["role"] == "assistant"
        assert isinstance(json.loads(row["tools"]), list)
        for message in row["prompt"] + row["completion"]:
            assert "content" in message
            for call in message.get("tool_calls", []):
                assert isinstance(json.loads(call["function"]["arguments"]), dict)
