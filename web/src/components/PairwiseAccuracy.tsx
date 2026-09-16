import type { PairwiseSummary } from "../types";

function pct(v: number, digits = 0): string {
  return (v * 100).toFixed(digits) + "%";
}

export function PairwiseAccuracy({ data }: { data: PairwiseSummary }) {
  const sorted = [...data.pairs].sort((a, b) => b.balanced_accuracy - a.balanced_accuracy);
  const shown = [...sorted.slice(0, 5), ...sorted.slice(-5)];
  const max = sorted[0].balanced_accuracy;
  const min = sorted[sorted.length - 1].balanced_accuracy;

  return (
    <div className="card">
      <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)", fontFamily: "IBM Plex Mono, monospace", marginBottom: 8 }}>
        Easiest to tell apart
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {shown.map((p, i) => {
          const w = ((p.balanced_accuracy - min) / (max - min)) * 100;
          const isEasiest = i < 5;
          return (
            <div key={p.pair.join("-")}>
              {i === 5 && (
                <>
                  <div style={{ fontSize: 11.5, color: "var(--text-muted)", padding: "4px 0" }}>
                    {data.n_pairs - 10} more pairs in between · {pct(data.median, 0)} median
                  </div>
                  <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)", fontFamily: "IBM Plex Mono, monospace", margin: "6px 0 8px" }}>
                    Hardest to tell apart
                  </div>
                </>
              )}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 44px", alignItems: "center", gap: 10 }}>
                <div>
                  <div style={{ fontSize: 12.5, marginBottom: 3 }}>
                    <b>{p.pair[0]}</b> vs <b>{p.pair[1]}</b>
                  </div>
                  <div className="bar-track" style={{ height: 6 }}>
                    <div className="bar-fill" style={{ width: `${Math.max(4, w)}%`, background: isEasiest ? "var(--good)" : "var(--secondary)" }} />
                  </div>
                </div>
                <span className="mono" style={{ fontSize: 12.5, textAlign: "right" }}>{pct(p.balanced_accuracy)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
