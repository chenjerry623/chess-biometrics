import { useState } from "react";
import { Link } from "react-router-dom";
import playersData from "./data/players.json";

interface PlayerRow {
  username: string;
  current_rating: number | null;
  n_games: number | null;
  date_min: string | null;
  date_max: string | null;
  recall: number | null;
}

const PLAYERS = (playersData as { players: PlayerRow[] }).players;

function pct(v: number | null, digits = 0): string {
  if (v == null) return "—";
  return (v * 100).toFixed(digits) + "%";
}

export function PlayersPage() {
  const [q, setQ] = useState("");
  const filtered = PLAYERS.filter((p) => p.username.toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="wrap" style={{ maxWidth: 720 }}>
      <div style={{ padding: "24px 0 16px" }}>
        <Link to="/" style={{ fontSize: 12.5, fontFamily: "IBM Plex Mono, monospace", color: "var(--text-muted)" }}>
          ← back
        </Link>
        <h2 style={{ fontSize: 22, marginTop: 10 }}>Players in the pool</h2>
        <p className="deck" style={{ marginTop: 6 }}>
          Bullet games only, one row per account. Recall is how often the identification model correctly names this
          player when it's actually them. One account here (jerrycdzn) shares an identity with another
          (cdznjerry) for identification purposes, so it has no recall of its own.
        </p>
      </div>
      <input
        className="search-input"
        type="text"
        placeholder="Search players…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <div style={{ marginTop: 12, marginBottom: 40 }}>
        <div className="players-row players-head">
          <span>Player</span>
          <span>Rating</span>
          <span>Games</span>
          <span>Recall</span>
        </div>
        {filtered.map((p) => (
          <Link className="players-row players-row-link" key={p.username} to={`/players/${p.username}`}>
            <span>{p.username}</span>
            <span className="mono">{p.current_rating ? Math.round(p.current_rating) : "—"}</span>
            <span className="mono">{p.n_games ?? "—"}</span>
            <span className="mono">{pct(p.recall)}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
