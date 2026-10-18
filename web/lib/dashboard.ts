import { type Arm, ARMS, type GenerationRecord, currentScore, isReportable } from "./api";

export const ARM_LABELS: Record<Arm, string> = {
  A: "A · Frozen base",
  B: "B · Failure memory",
  C: "C · GEPA prompt",
  D: "D · Weight updates (ours)",
  E: "E · D + GEPA",
};

export interface Point {
  generation: number;
  mean: number;
  min: number;
  max: number;
  n: number; // seeds contributing
}

export interface Series {
  arm: Arm;
  points: Point[];
}

function summarize(generation: number, values: number[]): Point {
  return {
    generation,
    mean: values.reduce((a, b) => a + b, 0) / values.length,
    min: Math.min(...values),
    max: Math.max(...values),
    n: values.length,
  };
}

/** The shared generation-0 score: a generation-0 eval score if recorded, else generation 1's parent score. */
export function baseScore(records: GenerationRecord[]): number | null {
  const g0 = records.find((r) => r.generation === 0 && r.eval_score != null);
  if (g0) return g0.eval_score;
  return records.find((r) => r.generation === 1 && r.parent_eval_score != null)?.parent_eval_score ?? null;
}

/** Eval score of the version in use after each generation, mean/min/max over seeds. Same rules as
 *  experiments/scar_experiments/report.py, so the dashboard and docs/RESULTS.md never disagree. */
export function buildSeries(all: GenerationRecord[], nGenerations = 4) {
  const records = all.filter(isReportable);
  const base = baseScore(records);
  const lineages = new Map<string, Map<number, number>>(); // "D-0" -> generation -> score
  let maxGen = nGenerations;

  for (const r of records) {
    if (r.arm === "A" || r.generation === 0) continue;
    const s = currentScore(r);
    if (s == null) continue; // in progress
    const key = `${r.arm}-${r.seed}`;
    if (!lineages.has(key)) lineages.set(key, new Map(base != null ? [[0, base]] : []));
    lineages.get(key)!.set(r.generation, s);
    maxGen = Math.max(maxGen, r.generation);
  }

  const series: Series[] = [];
  if (base != null) {
    // arm A never learns: generation 0 repeated (SPEC 9.10)
    series.push({ arm: "A", points: Array.from({ length: maxGen + 1 }, (_, g) => summarize(g, [base])) });
  }
  for (const arm of ARMS.filter((a) => a !== "A")) {
    const mine = [...lineages.entries()].filter(([k]) => k.startsWith(`${arm}-`)).map(([, v]) => v);
    const gens = [...new Set(mine.flatMap((m) => [...m.keys()]))].sort((a, b) => a - b);
    const points = gens.map((g) => summarize(g, mine.filter((m) => m.has(g)).map((m) => m.get(g)!)));
    if (points.length) series.push({ arm, points });
  }
  return { series, maxGen, base, reportable: records };
}
