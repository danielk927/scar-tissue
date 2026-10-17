# Jev (TypeSafe System One): verified usage notes

Owner: Person 2. Verified 2026-10-17 against the installed SDK source and official docs. **No real API call has been made yet** (no key at time of writing).

Citation keys used below:

- **(src: `<file>`)**: installed `typesafe-sdk==0.7.5`, path relative to `site-packages/`.
- **(offline)**: behaviour observed by running the installed SDK v0.7.5 against an `httpx2.MockTransport` (no network).
- **(docs: `<url>`)**: official docs page, fetched 2026-10-17.

URLs:
[primitives]: https://docs.typesafe.ai/primitives ·
[models]: https://docs.typesafe.ai/models ·
[databricks]: https://docs.databricks.com/aws/en/machine-learning/model-serving/query-systemone-models ·
[tev1]: https://ollama.com/library/tev1

## Package & version

- PyPI name `typesafe-sdk` (`uv pip install typesafe_sdk` resolves to it); import name `typesafe_sdk`. Installed **0.7.5**, Python >=3.10 (src: `typesafe_sdk-0.7.5.dist-info/METADATA`).
- Dependencies: `httpx2`, `pydantic>=2.12`, `tenacity`, `anyio`, `truststore`. Optional extras: `http2`, `pil`, `zstd` (src: METADATA).
- **Pin `typesafe-sdk==0.7.5`** in our requirements. Everything below describes this version.
- Public exports include `TypeSafeClient`, `AsyncTypeSafeClient`, `Choice`, `Noul`, `Score`, `Box`, `NoulCriteria`, `RetryPolicy`, `SystemOneResponse`, `NoulAnswer`, `ChoiceAnswer`, `ScoreAnswer`, `Usage`, and the `TypeSafe*Error` classes (src: `typesafe_sdk/__init__.py`).

## Client construction and model pinning (spec F10)

```python
TypeSafeClient(*, api_key=None, model=None, retry: RetryPolicy | None = None,
               timeout: float | httpx2.Timeout | None = None, headers=None,
               transport=None, http_client=None, base_url=None,
               compression: Literal["zstd", False] | None = None)
```

`AsyncTypeSafeClient` takes the same arguments, with `httpx2.AsyncBaseTransport` and `httpx2.AsyncClient` in place of the sync types. All arguments are keyword-only (src: `typesafe_sdk/_core/client/sync/client.py`, `.../aio/client.py`).

Resolution order is explicit argument, then env var, then default. Empty or whitespace-only env values are ignored (src: `typesafe_sdk/_core/config.py`, `typesafe_sdk/constants.py`).

| Setting | Constructor arg | Env var | Default |
|---|---|---|---|
| API key | `api_key` | `TYPESAFE_API_KEY` | none: raises `TypeSafeError` at construction |
| Base URL | `base_url` | `TYPESAFE_BASE_URL` | `https://api.typesafe.ai` (trailing `/` stripped) |
| Model | `model` | `TYPESAFE_DEFAULT_MODEL` | `jev-latest` |
| Timeout | `timeout` | none | `10.0` s **per HTTP operation** |
| Log level | none | `TYPESAFE_LOG_LEVEL` | none |

- Auth is sent as `Authorization: Bearer <key>` (src: `typesafe_sdk/_core/transport.py`).
- **Pinning `jev-1.14.0` works three ways**, all confirmed (offline):
  1. env `TYPESAFE_DEFAULT_MODEL=jev-1.14.0`: **the SDK honors it**, which answers the F10 VERIFY;
  2. `TypeSafeClient(model="jev-1.14.0")`;
  3. per call: `client.system_one(..., model="jev-1.14.0")`, which overrides the client default.

  We use the env var (spec `.env`) **and** pass `model=` explicitly in code, so a missing env var can't silently fall back to `jev-latest`.
- **Aliases** (docs: [models]): `jev-latest` → **`jev-1.13.0`** ("most recent stable, official release"); `jev-preview` → `jev-1.14.0` ("most recent release, official or not"). "Jev 1.14 is the newest Jev release, available from October 14, 2026." So **`jev-latest` is NOT 1.14 today**. Pinning is required to get 1.14.
- `response.model` reports the model that actually answered, which may differ from the alias sent (src: `typesafe_sdk/_schemas/models.py`). Store it as `jev_model`.
- `client.models.list()` returns `ListModelsResponse.models` (`name`, `description`, `release_date`) from `GET /v1/models` (src: `typesafe_sdk/_core/client/sync/client.py`, `_core/response_types.py`).

## Question types (spec 9.4 VERIFY)

All three are pydantic models with `extra="forbid"`; unknown fields raise `pydantic.ValidationError` (src: `typesafe_sdk/_core/question_types.py`; offline).

```python
Choice(criteria: Mapping[str, Content | None], instructions: Content | None = None)
Noul(instructions: Content | None = None, criteria: NoulCriteria | None = None)
Score(criteria: Sequence[Content], instructions: Content | None = None)   # min_length=1
```

- `Content` = `str`, a JSON-like mapping or sequence, or an `Asset` (src: `typesafe_sdk/_core/content_types.py`).
- **Choice**: `criteria` is a dict of option label → description (or `None` for a label-only option). It must be non-empty, or `TypeSafeError` is raised before sending. `instructions` is optional (src: `_core/question_types.py`, `_core/questions.py`). Option count: 1–255 (docs: [databricks]); the SDK enforces no upper bound.
- **Noul**: `criteria` is a **plain dict with keys `"true"` and/or `"false"`** (`NoulCriteria` is a closed TypedDict; the wire model rejects other keys). Either `instructions` or at least one described outcome is required (src: `_core/question_types.py`, `_core/questions.py`). `Noul(instructions=..., criteria={"yes": ...})` raises `ValidationError` (offline). The primitives page only says criteria clarify "what yes and no mean" and gives no Python example with criteria (docs: [primitives]); the `true`/`false` keys come from the source and [databricks].
- **Score**: `criteria` is an ordered list; position = score starting at 0 (src: `_schemas/models.py`). 1–10 levels (docs: [databricks]).
- Raw dicts also work, e.g. `{"type": "noul", "instructions": ..., "criteria": {...}}` (src: client docstring).
- **Spec 9.4's `TRIAGE_QUESTIONS` block is correct as written.** It serialises to the verified REST shape (offline).

## Response shape

`system_one(...)` returns `SystemOneResponse` (src: `_core/response_types.py`, `_schemas/models.py`):

| Attribute | Type |
|---|---|
| `response.model` | `str` |
| `response.usage` | `Usage(input_tokens: int \| None, output_tokens: int \| None)` |
| `response.answers` | `dict[str, NoulAnswer \| ChoiceAnswer \| ScoreAnswer \| BoxAnswer]` keyed by our question IDs |
| `response.nouls` / `.choices` / `.scores` / `.boxes` | the same answers filtered by type, with precise types (prefer these for type-checking) |

| Answer | Fields |
|---|---|
| `NoulAnswer` | `type="noul"`, `noul: float` (P(yes), 0–1) |
| `ChoiceAnswer` | `type="choice"`, `choice: str`, `confidence: float`, `probabilities: dict[str, float]` (sum ≈ 1) |
| `ScoreAnswer` | `type="score"`, `score: float` (probability-weighted, may be fractional), `confidence: float`, `legend: dict[int, ...]`, `probabilities: dict[int, float]` |

- Answer objects are frozen and strict. Unknown future answer types are dropped with a log warning (src: `_core/response_types.py`).
- `response.answers["learnable"].noul` (spec F9 form) works at runtime. `response.nouls["learnable"].noul` is the typed equivalent (offline).
- Pricing: input tokens only, $0.042 per Mtok; output tokens are free (docs: [models]).
- Context limits: 64k tokens per request (state + all questions) and 32k per question (state + one question). The `TypeSafe-Danger-Zone-Long-Context: true` header raises both to 262k (docs: [models]). Our ~8k-token state fits comfortably.

## Async + retries + errors

```python
await async_client.system_one(state, questions, *, model=None, assets=None, retry=None,
                              timeout=None, extra_headers=None, extra_body=None, response_model=None)
```

The sync method has the same signature. `retry` and `timeout` here override the client values for that call only. Close the client with `with` (sync), or `async with` / `await client.aclose()` (async) (src: client files).

`RetryPolicy` is a frozen dataclass; defaults below (src: `typesafe_sdk/_core/retry.py`):

| Field | Default |
|---|---|
| `max_retries` | `2` (retries after the first attempt; `0` disables) |
| `backoff_initial` / `backoff_max` | `0.5` / `5.0` s (doubles each attempt) |
| `backoff_jitter` | `0.25` |
| `http_statuses` | `{408, 429, 500..599}` |
| `respect_retry_after` | `True` (`retry-after-ms`, then `retry-after`) |
| `api_connection_error` / `api_timeout_error` | `True` / `True` |
| `exceptions` | `set()` |
| `predicate` | `None` |
| `timeout` | `30.0` s: **total retry budget per call**, including delays. Stops before a retry whose delay would reach the budget. |

- Spec 7.4 says "Jev call 30 s with up to 3 retries". Our interpretation (not from the docs): `TypeSafeClient(timeout=10.0, retry=RetryPolicy(max_retries=3, timeout=30.0))`. A per-operation `timeout=30.0` would use up the whole budget on the first attempt, leaving no time for retries.

Exceptions (src: `typesafe_sdk/_core/errors.py`). All derive from `TypeSafeError`:

| Class | When |
|---|---|
| `TypeSafeError` | client-side problems: missing or invalid key, empty questions, bad timeout |
| `TypeSafeAPIError` | any non-2xx; has `.status`, `.body`, `.headers`, `.endpoint`, `.request_id` |
| `TypeSafeBadRequestError` | 400 |
| `TypeSafeAuthenticationError` | 401 |
| `TypeSafePermissionDeniedError` | 403 |
| `TypeSafeNotFoundError` | 404 |
| `TypeSafeUnprocessableEntityError` | 422 |
| `TypeSafeRateLimitError` | 429; adds `.retry_after_ms` |
| `TypeSafeInternalServerError` | 5xx |
| `TypeSafeAPIConnectionError` (also a `ConnectionError`) | no HTTP response |
| `TypeSafeAPITimeoutError` (subclass of the connection error and `TimeoutError`) | timeout |
| `TypeSafeAPIResponseValidationError` | 2xx with a malformed body; has `.field_path` |

These are raised after retries are used up (src: client docstrings; offline 429 test).

## Rate limits

- "Rate limits are set per organization", measured in tokens/s and requests/s. A request over either limit returns `429 Too Many Requests`; the SDKs retry by default and follow `retry-after`. Higher limits are available on custom and enterprise plans (docs: [models]).
- **Our org's actual numbers: UNKNOWN.** They are shown only in the developer console. Check once we have a key, and update spec F11.

## Tev1 fallback

From the docs ([tev1]) unless noted:

- `ollama pull tev1:4b` (= `tev1:latest`) or `ollama pull tev1:0.8b`. Needs Ollama >= 0.35. It is served at `http://localhost:11434/v1/systemone` ("follows TypeSafe's Jev API").
- Env for the SDK: `TYPESAFE_BASE_URL=http://localhost:11434`, `TYPESAFE_API_KEY=ollama` (any value; Ollama ignores it), `TYPESAFE_DEFAULT_MODEL=tev1` (or `tev1:0.8b`). Their example uses `TypeSafeClient(timeout=120)`.
- The SDK appends `/v1/systemone` to the base URL, giving `http://localhost:11434/v1/systemone` (offline). **If our code passes `model="jev-1.14.0"` explicitly, it overrides the env var**, so the fallback path must also switch the explicit model to `tev1`.
- **Limits that matter for us:** "runs with a context of about 2,000 tokens". Our triage state is about 8k tokens, so it **must be cut much harder for Tev1**. Choice/score accept 2–26 options (best with 2–24); our 8 causes are fine. Accuracy is lower: 73.3% (4B) vs 76.0% for Jev 1.13 on Bespoke Labs' sets. Calibration is not fully tested.
- The SDK zstd-compresses bodies of 1 KB or more when zstd is available (Python 3.14+, or the `zstd` extra) (src: `_core/compression.py`, `_core/transport.py`). Ollama's support for `Content-Encoding: zstd` is undocumented. Pass `compression=False` for the fallback as a precaution (our suggestion).

## Ready-to-use snippet

```python
import os
from typesafe_sdk import Choice, Noul, RetryPolicy, TypeSafeClient, TypeSafeError

JEV_MODEL = os.environ.get("TYPESAFE_DEFAULT_MODEL", "jev-1.14.0")  # pin; jev-latest is 1.13.0

TRIAGE_QUESTIONS = {
    "cause": Choice(
        instructions="Why did this accounting agent's run fail? Judge from `steps`, `final_answer`, `stop_reason` and `verifier_detail`.",
        criteria={
            "wrong_tool": "...",
            "bad_tool_args": "...",
            "reasoning_error": "...",
            "wrong_final_format": "...",
            "gave_up": "...",
            "environment_error": "...",
            "ambiguous_task": "...",
            "other": "...",
        },  # full descriptions: SPEC.md section 9.4
    ),
    "learnable": Noul(
        instructions="A better model using the same tools and the same information could have answered this task correctly.",
        criteria={
            "true": "...",
            "false": "...",
        },
    ),
}

client = TypeSafeClient(  # reads TYPESAFE_API_KEY (and TYPESAFE_BASE_URL for Tev1)
    model=JEV_MODEL,
    timeout=10.0,
    retry=RetryPolicy(max_retries=3, timeout=30.0),
)

def triage(state: dict) -> tuple[str, float, float, dict[str, float], str]:
    r = client.system_one(state=state, questions=TRIAGE_QUESTIONS, model=JEV_MODEL)
    cause = r.choices["cause"]
    learnable_p = r.nouls["learnable"].noul
    return cause.choice, cause.confidence, learnable_p, cause.probabilities, r.model
    # TypeSafeError subclasses (rate limit, auth, timeout, ...) propagate -> service returns 503
```

## Spot check (P2-07)

Not done yet.
