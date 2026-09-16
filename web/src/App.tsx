import { useState } from "react";
import data from "./data/identification.json";
import type { IdentificationData } from "./types";
import { Hero } from "./components/Hero";
import { ModelComparison } from "./components/ModelComparison";
import { AccuracyCurve } from "./components/AccuracyCurve";
import { GameExplainer } from "./components/GameExplainer";

const DATA = data as unknown as IdentificationData;

function pct(v: number, digits = 1): string {
  return (v * 100).toFixed(digits) + "%";
}

export default function App() {
  const [mode, setMode] = useState<"biometric" | "full">("biometric");
  const cohort = mode === "biometric" ? DATA.pool_biometric : DATA.pool;

  return (
    <>
      <div className="topbar">
        <div className="brand">
          <span className="dot" />
          <h1>Time Signature</h1>
        </div>
        <span className="badge">Player identification</span>
      </div>

      <div className="wrap">
        <Hero cohort={cohort} nPlayers={DATA.n_players} />

        <div className="toggle-row">
          <div className="switch">
            <button className={mode === "biometric" ? "active" : ""} type="button" onClick={() => setMode("biometric")}>
              Biometrics only
            </button>
            <button className={mode === "full" ? "active" : ""} type="button" onClick={() => setMode("full")}>
              Full (incl. move choice)
            </button>
          </div>
          <span className="deck">
            {mode === "biometric"
              ? "Clock allocation, pace, and session timing only — the model never sees what was actually played."
              : "Adds move-quality signals on top of the timing data."}
          </span>
        </div>

        <section>
          <div className="sec-head">
            <div className="eyebrow">Model</div>
            <h2>Picking one player out of {cohort.class_counts_n}</h2>
            <p>
              {cohort.n_games_total} games total. On a single game, the model gets it right{" "}
              {pct(cohort.balanced_accuracy)} of the time — random guessing would land {pct(cohort.chance_baseline, 1)}
              , and just picking the most common player would get {pct(cohort.majority_baseline, 1)}.
            </p>
          </div>
          <div className="grid duo">
            <ModelComparison cohort={cohort} />
            <AccuracyCurve cohort={cohort} />
          </div>
        </section>

        <section>
          <div className="sec-head">
            <div className="eyebrow">Explainability</div>
            <h2>Look inside a prediction</h2>
            <p>Held-out games with the model's actual reasoning attached — click through the moves and see what it noticed.</p>
          </div>
          <GameExplainer games={DATA.narrated_games} />
        </section>
      </div>

      <footer>
        chess-clock · a personal clock-management analyzer · {DATA.n_players} players in the pool
        {/* TODO: link to the GitHub repo once it's published */}
      </footer>
    </>
  );
}
