"""Judge flow per SPEC 9.12 with mocked Jev, parse LM and env (P2-08)."""


import pytest
from conftest import FakeEnv, FakeJev, FakeLLM, assistant
from scar_schemas.models import FromParamsResult, JudgeParseRequest, TaskFamily, TemplateSpec

from scar_agent import judge as judge_mod
from scar_agent.clients import Endpoint
from scar_agent.jev import ChoiceResult
from scar_agent.judge import Judge, JudgeError, _extract_json

TEMPLATES = [
    TemplateSpec(template_id="agg.total_by_account_period", family=TaskFamily.aggregation, answer_type="number",
                 description="Total spend in one account between two dates.", params_schema={"type": "object"},
                 phrasings=["p"] * 5, difficulty=1),
    TemplateSpec(template_id="anom.duplicate_payments", family=TaskFamily.anomaly, answer_type="id_set",
                 description="Find duplicate payments in a period.", params_schema={"type": "object"},
                 phrasings=["p"] * 5, difficulty=2),
]


class JudgeEnv(FakeEnv):
    def __init__(self):
        super().__init__()
        self.from_params_calls = []

    async def get_companies(self, split=None):
        return [{"company_id": "company-004", "name": "Stitchform", "industry": "x", "split": "train"}]

    async def get_templates(self):
        return TEMPLATES

    async def from_params(self, req):
        self.from_params_calls.append(req)
        return FromParamsResult(task=None, interpretation="Total spend in Rent for Stitchform from 2026-01-01 to 2026-03-31",
                                valid=True)


class FakeTrain:
    def __init__(self, gens):
        self.gens = gens

    async def generations(self, arm, seed):
        return self.gens

    async def current(self, arm, seed):
        return {"model_version": "base", "prompt_version": "system_v1"}


def make_judge(store, choice, gens=()):
    return Judge(store, JudgeEnv(), FakeTrain(list(gens)), FakeJev(choice=choice), runner=None)


@pytest.fixture
def parse_lm(monkeypatch):
    llm = FakeLLM([assistant('```json\n{"account_id": "company-004:acct-1", "start_date": "2026-01-01", "end_date": "2026-03-31"}\n```')])
    monkeypatch.setattr(judge_mod, "parse_lm_endpoint", lambda: Endpoint("http://lm", "m", "k"))
    monkeypatch.setattr(judge_mod, "openai_client", lambda ep: llm)
    return llm


async def test_parse_valid(store, parse_lm):
    j = make_judge(store, ChoiceResult("agg.total_by_account_period", 0.92, {}))
    out = await j.parse(JudgeParseRequest(company_id="company-004", question="How much rent in Q1 2026?"))
    assert out.valid and out.template_id == "agg.total_by_account_period"
    assert out.params["account_id"] == "company-004:acct-1"
    assert out.interpretation.startswith("Total spend in Rent")
    _, _, criteria = j.jev.choose_calls[0]
    assert criteria["other"] == "The question does not match any template."
    assert criteria["anom.duplicate_payments"] == "Find duplicate payments in a period."
    assert j.env.from_params_calls[0].dry_run is True
    assert "company-004:acct-1" in parse_lm.requests[0]["messages"][0]["content"]  # real IDs given to the LM


@pytest.mark.parametrize("choice", [ChoiceResult("other", 0.9, {}), ChoiceResult("agg.total_by_account_period", 0.3, {})])
async def test_parse_unmatched_is_friendly(store, choice):
    out = await make_judge(store, choice).parse(JudgeParseRequest(company_id="company-004", question="What's the weather?"))
    assert not out.valid and out.template_id is None
    assert out.error.count("\n- ") == 3


async def test_parse_rejects_non_train_company(store):
    with pytest.raises(JudgeError):
        await make_judge(store, None).parse(JudgeParseRequest(company_id="company-001", question="q"))


@pytest.mark.parametrize("gens,expected", [
    ([], 1),
    ([{"generation": 1, "status": "promoted"}, {"generation": 2, "status": "training"}], 2),
    ([{"generation": 1, "status": "promoted"}, {"generation": 2, "status": "rejected"}], 3),
])
async def test_next_generation(store, gens, expected):
    assert await make_judge(store, None, gens).next_generation() == expected


def test_extract_json_variants():
    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json('Sure!\n```json\n{"a": 2}\n```') == {"a": 2}
    with pytest.raises(ValueError):
        _extract_json("no json here")
