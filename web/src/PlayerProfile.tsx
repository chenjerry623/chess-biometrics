import { Link, useParams } from "react-router-dom";
import playersData from "./data/players.json";

interface PlayerRow {
  username: string;
  current_rating: number | null;
  n_games: number | null;
  date_min: string | null;
  date_max: string | null;
  recall: number | null;
  premove_rate: number | null;
  opening_think: number | null;
  middlegame_think: number | null;
  endgame_think: number | null;
  opening_share: number | null;
  middlegame_share: number | null;
  endgame_share: number | null;
}

const PLAYERS = (playersData as { players: PlayerRow[] }).players;

function pct(v: number | null, digits = 0): string {
  if (v == null) return "—";
  return (v * 100).toFixed(digits) + "%";
}

function secs(v: number | null): string {
  if (v == null) return "—";
  return v.toFixed(1) + "s";
}

export function PlayerProfile() {
  const { username } = useParams();
  const p = PLAYERS.find((x) => x.username === username);

  if (!p) {
    return (
      <div className="wrap" style={{ maxWidth: 560, padding: "24px 0" }}>
        <Link to="/players" style={{ fontSize: 12.5, fontFamily: "IBM Plex Mono, monospace", color: "var(--text-muted)" }}>← back to player list</Link>
        <p style={{ marginTop: 16 }}>No player found for "{username}".</p>
      </div>
    );
  }

  return (
    <div className="wrap" style={{ maxWidth: 560 }}>
      <div style={{ padding: "24px 0 16px" }}>
        <Link to="/players" style={{ fontSize: 12.5, fontFamily: "IBM Plex Mono, monospace", color: "var(--text-muted)" }}>← back to player list</Link>
        <h2 style={{ fontSize: 26, marginTop: 10 }}>{p.username}</h2>
        <p className="deck" style={{ marginTop: 4 }}>
          Bullet games only, {p.date_min} to {p.date_max}.
        </p>
      </div>

      <div className="grid tri" style={{ marginBottom: 20 }}>
        <div className="stat">
          <span className="k">Rating</span>
          <span className="v mono">{p.current_rating != null ? Math.round(p.current_rating) : "—"}</span>
        </div>
        <div className="stat">
          <span className="k">Games</span>
          <span className="v mono">{p.n_games ?? "—"}</span>
        </div>
        <div className="stat">
          <span className="k">Recall</span>
          <span className="v mono">{pct(p.recall)}</span>
        </div>
      </div>

      <p className="footnote" style={{ marginBottom: 24 }}>
        <b>Recall:</b> out of every held-out game that was actually {p.username}'s, the share the identification
        model correctly named as {p.username}. Higher means their habits are more distinctive within the pool.
      </p>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3 style={{ fontSize: 15, marginBottom: 10 }}>How they use the clock</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="players-row" style={{ gridTemplateColumns: "1fr 70px 70px" }}>
            <span></span><span className="mono" style={{ color: "var(--text-muted)", fontSize: 11 }}>think time</span><span className="mono" style={{ color: "var(--text-muted)", fontSize: 11 }}>of moves</span>
          </div>
          <div className="players-row" style={{ gridTemplateColumns: "1fr 70px 70px" }}>
            <span>Opening</span><span className="mono">{secs(p.opening_think)}</span><span className="mono">{pct(p.opening_share)}</span>
          </div>
          <div className="players-row" style={{ gridTemplateColumns: "1fr 70px 70px" }}>
            <span>Middlegame</span><span className="mono">{secs(p.middlegame_think)}</span><span className="mono">{pct(p.middlegame_share)}</span>
          </div>
          <div className="players-row" style={{ gridTemplateColumns: "1fr 70px 70px", borderBottom: "none" }}>
            <span>Endgame</span><span className="mono">{secs(p.endgame_think)}</span><span className="mono">{pct(p.endgame_share)}</span>
          </div>
        </div>
      </div>

      <p className="footnote" style={{ marginBottom: 40 }}>
        <b>Premove rate:</b> {pct(p.premove_rate)} of their moves were played instantly, with no real think time, a
        rough proxy for how often they pre-plan a move before it's their turn.
      </p>
    </div>
  );
}
