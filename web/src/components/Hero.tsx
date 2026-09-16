import { useState } from "react";
import type { Cohort } from "../types";

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null || !isFinite(v)) return "—";
  return (v * 100).toFixed(digits) + "%";
}

export function Hero({ cohort, nPlayers }: { cohort: Cohort; nPlayers: number }) {
  const points = cohort.multi_game_accuracy;
  const defaultIdx = Math.max(0, points.findIndex((c) => c.n_games === 50));
  const [idx, setIdx] = useState(defaultIdx >= 0 ? defaultIdx : points.length - 1);
  const headline = points[idx];
  const frac = Math.max(0, Math.min(1, headline?.balanced_accuracy ?? 0));
  const needleAngle = -90 + frac * 360;

  const size = 200, cx = size / 2, cy = size / 2, faceR = 88, tickOuter = 82, tickInner = 72, needleLen = 62;

  const ticks = Array.from({ length: 10 }, (_, i) => {
    const angle = (-90 + i * 36) * (Math.PI / 180);
    const major = i % 5 === 0;
    const r1 = major ? tickInner - 6 : tickInner;
    return {
      x1: cx + Math.cos(angle) * tickOuter, y1: cy + Math.sin(angle) * tickOuter,
      x2: cx + Math.cos(angle) * r1, y2: cy + Math.sin(angle) * r1,
      major,
    };
  });

  const needleRad = (needleAngle * Math.PI) / 180;
  const needleX = cx + Math.cos(needleRad) * needleLen;
  const needleY = cy + Math.sin(needleRad) * needleLen;

  return (
    <section className="hero" style={{ borderBottom: "1px solid var(--line)" }}>
      <div>
        <h2>Everyone plays the clock differently.</h2>
        <p className="thesis">
          Think of it as a heartbeat. How long someone thinks, how their pace shifts under pressure, when they tend
          to log on. That rhythm is distinct enough to tell players apart without looking at a single move they
          made. Tested against {nPlayers} players, on games the model had never seen.
        </p>
      </div>
      <div className="hero-dial">
        <div style={{ position: "relative", width: size, height: size }}>
          <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size}>
            <circle cx={cx} cy={cy} r={faceR} fill="var(--surface)" stroke="var(--bezel)" strokeWidth={6} />
            {ticks.map((t, i) => (
              <line key={i} x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} stroke="var(--text-muted)" strokeWidth={t.major ? 2.5 : 1.5} opacity={t.major ? 0.8 : 0.4} />
            ))}
            <circle cx={cx} cy={cy} r={tickInner - 10} fill="none" stroke="var(--surface-2)" strokeWidth={10} />
            <circle
              cx={cx} cy={cy} r={tickInner - 10} fill="none" stroke="var(--accent)" strokeWidth={10}
              strokeDasharray={`${2 * Math.PI * (tickInner - 10) * frac} ${2 * Math.PI * (tickInner - 10)}`}
              strokeLinecap="round"
              transform={`rotate(-90 ${cx} ${cy})`}
            />
            <line x1={cx} y1={cy} x2={needleX} y2={needleY} stroke="var(--accent-strong)" strokeWidth={2.5} strokeLinecap="round" />
            <circle cx={cx} cy={cy} r={4.5} fill="var(--accent-strong)" />
          </svg>
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", paddingTop: 18 }}>
            <div className="digit mono" style={{ fontSize: 32, background: "var(--surface-2)", padding: "4px 10px", borderRadius: 6, boxShadow: "var(--inset)" }}>
              {pct(headline?.balanced_accuracy)}
            </div>
          </div>
        </div>
        <div className="label" style={{ marginTop: 6 }}>balanced accuracy</div>
        <div className="games-adjust" role="group" aria-label="Number of games">
          {points.map((p, i) => (
            <button key={p.n_games} type="button" className={i === idx ? "active" : ""} onClick={() => setIdx(i)}>
              {p.n_games}
            </button>
          ))}
        </div>
        <div className="sub">games aggregated · {pct(cohort.chance_baseline, 0)} by chance</div>
      </div>
    </section>
  );
}
