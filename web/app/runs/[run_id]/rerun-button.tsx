"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { type GenerationRecord, type RunResult, getJSON, nextJudgeGeneration, postJSON, serviceUrl } from "@/lib/api";

/** Re-runs the same task on the current promoted D-s0 model (SPEC 9.12 step 6, the demo payoff). */
export function RerunButton({ taskId }: { taskId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function rerun() {
    setBusy(true);
    setError(null);
    try {
      const train = serviceUrl("train");
      const [current, gens] = await Promise.all([
        getJSON<{ model_version: string; prompt_version: string }>(`${train}/models/current?arm=D&seed=0`),
        getJSON<GenerationRecord[]>(`${train}/generations?arm=D&seed=0`),
      ]);
      const [run] = await postJSON<RunResult[]>(`${serviceUrl("agent")}/agent/run`, {
        task_id: taskId,
        model_version: current.model_version,
        prompt_version: current.prompt_version,
        arm: "D",
        seed: 0,
        generation: nextJudgeGeneration(gens),
        purpose: "judge",
        temperature: 0.0,
        n: 1,
      });
      router.push(`/runs/${run.run_id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex flex-col">
      <button
        onClick={rerun}
        disabled={busy}
        className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
      >
        {busy ? "Running on newest model…" : "Re-run on newest model"}
      </button>
      {error && <span className="mt-1 text-xs" style={{ color: "var(--critical)" }}>{error}</span>}
    </span>
  );
}
