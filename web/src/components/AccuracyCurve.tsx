import { Fragment } from "react";
import type { Cohort } from "../types";

function pct(v: number, digits = 1): string {
  return (v * 100).toFixed(digits) + "%";
}

export function AccuracyCurve({ cohort }: { cohort: Cohort }) {
  const points = cohort.multi_game_accuracy;
  const W = 560, H = 220, padL = 42, padR = 16, padT = 16, padB = 30;
  const innerW = W - padL - padR, innerH = H - padT - padB;
  const xMax = points[points.length - 1].n_games;

  const x = (n: number) => padL + (Math.log(n + 1) / Math.log(xMax + 1)) * innerW;
  const y = (v: number) => padT + (1 - v) * innerH;

  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.n_games)} ${y(p.balanced_accuracy)}`).join(" ");
  const chanceY = y(cohort.chance_baseline);
  const best = points.reduce((a, b) => (b.balanced_accuracy > a.balanced_accuracy ? b : a), points[0]);

  return (
    <div className="card">
      <h3>More games, better guess</h3>
      <p className="deck" style={{ marginBottom: 6, color: "var(--accent-strong)", fontWeight: 600 }}>
        Peaks at {best.n_games} games: {pct(best.balanced_accuracy)} balanced accuracy.
      </p>
      <p className="deck" style={{ marginBottom: 10 }}>
        A single game doesn't say much on its own. Watch a handful from the same {cohort.class_counts_n}-player pool
        and it adds up fast.
      </p>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: W }}>
        <line x1={padL} y1={chanceY} x2={W - padR} y2={chanceY} stroke="var(--text-muted)" strokeDasharray="3 3" opacity={0.5} />
        <text x={W - padR} y={chanceY - 4} textAnchor="end" fontSize={10} fill="var(--text-muted)" fontFamily="IBM Plex Mono, monospace">
          chance
        </text>
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line x1={padL} y1={padT + (1 - f) * innerH} x2={W - padR} y2={padT + (1 - f) * innerH} stroke="var(--line)" strokeWidth={1} />
            <text x={padL - 6} y={padT + (1 - f) * innerH + 3} textAnchor="end" fontSize={10} fill="var(--text-muted)" fontFamily="IBM Plex Mono, monospace">
              {(f * 100).toFixed(0)}%
            </text>
          </g>
        ))}
        <path d={linePath} fill="none" stroke="var(--accent)" strokeWidth={2.5} />
        {points.map((p) => (
          <g key={p.n_games}>
            <circle cx={x(p.n_games)} cy={y(p.balanced_accuracy)} r={4} fill="var(--accent)" />
            <text x={x(p.n_games)} y={H - padB + 16} textAnchor="middle" fontSize={10.5} fill="var(--text-muted)" fontFamily="IBM Plex Mono, monospace">
              {p.n_games}
            </text>
          </g>
        ))}
        <text x={(padL + W - padR) / 2} y={H - 2} textAnchor="middle" fontSize={10.5} fill="var(--text-muted)" fontFamily="IBM Plex Mono, monospace">
          games aggregated
        </text>
      </svg>

      <div style={{ marginTop: 14, borderTop: "1px solid var(--line)", paddingTop: 10 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "6px 12px", fontSize: 12.5 }}>
          <span className="mono" style={{ color: "var(--text-muted)", fontWeight: 600 }}>games</span>
          <span className="mono" style={{ color: "var(--text-muted)", fontWeight: 600 }}>balanced acc.</span>
          <span className="mono" style={{ color: "var(--text-muted)", fontWeight: 600 }}>raw acc.</span>
          {points.map((p) => (
            <Fragment key={p.n_games}>
              <span className="mono">{p.n_games}</span>
              <span className="mono">{pct(p.balanced_accuracy)}</span>
              <span className="mono" style={{ color: "var(--text-muted)" }}>{pct(p.overall_accuracy)}</span>
            </Fragment>
          ))}
        </div>
      </div>
    </div>
  );
}
