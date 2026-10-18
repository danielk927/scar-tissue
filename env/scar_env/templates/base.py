"""Shared pieces of the template catalog: the Template base class, company context, param types, periods, formatting.

Every template answers with SQL over ledger.* scoped to one company (R5). Docs with the exact SQL: docs/TEMPLATES.md.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from string import Formatter
from typing import Annotated, Any, ClassVar

import psycopg
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError
from scar_schemas.models import AnswerType, ExpectedAnswer, TaskFamily, TemplateSpec

MAX_IDS = 50  # id_set answers with more IDs are degenerate (SPEC 9.5)
CENT = Decimal("0.01")
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
# Aggregation period length by difficulty (days); difficulty 1 also stays inside one calendar month.
TIER_DAYS = {1: (7, 31), 2: (32, 92), 3: (93, 366)}
# Anomaly periods are 7..M days with M drawn from here, so dense companies still get short windows.
ANOMALY_MAX_DAYS = (14, 31, 92, 183, 366)

FORBID_EXTRA = ConfigDict(extra="forbid")
DateStr = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$", json_schema_extra={"format": "date"})]
MonthStr = Annotated[str, Field(pattern=r"^\d{4}-\d{2}$")]
Money = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2), AfterValidator(lambda d: d.quantize(CENT))]

ACCOUNT_DESC = "Account ID (account_id from get_accounts)."
VENDOR_DESC = "Vendor ID (vendor_id from get_vendors)."
START_DESC = "First day of the period, YYYY-MM-DD, inclusive."
END_DESC = "Last day of the period, YYYY-MM-DD, inclusive."


class Period(BaseModel):
    model_config = FORBID_EXTRA
    start_date: DateStr = Field(description=START_DESC)
    end_date: DateStr = Field(description=END_DESC)


class VendorPeriod(BaseModel):
    model_config = FORBID_EXTRA
    vendor_id: str = Field(description=VENDOR_DESC)
    start_date: DateStr = Field(description=START_DESC)
    end_date: DateStr = Field(description=END_DESC)


class ParamError(ValueError):
    """Unknown template or company, or invalid params. The message is meant for the caller (FromParamsResult.error)."""


@dataclass(frozen=True)
class Company:
    """What templates need to know about one company. `first`/`last` bound its transactions (None if it has none)."""

    company_id: str
    name: str
    split: str
    first: date | None
    last: date | None
    accounts: dict[str, tuple[str, str]]  # account_id -> (name, account_type), ordered by account_id
    vendors: dict[str, str]  # vendor_id -> name, ordered by vendor_id

    def accounts_of_type(self, account_type: str) -> list[str]:
        return [a for a, (_, t) in self.accounts.items() if t == account_type]

    def account_name(self, account_id: str) -> str:
        return self.accounts[account_id][0]


def load_company(conn: psycopg.Connection, company_id: str) -> Company | None:
    row = conn.execute(
        "SELECT c.name, c.split, MIN(t.txn_date), MAX(t.txn_date) FROM ledger.companies c"
        " LEFT JOIN ledger.transactions t ON t.company_id = c.company_id"
        " WHERE c.company_id = %s GROUP BY c.name, c.split",
        (company_id,),
    ).fetchone()
    if row is None:
        return None
    accounts = conn.execute(
        "SELECT account_id, name, account_type FROM ledger.accounts WHERE company_id = %s ORDER BY account_id",
        (company_id,),
    ).fetchall()
    vendors = conn.execute(
        "SELECT vendor_id, name FROM ledger.vendors WHERE company_id = %s ORDER BY vendor_id", (company_id,)
    ).fetchall()
    return Company(
        company_id=company_id,
        name=row[0],
        split=row[1],
        first=row[2],
        last=row[3],
        accounts={a: (n, t) for a, n, t in accounts},
        vendors=dict(vendors),
    )


@dataclass(frozen=True)
class Answer:
    expected: ExpectedAnswer
    degenerate: bool = False  # True: the sampler must reject these params (SPEC 9.5)


def id_set_answer(ids: list[str]) -> Answer:
    return Answer(ExpectedAnswer(answer_type=AnswerType.id_set, id_set=ids), degenerate=not 1 <= len(ids) <= MAX_IDS)


class Template:
    """One task template. Subclasses set the class attributes and implement the five methods below.

    `difficulty` is the base (TemplateSpec.difficulty); `difficulties` lists every difficulty `sample` can honor.
    Params are plain JSON values; `validate` returns them normalized (money as strings with 2 decimals).
    """

    template_id: ClassVar[str]
    family: ClassVar[TaskFamily]
    answer_type: ClassVar[AnswerType]
    description: ClassVar[str]
    difficulty: ClassVar[int]
    difficulties: ClassVar[tuple[int, ...]]
    phrasings: ClassVar[tuple[str, ...]]
    Params: ClassVar[type[BaseModel]]
    sql: ClassVar[tuple[str, ...]] = ()  # answer SQL, quoted verbatim in docs/TEMPLATES.md

    @property
    def spec(self) -> TemplateSpec:
        return TemplateSpec(
            template_id=self.template_id,
            family=self.family,
            answer_type=self.answer_type,
            description=self.description,
            params_schema=self.Params.model_json_schema(),
            phrasings=list(self.phrasings),
            difficulty=self.difficulty,
        )

    def validate(self, conn: psycopg.Connection, company: Company, params: Any) -> dict[str, Any]:
        """Schema check, then `check` against the company's ledger. Raises ParamError."""
        try:
            model = self.Params.model_validate(params)
        except ValidationError as exc:
            raise ParamError(
                "; ".join(f"{'.'.join(map(str, e['loc'])) or 'params'}: {e['msg']}" for e in exc.errors())
            ) from None
        clean = model.model_dump(mode="json")
        self.check(conn, company, clean)
        return clean

    def render(self, company: Company, params: dict[str, Any], phrasing: str) -> str:
        return phrasing.format(**self.placeholders(company, params))

    def sample(
        self, conn: psycopg.Connection, company: Company, rng: random.Random, difficulty: int
    ) -> dict[str, Any] | None:
        """Random params for this company at this difficulty, or None if the company cannot support it."""
        raise NotImplementedError

    def check(self, conn: psycopg.Connection, company: Company, p: dict[str, Any]) -> None:
        """Semantic checks beyond the JSON Schema (IDs belong to the company, dates). Raises ParamError."""
        raise NotImplementedError

    def answer(self, conn: psycopg.Connection, company: Company, p: dict[str, Any]) -> Answer:
        """The expected answer for validated params. Raises ParamError if it is undefined."""
        raise NotImplementedError

    def placeholders(self, company: Company, p: dict[str, Any]) -> dict[str, str]:
        """Values for the phrasings' {placeholders}: names, dates and amounts, never raw account or vendor IDs."""
        raise NotImplementedError

    def interpretation(self, company: Company, p: dict[str, Any]) -> str:
        """Canonical one-sentence restatement; never contains the answer."""
        raise NotImplementedError


def placeholder_names(phrasing: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(phrasing) if name}


# ---------- checks ----------


def check_account(company: Company, p: dict[str, Any], field: str, account_type: str | None = None) -> None:
    account_id = p[field]
    if account_id not in company.accounts:
        raise ParamError(f"{field}: {account_id} is not an account of {company.company_id}")
    name, actual = company.accounts[account_id]
    if account_type and actual != account_type:
        raise ParamError(f"{field}: {name} ({account_id}) has account_type {actual}, expected {account_type}")


def check_vendor(company: Company, p: dict[str, Any], field: str = "vendor_id") -> None:
    if p[field] not in company.vendors:
        raise ParamError(f"{field}: {p[field]} is not a vendor of {company.company_id}")


def parse_date(p: dict[str, Any], field: str) -> date:
    try:
        return date.fromisoformat(p[field])
    except ValueError:
        raise ParamError(f"{field}: {p[field]} is not a valid date") from None


def parse_month(p: dict[str, Any], field: str) -> date:
    """First day of a YYYY-MM month."""
    try:
        return date.fromisoformat(p[field] + "-01")
    except ValueError:
        raise ParamError(f"{field}: {p[field]} is not a valid month") from None


def require_data(company: Company) -> tuple[date, date]:
    if company.first is None or company.last is None:
        raise ParamError(f"{company.company_id} has no transactions")
    return company.first, company.last


def check_period(company: Company, p: dict[str, Any]) -> None:
    """start_date <= end_date, and the period overlaps the company's data range."""
    start, end = parse_date(p, "start_date"), parse_date(p, "end_date")
    if start > end:
        raise ParamError(f"start_date {start} is after end_date {end}")
    first, last = require_data(company)
    if start > last or end < first:
        raise ParamError(f"{start} to {end} is outside {company.company_id}'s data ({first} to {last})")


# ---------- dates and periods ----------


def month_end(d: date) -> date:
    return next_month(d.replace(day=1)) - timedelta(days=1)


def next_month(first_day: date) -> date:
    return date(first_day.year + first_day.month // 12, first_day.month % 12 + 1, 1)


def prev_month(first_day: date) -> date:
    return (first_day - timedelta(days=1)).replace(day=1)


def month_label(first_day: date) -> str:
    return f"{MONTH_NAMES[first_day.month - 1]} {first_day.year}"


def full_months(company: Company) -> list[date]:
    """First days of the months that lie entirely inside the company's data range."""
    if company.first is None or company.last is None:
        return []
    m = company.first.replace(day=1)
    if m < company.first:
        m = next_month(m)
    out = []
    while month_end(m) <= company.last:
        out.append(m)
        m = next_month(m)
    return out


def month_ends(company: Company) -> list[date]:
    """Last days of months that fall inside the company's data range."""
    if company.first is None or company.last is None:
        return []
    out, m = [], company.first.replace(day=1)
    while month_end(m) <= company.last:
        out.append(month_end(m))
        m = next_month(m)
    return out


def window(
    rng: random.Random, lo: date, hi: date, a0: date, a1: date, min_days: int, max_days: int
) -> tuple[date, date] | None:
    """A random period of min_days..max_days days with lo <= start <= a0 <= a1 <= end <= hi, or None."""
    shortest = max(min_days, (a1 - a0).days + 1)
    longest = min(max_days, (hi - lo).days + 1)
    if shortest > longest:
        return None
    length = rng.randint(shortest, longest)
    earliest = max(lo, a1 - timedelta(days=length - 1))
    latest = min(a0, hi - timedelta(days=length - 1))
    start = earliest + timedelta(days=rng.randint(0, (latest - earliest).days))
    return start, start + timedelta(days=length - 1)


def tier_period(rng: random.Random, company: Company, anchor: date, difficulty: int) -> tuple[date, date] | None:
    """A period containing `anchor` inside the data range, sized by difficulty (TIER_DAYS)."""
    lo, hi = company.first, company.last
    min_days, max_days = TIER_DAYS[difficulty]
    if difficulty == 1:
        lo, hi = max(lo, anchor.replace(day=1)), min(hi, month_end(anchor))
        min_days = min(min_days, (hi - lo).days + 1)
    return window(rng, lo, hi, anchor, anchor, min_days, max_days)


def anomaly_period(rng: random.Random, company: Company, a0: date, a1: date) -> tuple[date, date] | None:
    """A period containing [a0, a1] inside the data range, 7..M days long with M from ANOMALY_MAX_DAYS."""
    return window(rng, company.first, company.last, a0, a1, 7, rng.choice(ANOMALY_MAX_DAYS))


def period_params(period: tuple[date, date] | None) -> dict[str, str] | None:
    return period and {"start_date": period[0].isoformat(), "end_date": period[1].isoformat()}


# ---------- formatting and SQL helpers ----------


def money(x: str | Decimal) -> str:
    return f"${Decimal(x):,.2f}"


def column(conn: psycopg.Connection, sql: str, args: tuple | dict) -> list:
    return [row[0] for row in conn.execute(sql, args)]


def sql_args(company: Company, p: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"company_id": company.company_id, **p, **extra}
