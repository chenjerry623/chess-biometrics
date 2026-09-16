import type { Cohort } from "../types";

function pct(v: number, digits = 0): string {
  return (v * 100).toFixed(digits) + "%";
}

export function MetricsOverview({ cohort }: { cohort: Cohort }) {
  const p = cohort.correctness_patterns;
  const session = p?.session_position_buckets;
  const sessionKeys = session ? Object.keys(session) : [];
  const earliest = sessionKeys[0] ? session![sessionKeys[0]] : null;
  const latest = sessionKeys[sessionKeys.length - 1] ? session![sessionKeys[sessionKeys.length - 1]] : null;

  return (
    <div className="card">
      <h3>What we actually measured</h3>
      <p className="deck" style={{ marginBottom: 14 }}>
        Balanced accuracy is the headline number, but it's not the only thing worth checking. A few others:
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div>
          <div className="stat">
            <span className="k">Is the right answer even in the top few guesses?</span>
            <span style={{ fontSize: 13 }}>
              Top-1: <b>{pct(cohort.rank_k_accuracy[0]?.overall_accuracy)}</b> · Top-3:{" "}
              <b>{pct(cohort.rank_k_accuracy[1]?.overall_accuracy)}</b> · Top-5:{" "}
              <b>{pct(cohort.rank_k_accuracy[2]?.overall_accuracy)}</b> · Top-10:{" "}
              <b>{pct(cohort.rank_k_accuracy[3]?.overall_accuracy)}</b>
            </span>
          </div>
        </div>

        <div>
          <div className="stat">
            <span className="k">How does it hold up per player, not just on average?</span>
            <span style={{ fontSize: 13 }}>
              Some players are much easier to spot than others — recall ranges from{" "}
              <b>{pct(cohort.per_class_recall_summary.min)}</b> to{" "}
              <b>{pct(cohort.per_class_recall_summary.max)}</b> across the pool, median{" "}
              <b>{pct(cohort.per_class_recall_summary.median)}</b>.
            </span>
          </div>
        </div>

        {cohort.most_confused_pair && (
          <div>
            <div className="stat">
              <span className="k">Who does it mix up the most?</span>
              <span style={{ fontSize: 13 }}>
                <b>{cohort.most_confused_pair[0]}</b> gets mistaken for <b>{cohort.most_confused_pair[1]}</b>{" "}
                {pct(cohort.most_confused_rate)} of the time — the single most confused pair in the pool.
              </span>
            </div>
          </div>
        )}

        {earliest && latest && (
          <div>
            <div className="stat">
              <span className="k">Does it matter how far into a session someone is?</span>
              <span style={{ fontSize: 13 }}>
                Yes, a lot — <b>{pct(earliest.accuracy)}</b> accuracy on someone's first couple games of a session
                vs. <b>{pct(latest.accuracy)}</b> once they're well into one. Makes sense: more games in, more
                rhythm to read.
              </span>
            </div>
          </div>
        )}

        {p && (
          <div>
            <div className="stat">
              <span className="k">Does winning or losing the last game change anything?</span>
              <span style={{ fontSize: 13 }}>
                Basically not — accuracy sits within a point of {pct(p.tilt_buckets["won previous game"]?.accuracy ?? 0)}{" "}
                whether they just won, lost, or drew. We checked, and it just isn't a signal here.
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
