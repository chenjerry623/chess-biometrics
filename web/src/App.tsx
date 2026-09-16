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
        <span className="badge">Player identification · Phase 1</span>
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
              ? "Default: clock allocation, pace, session timing — zero use of what moves were actually played."
              : "Optional: adds move-quality/typicality signals on top of biometrics."}
          </span>
        </div>

        <section>
          <div className="sec-head">
            <div className="eyebrow">Model</div>
            <h2>{cohort.class_counts_n}-player identification, held out honestly</h2>
            <p>
              {cohort.n_games_total} games. {pct(cohort.balanced_accuracy)} balanced accuracy on a single game vs.{" "}
              {pct(cohort.chance_baseline, 1)} chance and {pct(cohort.majority_baseline, 1)} majority-class baseline.
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
            <h2>Why did it decide that?</h2>
            <p>Real held-out games, real SHAP-derived reasoning, linked back to the actual positions that drove the prediction.</p>
          </div>
          <GameExplainer games={DATA.narrated_games} />
        </section>
      </div>

      <footer>
        chess-clock · personal clock-management analyzer · player pool: {DATA.n_players}
        {/* TODO: link to the GitHub repo once it's published */}
      </footer>
    </>
  );
}
