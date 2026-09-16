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
        <div className="clock-body">
          <div className="clock-plunger" aria-hidden="true" />
          <div className="clock-screen">
            <div className="clock-digits mono">{pct(headline?.balanced_accuracy)}</div>
            <div className="clock-caption">balanced accuracy</div>
          </div>
        </div>
        <div className="games-adjust" role="group" aria-label="Number of games">
          {points.map((p, i) => (
            <button key={p.n_games} type="button" className={i === idx ? "active" : ""} onClick={() => setIdx(i)}>
              {p.n_games}
            </button>
          ))}
        </div>
        <div className="sub">after watching {headline?.n_games ?? "?"} games · {pct(cohort.chance_baseline, 0)} by chance</div>
      </div>
    </section>
  );
}
