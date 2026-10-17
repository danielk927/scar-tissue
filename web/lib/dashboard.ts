import { type Arm, ARMS, type GenerationRecord, currentScore } from "./api";

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

/** Eval score of the version in use after each generation, aggregated over seeds (SPEC 2.3). */
export function buildSeries(records: GenerationRecord[], nGenerations = 4) {
  const base = records.find((r) => r.generation === 1 && r.parent_eval_score != null)?.parent_eval_score ?? null;
  let maxGen = nGenerations;
  const series: Series[] = [];

  for (const arm of ARMS) {
    const byGen = new Map<number, number[]>();
    const seedsWithData = new Set<number>();
    for (const r of records.filter((x) => x.arm === arm)) {
      const s = currentScore(r);
      if (s == null) continue;
      seedsWithData.add(r.seed);
      byGen.set(r.generation, [...(byGen.get(r.generation) ?? []), s]);
      maxGen = Math.max(maxGen, r.generation);
    }
    if (base != null && seedsWithData.size) byGen.set(0, Array(seedsWithData.size).fill(base));
    const points = [...byGen.entries()].sort(([a], [b]) => a - b).map(([g, vs]) => summarize(g, vs));
    if (points.length) series.push({ arm, points });
  }

  // Arm A never learns: evaluated once, equal to generation 0 (SPEC 9.10)
  if (base != null && !series.some((s) => s.arm === "A")) {
    const points = Array.from({ length: maxGen + 1 }, (_, g) => summarize(g, [base]));
    series.unshift({ arm: "A", points });
  }
  series.sort((a, b) => ARMS.indexOf(a.arm) - ARMS.indexOf(b.arm));
  return { series, maxGen, base };
}
