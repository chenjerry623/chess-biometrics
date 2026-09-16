"""Discover Chess.com players spanning diverse bullet rating bands, from a
large public club's member list, for building a rating-diverse
identification pool. One-off script, not part of the regular pipeline.

Usage:
    python -m scripts.discover_players --club team-usa --sample 120 --per-band 3
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import requests

HEADERS = {"User-Agent": "chess-clock-analyzer/0.1 (personal research project; contact: chenjerry623@gmail.com)"}

BANDS = [
    (0, 800), (800, 1100), (1100, 1400), (1400, 1700),
    (1700, 2000), (2000, 2300), (2300, 4000),
]


def get_members(club: str) -> list[str]:
    resp = requests.get(f"https://api.chess.com/pub/club/{club}/members", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    d = resp.json()
    return sorted({m["username"] for tier in d.values() for m in tier})


def get_bullet_rating(username: str) -> tuple[int, int] | None:
    """Returns (rating, n_games_estimate) or None if no usable bullet history."""
    try:
        resp = requests.get(f"https://api.chess.com/pub/player/{username}/stats", headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        d = resp.json()
        bullet = d.get("chess_bullet")
        if not bullet or "last" not in bullet:
            return None
        rating = bullet["last"]["rating"]
        n = bullet.get("record", {}).get("win", 0) + bullet.get("record", {}).get("loss", 0) + bullet.get("record", {}).get("draw", 0)
        return rating, n
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--club", default="team-usa")
    parser.add_argument("--sample", type=int, default=150, help="how many club members to probe")
    parser.add_argument("--per-band", type=int, default=3)
    parser.add_argument("--min-games", type=int, default=300)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--usernames-file", type=Path, default=None,
                         help="JSON list of usernames to probe instead of a club's member list")
    parser.add_argument("--exclude-file", type=Path, default=None,
                         help="JSON list of usernames to skip (already fetched elsewhere)")
    args = parser.parse_args()

    if args.usernames_file:
        print(f"Loading usernames from {args.usernames_file}...")
        members = json.loads(args.usernames_file.read_text())
        print(f"  {len(members)} candidates")
    else:
        print(f"Fetching member list from club '{args.club}'...")
        members = get_members(args.club)
        print(f"  {len(members)} total members")

    if args.exclude_file and args.exclude_file.exists():
        exclude = set(json.loads(args.exclude_file.read_text()))
        before = len(members)
        members = [m for m in members if m not in exclude]
        print(f"  excluded {before - len(members)} already-known usernames")

    rng = random.Random(args.seed)
    rng.shuffle(members)
    probe = members[: args.sample]

    by_band: dict[tuple[int, int], list[tuple[str, int, int]]] = {b: [] for b in BANDS}
    checked = 0
    for username in probe:
        result = get_bullet_rating(username)
        checked += 1
        if checked % 20 == 0:
            print(f"  probed {checked}/{len(probe)}...")
        if result is None:
            continue
        rating, n = result
        if n < args.min_games:
            continue
        for lo, hi in BANDS:
            if lo <= rating < hi:
                by_band[(lo, hi)].append((username, rating, n))
                break
        time.sleep(0.35)

    print("\nCandidates found per band:")
    selected = []
    for band, candidates in by_band.items():
        candidates.sort(key=lambda c: -c[2])  # prefer more games
        picked = candidates[: args.per_band]
        print(f"  {band}: {len(candidates)} candidates, picking {[c[0] for c in picked]}")
        selected.extend(picked)

    out = Path("data/discovered_players.json")
    out.write_text(json.dumps([{"username": u, "rating": r, "n_games": n} for u, r, n in selected], indent=2))
    print(f"\nWrote {len(selected)} selected players to {out}")


if __name__ == "__main__":
    main()
