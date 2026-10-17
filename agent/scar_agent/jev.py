"""Jev client and the triage questions (SPEC 9.4). Verified usage: docs/JEV.md."""

from dataclasses import dataclass
from typing import Any

from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, RetryPolicy

from .config import env

# pinned (D-08); jev-latest currently resolves to jev-1.13.0 (docs/JEV.md). Passed explicitly on every call,
# so the Tev1 fallback (F19) works by setting TYPESAFE_DEFAULT_MODEL=tev1 and TYPESAFE_BASE_URL.
JEV_MODEL = env("TYPESAFE_DEFAULT_MODEL", "jev-1.14.0")

# question texts are part of the contract (SPEC 9.4)
TRIAGE_QUESTIONS = {
    "cause": Choice(
        instructions="Why did this accounting agent's run fail? Judge from `steps`, `final_answer`, `stop_reason` and `verifier_detail`.",
        criteria={
            "wrong_tool": "The agent used tools that could not answer the question, or skipped a tool it clearly needed.",
            "bad_tool_args": "The agent called a reasonable tool with wrong arguments, such as wrong dates, account, vendor, or filters.",
            "reasoning_error": "The agent retrieved the right data but computed or concluded incorrectly.",
            "wrong_final_format": "The agent had the right answer but submitted it in the wrong field or format.",
            "gave_up": "The agent stopped without submitting an answer, or never finished.",
            "environment_error": "A tool or service failed in a way the agent could not have fixed, such as timeouts or server errors.",
            "ambiguous_task": "The question itself is unclear or could reasonably be read in more than one way.",
            "other": "None of the above describes the failure.",
        },
    ),
    "learnable": Noul(
        instructions="A better model using the same tools and the same information could have answered this task correctly.",
        criteria={
            "true": "The failure came from the agent's own choices or reasoning.",
            "false": "The failure came from the environment, a broken tool, or an unanswerable question.",
        },
    ),
}


@dataclass
class Triage:
    cause: str
    cause_confidence: float
    cause_probabilities: dict[str, float]
    learnable_p: float
    jev_model: str


@dataclass
class ChoiceResult:
    choice: str
    confidence: float
    probabilities: dict[str, float]


class Jev:
    def __init__(self, client: Any | None = None):
        # SPEC 7.4: 30 s with up to 3 retries -> 10 s per attempt inside a 30 s budget (docs/JEV.md)
        self.client = client or AsyncTypeSafeClient(
            model=JEV_MODEL, timeout=10.0, retry=RetryPolicy(max_retries=3, timeout=30.0))

    async def triage(self, state: dict) -> Triage:
        r = await self.client.system_one(state=state, questions=TRIAGE_QUESTIONS, model=JEV_MODEL)
        cause = r.choices["cause"]
        return Triage(
            cause=cause.choice,
            cause_confidence=cause.confidence,
            cause_probabilities=dict(cause.probabilities),
            learnable_p=r.nouls["learnable"].noul,
            jev_model=r.model,
        )

    async def choose(self, state: Any, instructions: str, criteria: dict[str, str | None]) -> ChoiceResult:
        r = await self.client.system_one(
            state=state, questions={"q": Choice(instructions=instructions, criteria=criteria)},
            model=JEV_MODEL)
        a = r.choices["q"]
        return ChoiceResult(a.choice, a.confidence, dict(a.probabilities))
