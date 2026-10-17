// Browser Sentry: tracing (added automatically by @sentry/nextjs) and Session Replay (docs/SENTRY.md section 8).
import * as Sentry from "@sentry/nextjs";

// Free plan has 50 replays (docs/SENTRY.md section 11): set NEXT_PUBLIC_SENTRY_REPLAY_RATE=1 for the demo.
const replayRate = Number(process.env.NEXT_PUBLIC_SENTRY_REPLAY_RATE ?? "0");

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? "hackathon",
  tracesSampleRate: 1.0,
  integrations: [Sentry.replayIntegration()],
  replaysSessionSampleRate: replayRate,
  replaysOnErrorSampleRate: 1.0,
});

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
