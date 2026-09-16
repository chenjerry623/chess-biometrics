import { useState } from "react";
import data from "./data/identification.json";
import type { IdentificationData } from "./types";
import { Hero } from "./components/Hero";
import { ModelComparison } from "./components/ModelComparison";
import { AccuracyCurve } from "./components/AccuracyCurve";
import { GameExplainer } from "./components/GameExplainer";
import { MetricsOverview } from "./components/MetricsOverview";
import { ConfidenceTrace } from "./components/ConfidenceTrace";
import { PairwiseAccuracy } from "./components/PairwiseAccuracy";

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

        {DATA.pairwise && (
          <section>
            <div className="sec-head">
              <div className="eyebrow">How general is this?</div>
              <h2>The simplest case: just two players</h2>
              <p>
                Telling {cohort.class_counts_n} players apart is the hard version. How well does this work for the
                simplest possible case — just picking between two specific people? Run once per pair, so this is a
                real spread across many pairs, not one result.
              </p>
            </div>
            <PairwiseAccuracy data={DATA.pairwise} />
          </section>
        )}

        {DATA.confidence_trace && (
          <section>
            <div className="sec-head">
              <div className="eyebrow">Proof, not just a number</div>
              <h2>One game is a guess. Twenty is a pattern.</h2>
              <p>
                The accuracy curve above is an average over the whole pool. Here's what it actually looks like for
                one real account, game by game — every point is a real Chess.com game you can go check.
              </p>
            </div>
            <ConfidenceTrace trace={DATA.confidence_trace} />
          </section>
        )}

        <section>
          <div className="sec-head">
            <div className="eyebrow">Under the hood</div>
            <h2>What we actually checked</h2>
            <p>Balanced accuracy is one number. Here's the rest of what came out of evaluating this seriously.</p>
          </div>
          <MetricsOverview cohort={cohort} />
        </section>

        <section>
          <div className="sec-head">
            <div className="eyebrow">Explainability</div>
            <h2>Look inside a prediction</h2>
            <p>
              Held-out games with the model's actual reasoning attached, picked at random — including the ones it
              gets wrong. A single game is the hardest version of this problem by design (see the accuracy curve
              above); the point here is showing real reasoning, not just the wins.
            </p>
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
