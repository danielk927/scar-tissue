"""Template catalog (SPEC 9.5, P1-06). Exact SQL and pinned definitions: docs/TEMPLATES.md.

Internal API (the mutator and judge flow build on it; tasks.py wraps it into Tasks):
- `TEMPLATES`: template_id -> Template, in SPEC 9.5 order. `get_template(template_id)` raises ParamError if unknown.
- `load_company(conn, company_id) -> Company | None`: names, account types and data range the templates need.
- Template attributes: `spec` (TemplateSpec), `difficulty` (base), `difficulties` (what `sample` can honor),
  `phrasings`, `Params` (pydantic model behind `spec.params_schema`), `sql` (answer SQL).
- Template methods, all scoped to one company:
  `sample(conn, company, rng, difficulty) -> params | None` (None: the company cannot support this template),
  `validate(conn, company, params) -> normalized params` (raises ParamError),
  `answer(conn, company, params) -> Answer(expected, degenerate)` (raises ParamError if the answer is undefined),
  `render(company, params, phrasing) -> prompt`, `interpretation(company, params) -> str` (never the answer).
"""

from .aggregation import (
    CountByAccountPeriod,
    MomChangeAccount,
    ShareOfExpense,
    TopVendorBySpend,
    TotalByAccountPeriod,
    TotalByVendorPeriod,
)
from .anomaly import DuplicatePayments, RoundAmountsOver, VendorOutliers
from .base import MAX_IDS, Answer, Company, ParamError, Template, load_company
from .bookkeeping import Accrual, PrepaidAmortization, Reclassify

TEMPLATES: dict[str, Template] = {
    t.template_id: t
    for t in (
        TotalByAccountPeriod(),
        TotalByVendorPeriod(),
        CountByAccountPeriod(),
        TopVendorBySpend(),
        MomChangeAccount(),
        ShareOfExpense(),
        DuplicatePayments(),
        RoundAmountsOver(),
        VendorOutliers(),
        Accrual(),
        PrepaidAmortization(),
        Reclassify(),
    )
}


def get_template(template_id: str) -> Template:
    try:
        return TEMPLATES[template_id]
    except KeyError:
        raise ParamError(f"unknown template_id: {template_id}") from None


__all__ = ["MAX_IDS", "TEMPLATES", "Answer", "Company", "ParamError", "Template", "get_template", "load_company"]
