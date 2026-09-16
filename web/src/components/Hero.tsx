import type { Cohort } from "../types";

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null || !isFinite(v)) return "—";
  return (v * 100).toFixed(digits) + "%";
}

export function Hero({ cohort, nPlayers }: { cohort: Cohort; nPlayers: number }) {
  const headline = cohort.multi_game_accuracy.find((c) => c.n_games === 20) ?? cohort.multi_game_accuracy[cohort.multi_game_accuracy.length - 1];
  const r = 72;
  const c = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(1, headline?.balanced_accuracy ?? 0));

  return (
    <section className="hero" style={{ borderBottom: "1px solid var(--line)" }}>
      <div>
        <h2>Everyone plays the clock differently.</h2>
        <p className="thesis">
          Think of it like a heartbeat — how long a player thinks, how their pace shifts under pressure, when they
          tend to log on. That rhythm turns out to be distinctive enough to tell players apart, without looking at a
          single move they made. Tested against {nPlayers} players, on games the model had never seen.
        </p>
      </div>
      <div className="hero-dial">
        <div className="label">Accuracy over {headline?.n_games ?? "?"} games</div>
        <div style={{ position: "relative", width: 200, height: 200 }}>
          <svg viewBox="0 0 200 200" width={200} height={200}>
            <circle cx={100} cy={100} r={r} fill="none" stroke="var(--surface-2)" strokeWidth={14} />
            <circle
              cx={100} cy={100} r={r} fill="none" stroke="var(--accent)" strokeWidth={14}
              strokeDasharray={`${c * frac} ${c}`}
              strokeLinecap="round"
              transform="rotate(-90 100 100)"
            />
          </svg>
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <div className="digit" style={{ fontSize: 38 }}>{pct(headline?.balanced_accuracy)}</div>
          </div>
        </div>
        <div className="sub">vs. {pct(cohort.chance_baseline, 0)} chance across {cohort.class_counts_n} players</div>
      </div>
    </section>
  );
}
