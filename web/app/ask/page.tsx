"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  type Company,
  type JudgeParseResult,
  type JudgeRunResult,
  getJSON,
  postJSON,
  serviceUrl,
} from "@/lib/api";
import { AnswerView, PassBadge } from "@/components/run-bits";

type Phase = "idle" | "parsing" | "parsed" | "running" | "done";

export default function AskPage() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [companyId, setCompanyId] = useState("");
  const [question, setQuestion] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [parsed, setParsed] = useState<JudgeParseResult | null>(null);
  const [result, setResult] = useState<JudgeRunResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJSON<Company[]>(`${serviceUrl("env")}/companies?split=train`)
      .then((cs) => {
        const sorted = [...cs].sort((a, b) => a.name.localeCompare(b.name));
        setCompanies(sorted);
        if (sorted.length) setCompanyId(sorted[0].company_id);
      })
      .catch((e) => setError(`Couldn't load companies: ${e.message}`));
  }, []);

  async function parse(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setParsed(null);
    setResult(null);
    setPhase("parsing");
    try {
      const p = await postJSON<JudgeParseResult>(`${serviceUrl("agent")}/judge/parse`, {
        company_id: companyId,
        question,
      });
      setParsed(p);
      setPhase(p.valid ? "parsed" : "idle");
    } catch (err) {
      setError((err as Error).message);
      setPhase("idle");
    }
  }

  async function run() {
    if (!parsed?.template_id) return;
    setError(null);
    setPhase("running");
    try {
      const r = await postJSON<JudgeRunResult>(`${serviceUrl("agent")}/judge/run`, {
        company_id: companyId,
        template_id: parsed.template_id,
        params: parsed.params,
        question,
      });
      setResult(r);
      setPhase("done");
    } catch (err) {
      setError((err as Error).message);
      setPhase("parsed");
    }
  }

  const company = companies.find((c) => c.company_id === companyId);
  const busy = phase === "parsing" || phase === "running";

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Ask the bookkeeping agent</h1>
        <p className="mt-1 text-sm text-ink-2">
          Pick a company and ask a question about its books. If the agent gets it wrong, that failure becomes
          training data for the next model.
        </p>
      </div>

      <form onSubmit={parse} className="card space-y-4 p-5">
        <label className="block text-sm">
          <span className="text-ink-2">Company</span>
          <select
            className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2"
            value={companyId}
            onChange={(e) => setCompanyId(e.target.value)}
            disabled={busy}
          >
            {companies.map((c) => (
              <option key={c.company_id} value={c.company_id}>
                {c.name}
                {c.industry ? ` (${c.industry})` : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          <span className="text-ink-2">Question</span>
          <textarea
            className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2"
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="How much did we spend on rent between 2026-01-01 and 2026-03-31?"
            disabled={busy}
          />
        </label>
        <button
          type="submit"
          disabled={busy || !question.trim() || !companyId}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {phase === "parsing" ? "Reading your question…" : "Ask"}
        </button>
      </form>

      {error && (
        <p role="alert" className="card border-l-4 p-4 text-sm" style={{ borderLeftColor: "var(--critical)" }}>
          {error}
        </p>
      )}

      {parsed && !parsed.valid && (
        <div className="card whitespace-pre-line p-5 text-sm">{parsed.error}</div>
      )}

      {parsed?.valid && phase !== "done" && (
        <div className="card space-y-3 p-5">
          <p className="text-sm text-ink-2">Here&apos;s how the agent understood your question:</p>
          <p className="text-lg">{parsed.interpretation}</p>
          <p className="text-xs text-muted">
            Template <code>{parsed.template_id}</code> · Jev confidence {(parsed.template_confidence * 100).toFixed(0)}%
          </p>
          <div className="flex gap-3">
            <button
              onClick={run}
              disabled={busy}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {phase === "running" ? "Agent is working… (up to 3 minutes)" : "Yes, run it"}
            </button>
            <button
              onClick={() => {
                setParsed(null);
                setPhase("idle");
              }}
              disabled={busy}
              className="rounded-md border border-line px-4 py-2 text-sm"
            >
              Rephrase
            </button>
          </div>
        </div>
      )}

      {result && (
        <div className="card space-y-3 p-5">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">Result for {company?.name}</h2>
            <PassBadge passed={result.run.passed} />
          </div>
          <AnswerView run={result.run} />
          <div className="flex flex-wrap gap-4 text-sm">
            <Link className="text-accent underline" href={`/runs/${result.run.run_id}`}>
              Open the full run
            </Link>
            {result.sentry_trace_url && (
              <a className="text-accent underline" href={result.sentry_trace_url} target="_blank" rel="noreferrer">
                Sentry trace
              </a>
            )}
          </div>
          {!result.run.passed && (
            <p className="text-sm text-ink-2">
              This failure goes into the next generation&apos;s training batch. Re-run it on the newest model from
              the run page once a new generation ships.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
