import type { MoveEntry, TopReason } from "../types";

export const FEATURE_LABEL: Record<string, string> = {
  think_time_mean: "Average time per move", think_time_std: "How much their pace varies",
  cp_loss_mean: "Move accuracy", cp_loss_std: "Consistency of move accuracy",
  maia_surprise_mean: "How often they play unexpected moves", maia_surprise_std: "Swings in move predictability",
  maia_prob_played_mean: 'How "typical" their moves are for their strength', maia_prob_played_std: "Consistency of move typicality",
  n_captures_available_mean: "Average tactical complexity faced", n_captures_available_std: "Swings in tactical complexity",
  n_captures_available_trend: "Tactical complexity, rising or falling in-game",
  low_time: "How often they play under time pressure", is_sacrifice: "How often they sacrifice material",
  own_skewers: "How often they set up skewers", opp_skewers: "How often they face skewers",
  think_time_trend: "Speeding up or slowing down over a game", cp_loss_trend: "Accuracy improving or fading over a game",
  maia_surprise_trend: "Moves getting more/less surprising over a game", maia_prob_played_trend: "Move typicality shifting over a game",
  opening_time_share: "Share of time spent in the opening", middlegame_time_share: "Share of time spent in the middlegame",
  endgame_time_share: "Share of time spent in the endgame", session_game_index: "How far into a playing session",
  log_minutes_since_prev_game: "Time since their last game", low_time_think_ratio: "How they use time when the clock is low",
  rhythm_autocorr: "How steady their move-to-move rhythm is", tempo_match_corr: "How much they mirror their opponent's pace",
  post_mistake_think_delta: "How they react after a mistake", prev_game_result: "Whether they just won or lost",
  n_plies: "Game length", hour: "Time of day they tend to play", dow: "Day of week they tend to play",
};

export function featureLabel(name: string): string {
  return FEATURE_LABEL[name] || name;
}

const FEATURE_UNIT: Record<string, "s" | "%"> = {
  think_time_mean: "s", think_time_std: "s", think_time_trend: "s",
  low_time_think_ratio: "s", post_mistake_think_delta: "s",
  maia_prob_played_mean: "%", maia_prob_played_std: "%", maia_prob_played_trend: "%",
  low_time: "%", is_sacrifice: "%", own_skewers: "%", opp_skewers: "%",
  opening_time_share: "%", middlegame_time_share: "%", endgame_time_share: "%",
};

export function formatFeatureValue(feature: string, value: number | null | undefined): string {
  if (value == null || !isFinite(value)) return "—";
  if (feature === "session_game_index") return `game #${Math.round(value)} of that session`;
  if (feature === "n_plies") return `${Math.round(value)} moves`;
  if (feature === "prev_game_result") {
    return value > 0.5 ? "won their previous game" : value < -0.5 ? "lost their previous game" : "drew or it was their first game";
  }
  const unit = FEATURE_UNIT[feature];
  if (unit === "%") return (value * 100).toFixed(0) + "%";
  if (unit === "s") return value.toFixed(1) + "s";
  return value.toFixed(2);
}

export function describeVsTypical(value: number | null | undefined, mean: number | null | undefined, std: number | null | undefined): string {
  if (mean == null || std == null || std <= 0 || value == null) return "";
  const z = (value - mean) / std;
  if (z > 1.2) return "much higher than the typical player";
  if (z > 0.4) return "somewhat higher than the typical player";
  if (z < -1.2) return "much lower than the typical player";
  if (z < -0.4) return "somewhat lower than the typical player";
  return "close to the typical player's own value";
}

/** Merges hour_sin/hour_cos and dow_sin/dow_cos pairs into one signed
 * "hour"/"dow" entry (a cyclical feature split across two raw model
 * inputs reads as two confusing bars otherwise) — signed sum preserves
 * direction, unlike a quadrature magnitude which would discard it. */
export function mergeCyclicalFeatures(reasons: TopReason[]): TopReason[] {
  const out: TopReason[] = [];
  const seen = new Set<string>();
  for (const r of reasons) {
    let base = r.feature;
    let merged = false;
    if (base === "hour_sin" || base === "hour_cos") { base = "hour"; merged = true; }
    if (base === "dow_sin" || base === "dow_cos") { base = "dow"; merged = true; }
    if (!merged) { out.push(r); continue; }
    if (seen.has(base)) continue;
    seen.add(base);
    const pair = reasons.filter((x) =>
      base === "hour" ? x.feature === "hour_sin" || x.feature === "hour_cos" : x.feature === "dow_sin" || x.feature === "dow_cos"
    );
    const contribution = pair.reduce((s, x) => s + x.contribution, 0);
    out.push({ feature: base, contribution, value: NaN, pool_mean: NaN, pool_std: NaN });
  }
  return out;
}

export interface ReasonSentenceResult {
  html: string;
  linkedMoveIdx?: number;
}

export function reasonSentence(r: TopReason, predictedPlayer: string, moves?: MoveEntry[]): ReasonSentenceResult {
  const label = featureLabel(r.feature);
  const toward = r.contribution >= 0;
  const forOrAgainst = toward ? "for" : "against";

  if (r.feature === "hour" || r.feature === "dow") {
    return { html: `Even the timing of this game (what hour, what day) points ${forOrAgainst} <b>${predictedPlayer}</b>.` };
  }

  if (r.feature === "post_mistake_think_delta" && r.linked_move_idx != null && moves) {
    const mistakeMove = r.linked_mistake_idx != null ? moves[r.linked_mistake_idx] : undefined;
    const reactionMove = moves[r.linked_move_idx];
    if (mistakeMove && reactionMove && reactionMove.think_time != null) {
      const slower = (r.linked_delta || 0) >= 0;
      return {
        html: `After missing <b>${mistakeMove.san}</b>, they took <b>${reactionMove.think_time.toFixed(1)}s</b> on the next move, ${slower ? "much longer" : "much quicker"} than they usually do. Points ${forOrAgainst} <b>${predictedPlayer}</b>.`,
        linkedMoveIdx: r.linked_move_idx,
      };
    }
  }

  if (r.feature === "low_time_think_ratio" && r.linked_move_idx != null && moves) {
    const m = moves[r.linked_move_idx];
    if (m && m.think_time != null) {
      return {
        html: `Clock almost out, and they still spent <b>${m.think_time.toFixed(1)}s</b> on <b>${m.san}</b>. Points ${forOrAgainst} <b>${predictedPlayer}</b>.`,
        linkedMoveIdx: r.linked_move_idx,
      };
    }
  }

  const val = formatFeatureValue(r.feature, r.value);
  const cmp = describeVsTypical(r.value, r.pool_mean, r.pool_std);
  return { html: `${label}: <b>${val}</b>${cmp ? `, ${cmp}` : ""}. Points ${forOrAgainst} <b>${predictedPlayer}</b>.` };
}
