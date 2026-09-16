"""Parse PGN into per-ply records, extracting think-time from clock comments.

This is where the critical gotcha lives. See flag_noise() — bullet clock data is
contaminated with premoves, mouse-slips and lag. Every one of those looks like a
near-instant "decision" that was never actually a decision. Characterize this
distribution before trusting any downstream model.

Usage:
    python -m src.parse_pgn --pgn data/raw/<user>_bullet.pgn --player <username>
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import chess
import chess.pgn
import pandas as pd

PARSED_DIR = Path("data/parsed")
CLOCK_RE = re.compile(r"\[%clk\s+(\d+):(\d+):(\d+(?:\.\d+)?)\]")

# Think-times at or below this are almost certainly premoves, not decisions.
# Tune this after looking at the actual distribution — do not accept the default blindly.
PREMOVE_THRESHOLD_S = 0.12

# A move "costing" more than this multiple of the whole base time control is
# flagged as a probable lag spike rather than genuine thought. Tune per format —
# 1-minute bullet vs 3+2 blitz will want different values in practice.
LAG_MULTIPLIER = 0.6


def parse_clock(comment: str) -> float | None:
    """Extract remaining clock seconds from a PGN move comment."""
    m = CLOCK_RE.search(comment or "")
    if not m:
        return None
    hours, minutes, seconds = m.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_time_control(tc: str) -> tuple[float, float]:
    """Returns (base_seconds, increment_seconds).

    Handles both conventions:
      Lichess:   always "base+inc"  -> "60+0", "180+2"
      Chess.com: omits increment when zero -> "60", "180+2"
    Chess.com daily games use "1/259200" (seconds per move); those have no
    usable think-time signal, so they return NaN and get filtered downstream.
    """
    if not tc:
        return float("nan"), 0.0
    if "/" in tc:  # correspondence — not modelable
        return float("nan"), 0.0
    try:
        if "+" in tc:
            base, inc = tc.split("+")
            return float(base), float(inc)
        return float(tc), 0.0
    except ValueError:
        return float("nan"), 0.0


def _game_id(headers) -> str:
    """Lichess puts the game URL in Site; Chess.com puts "Chess.com" there and
    the real URL in Link. Fall back to a date+players hash if neither exists."""
    link = headers.get("Link") or ""
    if link:
        return link.rstrip("/").rsplit("/", 1)[-1]
    site = headers.get("Site") or ""
    if site.startswith("http"):
        return site.rstrip("/").rsplit("/", 1)[-1]
    return f"{headers.get('UTCDate','')}_{headers.get('UTCTime','')}_{headers.get('White','')}"


def parse_game(game: chess.pgn.Game, target_player: str) -> list[dict]:
    """Yield one record per ply played *by the target player*.

    We only model the target player's decisions. Opponent moves still advance the
    board state but are not training rows.
    """
    headers = game.headers
    white = headers.get("White", "")
    black = headers.get("Black", "")

    target_lower = target_player.lower()
    if target_lower == white.lower():
        target_color = chess.WHITE
        player_rating = headers.get("WhiteElo")
        opp_rating = headers.get("BlackElo")
    elif target_lower == black.lower():
        target_color = chess.BLACK
        player_rating = headers.get("BlackElo")
        opp_rating = headers.get("WhiteElo")
    else:
        return []

    base_s, inc_s = parse_time_control(headers.get("TimeControl", ""))

    records: list[dict] = []
    board = game.board()
    # Clock starts at base time for both sides.
    prev_clock = {chess.WHITE: base_s, chess.BLACK: base_s}
    # Destination square of the immediately preceding move, if it was a
    # capture — always the opponent's move by the time it's our turn, since
    # colors alternate every ply. Lets us tell a recapture (retake on the
    # square they just captured on — high eval swing, but often near-zero
    # real thought) apart from a "cold" capture that requires spotting it.
    last_capture_square: int | None = None
    # How long the opponent spent on the move immediately before ours. A fast
    # move on a genuinely hard position isn't necessarily "solved instantly
    # cold" — it can mean thinking on the opponent's time while they moved,
    # then playing quickly once it's your turn. Lets us test that directly
    # instead of assuming a quick move always means low effort.
    last_opp_think_time: float | None = None
    # Which side (if any) each color has castled to, as of the current
    # position — not derivable from a single FEN once the rooks have moved
    # again, so it has to be tracked as the game is walked. Opposite-side
    # castling is one of the biggest complexity drivers in chess (races,
    # both kings exposed to a pawn storm) and pure material/mobility
    # features don't capture it at all.
    castled_side: dict[chess.Color, str | None] = {chess.WHITE: None, chess.BLACK: None}

    for ply_idx, node in enumerate(game.mainline()):
        mover = board.turn
        move = node.move
        clock_after = parse_clock(node.comment)
        is_capture_now = board.is_capture(move)
        own_castled_before = castled_side[target_color]
        opp_castled_before = castled_side[not target_color]

        if mover == target_color and clock_after is not None:
            before = prev_clock[mover]
            # think_time = (clock before) - (clock after) + increment received
            think_time = (before - clock_after + inc_s) if pd.notna(before) else None

            records.append(
                {
                    "game_id": _game_id(headers),
                    "source": "chesscom" if headers.get("Link") else "lichess",
                    "date": headers.get("UTCDate"),
                    "utc_time": headers.get("UTCTime"),
                    "player": target_player,
                    "color": "white" if target_color == chess.WHITE else "black",
                    "player_rating": pd.to_numeric(player_rating, errors="coerce"),
                    "opp_rating": pd.to_numeric(opp_rating, errors="coerce"),
                    "result": headers.get("Result"),
                    "eco": headers.get("ECO"),
                    "opening": headers.get("Opening"),
                    "time_control": headers.get("TimeControl"),
                    "base_s": base_s,
                    "increment_s": inc_s,
                    "ply": ply_idx,
                    "move_number": board.fullmove_number,
                    "fen_before": board.fen(),
                    "move_uci": move.uci(),
                    "move_san": board.san(move),
                    "clock_before": before,
                    "clock_after": clock_after,
                    "think_time": think_time,
                    # opponent's clock at this moment — matters for flagging/pressure
                    "opp_clock": prev_clock[not mover],
                    "legal_move_count": board.legal_moves.count(),
                    "is_capture": is_capture_now,
                    "is_recapture": is_capture_now and move.to_square == last_capture_square,
                    "gives_check": board.gives_check(move),
                    "opp_think_time_prev": last_opp_think_time,
                    "own_castled_side": own_castled_before,
                    "opp_castled_side": opp_castled_before,
                    "opposite_side_castling": (
                        own_castled_before is not None
                        and opp_castled_before is not None
                        and own_castled_before != opp_castled_before
                    ),
                }
            )
        elif mover != target_color and clock_after is not None:
            opp_before = prev_clock[mover]
            last_opp_think_time = (
                (opp_before - clock_after + inc_s) if pd.notna(opp_before) else None
            )

        if clock_after is not None:
            prev_clock[mover] = clock_after
        last_capture_square = move.to_square if is_capture_now else None
        if board.is_castling(move):
            castled_side[mover] = "kingside" if board.is_kingside_castling(move) else "queenside"
        board.push(move)

    # Total plies in the game, useful for phase normalization.
    total = len(records)
    for i, r in enumerate(records):
        r["player_move_index"] = i
        r["player_moves_total"] = total

    return records


def flag_noise(df: pd.DataFrame) -> pd.DataFrame:
    """Flag think-times that are probably not real decisions.

    Two distinct failure modes, kept separate rather than lumped together:
      - premove/instant: near-zero time, no real-time decision happened
      - lag spike: implausibly large time relative to the time control, more
        consistent with a connection stall/reconnect than genuine long thought

    This can't be proven from clock data alone (there's no server-side ping log
    here) — lag_suspect is a heuristic, not a certain classification. Report the
    rate, don't silently trust or drop it. In bullet, premoves alone can be a
    large share of all moves.
    """
    df = df.copy()
    df["is_premove"] = df["think_time"] <= PREMOVE_THRESHOLD_S
    df["is_negative"] = df["think_time"] < 0  # clock inconsistency / lag artifact

    # A single move consuming a large multiple of the ENTIRE base time control
    # is far more consistent with a stall than a real decision, even accounting
    # for genuine long "panic" thinks.
    df["lag_suspect"] = df["think_time"] > (LAG_MULTIPLIER * df["base_s"])

    df["is_suspect"] = (
        df["is_premove"] | df["is_negative"] | df["lag_suspect"] | df["think_time"].isna()
    )
    return df


def parse_file(pgn_path: Path, player: str) -> pd.DataFrame:
    rows: list[dict] = []
    games_seen = 0

    with pgn_path.open(encoding="utf-8", errors="replace") as fh:
        while True:
            game = chess.pgn.read_game(fh)
            if game is None:
                break
            games_seen += 1
            rows.extend(parse_game(game, player))
            if games_seen % 250 == 0:
                print(f"  parsed {games_seen} games, {len(rows)} plies")

    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit(f"No plies found for player '{player}' in {pgn_path}")

    # Correspondence / unparseable time controls have no usable think-time.
    before = len(df)
    df = df[df["base_s"].notna()].copy()
    if len(df) < before:
        print(f"Dropped {before - len(df)} plies with unusable time control")

    df = flag_noise(df)

    print(f"\nParsed {games_seen} games -> {len(df)} player plies")
    print(f"  premove/instant:  {100 * df['is_premove'].mean():.1f}%")
    print(f"  lag_suspect:      {100 * df['lag_suspect'].mean():.1f}%")
    print(f"  negative/missing: {100 * (df['is_negative'] | df['think_time'].isna()).mean():.1f}%")
    print(f"  total suspect:    {100 * df['is_suspect'].mean():.1f}%")
    print("\nThink-time distribution (clean plies only):")
    print(df.loc[~df["is_suspect"], "think_time"].describe())
    print(
        "\n^ Look at this before going further. If the low tail dominates, "
        "revisit PREMOVE_THRESHOLD_S."
    )
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pgn", required=True, type=Path)
    parser.add_argument("--player", required=True)
    args = parser.parse_args()

    df = parse_file(args.pgn, args.player)

    PARSED_DIR.mkdir(parents=True, exist_ok=True)
    out = PARSED_DIR / f"{args.player}_plies.parquet"
    df.to_parquet(out, index=False)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
