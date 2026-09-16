"""Position complexity features.

This module is the heart of the project. The modeling is commodity; the features
are where chess knowledge becomes a real edge. Everything here is a hypothesis
about what makes a position *expensive to think about* — treat each one as
falsifiable and check whether it actually predicts think-time.

Feature families:
  criticality  — how much does getting this wrong cost?
  volatility   — how unstable is the evaluation?
  forcing      — are there checks/captures/threats demanding calculation?
  branching    — how many plausible options?
  tactical     — loose pieces, pins, geometry
  phase        — opening theory depth, material, endgame class
  clock         — time state and pressure
  trajectory   — how has the game been going?

Usage:
    python -m src.features --plies data/parsed/<u>_plies.parquet \
        --annotated data/annotated/<u>_plies_d12.parquet
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import chess
import numpy as np
import pandas as pd

FEATURES_DIR = Path("data/features")

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}


# --------------------------------------------------------------------------
# Board-derived features
# --------------------------------------------------------------------------

def material_count(board: chess.Board) -> tuple[float, float]:
    """(total material on board, material imbalance from side-to-move POV)."""
    total = 0.0
    diff = 0.0
    for piece_type, value in PIECE_VALUES.items():
        w = len(board.pieces(piece_type, chess.WHITE))
        b = len(board.pieces(piece_type, chess.BLACK))
        total += (w + b) * value
        diff += (w - b) * value
    if board.turn == chess.BLACK:
        diff = -diff
    return total, diff


def forcing_options(board: chess.Board) -> dict:
    """Count the moves that demand concrete calculation."""
    checks = captures = promotions = 0
    for move in board.legal_moves:
        if board.gives_check(move):
            checks += 1
        if board.is_capture(move):
            captures += 1
        if move.promotion:
            promotions += 1
    return {
        "n_checks_available": checks,
        "n_captures_available": captures,
        "n_promotions_available": promotions,
        "in_check": board.is_check(),
    }


def loose_pieces(board: chess.Board) -> dict:
    """Undefended pieces are tactical fuel — they make positions sharp.

    'Loose pieces drop off.' A position with hanging material on either side
    demands calculation even when the eval looks calm.
    """
    loose_own = loose_opp = 0
    for square, piece in board.piece_map().items():
        if piece.piece_type == chess.KING:
            continue
        attacked_by_them = board.is_attacked_by(not piece.color, square)
        defended_by_us = board.is_attacked_by(piece.color, square)
        if attacked_by_them and not defended_by_us:
            if piece.color == board.turn:
                loose_own += 1
            else:
                loose_opp += 1
    return {"loose_own": loose_own, "loose_opp": loose_opp}


def king_safety_proxy(board: chess.Board) -> dict:
    """Cheap proxy: attackers near each king, and whether castling is resolved."""
    out = {}
    for color, label in ((board.turn, "own"), (not board.turn, "opp")):
        king_sq = board.king(color)
        if king_sq is None:
            out[f"king_attackers_{label}"] = 0
            continue
        zone = chess.SquareSet(chess.BB_KING_ATTACKS[king_sq]) | {king_sq}
        out[f"king_attackers_{label}"] = sum(
            1 for sq in zone if board.is_attacked_by(not color, sq)
        )
    out["has_castled_rights"] = bool(board.castling_rights)
    return out


def pawn_structure_features(board: chess.Board) -> dict:
    """Open vs. closed position proxies from pawn structure alone.

    Open positions (clear files/diagonals) reward calculation and piece
    activity; closed ones (locked pawn chains) reward long-term planning
    over tactics. Plausibly costs a different amount of think-time than
    engine-eval criticality alone would predict — a "how sharp is the eval"
    feature and a "what shape is the position" feature aren't the same axis.
    """
    files_with_pawn = {chess.WHITE: set(), chess.BLACK: set()}
    for color in (chess.WHITE, chess.BLACK):
        for sq in board.pieces(chess.PAWN, color):
            files_with_pawn[color].add(chess.square_file(sq))

    open_files = sum(
        1
        for f in range(8)
        if f not in files_with_pawn[chess.WHITE] and f not in files_with_pawn[chess.BLACK]
    )
    half_open_files = sum(
        1
        for f in range(8)
        if (f in files_with_pawn[chess.WHITE]) != (f in files_with_pawn[chess.BLACK])
    )

    # A pawn directly blocked by an opposing pawn one square ahead can't
    # advance — the classic locked pawn-chain signature of a closed position.
    blocked_pawns = 0
    for sq in board.pieces(chess.PAWN, chess.WHITE):
        ahead = sq + 8
        if ahead <= chess.H8 and board.piece_type_at(ahead) == chess.PAWN and board.color_at(ahead) == chess.BLACK:
            blocked_pawns += 1
    for sq in board.pieces(chess.PAWN, chess.BLACK):
        ahead = sq - 8
        if ahead >= chess.A1 and board.piece_type_at(ahead) == chess.PAWN and board.color_at(ahead) == chess.WHITE:
            blocked_pawns += 1

    total_pawns = len(board.pieces(chess.PAWN, chess.WHITE)) + len(board.pieces(chess.PAWN, chess.BLACK))
    return {
        "open_files": open_files,
        "half_open_files": half_open_files,
        "blocked_pawns": blocked_pawns,
        "blocked_pawn_frac": (blocked_pawns / total_pawns) if total_pawns else 0.0,
    }


def pin_features(board: chess.Board) -> dict:
    """Pinned pieces — quiet tactical pressure that loose_pieces (undefended
    material) doesn't capture: a pinned piece can be perfectly defended and
    still be the reason a position demands careful calculation."""
    own_pinned = opp_pinned = 0
    for square, piece in board.piece_map().items():
        if piece.piece_type == chess.KING:
            continue
        if board.is_pinned(piece.color, square):
            if piece.color == board.turn:
                own_pinned += 1
            else:
                opp_pinned += 1
    return {"own_pinned": own_pinned, "opp_pinned": opp_pinned}


def passed_pawn_features(board: chess.Board) -> dict:
    """Passed pawns — no opposing pawn on the same or adjacent file ahead of
    it. Classic driver of concrete, must-calculate-precisely endgame/late-
    middlegame decisions (queening races, blockade timing)."""
    own_passed = opp_passed = 0
    for color in (chess.WHITE, chess.BLACK):
        opp_files_by_rank = [
            (chess.square_file(sq), chess.square_rank(sq))
            for sq in board.pieces(chess.PAWN, not color)
        ]
        for sq in board.pieces(chess.PAWN, color):
            file, rank = chess.square_file(sq), chess.square_rank(sq)
            blocked = any(
                abs(of - file) <= 1 and ((oR > rank) if color == chess.WHITE else (oR < rank))
                for of, oR in opp_files_by_rank
            )
            if not blocked:
                if color == board.turn:
                    own_passed += 1
                else:
                    opp_passed += 1
    return {"own_passed_pawns": own_passed, "opp_passed_pawns": opp_passed}


def bishop_pair_features(board: chess.Board) -> dict:
    """Retaining both bishops (vs. losing one) matters a lot in open
    positions — they cover both color complexes together. Interacts with
    pawn_structure_features rather than standing alone."""
    return {
        "own_bishop_pair": len(board.pieces(chess.BISHOP, board.turn)) >= 2,
        "opp_bishop_pair": len(board.pieces(chess.BISHOP, not board.turn)) >= 2,
    }


def pawn_weakness_features(board: chess.Board) -> dict:
    """Isolated/doubled pawns and pawn-island count — standard structural
    weakness indicators, distinct from the open/closed proxies above (a
    position can be "closed" and still have chronically weak pawns)."""
    feats = {}
    for color, label in ((board.turn, "own"), (not board.turn, "opp")):
        files_present = sorted({chess.square_file(sq) for sq in board.pieces(chess.PAWN, color)})
        file_counts = {}
        for sq in board.pieces(chess.PAWN, color):
            f = chess.square_file(sq)
            file_counts[f] = file_counts.get(f, 0) + 1

        doubled = sum(c - 1 for c in file_counts.values() if c > 1)
        isolated = sum(
            1 for f in files_present
            if (f - 1) not in file_counts and (f + 1) not in file_counts
        )
        islands = 0
        prev_file = None
        for f in files_present:
            if prev_file is None or f != prev_file + 1:
                islands += 1
            prev_file = f

        feats[f"{label}_doubled_pawns"] = doubled
        feats[f"{label}_isolated_pawns"] = isolated
        feats[f"{label}_pawn_islands"] = islands
    return feats


def fork_features(board: chess.Board) -> dict:
    """Forking pieces on the CURRENT board — a piece already attacking 2+
    enemy non-pawn pieces at once. Static geometry (like pin detection
    above), not a hypothetical move — distinct from n_captures_available,
    which counts single-target captures."""
    own_forks = opp_forks = 0
    for square, piece in board.piece_map().items():
        if piece.piece_type == chess.KING:
            continue
        targets = [
            sq for sq in board.attacks(square)
            if (p := board.piece_at(sq)) and p.color != piece.color and p.piece_type != chess.KING
        ]
        if len(targets) >= 2:
            if piece.color == board.turn:
                own_forks += 1
            else:
                opp_forks += 1
    return {"own_forking_pieces": own_forks, "opp_forking_pieces": opp_forks}


def discovered_check_features(board: chess.Board) -> dict:
    """How many of the side-to-move's legal moves deliver a discovered or
    double check — these demand recognizing a hidden line, not just an
    obvious direct threat, so they're a different kind of "forcing" than
    n_checks_available captures."""
    discovered = double = 0
    for move in board.legal_moves:
        board.push(move)
        if board.is_check():
            checkers = board.checkers()
            if len(checkers) >= 2:
                double += 1
            elif move.to_square not in checkers:
                discovered += 1
        board.pop()
    return {"n_discovered_check_moves": discovered, "n_double_check_moves": double}


_DIAGONAL_DIRS = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
_ORTHOGONAL_DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]


def _pieces_along_ray(board: chess.Board, start_sq: int, file_delta: int, rank_delta: int):
    """The first two OCCUPIED squares (with their pieces) along a ray from
    start_sq (exclusive), in order, stopping at the board edge. Deliberately
    walks PAST the first piece — board.attacks() stops there, which is
    exactly what a pin/skewer needs to see past."""
    file, rank = chess.square_file(start_sq), chess.square_rank(start_sq)
    found = []
    while True:
        file += file_delta
        rank += rank_delta
        if not (0 <= file <= 7 and 0 <= rank <= 7):
            return found
        piece = board.piece_at(chess.square(file, rank))
        if piece is not None:
            found.append(piece)
            if len(found) == 2:
                return found


def skewer_features(board: chess.Board) -> dict:
    """Skewers on the CURRENT board: a sliding piece attacks an enemy piece
    that is MORE valuable than a second enemy piece directly behind it on
    the same ray — the front piece must flee, exposing the back one.
    Deliberately excludes the king (an absolute pin to the king is already
    pin_features' job above) — this is specifically the "two ordinary
    pieces on one line" pattern pins don't cover."""
    own_skewers = opp_skewers = 0
    for square, piece in board.piece_map().items():
        if piece.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
            continue
        dirs = []
        if piece.piece_type in (chess.BISHOP, chess.QUEEN):
            dirs += _DIAGONAL_DIRS
        if piece.piece_type in (chess.ROOK, chess.QUEEN):
            dirs += _ORTHOGONAL_DIRS
        for file_delta, rank_delta in dirs:
            found = _pieces_along_ray(board, square, file_delta, rank_delta)
            if len(found) < 2:
                continue
            front, back = found
            if front.color == piece.color or back.color == piece.color:
                continue  # need BOTH to be enemy pieces
            if front.piece_type == chess.KING or back.piece_type == chess.KING:
                continue  # absolute pins to the king are pin_features' job
            if PIECE_VALUES[front.piece_type] > PIECE_VALUES[back.piece_type]:
                if piece.color == board.turn:
                    own_skewers += 1
                else:
                    opp_skewers += 1
    return {"own_skewers": own_skewers, "opp_skewers": opp_skewers}


def game_phase(board: chess.Board, total_material: float) -> str:
    """opening/middlegame/endgame — a coarse, reproducible move-number +
    material heuristic. Not real opening-theory detection, just enough to
    compare time allocation across the three conventional phases."""
    if total_material <= 20:
        return "endgame"
    if board.fullmove_number <= 12:
        return "opening"
    return "middlegame"


def board_features(fen: str) -> dict:
    board = chess.Board(fen)
    total_mat, mat_diff = material_count(board)

    legal_moves = board.legal_moves.count()
    feats = {
        "legal_moves": legal_moves,
        # No real choice existed — e.g. the only legal reply to a check.
        # High eval swing from "getting it wrong" doesn't imply hard to find.
        "is_forced": legal_moves == 1,
        "total_material": total_mat,
        "material_diff": mat_diff,
        "is_endgame": total_mat <= 20,
        "n_pieces": len(board.piece_map()),
        "halfmove_clock": board.halfmove_clock,
    }
    feats.update(forcing_options(board))
    feats.update(loose_pieces(board))
    feats.update(king_safety_proxy(board))
    feats.update(pawn_structure_features(board))
    feats.update(pin_features(board))
    feats.update(passed_pawn_features(board))
    feats.update(bishop_pair_features(board))
    feats.update(pawn_weakness_features(board))
    feats.update(fork_features(board))
    feats.update(discovered_check_features(board))
    feats.update(skewer_features(board))
    feats["game_phase"] = game_phase(board, total_mat)

    # Mobility ratio: side to move's options vs. a rough opponent estimate.
    feats["pawn_count"] = len(board.pieces(chess.PAWN, chess.WHITE)) + len(
        board.pieces(chess.PAWN, chess.BLACK)
    )
    return feats


# --------------------------------------------------------------------------
# Engine-derived features
# --------------------------------------------------------------------------

def _outcome_bucket(cp: float, mate_in: int | float | None) -> str:
    """Coarse result category a position falls into, mate-aware.

    Two forced mates of different length (mate-in-1 vs mate-in-10) are the
    SAME outcome — both winning — even though their cp proxies differ wildly.

    mate_in round-trips through parquet as float64, so a "no mate" value can
    arrive as either None or NaN depending on whether it came straight from
    annotate.py or was read back from disk — check both.
    """
    has_mate = mate_in is not None and pd.notna(mate_in)
    if has_mate and mate_in > 0:
        return "mate_for"
    if has_mate and mate_in <= 0:
        return "mate_against"
    if cp > 300:
        return "winning"
    if cp < -300:
        return "losing"
    return "balanced"


def move_value_entropy(cps: list[float], temperature: float = 100.0) -> float:
    """Shannon entropy (bits) of a softmax over candidate-move quality.

    gap_1_2/top_n_spread only look at the best two or the extremes; this
    treats "how many roughly-equally-good options exist" as a genuine
    probability distribution over the (up to 4) MultiPV candidates — 0 bits
    when one move clearly dominates, up to log2(4)=2 bits when all are
    equally good. temperature=100cp (~1 pawn) sets what counts as "close."
    Grounded in the same information-theoretic framing used in recent
    position-complexity research (e.g. Barthelemy's Chess960 complexity
    study), rather than an ad hoc spread/gap heuristic.
    """
    if len(cps) <= 1:
        return 0.0
    best = max(cps)
    weights = [math.exp(-(best - c) / temperature) for c in cps]
    total = sum(weights)
    probs = [w / total for w in weights]
    return -sum(p * math.log2(p) for p in probs if p > 0)


def engine_features(row: pd.Series) -> dict:
    """Criticality and volatility, from the MultiPV annotation."""
    top_cps = row.get("top_cps")
    if top_cps is None or (isinstance(top_cps, float) and pd.isna(top_cps)):
        return {}
    cps = list(top_cps)
    raw_mate_ins = row.get("top_mate_ins")
    if raw_mate_ins is None or (isinstance(raw_mate_ins, float) and pd.isna(raw_mate_ins)):
        mate_ins = [None] * len(cps)
    else:
        mate_ins = list(raw_mate_ins)

    best = cps[0]
    feats = {
        "eval_before": best,
        # Reference only now — outcome_changes below is the criticality
        # feature the model should actually use. gap_1_2's raw cp gap can be
        # misleading in both directions around forced mates.
        "gap_1_2": (best - cps[1]) if len(cps) > 1 else 0.0,
        # Volatility: wide spread means the position is sharp.
        "top_n_spread": (max(cps) - min(cps)) if len(cps) > 1 else 0.0,
        "move_value_entropy": move_value_entropy(cps),
        # How many moves are near-equivalent? Few = must find it. Many = relax.
        "n_moves_within_30cp": sum(1 for c in cps if abs(best - c) <= 30),
        "n_moves_within_100cp": sum(1 for c in cps if abs(best - c) <= 100),
        # Is the position already decided? People think less when lost/winning.
        "abs_eval": abs(best),
        "is_decided": abs(best) > 300,
    }

    bucket_best = _outcome_bucket(cps[0], mate_ins[0])
    feats["outcome_bucket"] = bucket_best

    if len(cps) > 1:
        bucket_second = _outcome_bucket(cps[1], mate_ins[1])
        feats["outcome_changes"] = bucket_best != bucket_second

        same_sign_mate = (
            mate_ins[0] is not None
            and pd.notna(mate_ins[0])
            and mate_ins[1] is not None
            and pd.notna(mate_ins[1])
            and (mate_ins[0] > 0) == (mate_ins[1] > 0)
        )
        feats["mate_distance_gap"] = (
            abs(abs(mate_ins[0]) - abs(mate_ins[1])) if same_sign_mate else None
        )

    feats["played_best_move"] = row.get("move_uci") == row.get("best_move")
    return feats


# --------------------------------------------------------------------------
# Clock and trajectory features
# --------------------------------------------------------------------------

def clock_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["clock_frac_remaining"] = df["clock_before"] / df["base_s"]
    df["clock_diff"] = df["clock_before"] - df["opp_clock"]
    df["behind_on_clock"] = df["clock_diff"] < 0
    # Severe time pressure is where quality collapses — find the personal cliff.
    df["low_time"] = df["clock_before"] < (0.15 * df["base_s"])
    df["game_progress"] = df["player_move_index"] / df["player_moves_total"].clip(lower=1)
    return df


def trajectory_features(df: pd.DataFrame) -> pd.DataFrame:
    """How the eval has moved recently — are they already in trouble?"""
    df = df.sort_values(["game_id", "ply"]).copy()
    g = df.groupby("game_id")["eval_before"]
    df["eval_delta_1"] = g.diff()
    df["eval_roll_std_3"] = g.transform(lambda s: s.rolling(3, min_periods=1).std())
    # Recent instability = the game just got sharp = probably worth thinking.
    df["recently_volatile"] = df["eval_roll_std_3"] > 100
    return df


# --------------------------------------------------------------------------
# Book / theory moves
# --------------------------------------------------------------------------

BOOK_PATH = Path("data/books/komodo.bin")
TABLEBASE_PATH = Path("data/tablebases")
TABLEBASE_MAX_PIECES = 5
MAIA_ANNOTATED_DIR = Path("data/maia_annotated")


def add_book_features(df: pd.DataFrame, book_path: Path = BOOK_PATH) -> pd.DataFrame:
    """Book/theory move detection via Polyglot lookup.

    in_book            — does the book have ANY known move here (still
                          within known theory)?
    played_book_move   — did the player's actual move match one of them?
    plies_since_book    — how many plies since the game left known theory
                          (0 while still in book) — the flip side of
                          game_phase's crude move-number heuristic.

    A genuinely memorized book move is close to zero real calculation —
    a different kind of "obvious" than is_recapture/is_forced (memorization,
    not pattern-forcing).
    """
    import chess.polyglot

    df = df.copy()
    in_book = []
    played_book = []
    with chess.polyglot.open_reader(str(book_path)) as reader:
        for fen, move_uci in zip(df["fen_before"], df["move_uci"]):
            board = chess.Board(fen)
            book_moves = {e.move.uci() for e in reader.find_all(board)}
            in_book.append(bool(book_moves))
            played_book.append(move_uci in book_moves)
    df["in_book"] = in_book
    df["played_book_move"] = played_book

    df = df.sort_values(["game_id", "ply"])

    def _plies_since_book(group: pd.DataFrame) -> pd.Series:
        counter = 0
        out = []
        for still_book in group["in_book"]:
            counter = 0 if still_book else counter + 1
            out.append(counter)
        return pd.Series(out, index=group.index)

    df["plies_since_book"] = df.groupby("game_id", group_keys=False).apply(_plies_since_book)
    return df


def add_tablebase_features(df: pd.DataFrame, tb_path: Path = TABLEBASE_PATH) -> pd.DataFrame:
    """Syzygy tablebase ground truth for endgames with <= TABLEBASE_MAX_PIECES
    total pieces on the board.

    tb_wdl            — exact result from the mover's perspective: 2=win,
                        1=cursed win (50-move-rule draw), 0=draw,
                        -1=blessed loss, -2=loss. NaN if not probeable
                        (too many pieces, or no table for this material).
    tb_dtz            — distance-to-zeroing: halfmoves until a capture/pawn
                        move that provably preserves the WDL result — how
                        far from "resolved" a known-technique endgame is.
    tb_move_optimal   — did the player's move keep the best available WDL
                        (from their own perspective)? Exact ground truth,
                        not a depth-12 approximation — cp_loss can be wrong
                        near tablebase territory since the engine hasn't
                        searched to conversion.

    Only meaningful for endgames; NaN everywhere else, which is fine — tree
    models handle missing values natively.
    """
    import chess.syzygy

    df = df.copy()
    wdls: list[float | None] = []
    dtzs: list[float | None] = []
    optimal: list[bool | None] = []

    with chess.syzygy.open_tablebase(str(tb_path)) as tb:
        for fen, move_uci in zip(df["fen_before"], df["move_uci"]):
            board = chess.Board(fen)
            if len(board.piece_map()) > TABLEBASE_MAX_PIECES:
                wdls.append(None)
                dtzs.append(None)
                optimal.append(None)
                continue
            try:
                wdl = tb.probe_wdl(board)
                dtz = tb.probe_dtz(board)
            except Exception:
                wdls.append(None)
                dtzs.append(None)
                optimal.append(None)
                continue
            wdls.append(wdl)
            dtzs.append(dtz)
            try:
                board.push(chess.Move.from_uci(move_uci))
                if len(board.piece_map()) <= TABLEBASE_MAX_PIECES:
                    wdl_after_own_pov = -tb.probe_wdl(board)
                    optimal.append(wdl_after_own_pov >= wdl)
                else:
                    optimal.append(None)
            except Exception:
                optimal.append(None)

    df["tb_wdl"] = wdls
    df["tb_dtz"] = dtzs
    df["tb_move_optimal"] = optimal
    return df


def add_sacrifice_features(df: pd.DataFrame) -> pd.DataFrame:
    """Voluntary material sacrifices — conceptually the OPPOSITE of
    is_recapture/is_forced: those flag "high stakes, near-zero real effort"
    moves; a sacrifice may be engine-clear (low cp_loss — objectively fine)
    yet plausibly still costs real human verification time, since
    voluntarily giving up material carries a psychological/confirmation
    cost distinct from raw engine difficulty. Tests that hypothesis
    directly rather than assuming it.

    NOTE on a fixed bug: an earlier version of this function compared
    material_diff before vs. immediately after the mover's OWN move, which
    can never show a loss — a sacrificed piece is only actually captured on
    the OPPONENT'S subsequent move, so that comparison always read as flat
    or a gain (confirmed directly on a Greek-gift Bxh7+ test position). The
    fix: look at the board immediately after the mover's move and ask
    whether any of the MOVER'S OWN pieces are now hanging (attacked by the
    opponent, undefended by the mover) — that's the material being offered,
    regardless of whether the opponent goes on to actually take it.

    material_offered — value (pawns) of the most valuable hanging piece the
                        mover's own move leaves attacked-and-undefended,
                        0 if none.
    is_sacrifice     — material_offered >= 3 (minor piece or more), NOT a
                        recapture or forced move (those aren't real
                        decisions), AND cp_loss stayed low (cp_loss <= 100)
                        — i.e. the engine confirms this wasn't just a
                        blunder.

    Deliberately does NOT attempt to separate a genuine mistake from a
    deliberate "objectively worse but practically motivated" choice when
    cp_loss is high — that's what maia_prob_played (add_maia_features) is
    for: a low-cp_loss-confirmed sacrifice and a "objectively bad but other
    humans at this rating would also play it" choice are different
    phenomena and shouldn't be conflated into one flag.
    """
    df = df.copy()
    material_offered: list[float] = []
    for fen, move_uci in zip(df["fen_before"], df["move_uci"]):
        board = chess.Board(fen)
        mover = board.turn
        try:
            board.push(chess.Move.from_uci(move_uci))
        except Exception:
            material_offered.append(0.0)
            continue
        best_hanging = 0.0
        for square, piece in board.piece_map().items():
            if piece.color != mover or piece.piece_type == chess.KING:
                continue
            if board.is_attacked_by(not mover, square) and not board.is_attacked_by(mover, square):
                best_hanging = max(best_hanging, PIECE_VALUES[piece.piece_type])
        material_offered.append(best_hanging)

    df["material_offered"] = material_offered
    obvious = df["is_recapture"] | df["is_forced"]
    df["is_sacrifice"] = (
        (df["material_offered"] >= 3.0) & (~obvious) & (df["cp_loss"] <= 100)
    )
    return df


def add_maia_features(df: pd.DataFrame, maia_path: Path) -> pd.DataFrame:
    """Human-move-likelihood features from Maia (see src/maia_query.py,
    src/maia_annotate.py) — a genuinely different signal from everything
    else in this file. Every other criticality/obviousness feature answers
    "how hard is this for an ENGINE," which is not the same question as "how
    hard is this for a HUMAN at this player's rating." A recapture can be
    engine-trivial and human-typical at once (is_forced/is_recapture already
    catch that), but a quiet positional move can be engine-easy (small
    gap_1_2) while still being something almost no human at this rating
    would find — and conversely, a "sacrifice" that looks alarming to a
    rigid engine-only view can be something most players at that strength
    play instinctively (a known pattern), which is exactly the "sacrifice
    vs. mistake" distinction the sacrifice features above can't make alone.

    maia_prob_played   — probability Maia (at this player's nearest rating
                          bin) assigns to the move actually played. Low value
                          = objectively-legal but human-atypical for someone
                          at this strength; distinct from cp_loss (which
                          only measures objective cost).
    maia_top1_prob      — probability of Maia's single most-likely move here
                          — a human-consensus analogue to engine top_n_spread:
                          high value = "obvious to a human," independent of
                          whether it's also engine-best.
    maia_is_top_choice  — did the player's move match Maia's single most
                          likely move.
    maia_surprise       — -log(maia_prob_played), a continuous "how
                          human-atypical was this" measure. Useful directly
                          as a feature, and as a cross-check on sacrifice
                          detection: a sound sacrifice with LOW maia_surprise
                          (other players at this strength commonly play it —
                          a known pattern) is a different phenomenon from one
                          with HIGH maia_surprise (this player found
                          something unusual for their level).
    maia_prob_best_move — probability Maia assigns to the ENGINE's best_move
                          at this position — independent of what was
                          actually played. Low value = the objectively
                          correct move is itself counter-intuitive for a
                          human at this rating.
    requires_overriding_instinct — best_move is NOT Maia's top choice AND
                          maia_prob_best_move is low (<0.10): a position
                          where playing well means resisting the intuitive
                          move, not just calculating deeply. This is a
                          genuinely different axis from gap_1_2/criticality
                          (which only says getting it wrong is costly, not
                          that instinct actively points the wrong way) — a
                          position can be highly critical AND intuitive (engine
                          top choice = human top choice, just needs to be
                          seen), or highly critical AND counter-intuitive
                          (the hard case this flags). Worth checking whether
                          THIS distinction, not gap_1_2 alone, is what
                          predicts underthought/panic moves.

    Requires maia_annotate.py to have been run for this plies file first
    (see PROJECT_LOG.md) — silently no-ops (returns df unchanged) if the
    file doesn't exist yet, same convention as book/tablebase features.
    """
    if not maia_path.exists():
        return df

    from src.maia_query import nearest_rating_bin

    df = df.copy()
    df["maia_rating_bin"] = df["player_rating"].apply(
        lambda r: nearest_rating_bin(r) if pd.notna(r) else None
    )
    maia = pd.read_parquet(maia_path)
    df = df.merge(maia, on=["fen_before", "maia_rating_bin"], how="left")

    def _row_stats(row) -> tuple[float | None, float | None, bool | None, float | None, float | None, bool | None]:
        moves, probs = row["maia_moves"], row["maia_probs"]
        if moves is None or (isinstance(moves, float) and pd.isna(moves)) or len(moves) == 0:
            return None, None, None, None, None, None
        move_to_p = dict(zip(moves, probs))
        played_p = move_to_p.get(row["move_uci"], 0.0)
        top_idx = int(np.argmax(probs))
        top_move = moves[top_idx]
        top_p = probs[top_idx]
        is_top = bool(row["move_uci"] == top_move)
        surprise = -math.log(max(played_p, 1e-6))

        best_move = row.get("best_move")
        best_p = move_to_p.get(best_move, 0.0) if best_move else None
        overrides_instinct = (
            (best_move is not None and best_move != top_move and best_p is not None and best_p < 0.10)
            if best_move else None
        )
        return played_p, top_p, is_top, surprise, best_p, overrides_instinct

    stats = df.apply(_row_stats, axis=1, result_type="expand")
    stats.columns = [
        "maia_prob_played", "maia_top1_prob", "maia_is_top_choice", "maia_surprise",
        "maia_prob_best_move", "requires_overriding_instinct",
    ]
    df = pd.concat([df, stats], axis=1)
    df = df.drop(columns=["maia_moves", "maia_probs"], errors="ignore")
    return df


# --------------------------------------------------------------------------
# Skill drift / non-stationarity
# --------------------------------------------------------------------------
#
# A pooled baseline trained across a long history quietly averages together
# different versions of the player. Two things follow from that:
#
#   1. `player_rating` is already a per-game covariate — it lets the model
#      condition on skill level at the time of that specific game, which
#      captures most of the effect of "I got better."
#   2. But rating doesn't capture a *style* shift at constant rating (e.g.
#      recognizing patterns faster without the rating having moved yet), so
#      games are also given a recency weight so the "current" baseline isn't
#      diluted by how you played a year ago.
#
# RECENCY_HALFLIFE_DAYS controls how fast old games fade. Don't just trust the
# default — check the drift diagnostic below and adjust.

RECENCY_HALFLIFE_DAYS = 90

# Gap (in minutes) between game START times beyond which we call it a new
# "sitting" rather than the same session. Games back-to-back in a bullet
# session look nothing like the same games played hours apart — fatigue,
# tilt, and warm-up effects are a within-session question, not just a
# calendar-time one.
SESSION_GAP_MINUTES = 20


def experience_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add date-derived covariates for skill drift.

    game_date          — parsed datetime (day resolution)
    days_since_first   — cumulative "experience" proxy, in days
    games_played_so_far— cumulative experience proxy, in game count
    recency_weight      — exponential decay; use as sample_weight when fitting
                          the "what should I do now" model. Use weight=1
                          uniformly instead when the goal is descriptive
                          ("how has my time management evolved").
    session_game_index — 0-based position within a continuous playing
                          session (a new session starts after a gap of
                          SESSION_GAP_MINUTES between game start times, or
                          for the first game ever / missing timestamp).
                          A fatigue/warm-up proxy: game 0 of a session is a
                          different animal from game 15.
    minutes_since_prev_game — gap to the previous game's start; NaN for a
                          fresh session's first game.
    """
    df = df.copy()
    df["game_date"] = pd.to_datetime(df["date"], format="%Y.%m.%d", errors="coerce")
    # Combine date + time-of-day for session ordering — day resolution alone
    # can't tell two games played minutes apart from two played hours apart.
    df["game_datetime"] = pd.to_datetime(
        df["date"].astype(str) + " " + df["utc_time"].astype(str),
        format="%Y.%m.%d %H:%M:%S",
        errors="coerce",
    )

    # One row per game, ordered by date, to get per-game sequence numbers
    # without leaking future games into games_played_so_far.
    game_dates = (
        df[["game_id", "game_date"]]
        .drop_duplicates()
        .sort_values("game_date")
        .reset_index(drop=True)
    )
    game_dates["games_played_so_far"] = range(1, len(game_dates) + 1)
    df = df.merge(game_dates[["game_id", "games_played_so_far"]], on="game_id", how="left")

    first_date = df["game_date"].min()
    last_date = df["game_date"].max()
    df["days_since_first"] = (df["game_date"] - first_date).dt.days
    days_before_last = (last_date - df["game_date"]).dt.days
    df["recency_weight"] = 0.5 ** (days_before_last / RECENCY_HALFLIFE_DAYS)

    game_times = (
        df[["game_id", "game_datetime"]]
        .drop_duplicates()
        .sort_values("game_datetime")
        .reset_index(drop=True)
    )
    gap_min = game_times["game_datetime"].diff().dt.total_seconds() / 60
    game_times["minutes_since_prev_game"] = gap_min
    new_session = gap_min.isna() | (gap_min > SESSION_GAP_MINUTES)
    game_times["session_id"] = new_session.cumsum()
    game_times["session_game_index"] = game_times.groupby("session_id").cumcount()
    df = df.merge(
        game_times[["game_id", "minutes_since_prev_game", "session_game_index"]],
        on="game_id",
        how="left",
    )

    return df


# --------------------------------------------------------------------------
# Target variable
# --------------------------------------------------------------------------

def add_cp_loss(df: pd.DataFrame) -> pd.DataFrame:
    """Centipawn loss of the move actually played.

    eval_before is from the mover's POV. The eval after their move, from the
    *opponent's* POV, must be negated to stay in the mover's frame.
    """
    df = df.sort_values(["game_id", "ply"]).copy()
    df["eval_after_opp_pov"] = df.groupby("game_id")["eval_before"].shift(-1)
    df["eval_after"] = -df["eval_after_opp_pov"]
    df["cp_loss"] = (df["eval_before"] - df["eval_after"]).clip(lower=0)
    # Standard-ish thresholds; adjust to taste.
    df["is_inaccuracy"] = df["cp_loss"] > 50
    df["is_mistake"] = df["cp_loss"] > 100
    df["is_blunder"] = df["cp_loss"] > 300
    return df


def build(plies_path: Path, annotated_path: Path) -> Path:
    plies = pd.read_parquet(plies_path)
    annotated = pd.read_parquet(annotated_path)

    print(f"Joining {len(plies)} plies with {len(annotated)} annotated positions")
    df = plies.merge(annotated, on="fen_before", how="inner")
    print(f"  -> {len(df)} rows with engine data")

    print("Extracting board features (this walks every position)...")
    board_feats = pd.DataFrame(
        [board_features(f) for f in df["fen_before"]], index=df.index
    )
    df = pd.concat([df, board_feats], axis=1)

    print("Extracting engine features...")
    eng_feats = pd.DataFrame(
        [engine_features(r) for _, r in df.iterrows()], index=df.index
    )
    # eval_before/gap_1_2/top_n_spread already exist on df from the annotated
    # merge (annotate.py writes them too) — drop the recomputed duplicates
    # rather than collide on concat.
    df = pd.concat(
        [df, eng_feats.drop(columns=["eval_before", "gap_1_2", "top_n_spread"], errors="ignore")],
        axis=1,
    )

    df = clock_features(df)
    df = add_cp_loss(df)
    df = trajectory_features(df)
    df = experience_features(df)
    if BOOK_PATH.exists():
        print("Looking up book/theory moves...")
        df = add_book_features(df)
    else:
        print(f"No opening book at {BOOK_PATH}, skipping book-move features.")

    if TABLEBASE_PATH.exists() and any(TABLEBASE_PATH.glob("*.rtbw")):
        print("Probing endgame tablebases...")
        df = add_tablebase_features(df)
    else:
        print(f"No tablebases at {TABLEBASE_PATH}, skipping tablebase features.")

    print("Detecting voluntary sacrifices...")
    df = add_sacrifice_features(df)

    maia_path = MAIA_ANNOTATED_DIR / f"{plies_path.stem}_maia.parquet"
    if maia_path.exists():
        print("Adding Maia human-move-likelihood features...")
        df = add_maia_features(df, maia_path)
    else:
        print(
            f"No Maia annotations at {maia_path} — run "
            f"`python -m src.maia_annotate --plies {plies_path}` first to enable "
            f"human-move-likelihood features. Skipping for now."
        )

    # log1p think-time: the distribution is heavily right-skewed, and relative
    # differences matter more than absolute ones.
    df["log_think_time"] = np.log1p(df["think_time"].clip(lower=0))

    FEATURES_DIR.mkdir(parents=True, exist_ok=True)
    out = FEATURES_DIR / f"{plies_path.stem}_features.parquet"
    df.to_parquet(out, index=False)

    print(f"\nWrote {out} — {len(df)} rows, {len(df.columns)} columns")
    print("\nSanity checks worth running now:")
    print("  * corr(think_time, gap_1_2)   — do you think longer on only-move positions?")
    print("  * corr(think_time, n_checks_available) — do forcing positions cost you time?")
    print("  * cp_loss grouped by clock_frac_remaining bucket — where is your cliff?")
    print("  If none of these correlate at all, the features are wrong, not the model.")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plies", required=True, type=Path)
    parser.add_argument("--annotated", required=True, type=Path)
    args = parser.parse_args()
    build(args.plies, args.annotated)


if __name__ == "__main__":
    main()
