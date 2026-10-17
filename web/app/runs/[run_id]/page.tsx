import { notFound } from "next/navigation";

import { RerunButton } from "./rerun-button";
import { AnswerView, PassBadge, Transcript } from "@/components/run-bits";
import { ApiError, type FailureRecord, type RunResult, getJSON, serviceUrl, traceUrl } from "@/lib/api";

export const dynamic = "force-dynamic";

async function loadFailure(run: RunResult): Promise<FailureRecord | null> {
  // GET /failures has no run_id filter (SPEC 8.5): filter by the run's lineage and generation instead
  const q = new URLSearchParams({ arm: run.arm, seed: String(run.seed), generation: String(run.generation) });
  try {
    const fs = await getJSON<FailureRecord[]>(`${serviceUrl("agent")}/failures?${q}`);
    return fs.find((f) => f.run_id === run.run_id) ?? null;
  } catch {
    return null;
  }
}

export default async function RunPage({ params }: { params: Promise<{ run_id: string }> }) {
  const { run_id } = await params;
  let run: RunResult;
  try {
    run = await getJSON<RunResult>(`${serviceUrl("agent")}/runs/${run_id}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }
  const failure = run.passed ? null : await loadFailure(run);
  const trace = traceUrl(run.sentry_trace_id, process.env.SENTRY_ORG);
  // user message is exactly "Company: {name}\n\n{prompt}" (SPEC 9.2)
  const userMsg = run.messages.find((m) => m.role === "user")?.content ?? "";
  const [companyLine, ...rest] = userMsg.split("\n\n");
  const question = rest.join("\n\n") || userMsg;
  const companyName = rest.length ? companyLine.replace(/^Company: /, "") : run.company_id;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs text-muted font-mono">{run.run_id}</p>
          <h1 className="mt-1 whitespace-pre-line text-xl font-semibold">{question}</h1>
          <p className="mt-1 text-sm text-ink-2">
            {companyName} · {run.family} · {run.purpose} · arm {run.arm}, seed {run.seed}, generation {run.generation}
          </p>
        </div>
        <PassBadge passed={run.passed} />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <section className="card space-y-3 p-5">
          <h2 className="font-semibold">Outcome</h2>
          <AnswerView run={run} />
          <div className="flex flex-wrap gap-3 pt-2">
            {trace && (
              <a className="text-sm text-accent underline" href={trace} target="_blank" rel="noreferrer">
                Open Sentry trace
              </a>
            )}
            <RerunButton taskId={run.task_id} />
          </div>
        </section>

        <section className="card space-y-2 p-5">
          <h2 className="font-semibold">Jev diagnosis</h2>
          {run.passed ? (
            <p className="text-sm text-ink-2">Passed runs aren&apos;t triaged.</p>
          ) : failure ? (
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted">Cause</dt>
              <dd>
                <strong>{failure.cause.replaceAll("_", " ")}</strong>{" "}
                <span className="text-muted">({(failure.cause_confidence * 100).toFixed(0)}% confidence)</span>
              </dd>
              <dt className="text-muted">Learnable</dt>
              <dd>{(failure.learnable_p * 100).toFixed(0)}%</dd>
              <dt className="text-muted">Used for training</dt>
              <dd>{failure.included ? "Yes" : `No (${failure.exclusion_reason})`}</dd>
              <dt className="text-muted">Model</dt>
              <dd>{failure.jev_model}</dd>
            </dl>
          ) : (
            <p className="text-sm text-ink-2">Not triaged yet. Jev runs during the next generation&apos;s mining step.</p>
          )}
        </section>
      </div>

      <section className="space-y-3">
        <h2 className="font-semibold">Transcript</h2>
        <Transcript messages={run.messages} />
      </section>
    </div>
  );
}
