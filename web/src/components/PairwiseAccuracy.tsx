import type { PairwiseSummary } from "../types";

function pct(v: number, digits = 0): string {
  return (v * 100).toFixed(digits) + "%";
}

export function PairwiseAccuracy({ data }: { data: PairwiseSummary }) {
  const sorted = [...data.pairs].sort((a, b) => a.balanced_accuracy - b.balanced_accuracy);
  const hardest = sorted[0];
  const easiest = sorted[sorted.length - 1];

  return (
    <div className="card">
      <h3>Not just one pair — {data.n_pairs} of them</h3>
      <p className="deck" style={{ marginBottom: 14 }}>
        The 2-player number elsewhere on this page is one specific pair. Here it is redone {data.n_pairs} times
        with random pairs from the full pool, each with its own cross-validated model — so this is a distribution,
        not a single result that might just be an easy pair.
      </p>
      <div className="grid duo" style={{ marginBottom: 14 }}>
        <div className="stat">
          <span className="k">Median accuracy, any random pair</span>
          <span className="v mono">{pct(data.median)}</span>
        </div>
        <div className="stat">
          <span className="k">Range across {data.n_pairs} pairs</span>
          <span className="v mono">{pct(data.min)}–{pct(data.max)}</span>
        </div>
      </div>
      <div className="strength-strip" style={{ height: 70 }}>
        {sorted.map((p) => {
          const h = Math.max(2, ((p.balanced_accuracy - 0.5) / 0.5) * 100);
          const color = p.balanced_accuracy >= 0.85 ? "var(--good)" : p.balanced_accuracy >= 0.65 ? "var(--accent)" : "var(--secondary)";
          return (
            <div
              key={p.pair.join("-")}
              className="strength-bar"
              title={`${p.pair[0]} vs ${p.pair[1]}: ${pct(p.balanced_accuracy)}`}
              style={{ height: `${h}%`, background: color }}
            />
          );
        })}
      </div>
      <div className="strength-scale" style={{ marginTop: 6 }}>
        <span>hardest pair to tell apart</span>
        <span>50% (coin flip)</span>
        <span>easiest pair</span>
      </div>
      <p className="deck" style={{ marginTop: 12 }}>
        Hardest: <b>{hardest.pair[0]}</b> vs <b>{hardest.pair[1]}</b> ({pct(hardest.balanced_accuracy)}) · Easiest:{" "}
        <b>{easiest.pair[0]}</b> vs <b>{easiest.pair[1]}</b> ({pct(easiest.balanced_accuracy)})
      </p>
    </div>
  );
}
