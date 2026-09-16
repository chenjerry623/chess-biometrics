import { useMemo, useState } from "react";
import type { MoveEntry, NarratedGame } from "../types";
import { Chessboard } from "./Chessboard";
import { mergeCyclicalFeatures, reasonSentence, featureLabel } from "../lib/reasoning";

function groupByMoveNumber(moves: MoveEntry[]): Map<number, { white?: MoveEntry & { idx: number }; black?: MoveEntry & { idx: number } }> {
  const map = new Map<number, { white?: MoveEntry & { idx: number }; black?: MoveEntry & { idx: number } }>();
  moves.forEach((m, i) => {
    if (!map.has(m.move_number)) map.set(m.move_number, {});
    const entry = map.get(m.move_number)!;
    if (m.ply % 2 === 0) entry.white = { ...m, idx: i };
    else entry.black = { ...m, idx: i };
  });
  return map;
}

function MoveCell({ entry, active, onClick }: { entry?: MoveEntry & { idx: number }; active: boolean; onClick: (idx: number) => void }) {
  if (!entry) return <span />;
  if (entry.is_own) {
    const flag = entry.key_moment === "mistake" ? "⚠" : entry.key_moment === "long_think" ? "⏱" : "";
    return (
      <button className={`move-cell own${active ? " active" : ""}`} type="button" onClick={() => onClick(entry.idx)}>
        <span>{entry.san}</span>
        <span className="mono" style={{ fontSize: 10, color: entry.key_moment ? "var(--accent-strong)" : "var(--text-muted)" }}>
          {entry.think_time != null ? entry.think_time.toFixed(1) + "s" : ""} {flag}
        </span>
      </button>
    );
  }
  return (
    <button className={`move-cell opp${active ? " active" : ""}`} type="button" onClick={() => onClick(entry.idx)} title="opponent's move">
      {entry.san}
    </button>
  );
}

export function GameExplainer({ games }: { games: NarratedGame[] }) {
  const [narratedIdx, setNarratedIdx] = useState(0);
  const game = games[narratedIdx % games.length];

  const firstKeyIdx = useMemo(() => game.moves.findIndex((m) => m.key_moment), [game]);
  const [moveIdx, setMoveIdx] = useState(firstKeyIdx >= 0 ? firstKeyIdx : 0);

  function goToGame(delta: number) {
    const next = (narratedIdx + delta + games.length) % games.length;
    setNarratedIdx(next);
    const g = games[next];
    const fk = g.moves.findIndex((m) => m.key_moment);
    setMoveIdx(fk >= 0 ? fk : 0);
  }

  const move = game.moves[moveIdx];
  const isWhiteToMove = move.fen_before.split(" ")[1] === "w";
  const byNumber = useMemo(() => groupByMoveNumber(game.moves), [game]);

  const mergedReasons = useMemo(() => {
    const merged = mergeCyclicalFeatures(game.top_reasons || []);
    return [...merged].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));
  }, [game]);
  const maxAbs = Math.max(...mergedReasons.map((r) => Math.abs(r.contribution)), 1e-9);

  const top2 = mergedReasons.slice(0, 2).map((r) => featureLabel(r.feature).toLowerCase());
  const summary = game.correct
    ? <>Correctly landed on <b>{game.predicted_player}</b>, leaning on <b>{top2[0] || ""}</b>{top2[1] ? <> and <b>{top2[1]}</b></> : null}.</>
    : <>Guessed <b>{game.predicted_player}</b> — actually <b>{game.true_player}</b> — pulled by <b>{top2[0] || ""}</b>{top2[1] ? <> and <b>{top2[1]}</b></> : null}.</>;

  const keyMoments = game.moves.map((m, i) => ({ m, i })).filter((x) => x.m.key_moment);
  const gameLink = /^\d+$/.test(String(game.game_id)) ? `https://www.chess.com/game/live/${game.game_id}` : null;

  return (
    <div className="card">
      <h3>Why did it decide that?</h3>
      <p className="deck" style={{ marginBottom: 4 }}>
        Game {game.game_id}, {game.moves.length} moves. {summary}
        {gameLink && <> <a href={gameLink} target="_blank" rel="noopener noreferrer">View this exact game on Chess.com →</a></>}
      </p>

      <div className="board-layout" style={{ marginTop: 12 }}>
        <div style={{ width: "100%", maxWidth: 280, marginInline: "auto" }}>
          <Chessboard fen={move.fen_before} size={280} />
          <div className="deck" style={{ textAlign: "center", marginTop: 6 }}>
            move {move.move_number} · {isWhiteToMove ? "white" : "black"} to play
          </div>
          <div
            className="deck"
            style={{
              textAlign: "center", marginTop: 4, color: "var(--accent-strong)", fontWeight: 600, minHeight: 38,
              display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden",
            }}
          >
            {move.key_reason ? `${move.key_moment === "mistake" ? "⚠" : "⏱"} ${move.key_reason}` : ""}
          </div>
          <div style={{ display: "flex", gap: 6, justifyContent: "center", marginTop: 8 }}>
            <button className="btn" type="button" disabled={moveIdx <= 0} onClick={() => setMoveIdx((i) => Math.max(0, i - 1))}>←</button>
            <button className="btn" type="button" disabled={moveIdx >= game.moves.length - 1} onClick={() => setMoveIdx((i) => Math.min(game.moves.length - 1, i + 1))}>→</button>
          </div>
        </div>
        <div>
          {keyMoments.length > 0 && (
            <div className="deck" style={{ marginBottom: 6 }}>
              Key moments:{" "}
              {keyMoments.map((x) => (
                <button
                  key={x.i}
                  className={`tag${x.m.key_moment !== "mistake" ? " secondary" : ""}`}
                  title={x.m.key_reason || ""}
                  style={{ marginRight: 4, marginBottom: 4 }}
                  onClick={() => setMoveIdx(x.i)}
                  type="button"
                >
                  {x.m.san} {x.m.key_moment === "mistake" ? "⚠" : "⏱"}
                </button>
              ))}
            </div>
          )}
          <div style={{ maxHeight: 280, overflowY: "auto", display: "flex", flexDirection: "column", gap: 1, border: "1px solid var(--bezel)", borderRadius: 8, padding: 4 }}>
            {[...byNumber.entries()].sort((a, b) => a[0] - b[0]).map(([num, pair]) => (
              <div className="move-pair-row" key={num}>
                <span className="mono move-num">{num}.</span>
                <MoveCell entry={pair.white} active={pair.white?.idx === moveIdx} onClick={setMoveIdx} />
                <MoveCell entry={pair.black} active={pair.black?.idx === moveIdx} onClick={setMoveIdx} />
              </div>
            ))}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 2 }}>
        {mergedReasons.map((r, i) => {
          const w = (Math.abs(r.contribution) / maxAbs) * 100;
          const toward = r.contribution >= 0;
          const color = toward ? "var(--accent)" : "var(--secondary)";
          const { html, linkedMoveIdx } = reasonSentence(r, game.predicted_player, game.moves);
          return (
            <div key={i} style={{ display: "flex", flexDirection: "column", gap: 4, paddingBlock: 6, borderBottom: "1px dashed var(--line)" }}>
              <div className="bar-track" style={{ height: 5 }}>
                <div className="bar-fill" style={{ width: `${w}%`, background: color }} />
              </div>
              <div
                style={{ fontSize: 12.5, lineHeight: 1.5, cursor: linkedMoveIdx != null ? "pointer" : "default" }}
                dangerouslySetInnerHTML={{ __html: html }}
                onClick={() => { if (linkedMoveIdx != null) setMoveIdx(linkedMoveIdx); }}
              />
            </div>
          );
        })}
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button className="btn primary" type="button" onClick={() => goToGame(1)}>Another game →</button>
      </div>
    </div>
  );
}
