import path from "node:path";

import { loadEnvConfig } from "@next/env";
import { withSentryConfig } from "@sentry/nextjs/config";
import type { NextConfig } from "next";

// One .env at the repo root for every service (SPEC 7.5); Next.js only looks in web/ by default.
loadEnvConfig(path.resolve(process.cwd(), ".."));

const AGENT = process.env.AGENT_SERVICE_URL ?? "http://localhost:8002";
const TRAIN = process.env.TRAIN_SERVICE_URL ?? "http://localhost:8003";
const ENV = process.env.ENV_SERVICE_URL ?? "http://localhost:8001";

const nextConfig: NextConfig = {
  // Same-origin proxies: the browser's sentry-trace/baggage headers pass straight through to the
  // services, so a judge's click and the backend run share one trace, with no CORS.
  async rewrites() {
    return [
      { source: "/api/agent/:path*", destination: `${AGENT}/:path*` },
      { source: "/api/train/:path*", destination: `${TRAIN}/:path*` },
      { source: "/api/env/:path*", destination: `${ENV}/:path*` },
    ];
  },
  experimental: {
    // default 30 s would cut off a judge run (up to 180 s, SPEC 7.4)
    proxyTimeout: 200_000,
  },
};

export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  silent: !process.env.CI,
});
