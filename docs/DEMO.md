# Demo

Owner: Person 2 (script and question list). Person 3 adds the demo-generation procedure under its own heading.

## Before judging

- Set `NEXT_PUBLIC_SENTRY_REPLAY_RATE=1` and restart the web app so every judge session is replayed.
- Open the saved Sentry views: issues list (grouped `agent_run_failed`), one agent trace, the AI agents dashboard.
- Confirm `GET /models/current?arm=D&seed=0` returns the newest promoted D-s0 model.

## Flow (SPEC 14, 1:00–2:30, Person 2)

1. `/ask`: judge picks a train company and asks a question; if they're stuck, use one from the list below.
2. Show the interpretation and confirm; the run takes up to a few minutes.
3. On failure: open the run page (transcript, verifier detail), then the Sentry trace, then the grouped Sentry issue.
4. After the next generation's mining, the run page shows Jev's diagnosis.
5. Payoff: "Re-run on newest model" on an earlier judge question the new generation fixed.

## Prepared questions (P2-11)

Ten questions on train companies that the current model fails. Not filled in yet: needs the base model and templates.

| # | Company | Question | Template | Fails on |
|---|---|---|---|---|

## Rehearsals

Not done yet.

## Person 3: demo generation procedure
