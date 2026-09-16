export interface ModelComparison {
  logistic_regression: number;
  gradient_boosting: number;
  lightgbm?: number;
}

export interface MultiGamePoint {
  n_games: number;
  overall_accuracy: number;
  balanced_accuracy: number;
  n_players_covered: number;
  n_players_total: number;
}

export interface TopReason {
  feature: string;
  contribution: number;
  value: number;
  pool_mean: number;
  pool_std: number;
  linked_move_idx?: number;
  linked_mistake_idx?: number;
  linked_delta?: number;
}

export interface SampleGame {
  game_id: string;
  true_player: string;
  predicted_player: string;
  correct: boolean;
  n_plies: number;
  probabilities: Record<string, number>;
  confidence: number;
  top_reasons: TopReason[];
}

export interface FeatureImportanceRow {
  feature: string;
  importance: number;
}

export interface RankKPoint {
  k: number;
  overall_accuracy: number;
  balanced_accuracy: number;
}

export interface SessionPositionBucket {
  accuracy: number;
  n_games: number;
}

export interface CorrectnessPatterns {
  within_player_correlations: { signal: string; within_player_corr_with_correctness: number; n_games: number }[];
  tilt_buckets: Record<string, SessionPositionBucket>;
  session_position_buckets: Record<string, SessionPositionBucket>;
}

export interface Cohort {
  cohort: string;
  feature_mode: "full" | "biometric";
  n_games_total: number;
  balanced_accuracy: number;
  overall_accuracy: number;
  majority_baseline: number;
  chance_baseline: number;
  model_used: string;
  model_comparison: ModelComparison;
  multi_game_accuracy: MultiGamePoint[];
  rank_k_accuracy: RankKPoint[];
  class_counts_n: number;
  per_class_recall: Record<string, number>;
  per_class_recall_summary: { min: number; max: number; median: number };
  most_confused_pair: [string, string] | null;
  most_confused_rate: number;
  correctness_patterns: CorrectnessPatterns | null;
  sample_games: SampleGame[];
  feature_importance?: FeatureImportanceRow[] | null;
}

export interface ConfidenceTracePoint {
  n_games: number;
  game_id: string;
  game_date: string;
  running_prob_correct: number;
  top_guess: string;
  top_guess_prob: number;
  correct_so_far: boolean;
}

export interface ConfidenceTrace {
  player: string;
  n_games: number;
  trace: ConfidenceTracePoint[];
}

export interface MoveEntry {
  san: string;
  is_own: boolean;
  think_time: number | null;
  fen_before: string;
  ply: number;
  move_number: number;
  is_mistake?: boolean;
  cp_loss?: number;
  gap_1_2?: number;
  n_captures_available?: number;
  clock_frac_remaining?: number;
  key_moment: "mistake" | "long_think" | null;
  key_reason: string | null;
}

export interface NarratedGame {
  game_id: string;
  true_player: string;
  predicted_player: string;
  correct: boolean;
  confidence: number;
  top_reasons: TopReason[];
  moves: MoveEntry[];
}

export interface IdentificationData {
  n_players: number;
  pool_biometric: Cohort;
  pool: Cohort;
  narrated_games: NarratedGame[];
  confidence_trace: ConfidenceTrace | null;
}
