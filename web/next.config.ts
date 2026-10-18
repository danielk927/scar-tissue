import path from "node:path";

import { loadEnvConfig } from "@next/env";
import { withSentryConfig } from "@sentry/nextjs/config";
import type { NextConfig } from "next";

// One .env at the repo root for every service (SPEC 7.5); Next.js only looks in web/ and caches that result, so
// forceReload (4th argument) is needed. Shell variables still win. instrumentation.ts repeats this for the server
// runtime, where service URLs are read per request by app/api/[service]/[...path]/route.ts.
loadEnvConfig(path.resolve(process.cwd(), ".."), process.env.NODE_ENV !== "production", console, true);

const nextConfig: NextConfig = {};

export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  silent: !process.env.CI,
});
