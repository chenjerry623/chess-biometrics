import type { Cohort } from "../types";

const LABELS: Record<string, string> = {
  logistic_regression: "Logistic regression",
  gradient_boosting: "Gradient boosting",
  lightgbm: "LightGBM",
};

export function ModelComparison({ cohort }: { cohort: Cohort }) {
  const entries = Object.entries(cohort.model_comparison).filter(([, v]) => v !== undefined) as [string, number][];
  const max = Math.max(...entries.map(([, v]) => v));

  return (
    <div className="card">
      <h3>Model comparison</h3>
      <p className="deck" style={{ marginBottom: 12 }}>
        All candidates are cross-validated every run — the best on held-out balanced accuracy wins, never assumed in
        advance.
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {entries.map(([key, v]) => {
          const isWinner = key === cohort.model_used;
          return (
            <div key={key} style={{ display: "grid", gridTemplateColumns: "150px 1fr 60px", alignItems: "center", gap: 10 }}>
              <span className="mono" style={{ fontSize: 12.5, color: isWinner ? "var(--accent-strong)" : "var(--text-muted)", fontWeight: isWinner ? 700 : 400 }}>
                {LABELS[key] || key}{isWinner ? " ✓" : ""}
              </span>
              <div className="bar-track" style={{ height: 8 }}>
                <div className="bar-fill" style={{ width: `${(v / max) * 100}%`, background: isWinner ? "var(--accent)" : "var(--secondary)" }} />
              </div>
              <span className="mono" style={{ fontSize: 12.5, textAlign: "right" }}>{(v * 100).toFixed(1)}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
