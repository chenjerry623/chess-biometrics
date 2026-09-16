"""Query Maia (github.com/CSSLab/maia-chess) for human-move-likelihood
policy distributions via the Lc0 (Leela Chess Zero) engine.

Maia is a set of neural nets trained to predict what a human of a given
rating would play, rating-bin by rating-bin (1100-1900 in steps of 100),
rather than to play the objectively best move. Given a position, Lc0 with
VerboseMoveStats enabled exposes the raw policy head output — a probability
for every legal move — via `info string` lines, before any real search has
deepened the position. `go nodes 1` is the point: it reads the policy head's
immediate "snap judgment" rather than letting search refine it, which is
what actually matches what Maia is trained to model.

This module is a thin, validated wrapper around that UCI mechanism —
analogous to how annotate.py drives Stockfish via chess.engine, but for a
policy distribution instead of an evaluation.

Usage (library):
    engine = open_maia_engine(1500)
    probs = query_maia_policy(engine, chess.Board())
    # {'e2e4': 0.5022, 'd2d4': 0.2334, ...}
    engine.quit()
"""

from __future__ import annotations

import re
from pathlib import Path

import chess
import chess.engine

MAIA_WEIGHTS_DIR = Path("data/maia_weights")
MAIA_RATING_BINS = list(range(1100, 2000, 100))  # 1100, 1200, ..., 1900

_UCI_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")
_PROB_RE = re.compile(r"\(?\s*P:\s*([\d.]+)\s*%\)?")


def nearest_rating_bin(rating: float) -> int:
    """Clamp+snap a player rating to the nearest Maia weight file's bin.
    Maia has no weights below 1100 or above 1900 — clamping rather than
    refusing keeps this usable for players outside that range, on the
    (reasonable) assumption that the 1100 and 1900 bins are the closest
    available proxy for "below beginner-net range" / "above net range"."""
    return min(MAIA_RATING_BINS, key=lambda b: abs(b - rating))


def weights_path(rating_bin: int, weights_dir: Path = MAIA_WEIGHTS_DIR) -> Path:
    return weights_dir / f"maia-{rating_bin}.pb.gz"


def _parse_move_stats_line(s: str) -> tuple[str, float] | None:
    """Parse one VerboseMoveStats `info string` line, e.g.:
        'e2e4  (  616) N:      1 (+ 0.00%) (P: 50.22%) (Q: ...) ...'
    Returns (uci_move, probability in [0,1]) or None if this line isn't a
    per-move stats line (lc0 also emits other info strings we don't want)."""
    parts = s.split()
    if not parts:
        return None
    move = parts[0]
    if not _UCI_MOVE_RE.match(move):
        return None
    m = _PROB_RE.search(s)
    if not m:
        return None
    return move, float(m.group(1)) / 100.0


def open_maia_engine(
    rating_bin: int, engine_path: str = "lc0", weights_dir: Path = MAIA_WEIGHTS_DIR
) -> chess.engine.SimpleEngine:
    """Open one Lc0 process configured with a specific rating bin's Maia
    weights. Loading weights is the expensive part of startup — reuse this
    engine across every position queried at this rating bin rather than
    reopening per-FEN."""
    wp = weights_path(rating_bin, weights_dir)
    if not wp.exists():
        raise FileNotFoundError(f"No Maia weights at {wp} — download from CSSLab/maia-chess")
    engine = chess.engine.SimpleEngine.popen_uci(engine_path)
    engine.configure({"WeightsFile": str(wp), "VerboseMoveStats": True})
    return engine


def query_maia_policy(engine: chess.engine.SimpleEngine, board: chess.Board) -> dict[str, float]:
    """Human-move policy distribution at this position, from the engine's
    currently-loaded Maia weights. {uci_move: probability}, summing to ~1
    over legal moves (Lc0's own float rounding, not renormalized here)."""
    probs: dict[str, float] = {}
    with engine.analysis(board, chess.engine.Limit(nodes=1)) as analysis:
        for info in analysis:
            s = info.get("string")
            if not s:
                continue
            parsed = _parse_move_stats_line(s)
            if parsed:
                move, p = parsed
                probs[move] = p
    return probs
