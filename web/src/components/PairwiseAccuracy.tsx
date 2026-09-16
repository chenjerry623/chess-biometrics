import { useState } from "react";
import type { PairwiseResult, PairwiseSummary } from "../types";

function pct(v: number, digits = 0): string {
  return (v * 100).toFixed(digits) + "%";
}

function PairRow({ p, w, color }: { p: PairwiseResult; w: number; color: string }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 44px", alignItems: "center", gap: 10 }}>
      <div>
        <div style={{ fontSize: 12.5, marginBottom: 3 }}>
          <b>{p.pair[0]}</b> vs <b>{p.pair[1]}</b>
        </div>
        <div className="bar-track" style={{ height: 6 }}>
          <div className="bar-fill" style={{ width: `${Math.max(4, w)}%`, background: color }} />
        </div>
      </div>
      <span className="mono" style={{ fontSize: 12.5, textAlign: "right" }}>{pct(p.balanced_accuracy)}</span>
    </div>
  );
}

function sectionLabel(text: string) {
  return (
    <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)", fontFamily: "IBM Plex Mono, monospace", marginBottom: 8 }}>
      {text}
    </div>
  );
}

export function PairwiseAccuracy({ data }: { data: PairwiseSummary }) {
  const [expanded, setExpanded] = useState(false);
  const sorted = [...data.pairs].sort((a, b) => b.balanced_accuracy - a.balanced_accuracy);
  const max = sorted[0].balanced_accuracy;
  const min = sorted[sorted.length - 1].balanced_accuracy;
  const width = (p: PairwiseResult) => ((p.balanced_accuracy - min) / (max - min)) * 100;
  const hidden = data.n_pairs - 10;

  if (expanded) {
    return (
      <div className="card">
        <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 440, overflowY: "auto", paddingRight: 4 }}>
          {sorted.map((p) => (
            <PairRow key={p.pair.join("-")} p={p} w={width(p)} color={p.balanced_accuracy >= data.median ? "var(--good)" : "var(--secondary)"} />
          ))}
        </div>
        <button type="button" className="btn" style={{ marginTop: 12 }} onClick={() => setExpanded(false)}>
          Show fewer
        </button>
      </div>
    );
  }

  return (
    <div className="card">
      {sectionLabel("Easiest to tell apart")}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {sorted.slice(0, 5).map((p) => (
          <PairRow key={p.pair.join("-")} p={p} w={width(p)} color="var(--good)" />
        ))}
      </div>
      {hidden > 0 && (
        <button type="button" className="btn" style={{ width: "100%", marginTop: 10, marginBottom: 10 }} onClick={() => setExpanded(true)}>
          Show all {data.n_pairs} pairs ({hidden} more, {pct(data.median, 0)} median)
        </button>
      )}
      {sectionLabel("Hardest to tell apart")}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {sorted.slice(-5).map((p) => (
          <PairRow key={p.pair.join("-")} p={p} w={width(p)} color="var(--secondary)" />
        ))}
      </div>
    </div>
  );
}
