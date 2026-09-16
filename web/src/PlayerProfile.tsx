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
  bucket_clean: number | null;
  bucket_overthought: number | null;
  bucket_underthought: number | null;
  bucket_panic: number | null;
  bucket_instinctive: number | null;
  sacrifice_rate: number | null;
  maia_top_choice_rate: number | null;
  maia_mean_prob_played: number | null;
  low_time_share: number | null;
  panic_rate_low_time: number | null;
  panic_rate_normal_time: number | null;
}

const PLAYERS = (playersData as { players: PlayerRow[] }).players;

function pct(v: number | null | undefined, digits = 0): string {
  if (v == null) return "—";
  return (v * 100).toFixed(digits) + "%";
}

function secs(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toFixed(1) + "s";
}

const BUCKETS: { key: keyof PlayerRow; label: string; color: string }[] = [
  { key: "bucket_clean", label: "Clean", color: "var(--good)" },
  { key: "bucket_instinctive", label: "Instinctive", color: "var(--accent)" },
  { key: "bucket_overthought", label: "Overthought", color: "var(--secondary)" },
  { key: "bucket_underthought", label: "Underthought", color: "#d99a3f" },
  { key: "bucket_panic", label: "Panic", color: "#c23b6a" },
];

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

      {p.bucket_clean != null && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3 style={{ fontSize: 15, marginBottom: 4 }}>How their moves break down</h3>
          <p className="deck" style={{ marginBottom: 10, fontSize: 12 }}>
            Whether time spent matched how much a position actually demanded.
          </p>
          <div className="bar-track" style={{ height: 14, display: "flex", overflow: "hidden" }}>
            {BUCKETS.map((b) => {
              const v = (p[b.key] as number | null) ?? 0;
              return v > 0 ? <div key={b.key} style={{ width: `${v * 100}%`, background: b.color }} title={`${b.label}: ${pct(v)}`} /> : null;
            })}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 14px", marginTop: 10 }}>
            {BUCKETS.map((b) => (
              <span key={b.key} style={{ fontSize: 11.5, display: "flex", alignItems: "center", gap: 5 }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, background: b.color, display: "inline-block" }} />
                {b.label} {pct(p[b.key] as number | null)}
              </span>
            ))}
          </div>
          <div className="footnote" style={{ marginTop: 14, marginBottom: 0 }}>
            Every move gets sorted by two things: did they spend more or less time than expected for a position that
            hard, and was the position actually critical (a real decision, not a forced or near-forced move).
            <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 5 }}>
              <div><b>Clean:</b> time spent roughly matched what the position called for.</div>
              <div><b>Instinctive:</b> quick, critical, and still the right move. Found it on pattern alone.</div>
              <div><b>Overthought:</b> took extra time on a position that didn't really need it, but got it right anyway.</div>
              <div><b>Underthought:</b> rushed through a genuinely critical position and got it wrong.</div>
              <div><b>Panic:</b> spent extra time and still got it wrong. The extra time didn't help.</div>
            </div>
          </div>
        </div>
      )}

      <div className="grid duo" style={{ marginBottom: 16 }}>
        <div className="stat">
          <span className="k">Plays the "obvious" move</span>
          <span className="v mono">{pct(p.maia_top_choice_rate)}</span>
        </div>
        <div className="stat">
          <span className="k">Sacrifices material</span>
          <span className="v mono">{pct(p.sacrifice_rate)}</span>
        </div>
      </div>

      {p.low_time_share != null && (
        <p className="footnote" style={{ marginBottom: 24 }}>
          <b>Under time pressure:</b> {pct(p.low_time_share)} of their moves were played with the clock running low.
          Panic-bucket rate in that state: {pct(p.panic_rate_low_time)}, versus {pct(p.panic_rate_normal_time)} with
          normal time.
        </p>
      )}

      <p className="footnote" style={{ marginBottom: 40 }}>
        <b>Premove rate:</b> {pct(p.premove_rate)} of their moves were played instantly, with no real think time, a
        rough proxy for how often they pre-plan a move before it's their turn.
      </p>
    </div>
  );
}
