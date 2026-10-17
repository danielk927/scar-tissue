import pytest
from scar_schemas.constants import MEMORY_MAX_LESSONS
from scar_schemas.models import LearnRequest

from scar_experiments.errors import InvalidRequestError, NotFoundError
from scar_experiments.learn_b import (
    LESSON_MAX_WORDS,
    PROMPT_MAX_TOKENS,
    build_prompt,
    clean_lesson,
    estimate_tokens,
    learn_b,
)
from scar_experiments.prompts import get_prompt_version

from .factories import SYSTEM_V1, FakeLM, add_failure


def _req(failures, generation=1, seed=0, current="system_v1", arm="B") -> LearnRequest:
    return LearnRequest(
        arm=arm,
        seed=seed,
        generation=generation,
        model_version="base",
        current_prompt_version=current,
        failure_ids=[f.failure_id for f in failures],
    )


def _numbered_lm():
    counter = iter(range(1000))
    return FakeLM(lambda _: f"When summing an account, do lesson number {next(counter)}.")


def _lessons(conn):
    return conn.execute("SELECT lineage, generation, failure_id, text FROM app.lessons ORDER BY failure_id").fetchall()


def test_learn_b_stores_lessons_and_prompt(conn):
    failures = [add_failure(conn) for _ in range(3)]
    lm = _numbered_lm()
    result = learn_b(conn, _req(failures), lm, SYSTEM_V1)

    assert result.prompt_version == "B-s0-g1"
    pv = get_prompt_version(conn, "B-s0-g1")
    assert (pv.arm, pv.seed, pv.generation, pv.parent_prompt_version, pv.is_candidate) == (
        "B",
        0,
        1,
        "system_v1",
        False,
    )
    lessons = _lessons(conn)
    assert [(lineage, g, fid) for lineage, g, fid, _ in lessons] == [
        ("B-s0", 1, f.failure_id) for f in sorted(failures, key=lambda f: f.failure_id)
    ]
    expected = SYSTEM_V1 + "\n\nLessons from past mistakes:\n" + "\n".join(f"- {t}" for *_, t in lessons)
    assert pv.text == expected
    assert "3 lessons in prompt (3 new" in result.notes

    # One reflection call per failure, each with the question, steps, verifier detail and Jev cause.
    assert len(lm.prompts) == 3
    first = lm.prompts[0]
    assert "How much was spent on rent" in first
    assert 'get_transactions({"account_id":"company-901:rent"}) -> [{"amount":"2500.00"}]' in first
    assert "number: expected 7500.00, got 7600" in first
    assert "bad_tool_args" in first
    assert "40 words" in first and '"When ..., do ..."' in first


def test_learn_b_is_idempotent(conn):
    failures = [add_failure(conn) for _ in range(2)]
    first = learn_b(conn, _req(failures), _numbered_lm(), SYSTEM_V1)
    again_lm = _numbered_lm()
    again = learn_b(conn, _req(failures), again_lm, SYSTEM_V1)
    assert again.prompt_version == first.prompt_version
    assert again_lm.prompts == []
    assert conn.execute("SELECT count(*) FROM app.prompt_versions").fetchone()[0] == 1


def test_existing_lesson_is_reused_not_regenerated(conn):
    failures = [add_failure(conn) for _ in range(2)]
    learn_b(conn, _req(failures[:1]), FakeLM(lambda _: "When reused, do keep me."), SYSTEM_V1)
    lm = FakeLM(lambda _: "When new, do this.")
    result = learn_b(conn, _req(failures, generation=2), lm, SYSTEM_V1)
    assert len(lm.prompts) == 1
    text = get_prompt_version(conn, result.prompt_version).text
    assert "- When reused, do keep me." in text and "- When new, do this." in text


def test_lesson_word_limit_and_single_line():
    raw = '- "When the question names a vendor,\n do ' + " ".join(f"w{i}" for i in range(60)) + '"'
    lesson = clean_lesson(raw)
    assert len(lesson.split()) == LESSON_MAX_WORDS
    assert "\n" not in lesson and lesson.startswith("When the question names a vendor, do w0")


def test_long_reply_is_cut_to_word_limit(conn):
    failures = [add_failure(conn)]
    learn_b(conn, _req(failures), FakeLM(lambda _: "When x, do " + "y " * 100), SYSTEM_V1)
    ((*_, text),) = _lessons(conn)
    assert len(text.split()) == LESSON_MAX_WORDS


def test_at_most_max_lessons_most_recent_first(conn):
    gen1 = [add_failure(conn, generation=1) for _ in range(15)]
    gen2 = [add_failure(conn, generation=2) for _ in range(10)]
    gen3 = [add_failure(conn, generation=3) for _ in range(2)]
    learn_b(conn, _req(gen1, generation=1), FakeLM(lambda _: "When gen1, do a."), SYSTEM_V1)
    learn_b(conn, _req(gen3, generation=3), FakeLM(lambda _: "When gen3, do c."), SYSTEM_V1)
    result = learn_b(conn, _req(gen2, generation=2), FakeLM(lambda _: "When gen2, do b."), SYSTEM_V1)

    lines = get_prompt_version(conn, result.prompt_version).text.split("Lessons from past mistakes:\n")[1].split("\n")
    assert len(lines) == MEMORY_MAX_LESSONS
    # Generation 3 lessons are newer than the request and never used; generation 2 comes first, then generation 1.
    assert lines == ["- When gen2, do b."] * 10 + ["- When gen1, do a."] * 10


def test_prompt_stays_under_token_cap():
    long_system = "x" * (3 * PROMPT_MAX_TOKENS - 600)
    lessons = [f"When case {i}, do " + "word " * 30 for i in range(20)]
    text, kept = build_prompt(long_system, lessons)
    assert estimate_tokens(text) < PROMPT_MAX_TOKENS and 0 < kept < 20
    assert text.endswith(f"- {lessons[kept - 1]}")
    full_text, full_kept = build_prompt(SYSTEM_V1, [" ".join(["word"] * LESSON_MAX_WORDS)] * MEMORY_MAX_LESSONS)
    assert full_kept == MEMORY_MAX_LESSONS and estimate_tokens(full_text) < PROMPT_MAX_TOKENS
    with pytest.raises(RuntimeError, match="token prompt cap"):
        build_prompt("x" * 3 * PROMPT_MAX_TOKENS, lessons)


def test_eval_failure_is_rejected_before_any_write_or_lm_call(conn):
    failures = [add_failure(conn), add_failure(conn, split="eval")]
    lm = _numbered_lm()
    with pytest.raises(RuntimeError, match="eval leakage"):
        learn_b(conn, _req(failures), lm, SYSTEM_V1)
    assert lm.prompts == []
    assert _lessons(conn) == []
    assert conn.execute("SELECT count(*) FROM app.prompt_versions").fetchone()[0] == 0


def test_bad_requests(conn):
    included = add_failure(conn)
    excluded = add_failure(conn, included=False)
    lm = _numbered_lm()
    with pytest.raises(NotFoundError, match="unknown failure: fail_missing"):
        learn_b(conn, _req([included]).model_copy(update={"failure_ids": ["fail_missing"]}), lm, SYSTEM_V1)
    with pytest.raises(InvalidRequestError, match="not included"):
        learn_b(conn, _req([included, excluded]), lm, SYSTEM_V1)
    with pytest.raises(InvalidRequestError, match="arm B"):
        learn_b(conn, _req([included], arm="C"), lm, SYSTEM_V1)
    assert lm.prompts == []
