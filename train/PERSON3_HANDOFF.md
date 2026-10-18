# Person 3 handoff

Prepared 2026-10-18 from the public `main` snapshot at `0c1f06722b6b86721a24f9f2a31cf75056f12bd2`.

## Delivered in this checkout

- `scar_train.campaign`: serial full-size run order for A/B/C/D, the minimum IC-3 sequence, and a next-generation demo command. It saves job IDs before polling, reconnects after interruption, recovers an accepted POST whose response was lost, handles a train-service restart, and bounds retries across driver restarts. Every completed generation's actual registry record and timestamped events are saved.
- `scar_train.fixtures` and `fixtures/training_examples_sample.jsonl`: 17 examples from 45 actual source runs, with failed/eval/dev/other-arm runs excluded, existing deduplication/caps retained, and transcripts preserved verbatim. Validation checks provenance and the real fixture passes the Veri prompt/completion conversion.
- Full-size arm-D requests reject fake trainers/deployers before touching the database or starting a background job. Tiny integration runs retain their existing behavior.
- `scar_train.keep_warm`: follows the promoted D-s0 model, checks its endpoint every two minutes for a bounded period, and leaves deployment settings and agent/eval tables untouched.
- The Person 3 section in `docs/DEMO.md` now explains preparation, warming, live judge-failure generations, recovery, recording and timing. `.env.example` includes the existing backend's previously undocumented Veri deployment/GPU settings.

## Acceptance status

| Work | Status |
| --- | --- |
| P3-01 shared schemas | Already implemented; all 7 schema tests pass; no shared contracts changed. |
| P3-02 infrastructure | Existing Docker/DDL scripts retained. This sandbox cannot connect to Postgres or initialize shared memory, so database integration was not rerun. |
| P3-03/P3-04 Veri research/base | Existing verified notes and deployed-base decision retained. No new cloud deployment created. |
| P3-05 toy path | Missing real-run training fixture added and validated; the team's previously recorded successful live toy test remains in `docs/VERI.md`. No additional billed toy job submitted. |
| P3-06/P3-07/P3-09/P3-10 service, builder, gate, loop | Existing implementations retained; the full-size fake-backend guard and runnable tests pass. Existing Postgres integration tests still require a database. |
| P3-08 training backend | Existing Veri backend's offline API/format/deployment tests pass, including conversion of the new real fixture. No new full-size trained candidate claimed. |
| P3-11 full-size experiments | Resumable campaign driver delivered and exercised with injected HTTP services. Actual experiment execution requires the team's experiment host and credentials. |
| P3-12/P3-13 teacher/GRPO | Optional stretch paths remain cut; no unverified provider integration added. |
| P3-14 demo | Procedure and bounded endpoint warmer delivered. A timed real cycle and screen recording require the live stack. |

## Validation performed

- Train: **62 passed**, **67 Postgres-dependent tests deselected** because this session denies database sockets/shared memory.
- Shared schemas: **7 passed**.
- `ruff check .` and `ruff format --check .` pass across all 40 train Python files.
- CLI `campaign --plan-only` prints the required 29-target default schedule without contacting services.
- CLI `fixtures validate` confirms all 17 shipped examples match the real source runs.
- Live `campaign --preflight` stops at `train GET /health: ConnectError`; it starts no training jobs.

The tests ran under Python 3.14 using existing local packages and the official `veri-sdk==0.2.44`/Postgres-driver wheels, because shell network access also prevents a normal `uv sync` here. The dependency files and lockfiles are unchanged.

## Run on the experiment host

Configure that host's repo-root `.env` with the team keys. The **train service** needs `TRAINER=veri`, `VERI_DEPLOYMENT_ID` and `VERI_API_KEY`; the agent and endpoint checks need `MODEL_API_KEY`. The actual ledger, train pool, all 300 frozen eval tasks, and registered `base` must be present in its database. Start or restart the three backend services there, retaining their database and loop state.

From `train/`:

```bash
uv sync
uv run python -m scar_train.veri wake
uv run python -m scar_train.campaign --preflight
uv run python -m scar_train.campaign --minimum --output-dir ../runs/ic3
uv run python -m scar_train.campaign --output-dir ../runs/overnight
```

The full campaign skips registry generations already completed by the minimum campaign. Rerun the same command/output directory after interruption. There is no automatic lineage reset or deletion.

For judging:

```bash
uv run python -m scar_train.keep_warm --minutes 120
uv run python -m scar_train.campaign --demo --plan-only
uv run python -m scar_train.campaign --demo --max-hours 2
```

Use a separate terminal for the warmer. Preserve the resulting JSON records, events and a real screen recording before the demo freeze. Never report the injected-service test output or tiny/fake runs as experimental results.

## Source checkout limitation

`git clone https://github.com/danielk927/scar-tissue.git` failed because this session's terminal cannot resolve GitHub. The exact public `main` ZIP was downloaded through the browser and extracted to this folder instead. It contains the complete source snapshot but **no `.git` history**. No commits, branches, pushes or remote changes were made.

A companion `person3.patch` next to the `scar-tissue/` folder contains these changes against the original downloaded snapshot. On a normal networked clone, check out the source revision above (or a compatible later revision), then use `git apply --check /path/to/person3.patch` followed by `git apply /path/to/person3.patch`. Review any newer teammate changes before integrating.
