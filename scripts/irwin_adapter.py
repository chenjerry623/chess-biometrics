"""Adapter that scores our own per-ply game data with the pretrained
"Irwin" cheat-detection neural net (github.com/clarkerubber/irwin, via
the Jearnest94/chesscom-irwin fork — an independent community research
project, not an official Lichess system; genuine trained Keras weights,
not retrained here).

RESULT (see PROJECT_LOG.md, "Tried importing an actual real-world
anti-cheat model"): the model's output is saturated on our data — every
game scores ~99.7-99.8/100 regardless of whether it's clean or 100%
engine-replaced. Root cause most likely domain shift (trained on
Lichess, we're on Chess.com — the source repo's own README warns about
exactly this). NOT currently usable; kept as documented reference code,
not wired into any pipeline. The cloned repo (needed for
modules/irwin/models/analysedGame.h5) was deleted after this
investigation — re-clone github.com/Jearnest94/chesscom-irwin and point
MODEL_PATH at it to run this again.

This is a faithful (not exact) port of AnalysedMove.tensor() /
AnalysedGame.tensor() / Game.boardTensorsByPlayerId() from that repo's
source (read directly, not guessed) onto our own annotated columns:
  - eval_before / top_cps (up to 4 MultiPV candidates, best-first,
    mover's POV) stand in for Irwin's `analyses` (up to ~15 candidates
    in the original — a real fidelity gap: positions with more than 4
    close options will under-count `ambiguity()`).
  - eval_after (mover's POV, already sign-corrected in features.py) for
    `self.engineEval` — inherits the SAME cross-search-instability
    caveat flagged for cp_loss elsewhere in this project (eval_after
    comes from an independent search at the next position), though the
    winningChances() sigmoid transform bounds the numeric damage that
    raw cp differences suffered.
  - think_time for `emt`.
  - fen_before + move_uci, replayed with python-chess, for the 3
    board-tensor features (destination-square advancement, legal move
    count, is-capture) and the piece-type embedding input — reproduced
    EXACTLY as the original code computes them, bugs and all (notably:
    `piece_at(move.to_square)` is read from the PRE-move board, so for
    a capture this is the captured piece's type, not the mover's own
    piece — replicated as-is since the model's weights were trained
    against whatever that code actually produced, not what it should
    have produced).

Usage (library):
    from scripts.irwin_adapter import IrwinScorer
    scorer = IrwinScorer()
    score = scorer.score_game(game_df)   # 0-100, higher = more suspicious
"""
from __future__ import annotations

import math
from pathlib import Path

import chess
import numpy as np
import pandas as pd

MODEL_PATH = Path("scratchpad/chesscom-irwin/modules/irwin/models/analysedGame.h5")
MAX_LEN = 60
K = 0.004  # winningChances sigmoid constant, taken directly from AnalysedMove.py


def winning_chances(cp: float | None, mate: float | None = None) -> float:
    if mate is not None and not (isinstance(mate, float) and math.isnan(mate)):
        return 1.0 if mate > 0 else 0.0
    if cp is None or (isinstance(cp, float) and math.isnan(cp)):
        return 0.5
    return 1.0 / (1.0 + math.exp(-K * cp))


def _row_move_features(row, emt_avg: float, wcl_avg: float) -> list[float]:
    top_cps = row.get("top_cps")
    top_cps = list(top_cps) if top_cps is not None and hasattr(top_cps, "__len__") else []
    top_moves = row.get("top_moves")
    top_moves = list(top_moves) if top_moves is not None and hasattr(top_moves, "__len__") else []

    move_uci = row.get("move_uci")
    true_rank = None
    for i, m in enumerate(top_moves):
        if m == move_uci:
            true_rank = i + 1
            break

    n_cand = len(top_cps)
    rank = min(15, (true_rank - 1) if true_rank is not None else _projected_rank(top_cps), n_cand or 15) + 1
    best_wc = winning_chances(top_cps[0]) if n_cand else 0.5
    ambiguity = sum(1 for cp in top_cps if abs(best_wc - winning_chances(cp)) < 0.05) + 1

    advantage = winning_chances(row.get("eval_after"))

    if true_rank is not None and true_rank != 1:
        dif_to_next_best = winning_chances(top_cps[true_rank - 2]) - advantage
    elif true_rank == 1:
        dif_to_next_best = 0.0
    elif n_cand:
        dif_to_next_best = winning_chances(top_cps[-1]) - advantage
    else:
        dif_to_next_best = 0.0

    if true_rank is not None and true_rank <= n_cand - 1:
        dif_to_next_worst = winning_chances(top_cps[true_rank]) - advantage
    else:
        dif_to_next_worst = 0.0

    wcl = max(0.0, best_wc - advantage)
    avg_wcl = float(np.mean([best_wc - winning_chances(cp) for cp in top_cps])) if n_cand else 0.0

    emt = row.get("think_time") or 0.0
    emt_norm = emt / (emt_avg + 1e-8)
    emt_var = abs(emt - emt_avg) / (emt_avg + 1e-8)

    return [
        float(rank), float(ambiguity), float(advantage),
        float(emt_norm), float(emt_var),
        float(dif_to_next_best), float(dif_to_next_worst),
        float(wcl), float(wcl - wcl_avg), float(avg_wcl),
    ]


def _projected_rank(top_cps: list[float]) -> int:
    if len(top_cps) <= 1:
        return 10
    return min(15, len(top_cps) + 2)  # crude fallback when the played move isn't among our (max 4) candidates


def _board_and_piece_features(fen_before: str, move_uci: str, is_white_pov: bool) -> tuple[list[float], int]:
    board = chess.Board(fen_before)
    try:
        move = chess.Move.from_uci(move_uci)
    except (ValueError, TypeError):
        return [0.0, 0.0, 0.0], 0
    to_rank = chess.square_rank(move.to_square)
    advancement = to_rank if is_white_pov else (7 - to_rank)
    legal_count = board.pseudo_legal_moves.count()
    is_capture = int(board.is_capture(move))
    piece = board.piece_at(move.to_square)  # PRE-move board — replicated exactly as upstream, see module docstring
    piece_type = piece.piece_type if piece is not None else 0
    return [float(advancement), float(legal_count), float(is_capture)], piece_type


def build_tensors(game_df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, int]:
    """game_df: one player's rows for one game, sorted by ply, with the
    columns produced by src/features.py (fen_before, move_uci, top_cps,
    top_moves, eval_after, think_time). Returns (game_tensor (60,13),
    piece_tensor (60,1), true_length)."""
    rows = game_df.to_dict("records")[:MAX_LEN]
    length = len(rows)
    emts = [r.get("think_time") or 0.0 for r in rows]
    emt_avg = float(np.mean(emts)) if emts else 0.0

    wcls_raw = []
    for r in rows:
        top_cps = r.get("top_cps")
        top_cps = list(top_cps) if top_cps is not None and hasattr(top_cps, "__len__") else []
        best_wc = winning_chances(top_cps[0]) if top_cps else 0.5
        wcls_raw.append(max(0.0, best_wc - winning_chances(r.get("eval_after"))))
    wcl_avg = float(np.mean(wcls_raw)) if wcls_raw else 0.0

    is_white_pov = bool(rows[0]["ply"] % 2 == 0) if rows else True

    move_feats, board_feats, piece_types = [], [], []
    for r in rows:
        move_feats.append(_row_move_features(r, emt_avg, wcl_avg))
        bf, pt = _board_and_piece_features(r["fen_before"], r["move_uci"], is_white_pov)
        board_feats.append(bf)
        piece_types.append(pt)

    combined = [mf + bf for mf, bf in zip(move_feats, board_feats)]
    pad_n = max(0, MAX_LEN - length)
    combined = combined + pad_n * [10 * [0.0] + [0.0, 0.0, 0.0]]
    piece_types = piece_types + pad_n * [0]

    game_tensor = np.array(combined, dtype="float32")[None, :, :]
    piece_tensor = np.array(piece_types, dtype="float32")[None, :, None]
    return game_tensor, piece_tensor, length


class IrwinScorer:
    def __init__(self, model_path: Path = MODEL_PATH):
        from keras.models import load_model
        self.model = load_model(str(model_path), compile=False)

    def score_game(self, game_df: pd.DataFrame) -> dict:
        gt, pt, length = build_tensors(game_df)
        out = self.model.predict([gt, pt], verbose=0)
        main_out = float(np.asarray(out[0]).flatten()[0])
        lstm_out = np.asarray(out[1]).flatten()[:length]
        isolated_out = np.asarray(out[2]).flatten()[:length]
        weighted_moves = 0.5 * (lstm_out + isolated_out)
        return {
            "game_score": main_out * 100,
            "move_scores": (weighted_moves * 100).tolist(),
            "max_move_score": float(weighted_moves.max()) * 100 if length else 0.0,
        }
