"""Fetch a player's games from the Lichess API.

Differences from Chess.com that actually matter:
  1. Single streaming export endpoint (games/user/{username}) instead of
     paginated monthly archives — no serial-request rule, no pagination.
  2. Clock comments need clocks=true explicitly (Chess.com includes them by
     default for live games).
  3. TimeControl is always written "base+inc" — parse_pgn.py's
     parse_time_control() already handles both conventions.
  4. Game ID lives in the Site header, not Link — parse_pgn.py's _game_id()
     already handles this (falls back to Site when Link is absent).
  5. A descriptive User-Agent turned out to be required in practice too
     (like Chess.com), even though the published API spec doesn't document
     it — a generic client UA gets silently routed to the web app's 404
     page instead of reaching the API at all. A personal API token (env var
     LICHESS_TOKEN) separately raises the rate limit — use it for anything
     beyond a quick smoke test.

Per CLAUDE.md: Lichess is for cross-source validation and pool expansion,
never the primary training set for this project's core players. Never pool
Chess.com and Lichess rows without treating source as a feature — rating
scales and player pools differ between sites.

Usage:
    export LICHESS_TOKEN=...   # optional but recommended for real pulls
    python -m src.fetch_games --player YOURNAME --perf bullet
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import requests

RAW_DIR = Path("data/raw")
EXPORT_URL = "https://lichess.org/api/games/user/{username}"
LIVE_PERF_TYPES = {"bullet", "blitz", "rapid", "classical"}

# Required in practice, undocumented in the spec — a generic client UA gets
# silently routed to the web app's 404 page rather than reaching the API.
HEADERS_BASE = {
    "User-Agent": "chess-clock-research/0.1 (personal research project; contact: chenjerry623@gmail.com)"
}


def fetch_games(
    username: str,
    perf_type: str = "bullet",
    rated_only: bool = True,
    max_games: int | None = None,
) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"{username}_lichess_{perf_type}.pgn"

    token = os.environ.get("LICHESS_TOKEN")
    headers = {**HEADERS_BASE, "Accept": "application/x-chess-pgn"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        print("No LICHESS_TOKEN set — using the low, unauthenticated rate limit.")

    params: dict[str, str | int] = {
        "perfType": perf_type,
        "clocks": "true",
        "evals": "false",
        "opening": "true",
    }
    if rated_only:
        params["rated"] = "true"
    if max_games:
        params["max"] = max_games

    print(f"Streaming games for {username} ({perf_type})...")
    resp = requests.get(
        EXPORT_URL.format(username=username),
        headers=headers,
        params=params,
        stream=True,
        timeout=(30, 300),
    )
    if resp.status_code == 404:
        raise SystemExit(f"No such Lichess user: {username}")
    if resp.status_code == 429:
        raise SystemExit(
            "429 rate limited — set LICHESS_TOKEN (an API token raises the "
            "limit substantially) or wait before retrying."
        )
    resp.raise_for_status()

    kept = 0
    with out_path.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            if not chunk:
                continue
            fh.write(chunk)
            kept += chunk.count(b"[Event ")

    if kept == 0:
        raise SystemExit(
            f"No games written — check the username and that they have rated "
            f"{perf_type} games (public profile required)."
        )
    print(f"Wrote ~{kept} games to {out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player", required=True, help="Lichess username")
    parser.add_argument(
        "--perf", default="bullet", choices=sorted(LIVE_PERF_TYPES), dest="perf_type"
    )
    parser.add_argument("--max", type=int, default=None, dest="max_games")
    parser.add_argument("--include-unrated", action="store_true")
    args = parser.parse_args()

    fetch_games(
        username=args.player,
        perf_type=args.perf_type,
        rated_only=not args.include_unrated,
        max_games=args.max_games,
    )


if __name__ == "__main__":
    main()
