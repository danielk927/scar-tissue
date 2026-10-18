import * as Sentry from "@sentry/nextjs";

export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    // the repo-root .env (SPEC 7.5) for server code: service URLs, SENTRY_ORG for trace links
    const [{ loadEnvConfig }, path] = await Promise.all([import("@next/env"), import("node:path")]);
    loadEnvConfig(path.resolve(process.cwd(), ".."), process.env.NODE_ENV !== "production", console, true);
    await import("./sentry.server.config");
  }
  if (process.env.NEXT_RUNTIME === "edge") await import("./sentry.edge.config");
}

export const onRequestError = Sentry.captureRequestError;
