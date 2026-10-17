# web (Person 2)

Next.js app on `:3000`: `/ask` (judge UI), `/runs/[run_id]` (run viewer), `/dashboard` (eval score by generation).

```bash
cd web
npm install
npm run dev
```

Reads the repo-root `.env` (`AGENT_SERVICE_URL`, `TRAIN_SERVICE_URL`, `ENV_SERVICE_URL`, `NEXT_PUBLIC_SENTRY_DSN`, `SENTRY_ORG`, `SENTRY_PROJECT`, optional `NEXT_PUBLIC_SENTRY_REPLAY_RATE`). The browser reaches the services through the `/api/{agent,train,env}/*` rewrites in `next.config.ts`.
