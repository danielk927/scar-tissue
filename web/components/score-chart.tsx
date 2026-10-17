"use client";

import { useRef, useState } from "react";

import { ARM_LABELS, type Series } from "@/lib/dashboard";

const W = 760;
const H = 340;
const M = { top: 16, right: 24, bottom: 40, left: 48 };
const PW = W - M.left - M.right;
const PH = H - M.top - M.bottom;

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
const color = (arm: string) => `var(--arm-${arm})`;

export function ScoreChart({ series, maxGen }: { series: Series[]; maxGen: number }) {
  const [hoverGen, setHoverGen] = useState<number | null>(null);
  const wrap = useRef<HTMLDivElement>(null);

  const all = series.flatMap((s) => s.points.map((p) => p.max));
  const yMax = Math.min(1, Math.ceil(((all.length ? Math.max(...all) : 0.5) + 0.05) * 10) / 10);
  const x = (g: number) => M.left + (maxGen === 0 ? 0 : (g / maxGen) * PW);
  const y = (v: number) => M.top + PH - (v / yMax) * PH;
  const tickStep = yMax > 0.5 ? 0.2 : 0.1;
  const yTicks = Array.from({ length: Math.round(yMax / tickStep) + 1 }, (_, i) => i * tickStep);
  const gens = Array.from({ length: maxGen + 1 }, (_, g) => g);

  function onMove(e: React.MouseEvent<SVGRectElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const frac = (e.clientX - box.left) / box.width;
    setHoverGen(Math.max(0, Math.min(maxGen, Math.round(frac * maxGen))));
  }

  const hoverRows =
    hoverGen == null
      ? []
      : series
          .map((s) => ({ arm: s.arm, p: s.points.find((p) => p.generation === hoverGen) }))
          .filter((r) => r.p)
          .sort((a, b) => b.p!.mean - a.p!.mean);

  return (
    <div className="space-y-3">
      <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm" aria-label="Legend">
        {series.map((s) => (
          <li key={s.arm} className="inline-flex items-center gap-2 text-ink-2">
            <svg width="18" height="8" aria-hidden>
              <line x1="1" y1="4" x2="17" y2="4" stroke={color(s.arm)} strokeWidth="2" strokeLinecap="round" />
            </svg>
            {ARM_LABELS[s.arm]}
          </li>
        ))}
      </ul>

      <div ref={wrap} className="relative overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[560px]" role="img" aria-label="Eval success rate by generation per arm">
          {yTicks.map((t) => (
            <g key={t}>
              <line x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth="1" />
              <text x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize="11" fill="var(--text-muted)">
                {Math.round(t * 100)}%
              </text>
            </g>
          ))}
          <line x1={M.left} x2={W - M.right} y1={y(0)} y2={y(0)} stroke="var(--axis)" strokeWidth="1" />
          {gens.map((g) => (
            <text key={g} x={x(g)} y={H - M.bottom + 18} textAnchor="middle" fontSize="11" fill="var(--text-muted)">
              {g}
            </text>
          ))}
          <text x={M.left + PW / 2} y={H - 6} textAnchor="middle" fontSize="11" fill="var(--text-muted)">
            Generation
          </text>

          {series.map((s) => {
            const band = s.points.filter((p) => p.n > 1);
            if (band.length < 2) return null;
            const d =
              band.map((p, i) => `${i ? "L" : "M"}${x(p.generation)},${y(p.max)}`).join("") +
              [...band].reverse().map((p) => `L${x(p.generation)},${y(p.min)}`).join("") +
              "Z";
            return <path key={`band-${s.arm}`} d={d} fill={color(s.arm)} fillOpacity="0.14" />;
          })}

          {hoverGen != null && (
            <line x1={x(hoverGen)} x2={x(hoverGen)} y1={M.top} y2={M.top + PH} stroke="var(--axis)" strokeWidth="1" />
          )}

          {series.map((s) => (
            <g key={s.arm}>
              <polyline
                points={s.points.map((p) => `${x(p.generation)},${y(p.mean)}`).join(" ")}
                fill="none"
                stroke={color(s.arm)}
                strokeWidth="2"
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {s.points.map((p) => (
                <circle
                  key={p.generation}
                  cx={x(p.generation)}
                  cy={y(p.mean)}
                  r={hoverGen === p.generation ? 5 : 4}
                  fill={color(s.arm)}
                  stroke="var(--surface-1)"
                  strokeWidth="2"
                />
              ))}
            </g>
          ))}

          <rect
            x={M.left}
            y={M.top}
            width={PW}
            height={PH}
            fill="transparent"
            onMouseMove={onMove}
            onMouseLeave={() => setHoverGen(null)}
          />
        </svg>

        {hoverGen != null && hoverRows.length > 0 && (
          <div
            className="card pointer-events-none absolute top-2 z-10 p-3 text-xs shadow-sm"
            style={{
              left: `${(x(hoverGen) / W) * 100}%`,
              transform: hoverGen > maxGen / 2 ? "translateX(calc(-100% - 12px))" : "translateX(12px)",
            }}
          >
            <p className="mb-1 font-semibold">Generation {hoverGen}</p>
            <table className="tabular">
              <tbody>
                {hoverRows.map(({ arm, p }) => (
                  <tr key={arm}>
                    <td className="pr-2">
                      <span className="inline-block h-2 w-2 rounded-full" style={{ background: color(arm) }} />
                    </td>
                    <td className="pr-3 text-ink-2">{ARM_LABELS[arm]}</td>
                    <td className="pr-3 text-right font-medium">{pct(p!.mean)}</td>
                    <td className="text-muted">
                      {p!.n > 1 ? `${pct(p!.min)}–${pct(p!.max)}, ${p!.n} seeds` : "1 seed"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
