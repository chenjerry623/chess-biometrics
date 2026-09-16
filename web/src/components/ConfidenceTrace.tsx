import type { ConfidenceTrace as ConfidenceTraceData } from "../types";

export function ConfidenceTrace({ trace }: { trace: ConfidenceTraceData }) {
  const points = trace.trace;
  const W = 640, H = 240, padL = 42, padR = 16, padT = 16, padB = 34;
  const innerW = W - padL - padR, innerH = H - padT - padB;
  const n = points.length;

  const x = (i: number) => padL + (i / (n - 1)) * innerW;
  const y = (v: number) => padT + (1 - v) * innerH;

  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(p.running_prob_correct)}`).join(" ");
  const flipIdx = points.findIndex((p) => p.correct_so_far);
  const last = points[points.length - 1];
  const first = points[0];
  const link = (id: string) => `https://www.chess.com/game/live/${id}`;

  return (
    <div className="card">
      <h3>Watching it figure out one real player</h3>
      <p className="deck" style={{ marginBottom: 10 }}>
        Same method as the accuracy curve above, traced through one real account's actual held-out games in order —
        not a new claim, just the existing one made concrete. After game 1 alone, the top guess is
        {" "}{first.correct_so_far ? <><b> already right</b></> : <><b> wrong</b> ({first.top_guess})</>}. By game{" "}
        {last.n_games}, it's {last.correct_so_far ? "landed on the right person" : "still not there"} at{" "}
        {(last.running_prob_correct * 100).toFixed(0)}% confidence.
      </p>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: W }}>
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line x1={padL} y1={padT + (1 - f) * innerH} x2={W - padR} y2={padT + (1 - f) * innerH} stroke="var(--line)" strokeWidth={1} />
            <text x={padL - 6} y={padT + (1 - f) * innerH + 3} textAnchor="end" fontSize={10} fill="var(--text-muted)" fontFamily="IBM Plex Mono, monospace">
              {(f * 100).toFixed(0)}%
            </text>
          </g>
        ))}
        {flipIdx >= 0 && (
          <line x1={x(flipIdx)} y1={padT} x2={x(flipIdx)} y2={H - padB} stroke="var(--good)" strokeDasharray="3 3" opacity={0.6} />
        )}
        <path d={linePath} fill="none" stroke="var(--accent)" strokeWidth={2.5} />
        {points.map((p, i) => (
          <circle key={p.game_id} cx={x(i)} cy={y(p.running_prob_correct)} r={i === n - 1 ? 4.5 : 2} fill={p.correct_so_far ? "var(--good)" : "var(--accent)"} />
        ))}
        <text x={padL} y={H - 8} fontSize={10.5} fill="var(--text-muted)" fontFamily="IBM Plex Mono, monospace">game 1</text>
        <text x={W - padR} y={H - 8} textAnchor="end" fontSize={10.5} fill="var(--text-muted)" fontFamily="IBM Plex Mono, monospace">game {n}</text>
      </svg>
      <p className="deck" style={{ marginTop: 8 }}>
        Player: <b>{trace.player}</b> ·{" "}
        <a href={link(first.game_id)} target="_blank" rel="noopener noreferrer">first game</a> ·{" "}
        <a href={link(last.game_id)} target="_blank" rel="noopener noreferrer">last game</a> — both real, both checkable.
      </p>
    </div>
  );
}
