// Same-origin proxy to the three services. URLs are read per request (AGENT/TRAIN/ENV_SERVICE_URL), so the demo can
// point a running web app at another laptop without a rebuild. The browser's sentry-trace/baggage headers are
// forwarded, and the server SDK's fetch instrumentation continues the trace into the service.
import { serviceBase } from "@/lib/api";

export const dynamic = "force-dynamic";

const FORWARD_HEADERS = ["content-type", "accept", "sentry-trace", "baggage", "traceparent"];
const SERVICES = new Set(["agent", "train", "env"]);

async function proxy(request: Request, ctx: { params: Promise<{ service: string; path: string[] }> }) {
  const { service, path } = await ctx.params;
  if (!SERVICES.has(service)) {
    return Response.json({ error: "unknown_service", detail: service }, { status: 404 });
  }
  const target = `${serviceBase(service as "agent" | "train" | "env")}/${path.map(encodeURIComponent).join("/")}${new URL(request.url).search}`;
  const headers = new Headers();
  for (const h of FORWARD_HEADERS) {
    const v = request.headers.get(h);
    if (v) headers.set(h, v);
  }
  try {
    const res = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.text(),
      cache: "no-store",
      signal: AbortSignal.timeout(200_000), // a judge run can take up to 180 s (SPEC 7.4)
    });
    return new Response(res.body, {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") ?? "application/json" },
    });
  } catch (e) {
    return Response.json(
      { error: "service_unreachable", detail: `${service} at ${serviceBase(service as "agent")}: ${(e as Error).message}` },
      { status: 503 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
