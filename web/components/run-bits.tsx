import type { ChatMessage, RunResult } from "@/lib/api";

export function PassBadge({ passed }: { passed: boolean }) {
  // status color never alone: icon + label
  return passed ? (
    <span className="inline-flex items-center gap-1 text-sm font-medium" style={{ color: "var(--good-text)" }}>
      <span aria-hidden>✓</span> Passed
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-sm font-medium" style={{ color: "var(--critical)" }}>
      <span aria-hidden>✕</span> Failed
    </span>
  );
}

export function AnswerView({ run }: { run: RunResult }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
      <dt className="text-muted">Agent&apos;s answer</dt>
      <dd className="font-mono">{run.final_answer ? JSON.stringify(run.final_answer) : "none submitted"}</dd>
      <dt className="text-muted">Verifier</dt>
      <dd className="font-mono">{run.verify_detail}</dd>
      <dt className="text-muted">Stopped because</dt>
      <dd>{run.stop_reason}</dd>
      <dt className="text-muted">Model</dt>
      <dd>
        {run.model_version} · {run.steps} steps · {(run.latency_ms / 1000).toFixed(1)} s
      </dd>
    </dl>
  );
}

function pretty(content: string | null): string {
  if (!content) return "";
  try {
    return JSON.stringify(JSON.parse(content), null, 2);
  } catch {
    return content;
  }
}

export function Transcript({ messages }: { messages: ChatMessage[] }) {
  return (
    <ol className="space-y-3">
      {messages.map((m, i) => (
        <li key={i} className="card p-3 text-sm">
          <div className="mb-1 text-xs uppercase tracking-wide text-muted">
            {m.role}
            {m.role === "tool" && m.name ? ` · ${m.name}` : ""}
          </div>
          {m.content && (
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words font-mono text-xs">
              {m.role === "tool" ? pretty(m.content) : m.content}
            </pre>
          )}
          {m.tool_calls?.map((c) => (
            <pre key={c.id} className="mt-1 whitespace-pre-wrap break-words font-mono text-xs">
              → {c.name}({JSON.stringify(c.arguments)})
            </pre>
          ))}
        </li>
      ))}
    </ol>
  );
}
