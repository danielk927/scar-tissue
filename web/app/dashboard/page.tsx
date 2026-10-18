import { ScoreChart } from "@/components/score-chart";
import { type GenerationRecord, getJSON, serviceUrl } from "@/lib/api";
import { ARM_LABELS, buildSeries } from "@/lib/dashboard";

export const dynamic = "force-dynamic";

const pct = (v: number | null) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);

async function loadGenerations(): Promise<{ records: GenerationRecord[]; error: string | null }> {
  try {
    return { records: await getJSON<GenerationRecord[]>(`${serviceUrl("train")}/generations`), error: null };
  } catch (e) {
    return { records: [], error: (e as Error).message };
  }
}

export default async function DashboardPage() {
  const { records, error } = await loadGenerations();
  const { series, maxGen, base, reportable } = buildSeries(records);
  const sorted = [...reportable].sort(
    (a, b) => a.arm.localeCompare(b.arm) || a.seed - b.seed || a.generation - b.generation,
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Eval success rate by generation</h1>
        <p className="mt-1 text-sm text-ink-2">
          Score on <code>eval_v1</code> (300 tasks from companies never trained on) of the version in use after each
          generation. Lines are the mean over seeds; bands span min to max. Rejected candidates don&apos;t ship, so a
          rejection keeps the parent&apos;s score.
        </p>
      </div>

      {error ? (
        <p className="card p-5 text-sm">Train service unavailable: {error}</p>
      ) : series.length === 0 ? (
        <p className="card p-5 text-sm">No finished generations yet.</p>
      ) : (
        <section className="card p-5">
          <ScoreChart series={series} maxGen={maxGen} />
        </section>
      )}

      {series.length > 0 && (
        <section className="card overflow-x-auto p-5">
          <h2 className="mb-3 font-semibold">Mean eval score per arm</h2>
          <table className="tabular w-full text-sm">
            <thead className="text-left text-muted">
              <tr>
                <th className="py-1 pr-4 font-normal">Arm</th>
                {Array.from({ length: maxGen + 1 }, (_, g) => (
                  <th key={g} className="py-1 pr-4 text-right font-normal">
                    Gen {g}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {series.map((s) => (
                <tr key={s.arm} className="border-t border-line">
                  <td className="py-1.5 pr-4">
                    <span className="mr-2 inline-block h-2 w-2 rounded-full" style={{ background: `var(--arm-${s.arm})` }} />
                    {ARM_LABELS[s.arm]}
                  </td>
                  {Array.from({ length: maxGen + 1 }, (_, g) => {
                    const p = s.points.find((x) => x.generation === g);
                    return (
                      <td key={g} className="py-1.5 pr-4 text-right">
                        {p ? pct(p.mean) : "—"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {base != null && <p className="mt-2 text-xs text-muted">Base model (generation 0): {pct(base)}</p>}
        </section>
      )}

      {!error && (
      <section className="card overflow-x-auto p-5">
        <h2 className="mb-3 font-semibold">Generations</h2>
        {sorted.length === 0 ? (
          <p className="text-sm text-ink-2">None yet.</p>
        ) : (
          <table className="tabular w-full text-sm">
            <thead className="text-left text-muted">
              <tr>
                {["Generation", "Status", "Traffic", "Failures (incl.)", "Variants", "Examples", "Candidate", "Parent", "Regression", "In use"].map(
                  (h) => (
                    <th key={h} className="py-1 pr-4 font-normal">
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {sorted.map((g) => (
                <tr key={g.generation_id} className="border-t border-line">
                  <td className="py-1.5 pr-4 font-mono">{g.generation_id}</td>
                  <td className="pr-4">{g.status}</td>
                  <td className="pr-4">{g.traffic_runs}</td>
                  <td className="pr-4">
                    {g.failures_mined} ({g.failures_included})
                  </td>
                  <td className="pr-4">{g.variants_created}</td>
                  <td className="pr-4">{g.examples_built}</td>
                  <td className="pr-4">{pct(g.eval_score)}</td>
                  <td className="pr-4">{pct(g.parent_eval_score)}</td>
                  <td className="pr-4">
                    {pct(g.regression_score)}
                    {g.parent_regression_score != null && (
                      <span className="text-muted"> vs {pct(g.parent_regression_score)}</span>
                    )}
                  </td>
                  <td className="pr-4 font-mono text-xs">
                    {g.current_model_version} / {g.current_prompt_version}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      )}
    </div>
  );
}
