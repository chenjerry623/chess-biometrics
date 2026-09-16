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
