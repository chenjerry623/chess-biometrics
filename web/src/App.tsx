import { useState } from "react";
import { Link } from "react-router-dom";
import data from "./data/identification.json";
import type { IdentificationData } from "./types";
import { Hero } from "./components/Hero";
import { ModelComparison } from "./components/ModelComparison";
import { AccuracyCurve } from "./components/AccuracyCurve";
import { GameExplainer } from "./components/GameExplainer";
import { PairwiseAccuracy } from "./components/PairwiseAccuracy";
import { ProofOutput } from "./components/ProofOutput";

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
              ? "Clock allocation, pace, and session timing only. The model never sees what was actually played."
              : "Adds move-quality signals on top of the timing data."}
          </span>
        </div>

        <section>
          <div className="sec-head">
            <div className="eyebrow">01</div>
            <h2>Gets it right {pct(cohort.balanced_accuracy)} of the time, from one game</h2>
            <p>
              Random guessing among {cohort.class_counts_n} players would land {pct(cohort.chance_baseline, 1)}.
              This is measured across {cohort.n_games_total.toLocaleString()} real games.
            </p>
          </div>
          <div className="grid duo">
            <ModelComparison cohort={cohort} />
            <AccuracyCurve cohort={cohort} />
          </div>
          <p className="footnote">
            <b>Balanced accuracy:</b> the average of each player's own recall (their correct guesses divided by
            their total games). This keeps a player with a thousand games from outweighing one with fifty.
          </p>
          <p className="footnote">
            <b>The pool:</b> {DATA.n_players} players, mostly a rating-diverse random sample pulled from a large
            Chess.com club across seven bullet rating bands, from beginner to 2300+. A handful of well-known titled
            players and streamers (Hikaru, Magnus Carlsen, Fabiano Caruana, and others) were added for variety.
          </p>
        </section>

        {DATA.pairwise && (
          <section>
            <div className="sec-head">
              <div className="eyebrow">02</div>
              <h2>How accurately can we predict a game between 2 possible players?</h2>
              <p>
                Median {pct(DATA.pairwise.median, 0)} across {DATA.pairwise.n_pairs} random pairs from the pool,
                each with its own model.
              </p>
            </div>
            <PairwiseAccuracy data={DATA.pairwise} />
          </section>
        )}

        <section>
          <div className="sec-head">
            <div className="eyebrow">03</div>
            <h2>Look inside a prediction</h2>
            <p>
              Correct predictions from the held-out set, as many as we have. Click through the moves to see what the
              model noticed.
            </p>
          </div>
          <GameExplainer games={DATA.narrated_games} />
        </section>

        <section>
          <div className="sec-head">
            <div className="eyebrow">04</div>
            <h2>What the model actually returns</h2>
            <p>The raw prediction for one real held-out game.</p>
          </div>
          <ProofOutput cohort={cohort} />
        </section>

        <section style={{ borderBottom: "none" }}>
          <div className="card github-card">
            <div>
              <h3 style={{ fontSize: 20, marginBottom: 6 }}>See the full project on GitHub</h3>
              <p className="deck" style={{ fontSize: 13.5 }}>
                Everything here comes from a real, open pipeline: fetching games from Chess.com, Stockfish and Maia
                engine annotation, feature engineering, the model code itself, and this dashboard. Read the code,
                check the methodology, or run it yourself.
              </p>
            </div>
            <a
              className="btn primary"
              href="https://github.com/chenjerry623/chess-biometrics"
              target="_blank"
              rel="noopener noreferrer"
              style={{ whiteSpace: "nowrap" }}
            >
              View on GitHub →
            </a>
          </div>
        </section>
      </div>

      <footer>
        chess-clock · a personal clock-management analyzer · {DATA.n_players} players in the pool ·{" "}
        <Link to="/players" className="subtle-link">player list</Link>
      </footer>
    </>
  );
}
