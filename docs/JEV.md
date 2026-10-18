# Jev (TypeSafe System One): verified usage notes

Owner: Person 2. Verified 2026-10-17 against the installed SDK source and official docs; live usage measured 2026-10-18 (section "Live usage").

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

## Live usage (2026-10-18)

- **Model reported by the API:** `jev-1.14.0` on every call (`response.model`), matching the pin.
- **Latency:** 70–160 ms per triage call, sequential (3 calls on real failed runs); `/failures/mine` triaged 18 failures in 0.6 s at `JEV_CONCURRENCY = 8`, and 9 more in 1.2 s on a re-mine.
- **Real mine (local stack, arm D, seed 7, generation 1, 40 traffic runs on `base`):** 27 failures triaged, 11 included. Causes: `gave_up` (no_submit and model_error runs), `reasoning_error`, `wrong_final_format`, `wrong_tool`, one `environment_error`. Excluded by rule: 5 `stop_reason_model_error`, 2 `stop_reason_timeout`, 1 `cause_environment_error`, 8 `learnable_p_below_threshold`. A second mine after 10 more runs triaged only the 9 new failures (9 Jev calls) and kept the 18 earlier records unchanged. These records are `fixtures/failures_sample.jsonl`.
- **Our org's rate limits:** not read yet; they are only shown at https://console.typesafe.ai (needs Person 2's login). No 429 seen at 8 concurrent calls.

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

Twenty triaged failures from `fixtures/failures_sample.jsonl`, picked with `random.Random(2026).sample(..., 20)` and sorted by run_id. All are real `base` traffic runs (arm D, seed 7, generation 1) triaged by `jev-1.14.0` on 2026-10-18. For each, write your own cause (one of: wrong_tool, bad_tool_args, reasoning_error, wrong_final_format, gave_up, environment_error, ambiguous_task, other) and whether a better model could have answered (yes/no), without looking at Jev's answer first if you can. The full transcript of any run is in the fixture, or at `/runs/<run_id>` in the web app.

### 1. `run_0953d05c61d7478189c9a25c50ace73f`

- **Question** (bookkeeping): Starting in March 2026, move $8,154.97 from Prepaid Expenses into Operating Expense in 4 equal monthly entries on each month-end (rounded to cents; the final entry absorbs rounding).
- **Steps:** get_accounts → get_vendors → get_transactions
- **Stop reason:** `model_error` · **final answer:** `null`
- **Verifier:** not submitted: model_error
- **Jev:** cause `gave_up` (0.82), learnable_p 0.72, included: no, stop_reason_model_error
- **Human label:** cause = ______ · learnable = ______

### 2. `run_12d7ef5cdc1640809e2b3fd1694813d5`

- **Question** (anomaly): Find possible duplicate payments from 2025-12-22 to 2026-01-19: for every pair of transactions with the same vendor and the same amount dated at most 3 days apart (both inside the period), list the later one. For same-day pairs, the later one is the one with the larger transaction ID.
- **Steps:** get_vendors → get_transactions → submit_answer
- **Stop reason:** `submitted` · **final answer:** `{"ids": ["company-073:planted-dup-0008:1"]}`
- **Verifier:** ids: expected 2, got 1 distinct; missing ['company-073:planted-dup-0001:1']; extra []
- **Jev:** cause `reasoning_error` (0.89), learnable_p 0.82, included: yes
- **Human label:** cause = ______ · learnable = ______

### 3. `run_23eb9a50a29b482fba75ad9ef047ca6b`

- **Question** (bookkeeping): We prepaid $23,689.03, recorded in Prepaid Expenses. Amortize it into Rent and Lease over 6 months starting October 2025: one entry on the last day of each month, each month's amount rounded to cents, with the final month absorbing any rounding difference.
- **Steps:** get_accounts → create_journal_entry → create_journal_entry → create_journal_entry → create_journal_entry → create_journal_entry → create_journal_entry
- **Stop reason:** `timeout` · **final answer:** `null`
- **Verifier:** not submitted: timeout
- **Jev:** cause `environment_error` (0.37), learnable_p 0.68, included: no, cause_environment_error
- **Human label:** cause = ______ · learnable = ______

### 4. `run_2a9df404d7724f88836438ce4fded1df`

- **Question** (anomaly): From 2026-04-04 to 2026-05-31 inclusive, flag all transactions of $250.00 or more whose amounts are exact multiples of $100.
- **Steps:** get_transactions → get_transactions → sql_query
- **Stop reason:** `model_error` · **final answer:** `null`
- **Verifier:** not submitted: model_error
- **Jev:** cause `gave_up` (0.94), learnable_p 0.87, included: no, stop_reason_model_error
- **Human label:** cause = ______ · learnable = ______

### 5. `run_4448aa9394ee47949141b424dc933ab0`

- **Question** (aggregation): From 2026-10-01 to 2026-10-05 inclusive, how many Income Tax Expense transactions are there?
- **Steps:** get_accounts → get_transactions → text reply → text reply
- **Stop reason:** `no_submit` · **final answer:** `null`
- **Verifier:** not submitted: no_submit
- **Jev:** cause `gave_up` (0.99), learnable_p 0.89, included: yes
- **Human label:** cause = ______ · learnable = ______

### 6. `run_5d01c00cfee34b5297b098a023632592`

- **Question** (aggregation): What is the total amount recorded in the Rent and Lease account from 2026-09-02 to 2026-09-29, inclusive?
- **Steps:** get_accounts → get_transactions → text reply → text reply
- **Stop reason:** `no_submit` · **final answer:** `null`
- **Verifier:** not submitted: no_submit
- **Jev:** cause `gave_up` (1.00), learnable_p 0.92, included: yes
- **Human label:** cause = ______ · learnable = ______

### 7. `run_61bac3bf022a4e6f99207046f25279a3`

- **Question** (bookkeeping): Record a reclassification entry, dated the same day as transaction company-055:bill-utilities-00309:1, that moves its full amount to Equipment Rental.
- **Steps:** get_transactions → text reply → submit_answer
- **Stop reason:** `submitted` · **final answer:** `{"number": 0}`
- **Verifier:** entry_ids: missing from final_answer
- **Jev:** cause `wrong_final_format` (0.26), learnable_p 0.46, included: no, learnable_p_below_threshold
- **Human label:** cause = ______ · learnable = ______

### 8. `run_6372fd4d42b64c07a2639c03db63cd3f`

- **Question** (aggregation): From 2025-12-01 to 2025-12-18 inclusive, what was our total spend with the vendor Eastgate Power & Light?
- **Steps:** get_vendors → get_transactions → text reply → text reply
- **Stop reason:** `no_submit` · **final answer:** `null`
- **Verifier:** not submitted: no_submit
- **Jev:** cause `gave_up` (1.00), learnable_p 0.92, included: yes
- **Human label:** cause = ______ · learnable = ______

### 9. `run_8426c8473cab4913b1cf1bf00ebb887d`

- **Question** (anomaly): From 2026-03-03 to 2026-04-01 inclusive, which charges from Vale Crossing Fulfillment exceeded 3 times that vendor's overall median transaction amount?
- **Steps:** get_vendors → get_transactions → sql_query → submit_answer
- **Stop reason:** `submitted` · **final answer:** `{"ids": []}`
- **Verifier:** ids: expected 1, got 0 distinct; missing ['company-008:bill-00971:1']; extra []
- **Jev:** cause `reasoning_error` (0.78), learnable_p 0.86, included: yes
- **Human label:** cause = ______ · learnable = ______

### 10. `run_86bcdc6d148844daa775dc39d05451cc`

- **Question** (anomaly): Find possible duplicate payments from 2026-07-11 to 2026-08-07: for every pair of transactions with the same vendor and the same amount dated at most 3 days apart (both inside the period), list the later one. For same-day pairs, the later one is the one with the larger transaction ID.
- **Steps:** get_vendors → get_transactions → get_transactions → sql_query (error) → sql_query (error) → sql_query (error) → get_transactions
- **Stop reason:** `timeout` · **final answer:** `null`
- **Verifier:** not submitted: timeout
- **Jev:** cause `gave_up` (0.61), learnable_p 0.70, included: no, stop_reason_timeout
- **Human label:** cause = ______ · learnable = ______

### 11. `run_a2950aad67eb4cbcbe72bac8466e7ffa`

- **Question** (bookkeeping): Transaction company-018:bill-utilities-00594:1 belongs in Insurance. Post one journal entry on its date to reclassify the whole amount there.
- **Steps:** get_transactions → text reply → get_accounts → get_transactions → text reply
- **Stop reason:** `no_submit` · **final answer:** `null`
- **Verifier:** not submitted: no_submit
- **Jev:** cause `gave_up` (0.97), learnable_p 0.40, included: no, learnable_p_below_threshold
- **Human label:** cause = ______ · learnable = ______

### 12. `run_a79bd51aee6744f2a5b493e103b3fd4e`

- **Question** (aggregation): What is the total amount recorded in the Interest Expense account from 2025-11-27 to 2026-07-19, inclusive?
- **Steps:** get_accounts → get_transactions → get_transactions
- **Stop reason:** `model_error` · **final answer:** `null`
- **Verifier:** not submitted: model_error
- **Jev:** cause `gave_up` (0.93), learnable_p 0.87, included: no, stop_reason_model_error
- **Human label:** cause = ______ · learnable = ______

### 13. `run_ac0b5f0358d44d6a9172502a119237b9`

- **Question** (bookkeeping): Transaction company-070:bill-premises-00066:1 belongs in Repairs and Maintenance. Post one journal entry on its date to reclassify the whole amount there.
- **Steps:** get_transactions → text reply → get_accounts → text reply
- **Stop reason:** `no_submit` · **final answer:** `null`
- **Verifier:** not submitted: no_submit
- **Jev:** cause `gave_up` (0.94), learnable_p 0.42, included: no, learnable_p_below_threshold
- **Human label:** cause = ______ · learnable = ______

### 14. `run_bfb16b1a61dd434a8bf438e7d1c05a26`

- **Question** (bookkeeping): Move transaction company-047:bill-utilities-00826:1 out of its current account and into Rent and Lease. Record one journal entry on the transaction's date for its full amount.
- **Steps:** get_transactions → text reply → get_vendors → text reply
- **Stop reason:** `no_submit` · **final answer:** `null`
- **Verifier:** not submitted: no_submit
- **Jev:** cause `gave_up` (0.90), learnable_p 0.35, included: no, learnable_p_below_threshold
- **Human label:** cause = ______ · learnable = ______

### 15. `run_c0ee180d1858447897b92f282930c29e`

- **Question** (anomaly): List the likely duplicate bills dated 2026-09-15 through 2026-09-23: a transaction counts if an earlier one in the period has the same vendor and amount and is at most 3 days older. Order is by date, then transaction ID.
- **Steps:** get_vendors → get_transactions → sql_query → text reply → submit_answer
- **Stop reason:** `submitted` · **final answer:** `{"ids": ["company-051:pbill-00405:1", "company-051:pbill-00406:1", "company-051:pbill-00408:1", "company-051:pbill-00409:1"]}`
- **Verifier:** ids: expected 7, got 4 distinct; missing ['company-051:pbill-00411:1', 'company-051:pbill-00412:1', 'company-051:pbill-00417:1']; extra []
- **Jev:** cause `reasoning_error` (0.51), learnable_p 0.84, included: yes
- **Human label:** cause = ______ · learnable = ______

### 16. `run_ccacb9708c144c66b8911bbdabb44765`

- **Question** (anomaly): Which transactions dated 2026-06-30 through 2026-07-12 are round amounts (exact multiples of $100) of $250.00 or more?
- **Steps:** get_transactions → submit_answer
- **Stop reason:** `submitted` · **final answer:** `{"ids": ["company-060:bill-insurance-00187:1", "company-060:bill-software-00186:1", "company-060:je-wageacc-00189:1"]}`
- **Verifier:** ids: expected 2, got 3 distinct; missing []; extra ['company-060:bill-software-00186:1']
- **Jev:** cause `reasoning_error` (0.84), learnable_p 0.63, included: yes
- **Human label:** cause = ______ · learnable = ______

### 17. `run_d4cbe77d267c4bc58acde822a57fbc0b`

- **Question** (aggregation): From 2025-12-01 to 2025-12-18 inclusive, what was our total spend with the vendor Eastgate Power & Light?
- **Steps:** get_vendors → get_transactions → text reply → text reply
- **Stop reason:** `no_submit` · **final answer:** `null`
- **Verifier:** not submitted: no_submit
- **Jev:** cause `gave_up` (1.00), learnable_p 0.93, included: yes
- **Human label:** cause = ______ · learnable = ______

### 18. `run_dd652a0187844815a2cdb923ab25bbe4`

- **Question** (anomaly): Check 2026-04-15 to 2026-04-27 for duplicate payments. Return the later transaction of every same-vendor, same-amount pair that is 0 to 3 days apart with both transactions in the period (ties on date go by transaction ID).
- **Steps:** get_vendors → get_transactions → sql_query → text reply → submit_answer
- **Stop reason:** `submitted` · **final answer:** `{"ids": ["company-008:bill-utilities-01536:1", "company-008:bill-utilities-01616:1", "company-008:bill-operating-01621:1", "company-008:bill-variable-services-01618:1", "company-008:bill-variable-services-01538:1", "company-008:bill-utilities-01537:1", "company-008:bill-utilities-01617:1"]}`
- **Verifier:** ids: expected 1, got 7 distinct; missing ['company-008:planted-dup-0004:1']; extra ['company-008:bill-operating-01621:1', 'company-008:bill-utilities-01536:1', 'company-008:bill-utilities-01537:1', 'company-008:bill-util…
- **Jev:** cause `reasoning_error` (0.54), learnable_p 0.92, included: yes
- **Human label:** cause = ______ · learnable = ______

### 19. `run_dfa18d53918940eda619fe6ff38071e3`

- **Question** (bookkeeping): Move transaction company-041:pbill-00150:1 out of its current account and into Legal and Professional Fees. Record one journal entry on the transaction's date for its full amount.
- **Steps:** get_accounts → get_transactions → text reply → text reply
- **Stop reason:** `no_submit` · **final answer:** `null`
- **Verifier:** not submitted: no_submit
- **Jev:** cause `gave_up` (0.94), learnable_p 0.51, included: no, learnable_p_below_threshold
- **Human label:** cause = ______ · learnable = ______

### 20. `run_ef086ab899754979b818221bf8825ca9`

- **Question** (bookkeeping): Transaction company-004:bill-01802:1 belongs in Shipping and Delivery. Post one journal entry on its date to reclassify the whole amount there.
- **Steps:** get_transactions → text reply → text reply
- **Stop reason:** `no_submit` · **final answer:** `null`
- **Verifier:** not submitted: no_submit
- **Jev:** cause `gave_up` (0.92), learnable_p 0.40, included: no, learnable_p_below_threshold
- **Human label:** cause = ______ · learnable = ______

**Agreement:** not computed yet (fill in the labels; then cause agreement = matching causes / 20, learnable agreement = matching yes/no (Jev yes = learnable_p ≥ 0.6) / 20).
