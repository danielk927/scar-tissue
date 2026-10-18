"""Learn-step B, failure memory (SPEC 9.10, P1-12): one lesson per included failure, appended to system_v1.

`conn` must be an autocommit connection: each lesson is committed as soon as it exists, so a re-call after a crash
reuses it instead of paying for it again.
"""

import math
from datetime import datetime, timezone

import psycopg
from scar_schemas.constants import MEMORY_MAX_LESSONS
from scar_schemas.guards import assert_train_only
from scar_schemas.models import LearnRequest, LearnResult, PromptVersion

from .clients import ReflectionLM
from .data import load_included_failures, load_runs, load_tasks, outcome, steps_summary
from .errors import DependencyError, InvalidRequestError
from .prompts import learned_result, store_prompt_version

LESSON_MAX_WORDS = 40
PROMPT_MAX_TOKENS = 3000
LESSONS_HEADER = "\n\nLessons from past mistakes:\n"

LESSON_PROMPT = """An accounting agent answers questions about one company's ledger by calling tools. This run failed.

Question:
{question}

Steps the agent took:
{steps}

Outcome: {outcome}
Verifier: {verify_detail}
Failure cause (triage): {cause}

Write one lesson that would help the agent avoid this mistake on similar questions for any company.
Use the form "When ..., do ...". At most 40 words. Do not mention specific IDs, names or amounts.
Reply with the lesson only."""


def estimate_tokens(text: str) -> int:
    """Conservative token count: 3 characters per token (English prose averages about 4)."""
    return math.ceil(len(text) / 3)


def clean_lesson(raw: str) -> str:
    """One line without a leading bullet or surrounding quotes, cut to LESSON_MAX_WORDS words."""
    text = " ".join(raw.split()).lstrip("-*• ").strip("\"'")
    return " ".join(text.split()[:LESSON_MAX_WORDS])


def build_prompt(system_v1: str, lessons: list[str]) -> tuple[str, int]:
    """system_v1 + header + one "- " line per lesson, dropping lessons from the end until under PROMPT_MAX_TOKENS.
    Returns the prompt and the number of lessons kept."""
    for keep in range(len(lessons), -1, -1):
        text = system_v1 + LESSONS_HEADER + "\n".join(f"- {lesson}" for lesson in lessons[:keep])
        if estimate_tokens(text) < PROMPT_MAX_TOKENS:
            return text, keep
    raise RuntimeError(f"system_v1 alone is over the {PROMPT_MAX_TOKENS}-token prompt cap")


def _store_lesson(conn: psycopg.Connection, lineage: str, generation: int, failure_id: str, text: str) -> None:
    """The lesson ID is `{lineage}:{failure_id}`, so the primary key keeps one lesson per (lineage, failure)."""
    conn.execute(
        "INSERT INTO app.lessons (lesson_id, lineage, generation, failure_id, text, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (lesson_id) DO NOTHING",
        (f"{lineage}:{failure_id}", lineage, generation, failure_id, text, datetime.now(timezone.utc)),
    )


def learn_b(conn: psycopg.Connection, req: LearnRequest, reflection_lm: ReflectionLM, system_v1: str) -> LearnResult:
    if req.arm != "B":
        raise InvalidRequestError(f"/learn/B needs arm B, got {req.arm}")
    version = f"B-s{req.seed}-g{req.generation}"
    if (done := learned_result(conn, version, req)) is not None:
        return done

    lineage = f"B-s{req.seed}"
    failures = load_included_failures(conn, req.failure_ids)
    tasks = load_tasks(conn, [f.task_id for f in failures])
    assert_train_only(list(tasks), conn)
    runs = load_runs(conn, [f.run_id for f in failures])

    have = {
        fid
        for (fid,) in conn.execute(
            "SELECT failure_id FROM app.lessons WHERE lineage = %s AND failure_id = ANY(%s) ORDER BY failure_id",
            (lineage, [f.failure_id for f in failures]),
        ).fetchall()
    }
    new = 0
    for f in failures:
        if f.failure_id in have:
            continue
        run = runs[f.run_id]
        prompt = LESSON_PROMPT.format(
            question=tasks[f.task_id].prompt,
            steps=steps_summary(run),
            outcome=outcome(run),
            verify_detail=run.verify_detail,
            cause=f.cause.value,
        )
        lesson = clean_lesson(reflection_lm(prompt))
        if not lesson:
            raise DependencyError(f"reflection LM returned an empty lesson for failure {f.failure_id}")
        _store_lesson(conn, lineage, req.generation, f.failure_id, lesson)
        new += 1

    # Most recent first: newest generation, then failure_id, so the order depends only on stored data.
    recent = conn.execute(
        "SELECT text FROM app.lessons WHERE lineage = %s AND generation <= %s"
        " ORDER BY generation DESC, failure_id LIMIT %s",
        (lineage, req.generation, MEMORY_MAX_LESSONS),
    ).fetchall()
    text, kept = build_prompt(system_v1, [t for (t,) in recent])
    stored = store_prompt_version(
        conn,
        PromptVersion(
            prompt_version=version,
            arm="B",
            seed=req.seed,
            generation=req.generation,
            text=text,
            parent_prompt_version=req.current_prompt_version,
            is_candidate=False,
            created_at=datetime.now(timezone.utc),
        ),
    )
    notes = f"{kept} lessons in prompt ({new} new from {len(failures)} failures); ~{estimate_tokens(text)} tokens"
    return LearnResult(prompt_version=stored.prompt_version, notes=notes)
