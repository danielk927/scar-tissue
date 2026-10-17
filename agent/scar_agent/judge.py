"""Judge flow (SPEC 9.12): Jev picks the template, the parse LM fills params, env validates."""

import json
import re

from scar_schemas.constants import EVAL_TEMPERATURE
from scar_schemas.models import (
    FromParamsRequest,
    JudgeParseRequest,
    JudgeParseResult,
    JudgeRunRequest,
    JudgeRunResult,
    Origin,
    RunRequest,
)

from . import observability as obs
from .clients import EnvClient, TrainClient, Unavailable, openai_client, parse_lm_endpoint
from .db import Store
from .jev import Jev
from .runner import Runner

JUDGE_ARM, JUDGE_SEED = "D", 0
MIN_TEMPLATE_CONFIDENCE = 0.5
OTHER = "other"
OTHER_CRITERION = "The question does not match any template."
TEMPLATE_INSTRUCTIONS = "Which task template does this accounting question match?"
FINISHED = {"promoted", "rejected"}


class JudgeError(Exception):
    """Invalid judge input (HTTP 422)."""


def example_questions(catalog: dict) -> list[str]:
    expense = next((a["name"] for a in catalog["accounts"] if a["account_type"] == "expense"), "Office Supplies")
    vendor = catalog["vendors"][0]["name"] if catalog["vendors"] else "your largest vendor"
    return [
        f"How much did we spend on {expense} between 2026-01-01 and 2026-03-31?",
        f"What was the total paid to {vendor} from 2026-04-01 to 2026-06-30?",
        "Which transactions look like duplicate payments between 2026-01-01 and 2026-06-30?",
    ]


def _extract_json(text: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    raw = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("params must be a JSON object")
    return value


def parse_lm_prompt(question: str, template, catalog: dict) -> str:
    return (
        "Extract the parameters of an accounting question as a JSON object.\n\n"
        f"Task template: {template.template_id} - {template.description}\n"
        f"Parameter JSON Schema:\n{json.dumps(template.params_schema, indent=2)}\n\n"
        "Use only IDs from these lists (copy them exactly). Dates are YYYY-MM-DD, months YYYY-MM.\n"
        f"Accounts:\n{json.dumps(catalog['accounts'], default=str)}\n"
        f"Vendors:\n{json.dumps(catalog['vendors'], default=str)}\n\n"
        f"Question: {question}\n\n"
        "Reply with only the JSON object."
    )


class Judge:
    def __init__(self, store: Store, env: EnvClient, train: TrainClient, jev: Jev, runner: Runner):
        self.store, self.env, self.train, self.jev, self.runner = store, env, train, jev, runner

    async def _require_train_company(self, company_id: str) -> None:
        train_ids = {c["company_id"] for c in await self.env.get_companies(split="train")}
        if company_id not in train_ids:
            raise JudgeError(f"{company_id} is not a train-split company")

    async def parse(self, req: JudgeParseRequest) -> JudgeParseResult:
        await self._require_train_company(req.company_id)
        catalog = await self.store.get_company_catalog(req.company_id)
        templates = {t.template_id: t for t in await self.env.get_templates()}
        criteria: dict[str, str | None] = {tid: t.description for tid, t in sorted(templates.items())}
        criteria[OTHER] = OTHER_CRITERION
        try:
            pick = await self.jev.choose(req.question, TEMPLATE_INSTRUCTIONS, criteria)
        except Exception as e:
            raise Unavailable(f"jev template choice failed: {e!r}") from e

        if pick.choice == OTHER or pick.choice not in templates or pick.confidence < MIN_TEMPLATE_CONFIDENCE:
            examples = "\n".join(f"- {q}" for q in example_questions(catalog))
            return JudgeParseResult(
                template_id=None, template_confidence=pick.confidence, params={}, interpretation="",
                valid=False,
                error=f"Sorry, I can't answer that kind of question yet. Try one like:\n{examples}")

        template = templates[pick.choice]
        endpoint = parse_lm_endpoint()
        if endpoint is None:
            raise Unavailable("PARSE_LM_* is not configured")
        try:
            resp = await openai_client(endpoint).chat.completions.create(
                model=endpoint.model_id, temperature=0.0,
                messages=[{"role": "user", "content": parse_lm_prompt(req.question, template, catalog)}])
            params = _extract_json(resp.choices[0].message.content or "")
        except (ValueError, json.JSONDecodeError) as e:
            return JudgeParseResult(template_id=template.template_id, template_confidence=pick.confidence,
                                    params={}, interpretation="", valid=False,
                                    error=f"Couldn't read the details of that question ({e}). Try rephrasing it.")
        except Exception as e:
            raise Unavailable(f"parse LM failed: {e!r}") from e

        check = await self.env.from_params(FromParamsRequest(
            template_id=template.template_id, company_id=req.company_id, params=params,
            origin=Origin.judge, prompt=req.question, dry_run=True))
        return JudgeParseResult(
            template_id=template.template_id, template_confidence=pick.confidence, params=params,
            interpretation=check.interpretation, valid=check.valid, error=check.error)

    async def next_generation(self) -> int:
        """Latest finished generation of D-s0, plus one, so the next mining picks the run up."""
        gens = await self.train.generations(JUDGE_ARM, JUDGE_SEED)
        finished = [g["generation"] for g in gens if g["status"] in FINISHED]
        return max(finished, default=0) + 1

    async def run(self, req: JudgeRunRequest) -> JudgeRunResult:
        await self._require_train_company(req.company_id)
        current = await self.train.current(JUDGE_ARM, JUDGE_SEED)
        generation = await self.next_generation()
        created = await self.env.from_params(FromParamsRequest(
            template_id=req.template_id, company_id=req.company_id, params=req.params,
            origin=Origin.judge, prompt=req.question, dry_run=False))
        if not created.valid or created.task is None:
            raise JudgeError(created.error or "invalid judge task")
        [run] = await self.runner.run(RunRequest(
            task_id=created.task.task_id, model_version=current["model_version"],
            prompt_version=current["prompt_version"], arm=JUDGE_ARM, seed=JUDGE_SEED,
            generation=generation, purpose="judge", temperature=EVAL_TEMPERATURE, n=1))
        return JudgeRunResult(task_id=created.task.task_id, run=run,
                              sentry_trace_url=obs.trace_url(run.sentry_trace_id))
