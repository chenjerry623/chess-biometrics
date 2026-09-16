"""Fetch each tracked player's LIVE current rating from Chess.com's public
stats API (https://api.chess.com/pub/player/{username}/stats), decoupled
entirely from PGN parsing.

Why this exists: build_profile()'s "current_rating" was computed from the
last recorded game's WhiteElo/BlackElo header — the rating ENTERING that
game, not the rating AFTER it. For a player who hasn't played since, that
gap is permanent (there's no "next game" to read a fresher pre-game Elo
from). Confirmed on cdznjerry: the PGN header said 2066 (rating entering
their last recorded game, Aug 2025), Chess.com's own live profile says
2100 (after that game's result was applied) — a real ~1.6% gap, not
noise, and it can only grow the longer a player stays inactive. This
script pulls the authoritative number directly from Chess.com instead of
re-deriving it from game history.

Cheap and safe to re-run often: one small JSON request per player (no PGN,
no Stockfish), same politeness convention as fetch_chesscom.py (serial,
0.4s between requests).

Usage:
    python -m scripts.refresh_current_ratings
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from scripts.export_dashboard_data import DATASETS

HEADERS = {
    "User-Agent": "chess-clock-analyzer/0.1 (personal research project; contact: chenjerry623@gmail.com)"
}
OUT_PATH = Path("data/raw/current_ratings.json")

MODE_TO_STATS_KEY = {"bullet": "chess_bullet", "blitz": "chess_blitz", "rapid": "chess_rapid"}


def fetch_stats(username: str) -> dict | None:
    resp = requests.get(
        f"https://api.chess.com/pub/player/{username.lower()}/stats",
        headers=HEADERS,
        timeout=30,
    )
    if resp.status_code == 404:
        print(f"  {username}: not found (404) — skipping")
        return None
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    ratings: dict[str, dict[str, dict]] = {}
    players = list(DATASETS.keys())
    print(f"Fetching live ratings for {len(players)} players...")
    for i, player in enumerate(players, 1):
        modes = DATASETS[player]
        try:
            stats = fetch_stats(player)
        except Exception as e:
            print(f"  [{i}/{len(players)}] {player}: FAILED ({type(e).__name__}: {e})")
            continue
        if not stats:
            continue
        ratings[player] = {}
        for mode in modes:
            key = MODE_TO_STATS_KEY.get(mode)
            last = stats.get(key, {}).get("last") if key else None
            if last and "rating" in last:
                ratings[player][mode] = {
                    "rating": last["rating"],
                    "as_of_unix": last.get("date"),
                }
        got = list(ratings[player].keys())
        print(f"  [{i}/{len(players)}] {player}: {got or 'no live rating found'}")
        time.sleep(0.4)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(ratings, indent=2))
    total = sum(len(v) for v in ratings.values())
    print(f"\nWrote {total} live ratings ({len(ratings)} players) to {OUT_PATH}")


if __name__ == "__main__":
    main()
