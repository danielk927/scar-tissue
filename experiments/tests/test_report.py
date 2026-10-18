import pytest
from psycopg.types.json import Jsonb
from scar_schemas.models import GenerationRecord

from scar_experiments.report import Cell, main, summarize

from .factories import NOW

BASE = 0.4


def _record(arm, seed, generation, status, eval_score=None, parent_eval_score=BASE) -> GenerationRecord:
    gid = f"{arm}-s{seed}-g{generation}"
    return GenerationRecord(
        generation_id=gid,
        arm=arm,
        seed=seed,
        generation=generation,
        status=status,
        parent_model_version="base",
        parent_prompt_version="system_v1",
        candidate_model_version=gid if arm in ("D", "E") else None,
        candidate_prompt_version=gid if arm in ("B", "C") else None,
        current_model_version="base",
        current_prompt_version="system_v1",
        eval_score=eval_score,
        parent_eval_score=parent_eval_score,
        started_at=NOW,
        finished_at=NOW,
    )


RECORDS = [
    _record("D", 0, 1, "promoted", 0.5),
    _record("D", 0, 2, "rejected", 0.45, 0.5),
    _record("D", 1, 1, "rejected", 0.35),
    _record("D", 1, 2, "promoted", 0.55),
    _record("D", 2, 1, "training"),  # unfinished: left out
    _record("C", 0, 1, "promoted", 0.42),
    _record("B", 0, 1, "rejected", 0.38),
]


def _insert(conn, records):
    for r in records:
        conn.execute(
            "INSERT INTO app.generations (generation_id, arm, seed, generation, status, body, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (r.generation_id, r.arm, r.seed, r.generation, r.status.value, Jsonb(r.model_dump(mode="json")), NOW),
        )


def test_summarize_uses_current_score_after_each_generation():
    cells, unfinished = summarize(RECORDS)
    assert unfinished == 1
    by_key = {(c.arm, c.generation): c for c in cells}
    assert [(c.arm, c.generation) for c in cells] == [
        ("A", 0),
        ("A", 1),
        ("A", 2),
        ("B", 0),
        ("B", 1),
        ("C", 0),
        ("C", 1),
        ("D", 0),
        ("D", 1),
        ("D", 2),
    ]
    assert by_key["A", 2] == Cell("A", 2, BASE, BASE, BASE, 1)
    assert by_key["B", 1] == Cell("B", 1, BASE, BASE, BASE, 1)  # rejected: parent's score
    assert by_key["C", 1] == Cell("C", 1, 0.42, 0.42, 0.42, 1)  # promoted: candidate's score
    assert by_key["D", 0] == Cell("D", 0, BASE, BASE, BASE, 2)
    assert by_key["D", 1] == Cell("D", 1, pytest.approx(0.45), 0.4, 0.5, 2)
    assert by_key["D", 2] == Cell("D", 2, pytest.approx(0.525), 0.5, 0.55, 2)


def test_failed_generation_keeps_the_parent_current():
    cells, left_out = summarize(
        [
            _record("D", 0, 1, "promoted", 0.5),
            _record("D", 0, 2, "failed", None, 0.5),
            _record("D", 0, 3, "failed", None, None),  # failed before a parent score was recorded: left out
        ]
    )
    by_key = {(c.arm, c.generation): c.mean for c in cells}
    assert by_key["D", 2] == 0.5 and ("D", 3) not in by_key and left_out == 1


def test_base_score_from_generation_zero_records_and_must_be_shared():
    zero = _record("D", 0, 0, "promoted", eval_score=0.41, parent_eval_score=None)
    cells, _ = summarize([zero, _record("D", 0, 1, "rejected", 0.3, 0.41)])
    assert {(c.arm, c.generation): c.mean for c in cells}["A", 1] == 0.41
    with pytest.raises(ValueError, match="one shared generation-0 eval score"):
        summarize([_record("D", 0, 1, "promoted", 0.5, 0.4), _record("C", 0, 1, "promoted", 0.5, 0.39)])
    with pytest.raises(ValueError, match="found \\[\\]"):
        summarize([])


def test_report_output_is_byte_identical_across_runs(conn, db_url, tmp_path):
    _insert(conn, RECORDS)
    outputs = []
    for run in ("one", "two"):
        out, png = tmp_path / run / "RESULTS.md", tmp_path / run / "results.png"
        main(["--database-url", db_url, "--out", str(out), "--png", str(png)])
        outputs.append((out.read_bytes(), png.read_bytes()))
    assert outputs[0] == outputs[1]

    markdown, image = outputs[0]
    assert image.startswith(b"\x89PNG") and b"Software" not in image and b"Matplotlib" not in image
    text = markdown.decode()
    assert "![Eval success rate by generation](results.png)" in text
    assert "| Arm | Generation | Mean | Min | Max | n |" in text
    assert "| D - Weight updates (ours) | 2 | 52.5% | 50.0% | 55.0% | 2 |" in text
    assert "| A - Frozen base | 2 | 40.0% | 40.0% | 40.0% | 1 |" in text
    # every gated candidate, rejected ones included, in generation order; the unfinished D-s2-g1 is not one
    rows = [line for line in text.splitlines() if line.startswith("| 1 |") or line.startswith("| 2 |")]
    assert rows == [
        "| 1 | B-s0 (Failure memory) | 38.0% | 40.0% | - | - | rejected |",
        "| 1 | C-s0 (GEPA) | 42.0% | 40.0% | - | - | promoted |",
        "| 1 | D-s0 (Weight updates (ours)) | 50.0% | 40.0% | - | - | promoted |",
        "| 1 | D-s1 (Weight updates (ours)) | 35.0% | 40.0% | - | - | rejected |",
        "| 2 | D-s0 (Weight updates (ours)) | 45.0% | 50.0% | - | - | rejected |",
        "| 2 | D-s1 (Weight updates (ours)) | 55.0% | 40.0% | - | - | promoted |",
    ]
    assert text.endswith("Generation records left out (still in progress, or without the score they need): 1.\n")


def test_report_without_finished_generations_exits_with_error(db_url, tmp_path):
    with pytest.raises(SystemExit, match="one shared generation-0 eval score"):
        main(["--database-url", db_url, "--out", str(tmp_path / "r.md"), "--png", str(tmp_path / "r.png")])
