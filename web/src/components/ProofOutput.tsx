import type { Cohort } from "../types";

export function ProofOutput({ cohort }: { cohort: Cohort }) {
  const correct = cohort.sample_games.filter((g) => g.correct).sort((a, b) => a.confidence - b.confidence);
  const g = correct[Math.floor(correct.length / 2)]; // a representative example, not the cherry-picked best
  if (!g) return null;

  const topCandidates = Object.entries(g.probabilities)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  const json = JSON.stringify(
    {
      game_id: g.game_id,
      true_player: g.true_player,
      predicted_player: g.predicted_player,
      correct: g.correct,
      top_candidates: Object.fromEntries(topCandidates.map(([p, v]) => [p, Number(v.toFixed(4))])),
      top_reasons: g.top_reasons.slice(0, 3).map((r) => ({ feature: r.feature, contribution: Number(r.contribution.toFixed(3)) })),
    },
    null,
    2
  );

  const link = /^\d+$/.test(String(g.game_id)) ? `https://www.chess.com/game/live/${g.game_id}` : null;

  return (
    <div className="card">
      <pre
        className="mono"
        style={{
          background: "var(--surface-2)", borderRadius: 8, padding: "14px 16px", fontSize: 12.5,
          overflowX: "auto", boxShadow: "var(--inset)", margin: 0,
        }}
      >
        {json}
      </pre>
      <p className="deck" style={{ marginTop: 10 }}>
        A median-confidence example, not the best one available — this is what the model actually outputs, not a
        polished summary of it.{" "}
        {link && <a href={link} target="_blank" rel="noopener noreferrer">Check the real game →</a>}
      </p>
    </div>
  );
}
