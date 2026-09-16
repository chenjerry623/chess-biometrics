"""One-off export: precompute every stat the dashboard artifact needs into a
single JSON file. Not part of the regular pipeline — run manually whenever
the dashboard needs a refresh after new data lands.

Usage:
    python -m scripts.export_dashboard_data
"""

from __future__ import annotations

import gc
import json
import random
from pathlib import Path

import numpy as np
import pandas as pd

FEATURES_DIR = Path("data/features")
INSIGHTS_DIR = Path("data/insights")
OUT_PATH = Path("/private/tmp/claude-501/-Users-jerrychen-Projects-chess-clock/8b9bcf87-b66e-4b98-9e9b-bba7f14095e2/scratchpad/dashboard/data.json")

# Populated by `python -m scripts.refresh_current_ratings` — Chess.com's
# live stats API, not derived from game history. Needed because a game's
# WhiteElo/BlackElo header is the rating ENTERING that game, not after it;
# for a player who hasn't played since, that gap between "last known
# pre-game Elo" and "actual current rating" is permanent and can be
# substantial (confirmed on cdznjerry: header said 2066, Chess.com's own
# live profile says 2100 — the player's real current rating). Missing
# entirely (script never run) or missing this specific player/mode both
# fall back to the old last-game-header estimate in build_profile().
_CURRENT_RATINGS_PATH = Path("data/raw/current_ratings.json")
CURRENT_RATINGS: dict = (
    json.loads(_CURRENT_RATINGS_PATH.read_text()) if _CURRENT_RATINGS_PATH.exists() else {}
)

DATASETS = {
    "cdznjerry": {"bullet": "cdznjerry_2023on_plies"},
    "BIG_TONKA_T": {
        "bullet": "BIG_TONKA_T_bullet_plies",
        "blitz": "BIG_TONKA_T_blitz_plies",
        "rapid": "BIG_TONKA_T_rapid_plies",
    },
    # Same real person as cdznjerry, a second Chess.com account used for
    # lower-rated play (1632-2190 vs. cdznjerry's 880-2119) — see
    # IDENTIFICATION_COHORTS below for why this is more than just another
    # pool entry.
    "jerrycdzn": {"bullet": "jerrycdzn_plies"},
    # A 6-player Lichess titled-player pool was tried here for a
    # rating-matched identification cohort and then REMOVED: Lichess's PGN
    # clock comments are whole-second resolution (Chess.com's are
    # decisecond), and at these players' fast bullet time controls that's
    # not just "less precise" — it's disqualifying. Confirmed directly:
    # ArtemChesSRU had think_time==0.0 on 58% of moves (the clock genuinely
    # cannot represent anything under 1s), which inflates is_premove far
    # beyond real premove behavior (the <=0.12s threshold, tuned for
    # Chess.com's finer resolution, can't distinguish a true instant
    # premove from a real ~0.7s decision once both round to 0). Every
    # think_time-derived stat for these players (phase-time, clutch,
    # premove rate) was unreliable, not just noisy — pulled entirely
    # rather than caveated. Chess.com-sourced players only, until a
    # same-resolution (Chess.com) rating-matched pool is built instead.
    # Celebrities / top professionals (Chess.com bullet, discovered batch —
    # see PROJECT_LOG.md "discovered_celebrities"). Each was fit as its OWN
    # per-player features/model/insights run, not pooled with the others —
    # see PROJECT_LOG.md "Caught before it shipped" for why that distinction
    # matters (a combined multi-player file silently cross-contaminates the
    # session/recency features and pools unrelated players' baselines).
    "hikaru": {"bullet": "hikaru_plies"},
    "magnuscarlsen": {"bullet": "magnuscarlsen_plies"},
    "fabianocaruana": {"bullet": "fabianocaruana_plies"},
    "firouzja2003": {"bullet": "firouzja2003_plies"},
    "lachesisq": {"bullet": "lachesisq_plies"},
    "danielnaroditsky": {"bullet": "danielnaroditsky_plies"},
    "gothamchess": {"bullet": "gothamchess_plies"},
    "anishgiri": {"bullet": "anishgiri_plies"},
    # Discovered player pool, group1: 31 players sampled across Chess.com
    # club membership + opponent-mining (see PROJECT_LOG.md
    # "discover_players.py"), spanning multiple rating bands including
    # untitled/lower-rated. Each fit as its own per-player run, same as
    # celebrities above.
    "alexanderkapogenio": {"bullet": "alexanderkapogenio_plies"},
    "caffy564": {"bullet": "caffy564_plies"},
    "chessetc": {"bullet": "chessetc_plies"},
    "cold_shoulder": {"bullet": "cold_shoulder_plies"},
    "cywuwu": {"bullet": "cywuwu_plies"},
    "divyanchampionship-1": {"bullet": "divyanchampionship-1_plies"},
    "em-thechessshark": {"bullet": "em-thechessshark_plies"},
    "fitz812": {"bullet": "fitz812_plies"},
    "jusgeode": {"bullet": "jusgeode_plies"},
    "letx": {"bullet": "letx_plies"},
    "louishenripaschalis": {"bullet": "louishenripaschalis_plies"},
    "lukino77": {"bullet": "lukino77_plies"},
    "mainemanfm": {"bullet": "mainemanfm_plies"},
    "mastermatthew52": {"bullet": "mastermatthew52_plies"},
    "matt_willym": {"bullet": "matt_willym_plies"},
    "puppymate": {"bullet": "puppymate_plies"},
    "racketeeree": {"bullet": "racketeeree_plies"},
    "samueljoshva": {"bullet": "samueljoshva_plies"},
    "sangamejay": {"bullet": "sangamejay_plies"},
    "savaggeone": {"bullet": "savaggeone_plies"},
    "sbcbri": {"bullet": "sbcbri_plies"},
    "sheldor2": {"bullet": "sheldor2_plies"},
    "sicarius1979": {"bullet": "sicarius1979_plies"},
    "simplechees": {"bullet": "simplechees_plies"},
    "skalybur": {"bullet": "skalybur_plies"},
    "swagasoclean": {"bullet": "swagasoclean_plies"},
    "tasiabella": {"bullet": "tasiabella_plies"},
    "thegranddameofdeath": {"bullet": "thegranddameofdeath_plies"},
    "trungwin315": {"bullet": "trungwin315_plies"},
    "tv35": {"bullet": "tv35_plies"},
    "wolf9057": {"bullet": "wolf9057_plies"},
    # group2: 30 more discovered players, same treatment as group1 above.
    "YperPaiktis": {"bullet": "YperPaiktis_plies"},
    "aximal": {"bullet": "aximal_plies"},
    "bensolo1": {"bullet": "bensolo1_plies"},
    "bhavin878": {"bullet": "bhavin878_plies"},
    "casper_belier": {"bullet": "casper_belier_plies"},
    "chessmastere4": {"bullet": "chessmastere4_plies"},
    "davit_tiraturyan": {"bullet": "davit_tiraturyan_plies"},
    "eeeooouuu": {"bullet": "eeeooouuu_plies"},
    "ericj2005": {"bullet": "ericj2005_plies"},
    "evad90": {"bullet": "evad90_plies"},
    "firerainbow": {"bullet": "firerainbow_plies"},
    "fjonleif04": {"bullet": "fjonleif04_plies"},
    "furiously-fast": {"bullet": "furiously-fast_plies"},
    "gmvidura": {"bullet": "gmvidura_plies"},
    "hoangjr": {"bullet": "hoangjr_plies"},
    "hopertz": {"bullet": "hopertz_plies"},
    "itsinitiative": {"bullet": "itsinitiative_plies"},
    "jpequens": {"bullet": "jpequens_plies"},
    "karthikcheckmate": {"bullet": "karthikcheckmate_plies"},
    "krisztina_libik": {"bullet": "krisztina_libik_plies"},
    "mavaddat": {"bullet": "mavaddat_plies"},
    "mervecix": {"bullet": "mervecix_plies"},
    "moodymountain": {"bullet": "moodymountain_plies"},
    "pawnarrow": {"bullet": "pawnarrow_plies"},
    "rsljsl": {"bullet": "rsljsl_plies"},
    "sjoshua2008": {"bullet": "sjoshua2008_plies"},
    "sumedham95": {"bullet": "sumedham95_plies"},
    "thenoise": {"bullet": "thenoise_plies"},
    "wilder12": {"bullet": "wilder12_plies"},
    "williamschill": {"bullet": "williamschill_plies"},
    # group0: 31 more discovered players, same treatment as group1/group2.
    "ChessproUSA": {"bullet": "ChessproUSA_plies"},
    "GK150": {"bullet": "GK150_plies"},
    "JuiceboxJoints": {"bullet": "JuiceboxJoints_plies"},
    "LDog17": {"bullet": "LDog17_plies"},
    "Mario86": {"bullet": "Mario86_plies"},
    "abhi-k": {"bullet": "abhi-k_plies"},
    "achmines": {"bullet": "achmines_plies"},
    "alehin": {"bullet": "alehin_plies"},
    "anurag_b": {"bullet": "anurag_b_plies"},
    "arjun_07_07": {"bullet": "arjun_07_07_plies"},
    "ashwa2e3": {"bullet": "ashwa2e3_plies"},
    "beg8878": {"bullet": "beg8878_plies"},
    "dylanfitzpatrick": {"bullet": "dylanfitzpatrick_plies"},
    "gm_chase2010": {"bullet": "gm_chase2010_plies"},
    "gradaschi": {"bullet": "gradaschi_plies"},
    "jbaez": {"bullet": "jbaez_plies"},
    "lancdaddy": {"bullet": "lancdaddy_plies"},
    "mr_pacheco": {"bullet": "mr_pacheco_plies"},
    "muckelchen": {"bullet": "muckelchen_plies"},
    "neeraj_ak": {"bullet": "neeraj_ak_plies"},
    "nicoaaa": {"bullet": "nicoaaa_plies"},
    "one_min_mania": {"bullet": "one_min_mania_plies"},
    "panchoponcho": {"bullet": "panchoponcho_plies"},
    "pminear": {"bullet": "pminear_plies"},
    "rds197719": {"bullet": "rds197719_plies"},
    "salman_ali_khan": {"bullet": "salman_ali_khan_plies"},
    "scott-fox21": {"bullet": "scott-fox21_plies"},
    "sushantrider": {"bullet": "sushantrider_plies"},
    "teyyub": {"bullet": "teyyub_plies"},
    "yumet023": {"bullet": "yumet023_plies"},
    "zeekybeans": {"bullet": "zeekybeans_plies"},
    # group3: 24 more discovered players (team-europe club), same treatment.
    "aggelosvelo": {"bullet": "aggelosvelo_plies"},
    "antoninosota": {"bullet": "antoninosota_plies"},
    "berettar": {"bullet": "berettar_plies"},
    "danielbolk": {"bullet": "danielbolk_plies"},
    "denizozyurek": {"bullet": "denizozyurek_plies"},
    "dragongana": {"bullet": "dragongana_plies"},
    "gratch": {"bullet": "gratch_plies"},
    "joltarn": {"bullet": "joltarn_plies"},
    "juan1992": {"bullet": "juan1992_plies"},
    "jusef": {"bullet": "jusef_plies"},
    "kcdriver": {"bullet": "kcdriver_plies"},
    "l0x": {"bullet": "l0x_plies"},
    "mizukyu0": {"bullet": "mizukyu0_plies"},
    "p3tter4": {"bullet": "p3tter4_plies"},
    "runchess": {"bullet": "runchess_plies"},
    "seanpetrosian": {"bullet": "seanpetrosian_plies"},
    "sirlumbreras": {"bullet": "sirlumbreras_plies"},
    "skanaska": {"bullet": "skanaska_plies"},
    "superbuddha": {"bullet": "superbuddha_plies"},
    "thakilan": {"bullet": "thakilan_plies"},
    "ulikcnarf": {"bullet": "ulikcnarf_plies"},
    "venca1234": {"bullet": "venca1234_plies"},
    "worgplayer": {"bullet": "worgplayer_plies"},
    "yossy45": {"bullet": "yossy45_plies"},
}

IDENTIFICATION_COHORTS = {
    # jerrycdzn was run as a THIRD, separate identification class briefly —
    # it's the same real person as cdznjerry (a second account, different
    # rating), and that test is exactly what confirmed the model tracks
    # the person, not the account (cdznjerry's misattributed games went to
    # jerrycdzn 67.6% of the time vs. 17.2% to BIG_TONKA_T — see
    # PROJECT_LOG.md). Having proven that, the user asked to stop treating
    # them as separate identities for identification specifically — they
    # ARE the same person, so back to a clean 2-way cohort, with the two
    # accounts' games merged into one combined identity (more data for
    # that class, not less). jerrycdzn keeps its own separate PROFILE in
    # DATASETS above — this only affects the identification cohort.
    "primary": {
        "cdznjerry": "cdznjerry_combined_plies",
        "BIG_TONKA_T": "BIG_TONKA_T_bullet_plies",
    },
    # The many-class version: every player whose full pipeline (Stockfish +
    # Maia + features) is done as of 2026-09-14 — celebrities + group1 (31
    # discovered players) + the two original identities. group0/2/3 join
    # this cohort once their pipelines finish; build_identification() itself
    # needed ZERO changes to go from 2-way to N-way — it was written
    # generically from the start (unique_players = sorted(set(y)), the
    # coef_.shape[0]==1 branch for binary vs. one-vs-rest weights, the N×N
    # confusion matrix) specifically so this expansion wouldn't require a
    # rewrite. Game counts range from ~335 (tasiabella) to ~5,743
    # (cdznjerry_combined) — StratifiedKFold(5) only needs 5+ games in the
    # smallest class, comfortably satisfied, so no players had to be
    # dropped for insufficient data.
    "pool": {
        "cdznjerry": "cdznjerry_combined_plies",
        "BIG_TONKA_T": "BIG_TONKA_T_bullet_plies",
        "hikaru": "hikaru_plies",
        "magnuscarlsen": "magnuscarlsen_plies",
        "fabianocaruana": "fabianocaruana_plies",
        "firouzja2003": "firouzja2003_plies",
        "lachesisq": "lachesisq_plies",
        "danielnaroditsky": "danielnaroditsky_plies",
        "gothamchess": "gothamchess_plies",
        "anishgiri": "anishgiri_plies",
        "alexanderkapogenio": "alexanderkapogenio_plies",
        "caffy564": "caffy564_plies",
        "chessetc": "chessetc_plies",
        "cold_shoulder": "cold_shoulder_plies",
        "cywuwu": "cywuwu_plies",
        "divyanchampionship-1": "divyanchampionship-1_plies",
        "em-thechessshark": "em-thechessshark_plies",
        "fitz812": "fitz812_plies",
        "jusgeode": "jusgeode_plies",
        "letx": "letx_plies",
        "louishenripaschalis": "louishenripaschalis_plies",
        "lukino77": "lukino77_plies",
        "mainemanfm": "mainemanfm_plies",
        "mastermatthew52": "mastermatthew52_plies",
        "matt_willym": "matt_willym_plies",
        "puppymate": "puppymate_plies",
        "racketeeree": "racketeeree_plies",
        "samueljoshva": "samueljoshva_plies",
        "sangamejay": "sangamejay_plies",
        "savaggeone": "savaggeone_plies",
        "sbcbri": "sbcbri_plies",
        "sheldor2": "sheldor2_plies",
        "sicarius1979": "sicarius1979_plies",
        "simplechees": "simplechees_plies",
        "skalybur": "skalybur_plies",
        "swagasoclean": "swagasoclean_plies",
        "tasiabella": "tasiabella_plies",
        "thegranddameofdeath": "thegranddameofdeath_plies",
        "trungwin315": "trungwin315_plies",
        "tv35": "tv35_plies",
        "wolf9057": "wolf9057_plies",
        "YperPaiktis": "YperPaiktis_plies",
        "aximal": "aximal_plies",
        "bensolo1": "bensolo1_plies",
        "bhavin878": "bhavin878_plies",
        "casper_belier": "casper_belier_plies",
        "chessmastere4": "chessmastere4_plies",
        "davit_tiraturyan": "davit_tiraturyan_plies",
        "eeeooouuu": "eeeooouuu_plies",
        "ericj2005": "ericj2005_plies",
        "evad90": "evad90_plies",
        "firerainbow": "firerainbow_plies",
        "fjonleif04": "fjonleif04_plies",
        "furiously-fast": "furiously-fast_plies",
        "gmvidura": "gmvidura_plies",
        "hoangjr": "hoangjr_plies",
        "hopertz": "hopertz_plies",
        "itsinitiative": "itsinitiative_plies",
        "jpequens": "jpequens_plies",
        "karthikcheckmate": "karthikcheckmate_plies",
        "krisztina_libik": "krisztina_libik_plies",
        "mavaddat": "mavaddat_plies",
        "mervecix": "mervecix_plies",
        "moodymountain": "moodymountain_plies",
        "pawnarrow": "pawnarrow_plies",
        "rsljsl": "rsljsl_plies",
        "sjoshua2008": "sjoshua2008_plies",
        "sumedham95": "sumedham95_plies",
        "thenoise": "thenoise_plies",
        "wilder12": "wilder12_plies",
        "williamschill": "williamschill_plies",
        "ChessproUSA": "ChessproUSA_plies",
        "GK150": "GK150_plies",
        "JuiceboxJoints": "JuiceboxJoints_plies",
        "LDog17": "LDog17_plies",
        "Mario86": "Mario86_plies",
        "abhi-k": "abhi-k_plies",
        "achmines": "achmines_plies",
        "alehin": "alehin_plies",
        "anurag_b": "anurag_b_plies",
        "arjun_07_07": "arjun_07_07_plies",
        "ashwa2e3": "ashwa2e3_plies",
        "beg8878": "beg8878_plies",
        "dylanfitzpatrick": "dylanfitzpatrick_plies",
        "gm_chase2010": "gm_chase2010_plies",
        "gradaschi": "gradaschi_plies",
        "jbaez": "jbaez_plies",
        "lancdaddy": "lancdaddy_plies",
        "mr_pacheco": "mr_pacheco_plies",
        "muckelchen": "muckelchen_plies",
        "neeraj_ak": "neeraj_ak_plies",
        "nicoaaa": "nicoaaa_plies",
        "one_min_mania": "one_min_mania_plies",
        "panchoponcho": "panchoponcho_plies",
        "pminear": "pminear_plies",
        "rds197719": "rds197719_plies",
        "salman_ali_khan": "salman_ali_khan_plies",
        "scott-fox21": "scott-fox21_plies",
        "sushantrider": "sushantrider_plies",
        "teyyub": "teyyub_plies",
        "yumet023": "yumet023_plies",
        "zeekybeans": "zeekybeans_plies",
        "aggelosvelo": "aggelosvelo_plies",
        "antoninosota": "antoninosota_plies",
        "berettar": "berettar_plies",
        "danielbolk": "danielbolk_plies",
        "denizozyurek": "denizozyurek_plies",
        "dragongana": "dragongana_plies",
        "gratch": "gratch_plies",
        "joltarn": "joltarn_plies",
        "juan1992": "juan1992_plies",
        "jusef": "jusef_plies",
        "kcdriver": "kcdriver_plies",
        "l0x": "l0x_plies",
        "mizukyu0": "mizukyu0_plies",
        "p3tter4": "p3tter4_plies",
        "runchess": "runchess_plies",
        "seanpetrosian": "seanpetrosian_plies",
        "sirlumbreras": "sirlumbreras_plies",
        "skanaska": "skanaska_plies",
        "superbuddha": "superbuddha_plies",
        "thakilan": "thakilan_plies",
        "ulikcnarf": "ulikcnarf_plies",
        "venca1234": "venca1234_plies",
        "worgplayer": "worgplayer_plies",
        "yossy45": "yossy45_plies",
    },
}
# Lichess is dropped entirely, per explicit user instruction — not just
# from profiles/DATASETS but from identification too. Not revisiting this;
# the 6 titled players' feature/model files are left on disk unused rather
# than deleted, in case they're wanted for something else later, but no
# code here builds a cohort from them.


def _f(x):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return None
    return float(x)


def build_profile(stem: str, player: str | None = None, mode: str | None = None) -> dict:
    df = pd.read_parquet(FEATURES_DIR / f"{stem}_features.parquet")
    ins = pd.read_parquet(INSIGHTS_DIR / f"{stem}_insights.parquet")
    clean = df[~df["is_suspect"]].dropna(subset=["think_time"])
    obvious = df["is_recapture"] | df["is_forced"]

    # --- basic summary ---
    has_dates = df["game_date"].notna().any()
    pregame_rating = None
    if has_dates:
        pregame_rating = _f(df.sort_values("game_date")["player_rating"].iloc[-1])

    # Prefer the live Chess.com rating over the last-recorded-game's
    # pre-game Elo header whenever we have one — see CURRENT_RATINGS'
    # module-level comment for why those two numbers genuinely differ,
    # not just noise. current_rating_source is surfaced in the exported
    # JSON so the dashboard can label which one is being shown.
    live = CURRENT_RATINGS.get(player, {}).get(mode) if player and mode else None
    if live is not None:
        current_rating = _f(live["rating"])
        current_rating_source = "live"
        current_rating_as_of = (
            pd.Timestamp(live["as_of_unix"], unit="s").date().isoformat()
            if live.get("as_of_unix") else None
        )
    else:
        current_rating = pregame_rating
        current_rating_source = "last_game_pregame"
        current_rating_as_of = str(df["game_date"].max().date()) if has_dates else None

    summary = {
        "n_games": int(df["game_id"].nunique()),
        "n_plies": int(len(df)),
        "rating_min": _f(df["player_rating"].min()),
        "rating_max": _f(df["player_rating"].max()),
        "current_rating": current_rating,
        "current_rating_source": current_rating_source,  # "live" (Chess.com stats API) or "last_game_pregame" (fallback)
        "current_rating_as_of": current_rating_as_of,
        "date_min": str(df["game_date"].min().date()) if has_dates else None,
        "date_max": str(df["game_date"].max().date()) if has_dates else None,
    }

    # --- buckets (from insights.py) ---
    buckets_overall = ins["bucket"].value_counts(normalize=True).round(4).to_dict()
    crit_sub = ins[ins["is_critical"]]
    buckets_critical = crit_sub["bucket"].value_counts(normalize=True).round(4).to_dict()

    # --- premove profile ---
    premove_path = INSIGHTS_DIR / f"{stem}_premove_profile.json"
    premove = json.loads(premove_path.read_text()) if premove_path.exists() else {}

    # --- time by game phase ("what situations they spend time in") ---
    # Mean, not median: Lichess's PGN clock comments are whole-second
    # resolution (Chess.com's are decisecond), and several of these
    # players' games run fast enough that the median think_time degenerates
    # to exactly the 1-second floor in every phase, hiding real variation
    # the mean still shows (confirmed directly: nihalsarin2004's endgame
    # median was a flat 1.0s despite mean 1.18s vs middlegame's 1.79s).
    phase_time = {}
    for phase in ["opening", "middlegame", "endgame"]:
        sub = clean[clean["game_phase"] == phase]
        phase_time[phase] = {
            "mean_think_time": _f(sub["think_time"].mean()),
            "share_of_moves": _f(len(sub) / len(clean)) if len(clean) else None,
        }

    # --- recognition: accuracy vs decisiveness (gap_1_2 quintile) ---
    d2 = clean.dropna(subset=["gap_1_2", "played_best_move"])
    recognition_curve = []
    if len(d2) > 200:
        q = pd.qcut(d2["gap_1_2"], 5, labels=False, duplicates="drop")
        acc_by_q = d2.groupby(q)["played_best_move"].mean()
        for qi, acc in acc_by_q.items():
            recognition_curve.append({"quintile": int(qi) + 1, "p_best_move": _f(acc)})
    # Instinctive rate on critical positions specifically — the "recognize it
    # without needing extra time" number.
    instinctive_rate_critical = _f(buckets_critical.get("instinctive"))

    # --- the core relationship this whole project is about: does think-
    # time actually respond to position difficulty, and does responding
    # pay off in accuracy? Same gap_1_2/quintile axis as recognition_curve
    # (low gap = harder decision, top two moves are close) so the two are
    # directly comparable, but this one plots the RESPONSE (time spent,
    # accuracy achieved) instead of just recognition. Never previously
    # surfaced as a continuous curve — only as discrete overthought/
    # underthought/panic/clean bucket percentages.
    d3 = clean.dropna(subset=["gap_1_2"])
    complexity_response_curve = []
    if len(d3) > 200:
        q3 = pd.qcut(d3["gap_1_2"], 5, labels=False, duplicates="drop")
        think_by_q = d3.groupby(q3)["think_time"].mean()
        cploss_by_q = d3.groupby(q3)["cp_loss"].mean()
        for qi in think_by_q.index:
            complexity_response_curve.append({
                "quintile": int(qi) + 1,
                "mean_think_time": _f(think_by_q.loc[qi]),
                "mean_cp_loss": _f(cploss_by_q.loc[qi]) if qi in cploss_by_q.index else None,
            })

    # --- clutch: does accuracy/speed hold up under time pressure ---
    low = clean[clean["low_time"]]
    normal = clean[~clean["low_time"]]
    clutch = {
        "low_time_share": _f(clean["low_time"].mean()),
        "cp_loss_low_time": _f(low["cp_loss"].mean()),
        "cp_loss_normal_time": _f(normal["cp_loss"].mean()),
        "panic_rate_low_time": _f(ins.loc[ins["low_time"], "bucket"].eq("panic").mean()) if "low_time" in ins.columns else None,
        "panic_rate_normal_time": _f(ins.loc[~ins["low_time"], "bucket"].eq("panic").mean()) if "low_time" in ins.columns else None,
    }

    # --- sacrifice behavior ---
    sac = clean[clean["is_sacrifice"]]
    non_sac = clean[~clean["is_sacrifice"]]
    sacrifice = {
        "rate": _f(clean["is_sacrifice"].mean()),
        "mean_think_time_sacrifice": _f(sac["think_time"].mean()),
        "mean_think_time_non_sacrifice": _f(non_sac["think_time"].mean()),
    }

    # --- Maia human-move-likelihood profile ---
    maia_rows = clean.dropna(subset=["maia_prob_played"])
    maia = {
        "mean_prob_played": _f(maia_rows["maia_prob_played"].mean()),
        "mean_surprise": _f(maia_rows["maia_surprise"].mean()),
        "top_choice_rate": _f(maia_rows["maia_is_top_choice"].mean()),
        "overriding_instinct_rate": _f(maia_rows["requires_overriding_instinct"].mean()),
    }

    # --- skewer sensitivity ---
    skewer = {
        "own_skewer_prevalence": _f((clean["own_skewers"] > 0).mean()),
        "opp_skewer_prevalence": _f((clean["opp_skewers"] > 0).mean()),
        "own_skewer_corr": _f(clean["own_skewers"].corr(clean["think_time"], method="spearman")),
        "opp_skewer_corr": _f(clean["opp_skewers"].corr(clean["think_time"], method="spearman")),
    }

    # --- top SHAP drivers, if a live/descriptive model exists ---
    top_shap = []
    r2 = None
    for suffix in ("thinktime_live", "thinktime_descriptive"):
        model_path = Path(f"data/models/{stem}_{suffix}.txt")
        if model_path.exists():
            try:
                import lightgbm as lgb
                import shap
                from src.model import COMPLEXITY_FEATURES, CLOCK_FEATURES, _prep_X, _time_split

                dfa = pd.read_parquet(FEATURES_DIR / f"{stem}_features.parquet")
                dfa = dfa[~dfa["is_suspect"]].dropna(subset=["think_time", "log_think_time", "player_rating"])
                feats = [c for c in COMPLEXITY_FEATURES + CLOCK_FEATURES + ["player_rating"] if c in dfa.columns]
                _, test = _time_split(dfa)
                test = test.sample(min(2000, len(test)), random_state=0)
                X_test = _prep_X(test, feats)
                booster = lgb.Booster(model_file=str(model_path))
                explainer = shap.TreeExplainer(booster)
                shap_values = explainer.shap_values(X_test)
                importance = pd.Series(np.abs(shap_values).mean(axis=0), index=feats).sort_values(ascending=False)
                top_shap = [{"feature": k, "importance": _f(v)} for k, v in importance.head(8).items()]
            except Exception as e:
                top_shap = []
            break

    profile = {
        "summary": summary,
        "buckets_overall": {k: _f(v) for k, v in buckets_overall.items()},
        "buckets_critical": {k: _f(v) for k, v in buckets_critical.items()},
        "premove": premove,
        "phase_time": phase_time,
        "recognition_curve": recognition_curve,
        "complexity_response_curve": complexity_response_curve,
        "instinctive_rate_critical": instinctive_rate_critical,
        "clutch": clutch,
        "sacrifice": sacrifice,
        "maia": maia,
        "skewer": skewer,
        "top_shap": top_shap,
    }
    profile["ratings"] = compute_ratings(profile)
    return profile


# --------------------------------------------------------------------------
# 0-100 "card" ratings — NBA2K-style attribute scores.
#
# Deliberately NOT percentile rank against whoever happens to be in the pool
# right now: with 2 players, percentile rank degenerates to one player
# always at 0 and the other at 100 on every attribute, and worse, a
# player's rating would silently shift every time someone new is added —
# a real problem for a card that's supposed to mean something on its own.
# Instead, each attribute is mapped from its raw stat onto a FIXED,
# domain-reasoned [lo, hi] anchor (chosen from the actual spread observed
# across this project's real players so far, not arbitrary) via linear
# interpolation, clipped to [0, 100]. Ratings stay comparable as the pool
# grows; they just won't span the full 0-100 range until a wider variety
# of real players populates the tails. Revisit anchors once more players
# exist — these are a reasonable first pass, not a permanent calibration.
# --------------------------------------------------------------------------

RATING_ATTRIBUTES = [
    # (key, label, lo, hi, higher_is_better, raw_format, one-line description, formula note)
    # Deliberately ONLY genuine time-usage / position-recognition SKILLS —
    # not descriptive style traits. Maia-likelihood ("Intuition") and
    # sacrifice rate ("Aggression") were cut from this list on direct user
    # feedback: matching the statistically most common human move isn't a
    # skill (a strong player deviating from it can still be objectively
    # right), and sacrifice rate is a style choice, not a competency. Both
    # remain visible in the raw-numbers panels below, just not as a
    # headline "card" rating.
    # lo/hi recalibrated 2026-09-13 from the actual observed distribution
    # across the full 44-player pool (see scripts/calibrate_ratings.py) —
    # the original bounds were guessed when only cdznjerry/BIG_TONKA_T
    # existed and drifted out of sync as the pool grew to include much
    # stronger (Hikaru, Magnus) and much weaker (untitled/lower-rated
    # discovered) players. Method: padded min/max (not raw min/max, so the
    # current extremes don't sit at a hard 0/100; not percentiles either,
    # given the pool is still only 44 entries — a percentile cutoff would
    # clip 2-3 real players at each end for no good reason at this size).
    # "recognition" was the worst-miscalibrated: the old hi=0.40 was 26%
    # above the actual observed max (0.317), so even the single best
    # recognizer in the whole pool could only ever score ~76/100.
    ("recognition", "Recognition", 0.07, 0.34, True, "pct",
     "Finds the right move fast on genuinely critical positions, without needing extra time.",
     "% of critical positions played both quickly AND correctly (the “Instinctive” bucket rate)."),
    ("efficiency", "Efficiency", 0.28, 0.59, True, "pct",
     "Time spent roughly matches how much the position actually demanded, across the whole game.",
     "% of ALL moves where allocation matched criticality (the “Clean” bucket rate)."),
    ("clutch", "Clutch", 0.09, 0.59, True, "ratio",
     "How little accuracy drops once the clock gets dangerously low.",
     "Ratio of centipawns lost at normal time ÷ centipawns lost under 15% time — closer to 1.0 means barely any drop-off."),
    ("composure", "Composure", 0.06, 0.35, False, "pct",
     "Staying out of the worst bucket: spending real time AND still getting it wrong.",
     "Inverse of overall Panic-bucket rate."),
    ("discipline", "Discipline", 0.00, 0.21, False, "pct",
     "Not premoving on positions that were actually critical — a real risk, not just a habit.",
     "Inverse of premove rate specifically on critical positions."),
]

# Descriptive/style traits — real, interesting, but not "skill" ratings per
# the user's explicit call. Computed the same way (fixed anchors, 0-100)
# but surfaced only in the raw-numbers detail, not the headline card.
STYLE_ATTRIBUTES = [
    # Recalibrated alongside RATING_ATTRIBUTES above — same 44-player pool,
    # same padded-min/max method.
    ("intuition", "Intuition", 0.27, 0.51, True, "pct",
     "How often their move matches Maia's single most human-likely move at their rating.",
     "% of moves that were the single most probable human move (Maia neural net, tuned to their rating)."),
    ("aggression", "Aggression", 0.01, 0.09, True, "pct",
     "Rate of sound, engine-confirmed material sacrifices — a style trait, not a value judgment.",
     "% of moves that voluntarily offer material and hold up to engine review (is_sacrifice)."),
]


def _rating_score(raw: float | None, lo: float, hi: float, higher_is_better: bool) -> int | None:
    if raw is None:
        return None
    x = (raw - lo) / (hi - lo)
    x = max(0.0, min(1.0, x))
    if not higher_is_better:
        x = 1.0 - x
    return round(x * 100)


def _build_rating_dict(attrs: list, raw_values: dict) -> dict:
    out = {}
    for key, label, lo, hi, higher_is_better, raw_format, desc, formula in attrs:
        out[key] = {
            "score": _rating_score(raw_values[key], lo, hi, higher_is_better),
            "raw": _f(raw_values[key]),
            "label": label,
            "description": desc,
            "formula": formula,
            "lo": lo,
            "hi": hi,
            "higher_is_better": higher_is_better,
            "raw_format": raw_format,
        }
    return out


def _raw_rating_values(profile: dict) -> dict:
    """The raw (pre-scoring) value behind every rating attribute, keyed the
    same as RATING_ATTRIBUTES/STYLE_ATTRIBUTES. Factored out of
    compute_ratings() so the lo/hi calibration pass below (see
    `calibrate_rating_bounds` in this file's __main__) can compute each
    attribute's real distribution across the whole player pool using the
    exact same raw-value logic the live scoring uses — recalibrating
    against a different set of numbers than what's actually scored would
    silently miscalibrate the scale."""
    return {
        "recognition": profile["instinctive_rate_critical"],
        "efficiency": profile["buckets_overall"].get("clean"),
        "clutch": (
            profile["clutch"]["cp_loss_normal_time"] / profile["clutch"]["cp_loss_low_time"]
            if profile["clutch"]["cp_loss_low_time"] else None
        ),
        "composure": profile["buckets_overall"].get("panic"),
        "discipline": profile["premove"].get("premove_rate_on_critical"),
        "intuition": profile["maia"]["top_choice_rate"],
        "aggression": profile["sacrifice"]["rate"],
    }


def compute_ratings(profile: dict) -> dict:
    """Returns {"skills": {... 5 headline skill ratings}, "style": {...
    descriptive traits, not skills}} — see RATING_ATTRIBUTES/STYLE_ATTRIBUTES
    docstring above for why these are kept separate."""
    raw_values = _raw_rating_values(profile)
    return {
        "skills": _build_rating_dict(RATING_ATTRIBUTES, raw_values),
        "style": _build_rating_dict(STYLE_ATTRIBUTES, raw_values),
    }


# Continuous features get BOTH mean and std per game — consistency/
# volatility is itself a candidate personal trait (a player who is always
# ~1.5s vs. one who swings 0.3s-6s can have the same mean and look
# identical on mean-only features). Rate/count features (already bounded,
# mostly 0/1 or small integers) keep mean only — a std of a near-binary
# rate carries little extra information relative to the doubled feature
# count and CV cost.
IDENT_FEATURES_CONTINUOUS = ["think_time", "cp_loss", "maia_surprise", "maia_prob_played", "n_captures_available"]
IDENT_FEATURES_RATE = ["low_time", "is_sacrifice", "own_skewers", "opp_skewers"]
IDENT_FEATURES = IDENT_FEATURES_CONTINUOUS + IDENT_FEATURES_RATE  # kept for callers that need the raw ply-level columns

# Must match the dashboard's own CONFUSION_MATRIX_MAX_CLASSES (index.html)
# — that's the point past which the UI stops rendering a full grid and
# switches to a top-confusions list, so it's also the point past which
# storing a full N×N confusion dict server-side is pure waste.
CONFUSION_MATRIX_STORE_FULL_MAX = 12


def _per_game_features(all_df: pd.DataFrame) -> pd.DataFrame:
    """Per-game feature vector from the FULL game, not a truncated sample —
    every clean ply the player made in that game feeds mean/std. Bullet
    games are just short (15-50 of the tracked player's own moves is
    normal), so a low n_plies is the real game length, not a subsampling
    artifact.

    Adds a genuinely ORDER-AWARE signal on top of the plain mean/std:
    "_trend" = second-half mean minus first-half mean, per continuous
    feature. Two players can have identical overall averages while one
    speeds up and the other slows down as the game progresses (fatigue,
    warm-up, time-pressure creep) — mean/std alone are blind to that since
    both treat a game as an unordered bag of plies; trend is the cheapest
    way to use move ORDER without a full sequence model.
    """
    cont = all_df.groupby(["player", "game_id"])[IDENT_FEATURES_CONTINUOUS].agg(["mean", "std"])
    cont.columns = [f"{col}_{stat}" for col, stat in cont.columns]
    cont = cont.reset_index()
    rate = all_df.groupby(["player", "game_id"])[IDENT_FEATURES_RATE].mean().reset_index()

    ordered = all_df.sort_values(["player", "game_id", "ply"]).copy()
    grp = ordered.groupby(["player", "game_id"])
    rank_frac = grp.cumcount() / (grp["ply"].transform("count") - 1).clip(lower=1)
    ordered["half"] = np.where(rank_frac < 0.5, "first", "second")
    half_means = (
        ordered.groupby(["player", "game_id", "half"])[IDENT_FEATURES_CONTINUOUS]
        .mean()
        .unstack("half")
    )
    trend = pd.DataFrame(index=half_means.index)
    for feat in IDENT_FEATURES_CONTINUOUS:
        trend[f"{feat}_trend"] = half_means[(feat, "second")] - half_means[(feat, "first")]
    trend = trend.reset_index()

    # Two more axes that are NOT proxies for anything above: WHERE in the
    # game the clock gets spent, and WHEN/how often the player shows up to
    # play — both genuinely personal habits, independent of move quality
    # or per-move speed (verified: adding these moved same-skill-band
    # identification accuracy up, not just full-pool accuracy, which is
    # the check that rules out these just being another skill proxy — see
    # PROJECT_LOG.md).
    phase_time = (
        all_df.groupby(["player", "game_id", "game_phase"])["think_time"].sum().unstack("game_phase", fill_value=0.0)
    )
    for ph in ("opening", "middlegame", "endgame"):
        if ph not in phase_time.columns:
            phase_time[ph] = 0.0
    phase_total = phase_time[["opening", "middlegame", "endgame"]].sum(axis=1).clip(lower=1e-9)
    phase_share = phase_time[["opening", "middlegame", "endgame"]].div(phase_total, axis=0)
    phase_share.columns = [f"{c}_time_share" for c in phase_share.columns]
    phase_share = phase_share.reset_index()

    # session_game_index / minutes_since_prev_game are constant within a
    # game (computed once per game, not per ply) — take the first value.
    # minutes_since_prev_game is heavily right-skewed (a few-minute gap vs.
    # multi-day gaps) so log1p keeps a handful of huge outlier gaps from
    # dominating after standardization; NaN means "first game of a fresh
    # session" (see features.py's add_session_features docstring) — a
    # LONG gap, not a zero one, so it's filled with log1p(1440) (24h) as a
    # deliberately large placeholder, not 0.
    pacing = all_df.groupby(["player", "game_id"])[["session_game_index", "minutes_since_prev_game"]].first()
    pacing["minutes_since_prev_game"] = pacing["minutes_since_prev_game"].fillna(1440.0)
    pacing["log_minutes_since_prev_game"] = np.log1p(pacing["minutes_since_prev_game"])
    pacing = pacing[["session_game_index", "log_minutes_since_prev_game"]].reset_index()

    # low_time_think_ratio — a per-game "panic ratio": mean think_time once
    # inside the danger zone (existing `low_time` flag, clock_frac<0.15)
    # over mean think_time outside it. Distinct from the player-level
    # `clutch` stat already in profiles (cp_loss-based, whole-history) —
    # this is per-GAME and think-TIME-based, so it can vary game to game
    # and isn't just re-deriving the same number. 1.0 (no observed change)
    # when a game never reached low time, not 0 — 0 would falsely claim
    # "instant premoves under pressure" for a game that never got there.
    lt = all_df.groupby(["player", "game_id", "low_time"])["think_time"].mean().unstack("low_time")
    lt = lt.rename(columns={False: "tt_normal", True: "tt_low"})
    for col in ("tt_normal", "tt_low"):
        if col not in lt.columns:
            lt[col] = np.nan
    lt["low_time_think_ratio"] = (lt["tt_low"] / lt["tt_normal"].replace(0, np.nan)).fillna(1.0)
    low_time_ratio = lt[["low_time_think_ratio"]].reset_index()

    def _safe_corr(a: pd.Series, b: pd.Series) -> float:
        if len(a) < 3 or a.std() == 0 or b.std() == 0:
            return np.nan
        return a.corr(b)

    # rhythm_autocorr — lag-1 autocorrelation of think_time within the
    # game: a steady, even pace autocorrelates differently than a
    # fast/slow/fast/slow rhythm, invisible to mean/std/trend which all
    # treat a game as an unordered bag of plies.
    rhythm = (
        ordered.groupby(["player", "game_id"])["think_time"]
        .apply(lambda s: _safe_corr(s, s.shift(1)))
        .rename("rhythm_autocorr")
        .reset_index()
    )

    # tempo_match_corr — does this player's own think_time track their
    # OPPONENT's previous move time (mirroring/tempo-matching), or stay
    # decoupled from it? `opp_think_time_prev` already existed as a
    # complexity-model feature; never used for identification before.
    tempo = (
        ordered.groupby(["player", "game_id"])
        .apply(lambda g: _safe_corr(g["think_time"], g["opp_think_time_prev"]), include_groups=False)
        .rename("tempo_match_corr")
        .reset_index()
    )

    # A per-game Pearson correlation between log1p(gap_1_2) and cp_loss was
    # tried here as a tractable approximation of the "Levy area" cross-term
    # from arXiv:2606.18544 ("Chess Signatures of Play") — see
    # PROJECT_LOG.md for the full writeup. Tested against both validation
    # gates and REVERTED: +0.4pp on the 102-player pool (31.8%→32.2%,
    # within CV noise) and flat-to-negative on the same-skill-band check
    # (49.6%→49.5%). A plain correlation is too coarse to capture what the
    # paper actually measures (a proper path-signature Levy area is
    # order/lead-lag-sensitive in a way Pearson correlation isn't); kept
    # out rather than adding a feature that doesn't clearly earn its
    # complexity, per this project's explicit overfitting caution. Regan's
    # intrinsic-rating (s, c) model is a more promising path to the same
    # idea but needs deeper MultiPV annotation than currently exists (see
    # PROJECT_LOG.md) — a real next step, not implemented here.

    # post_mistake_think_delta — think_time on the ply right after the
    # player's OWN mistake (is_mistake, already computed by features.py)
    # minus that game's overall mean: do they slow down and recalibrate,
    # or tilt and speed up? A real psychological trait, not a skill proxy
    # (a strong player who tilts and a weak player who tilts look similar
    # on this axis regardless of their very different cp_loss levels).
    ordered["_post_mistake"] = ordered.groupby(["player", "game_id"])["is_mistake"].shift(1).fillna(False)
    post_mean = ordered[ordered["_post_mistake"]].groupby(["player", "game_id"])["think_time"].mean()
    baseline_mean = ordered.groupby(["player", "game_id"])["think_time"].mean()
    post_mistake_delta = (post_mean - baseline_mean).rename("post_mistake_think_delta").reset_index()

    # base_s/increment_s (time-control sub-format within "bullet") was
    # tried and REVERTED here — see PROJECT_LOG.md. It passed the
    # same-skill-band gate (49.6%→53.9%) but the FULL POOL actually
    # dropped (31.8%→31.3%), with base_s/increment_s jumping into the top
    # feature importances. That combination is the signature of a
    # different confound the same-skill-band check was never built to
    # catch: which DISCOVERY BATCH an account's games were fetched from
    # (celebrities are single-format; some discovered players show
    # variance that may reflect real preference, but may also just be an
    # artifact of when/how their archive was fetched) — a shortcut, not
    # genuine behavior.

    # hour-of-day / day-of-week, circularly encoded (sin/cos so 23:59 and
    # 00:01 read as adjacent, not maximally far apart). Purely a personal
    # habit — completely orthogonal to skill or move quality, and about as
    # individually distinctive as anything in this feature set: "usually
    # plays weeknights around 9pm" is a real, stable fingerprint.
    game_dt = all_df.groupby(["player", "game_id"])["game_datetime"].first()
    hour_frac = game_dt.dt.hour + game_dt.dt.minute / 60.0
    dow = game_dt.dt.dayofweek
    when = pd.DataFrame({
        "hour_sin": np.sin(2 * np.pi * hour_frac / 24.0),
        "hour_cos": np.cos(2 * np.pi * hour_frac / 24.0),
        "dow_sin": np.sin(2 * np.pi * dow / 7.0),
        "dow_cos": np.cos(2 * np.pi * dow / 7.0),
    }).reset_index()

    # prev_game_result — win/loss/draw of the player's PRECEDING game in
    # this dataset (by datetime), as context for THIS game — a "did I just
    # win or lose" session-mood signal. Derived only from the prior game's
    # outcome, never this game's own plies, so it's real context, not
    # leakage of the label being predicted.
    game_result = all_df.groupby(["player", "game_id"])[["result", "color", "game_datetime"]].first().reset_index()
    won_white = game_result["result"] == "1-0"
    won_black = game_result["result"] == "0-1"
    is_white = game_result["color"] == "white"
    game_result["player_result"] = 0  # draw
    game_result.loc[(won_white & is_white) | (won_black & ~is_white), "player_result"] = 1
    game_result.loc[(won_white & ~is_white) | (won_black & is_white), "player_result"] = -1
    game_result = game_result.sort_values(["player", "game_datetime"])
    game_result["prev_game_result"] = game_result.groupby("player")["player_result"].shift(1).fillna(0.0)
    prev_result = game_result[["player", "game_id", "prev_game_result"]]

    per_game = (
        cont.merge(rate, on=["player", "game_id"])
        .merge(trend, on=["player", "game_id"])
        .merge(phase_share, on=["player", "game_id"])
        .merge(pacing, on=["player", "game_id"])
        .merge(low_time_ratio, on=["player", "game_id"])
        .merge(rhythm, on=["player", "game_id"])
        .merge(tempo, on=["player", "game_id"])
        .merge(post_mistake_delta, on=["player", "game_id"])
        .merge(when, on=["player", "game_id"])
        .merge(prev_result, on=["player", "game_id"])
    )
    # A single-ply game has no std/trend (NaN) — 0 is the correct value (no
    # within-game variation/trend observed), not a missing measurement.
    # Same reasoning extends to the new correlation/delta features: no
    # signal observed reads as neutral (0), not missing data.
    fill_cols = [
        c for c in per_game.columns
        if c.endswith("_std") or c.endswith("_trend") or c in
        ("rhythm_autocorr", "tempo_match_corr", "post_mistake_think_delta")
    ]
    per_game[fill_cols] = per_game[fill_cols].fillna(0.0)
    return per_game


def _rank_k_curve(y, proba, classes_order, ks=(1, 3, 5, 10)):
    """Rank-k accuracy: is the true player among the model's top-k guesses,
    not just its single best guess? Standard in face-recognition benchmarks
    (the "CMC curve" — Cumulative Match Characteristic) precisely because
    single-guess (rank-1) accuracy understates how much a system actually
    knows when the candidate pool is large: confusing two similar-looking
    people is a very different failure from a genuinely wrong guess, and a
    correct answer sitting at rank-2 or rank-3 is real, usable signal for a
    human reviewing a short list rather than trusting one verdict blind.
    """
    unique_players = sorted(set(y))
    class_index = {p: j for j, p in enumerate(classes_order)}
    y_idx = np.array([class_index[p] for p in y])
    order = np.argsort(-proba, axis=1)  # each row: class indices best -> worst
    curve = []
    seen_k = set()
    for k in ks:
        k = min(k, len(classes_order))
        if k in seen_k:
            continue  # cohort has fewer classes than this k — already covered by a smaller k
        seen_k.add(k)
        top_k = order[:, :k]
        hit = (top_k == y_idx[:, None]).any(axis=1)
        per_player = {p: _f(hit[y == p].mean()) for p in unique_players}
        curve.append({
            "k": k,
            "overall_accuracy": _f(hit.mean()),
            "balanced_accuracy": _f(float(np.mean(list(per_player.values())))),
        })
    return curve


def _multi_game_curve(y, proba, classes_order, group_sizes=(1, 3, 5, 10, 20, 50), weighted=False, sort_key=None):
    """Score-level fusion across multiple held-out games from the same
    player: average several games' per-class probability vectors and take
    the argmax of the AVERAGED vector, instead of judging each game alone.
    Every held-out probability here already comes from a model that never
    saw that specific game (standard k-fold cross_val_predict), so
    averaging several of them together is not leakage — it's the same
    score-fusion trick behind why keystroke-dynamics systems authenticate
    off a typing SESSION rather than one keystroke, why speaker
    verification wants several seconds of audio rather than one phoneme,
    and why authorship attribution is far more confident on a full essay
    than one sentence: a single sample is a noisy draw from a person's
    behavior, and noise commutes down as more independent samples are
    averaged. This measures the same idea for chess move/clock behavior —
    "how sure can we be after watching N games from this account?" — not
    "how sure after one."

    weighted=True switches from a plain average to a confidence-weighted
    one (each game's own max-probability as its weight) — a standard
    ensemble/fusion technique on the theory that a game the model is
    already unsure about (busy session, low-time scramble, tilt) carries
    less real signal and shouldn't count as much as a game it read
    cleanly. Computed as a genuine A/B alongside the uniform curve rather
    than assumed to help — see build_identification's model_comparison
    for the same discipline applied to model choice.

    sort_key, when given (one value per row, aligned with y/proba),
    switches game SELECTION from uniform-random partitioning to "pick
    each player's single best k games by sort_key" — the realistic
    version of "if we could choose which N games to observe, not just how
    many." Grounded in a real finding from _correctness_pattern_analysis:
    session_game_index is by far the strongest within-player predictor of
    whether a single game gets identified correctly (deep-into-a-session
    games are dramatically more legible than early-session ones on the
    full pool — 37% vs 23% single-game accuracy). Deliberately evaluates
    only ONE group per player (their actual top-k), not multiple
    partitioned groups the way the random/weighted curves do — partitioning
    a sorted-by-preference list into consecutive chunks dumps every
    player's WORST games into the later groups and drags the average
    below even the random baseline, which is a bug in the question, not
    a real result (confirmed the hard way on a first pass of this code).
    Same underlying idea as a speaker-verification system preferring one
    longer continuous utterance over scattered short clips.
    """
    rng = np.random.default_rng(0)
    unique_players = sorted(set(y))
    curve = []
    for k in group_sizes:
        per_player_acc = {}
        total_correct = 0
        total_groups = 0
        for p in unique_players:
            idx = np.where(y == p)[0].copy()
            if sort_key is not None:
                idx = idx[np.argsort(-sort_key[idx], kind="stable")]
                if len(idx) < k:
                    continue
                groups = [idx[:k]]  # only the single best-k group — see docstring
            else:
                rng.shuffle(idx)
                n_groups = len(idx) // k
                if n_groups == 0:
                    continue
                groups = [idx[g * k:(g + 1) * k] for g in range(n_groups)]
            correct = 0
            for group_idx in groups:
                if weighted:
                    conf = proba[group_idx].max(axis=1)
                    w = conf if conf.sum() > 0 else None
                    avg_proba = np.average(proba[group_idx], axis=0, weights=w)
                else:
                    avg_proba = proba[group_idx].mean(axis=0)
                pred_class = classes_order[int(np.argmax(avg_proba))]
                correct += int(pred_class == p)
            per_player_acc[p] = correct / len(groups)
            total_correct += correct
            total_groups += len(groups)
        curve.append({
            "n_games": k,
            "overall_accuracy": _f(total_correct / total_groups) if total_groups else None,
            "balanced_accuracy": _f(float(np.mean(list(per_player_acc.values())))) if per_player_acc else None,
            "n_players_covered": len(per_player_acc),
            "n_players_total": len(unique_players),
        })
    return curve


def _correctness_pattern_analysis(per_game: pd.DataFrame, y: np.ndarray, pred: np.ndarray) -> dict:
    """Does a game's own session/tilt context — how far into a session it
    was, whether the player just won or lost, time of day, low-time
    scrambling, rhythm — predict whether THIS specific held-out game gets
    correctly identified? Different question from feature importance
    (which asks "does this signal help tell players apart") — this asks
    "does this signal make an individual game more or less legible once
    we already know whose behavior we're comparing it to."

    Every correlation is computed on WITHIN-PLAYER z-scored signals: raw
    pooled correlation would be confounded by "some players are just
    easier to identify and also happen to play more low-session-index
    games" — z-scoring each signal against that player's own mean/spread
    first isolates "was this an unusual game FOR THIS PLAYER, and did
    that unusual-ness make it easier or harder to identify," which is
    the actually actionable question (it points at which games to trust
    for a multi-game read, not just which players are identifiable).
    """
    correct = (pred == y).astype(float)
    df = per_game.copy()
    df["_correct"] = correct
    df["_player"] = y

    signals = [
        "session_game_index", "log_minutes_since_prev_game", "prev_game_result",
        "low_time_think_ratio", "rhythm_autocorr", "tempo_match_corr",
        "post_mistake_think_delta", "n_plies", "think_time_std", "cp_loss_trend",
        "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    ]
    correlations = []
    for sig in signals:
        if sig not in df.columns:
            continue
        g = df.groupby("_player")[sig]
        mean = g.transform("mean")
        std = g.transform("std").replace(0, np.nan)
        z = (df[sig] - mean) / std
        valid = z.notna() & np.isfinite(z)
        if valid.sum() < 30:
            continue
        corr = np.corrcoef(z[valid], df.loc[valid, "_correct"])[0, 1]
        correlations.append({
            "signal": sig,
            "within_player_corr_with_correctness": _f(corr),
            "n_games": int(valid.sum()),
        })
    correlations.sort(key=lambda r: -abs(r["within_player_corr_with_correctness"] or 0))

    # Interpretable bucketed breakdowns for the two clearest "tilt/session"
    # signals, alongside the correlation ranking above — a plain "accuracy
    # when X" statement is easier to act on than a bare r value.
    tilt_buckets = {}
    if "prev_game_result" in df.columns:
        for label, mask in [
            ("lost previous game", df["prev_game_result"] < 0),
            ("drew / first game of session", df["prev_game_result"] == 0),
            ("won previous game", df["prev_game_result"] > 0),
        ]:
            if mask.sum() >= 30:
                tilt_buckets[label] = {"accuracy": _f(correct[mask.to_numpy()].mean()), "n_games": int(mask.sum())}

    session_buckets = {}
    if "session_game_index" in df.columns:
        try:
            terciles = pd.qcut(df["session_game_index"], 3, duplicates="drop")
            for label, mask in df.groupby(terciles, observed=True).groups.items():
                sel = df.index.isin(mask)
                if sel.sum() >= 30:
                    session_buckets[str(label)] = {"accuracy": _f(correct[sel].mean()), "n_games": int(sel.sum())}
        except ValueError:
            pass  # not enough distinct values to form 3 buckets — skip rather than crash

    return {
        "within_player_correlations": correlations,
        "tilt_buckets": tilt_buckets,
        "session_position_buckets": session_buckets,
    }


# Features that describe WHAT move/position the player produced or faced
# (move choice, move quality, tactical content) rather than HOW they
# responded to it in time — excluded from feature_mode="biometric" per an
# explicit scoping decision: position/move evaluation should only ever be
# used to measure the player's THINKING RESPONSE (think-time reacting to
# complexity, low-time scrambling, post-mistake pacing), never as a raw
# identity signal on its own. Two of these (maia_surprise*, maia_prob_
# played*) are consistently top-5 feature importances in the "full" model
# — removing them is a real, deliberate accuracy tradeoff, not free.
MOVE_CONTENT_FEATURES = {
    "cp_loss_mean", "cp_loss_std", "cp_loss_trend",
    "maia_surprise_mean", "maia_surprise_std", "maia_surprise_trend",
    "maia_prob_played_mean", "maia_prob_played_std", "maia_prob_played_trend",
    "is_sacrifice", "own_skewers", "opp_skewers",
    "n_captures_available_mean", "n_captures_available_std", "n_captures_available_trend",
}


def build_identification(cohort_name: str, player_stems: dict[str, str], feature_mode: str = "full") -> dict:
    """Cross-validated identification demo, using ONLY player-agnostic raw
    behavioral aggregates (not a per-player-fitted model, which would
    trivially "know" its own player) computed per game, within one cohort
    of players (all on the one mode they share).

    feature_mode="full" uses every available feature (move choice/quality
    included) — the version intended for a future anti-cheat combination.
    feature_mode="biometric" drops MOVE_CONTENT_FEATURES entirely, keeping
    only clock/timing/session signals — answers "how much can we tell
    purely from habits, with zero use of what moves they actually chose."

    Uses class-weight-balanced logistic regression with 5-fold CV rather
    than naive nearest-centroid: an unweighted method can score BELOW the
    trivial "always guess the most common player" baseline under class
    imbalance without actually being wrong about anything interesting —
    that's exactly what a first, naive nearest-centroid pass did on the
    "primary" cohort (70.8% vs an 85.4% majority baseline) before this was
    replaced. Balanced accuracy / per-class recall is the honest metric
    under imbalance, not raw accuracy against an inflated majority baseline.

    Also fits a HistGradientBoostingClassifier and a LightGBM classifier
    (both tree-based, capture nonlinear/interaction effects logistic
    regression can't, need no feature scaling) alongside logistic
    regression and reports whichever of the three does better on
    cross-validated balanced accuracy — a real comparison, not just an
    assumption that a fancier model helps. LightGBM is already a trusted,
    zero-new-dependency part of this project (src/model.py's per-player
    think-time/move-quality models) and tends to out-tune sklearn's HGB
    on tabular data, so it's worth a direct head-to-head rather than
    assuming HGB's past wins would hold.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.preprocessing import StandardScaler
    import lightgbm as lgb

    frames = []
    for player, stem in player_stems.items():
        df = pd.read_parquet(FEATURES_DIR / f"{stem}_features.parquet")
        df = df[~df["is_suspect"]].dropna(subset=["think_time"])
        df = df.dropna(subset=IDENT_FEATURES)
        df["player"] = player
        frames.append(df)
    all_df = pd.concat(frames, ignore_index=True)

    per_game = _per_game_features(all_df)
    feature_cols = [c for c in per_game.columns if c not in ("player", "game_id")]
    if feature_mode == "biometric":
        feature_cols = [c for c in feature_cols if c not in MOVE_CONTENT_FEATURES]
    n_plies = all_df.groupby(["player", "game_id"]).size().rename("n_plies")
    # Free the full per-ply data now — everything past this point only
    # needs the much smaller per-game aggregate. At 100+ players this was
    # genuinely large enough to matter: holding every player's full-history
    # per-ply DataFrame alive through the CV/model-fitting phase (which
    # itself needs real memory for HistGradientBoostingClassifier's
    # internal multi-threading) was hitting real system memory pressure on
    # this machine (observed: export process killed mid-run with system
    # free memory in the tens of MB). `del` + `gc.collect()` here is a
    # genuine, safe win, not just defensive style.
    del all_df, frames
    gc.collect()
    per_game = per_game.merge(n_plies, on=["player", "game_id"])
    per_game = per_game[per_game["n_plies"] >= 15].reset_index(drop=True)

    X_raw = per_game[feature_cols].to_numpy(dtype=float)
    X = StandardScaler().fit_transform(X_raw)
    y = per_game["player"].to_numpy()
    unique_players = sorted(set(y))
    n = len(per_game)

    cv = StratifiedKFold(5, shuffle=True, random_state=0)

    def _run(clf, X_):
        pred_ = cross_val_predict(clf, X_, y, cv=cv)
        proba_ = cross_val_predict(clf, X_, y, cv=cv, method="predict_proba")
        recall_ = {p: _f((pred_[y == p] == y[y == p]).mean()) for p in unique_players}
        bal_ = float(np.mean(list(recall_.values())))
        return pred_, proba_, recall_, bal_

    lr = LogisticRegression(class_weight="balanced", max_iter=1000)
    lr_pred, lr_proba, lr_recall, lr_bal = _run(lr, X)

    # HistGradientBoostingClassifier doesn't support class_weight directly;
    # approximate balancing via per-sample weights (inverse class
    # frequency) passed through cross_val_predict's fit_params.
    hgb = HistGradientBoostingClassifier(random_state=0, max_depth=4)
    class_freq = pd.Series(y).value_counts(normalize=True)
    sample_weight = pd.Series(y).map(lambda p: 1.0 / class_freq[p]).to_numpy()
    hgb_pred = cross_val_predict(hgb, X_raw, y, cv=cv, params={"sample_weight": sample_weight})
    hgb_proba = cross_val_predict(hgb, X_raw, y, cv=cv, method="predict_proba", params={"sample_weight": sample_weight})
    hgb_recall = {p: _f((hgb_pred[y == p] == y[y == p]).mean()) for p in unique_players}
    hgb_bal = float(np.mean(list(hgb_recall.values())))

    # LightGBM doesn't support class_weight either — same inverse-frequency
    # sample_weight approach as HGB above, for a fair comparison.
    lgbm = lgb.LGBMClassifier(random_state=0, max_depth=4, n_estimators=100, verbosity=-1)
    lgbm_pred = cross_val_predict(lgbm, X_raw, y, cv=cv, params={"sample_weight": sample_weight})
    lgbm_proba = cross_val_predict(lgbm, X_raw, y, cv=cv, method="predict_proba", params={"sample_weight": sample_weight})
    lgbm_recall = {p: _f((lgbm_pred[y == p] == y[y == p]).mean()) for p in unique_players}
    lgbm_bal = float(np.mean(list(lgbm_recall.values())))

    print(f"    [{cohort_name}] logistic regression balanced acc: {lr_bal:.3f} | HGB: {hgb_bal:.3f} | LightGBM: {lgbm_bal:.3f}")

    candidates = [
        ("logistic_regression", lr_pred, lr_proba, lr_recall, lr_bal),
        ("gradient_boosting", hgb_pred, hgb_proba, hgb_recall, hgb_bal),
        ("lightgbm", lgbm_pred, lgbm_proba, lgbm_recall, lgbm_bal),
    ]
    model_name, pred, proba, per_class_recall, best_bal = max(candidates, key=lambda c: c[4])
    balanced_accuracy = _f(best_bal)
    classes_order = sorted(unique_players)  # cross_val_predict's proba column order == sorted(np.unique(y))

    rank_k_accuracy = _rank_k_curve(y, proba, classes_order)
    multi_game_accuracy = _multi_game_curve(y, proba, classes_order)
    multi_game_accuracy_weighted = _multi_game_curve(y, proba, classes_order, weighted=True)
    session_index_key = (
        per_game["session_game_index"].to_numpy() if "session_game_index" in per_game.columns else None
    )
    multi_game_accuracy_session_selected = (
        _multi_game_curve(y, proba, classes_order, sort_key=session_index_key)
        if session_index_key is not None else None
    )
    correctness_patterns = _correctness_pattern_analysis(per_game, y, pred)
    if multi_game_accuracy and multi_game_accuracy_weighted:
        deltas = ", ".join(
            f"{u['n_games']}g: {(w['balanced_accuracy'] or 0) - (u['balanced_accuracy'] or 0):+.3f}"
            for u, w in zip(multi_game_accuracy, multi_game_accuracy_weighted)
        )
        print(f"    [{cohort_name}] confidence-weighted fusion vs. uniform (delta balanced acc): {deltas}")
    if multi_game_accuracy and multi_game_accuracy_session_selected:
        deltas = ", ".join(
            f"{u['n_games']}g: {(s['balanced_accuracy'] or 0) - (u['balanced_accuracy'] or 0):+.3f}"
            for u, s in zip(multi_game_accuracy, multi_game_accuracy_session_selected)
        )
        print(f"    [{cohort_name}] session-selected fusion vs. uniform (delta balanced acc): {deltas}")
    print(
        f"    [{cohort_name}] rank-1/3/5: "
        + ", ".join(f"{c['overall_accuracy']:.3f}" for c in rank_k_accuracy)
        + " | multi-game (1/5/20 games): "
        + ", ".join(
            f"{c['overall_accuracy']:.3f}" if c["overall_accuracy"] is not None else "n/a"
            for c in multi_game_accuracy if c["n_games"] in (1, 5, 20)
        )
    )

    # Confusion breakdown: for each true player, what fraction of their
    # misclassified games got attributed to each OTHER player. With >2
    # classes this is the only way to see WHICH wrong answer a model
    # reaches for — e.g. does jerrycdzn get confused with cdznjerry (same
    # real person, different account) more than with BIG_TONKA_T (a
    # genuinely different person)? per_class_recall alone can't show that.
    # Cap each row's stored entries once the cohort gets large — the same
    # size/legibility issue sample_games hit at 71 classes (see
    # PROJECT_LOG.md): a full N×N matrix at 102 classes is 327KB alone,
    # over the 256KB db document limit, and the dashboard's own
    # renderTopConfusions (>12 classes) only ever reads entries above a 5%
    # threshold anyway — a dense row of ~101 near-zero probabilities is
    # both wasted bytes and never rendered. Keep the diagonal (self) entry
    # always, plus each row's top few off-diagonal entries.
    CONFUSION_ROW_TOP_K = 8
    confusion = {}
    for true_p in unique_players:
        mask = y == true_p
        preds_for_p = pred[mask]
        full_row = {other_p: _f((preds_for_p == other_p).mean()) for other_p in unique_players}
        if len(unique_players) <= CONFUSION_MATRIX_STORE_FULL_MAX:
            confusion[true_p] = full_row
        else:
            top_others = sorted(
                (p for p in unique_players if p != true_p), key=lambda p: -full_row[p]
            )[:CONFUSION_ROW_TOP_K]
            confusion[true_p] = {p: full_row[p] for p in [true_p, *top_others]}

    lr.fit(X, y)
    # Binary case: coef_ has one row, sign is meaningful (which player it
    # points toward). Multiclass (6+ players): coef_ has one row per class
    # (one-vs-rest under the hood) with no single meaningful sign, so
    # report mean absolute weight across classes instead — still answers
    # "which behavioral signal matters most," just not "which direction."
    # Reported from logistic regression regardless of which model scored
    # higher — HistGradientBoosting has no linear coefficients to show,
    # and logistic regression's weights remain the interpretable half of
    # this comparison even when the tree model predicts better.
    if lr.coef_.shape[0] == 1:
        coefs = sorted(zip(feature_cols, lr.coef_[0].tolist()), key=lambda kv: -abs(kv[1]))
    else:
        mean_abs = np.abs(lr.coef_).mean(axis=0)
        coefs = sorted(zip(feature_cols, mean_abs.tolist()), key=lambda kv: -abs(kv[1]))

    # Stratify the displayed sample EVENLY per true player, not a single
    # random draw across the whole (often imbalanced) pool — a global draw
    # plus sorting by confidence let one majority class quietly dominate
    # every slot shown in the UI (confirmed directly: an early version of
    # this view showed 0 cdznjerry examples out of 18, purely because
    # BIG_TONKA_T has ~6x more games and so dominates a random draw before
    # confidence-sorting even runs).
    rng = np.random.default_rng(0)
    per_class_n = max(1, 40 // len(unique_players))
    sample_idx = np.concatenate([
        rng.choice(np.where(y == p)[0], size=min(per_class_n, (y == p).sum()), replace=False)
        for p in unique_players
    ])
    # Cap stored probabilities to the top few classes per sample, not the
    # full cohort — at large class counts this is both a size problem (a
    # 71-way probability dict per sample game blew the 256KB db document
    # limit, confusion+sample_games alone hit ~363KB) and a genuine UX
    # problem the size limit just happened to surface: the dashboard renders
    # one probability-bar SEGMENT per class per sample game, and 71 tiny
    # slivers is illegible regardless of storage cost. Always keep the true
    # player's own probability (so the UI can show what the model actually
    # assigned it, even if `n < 3 + 1` obscures at high class count) plus
    # the predicted player's if it's a miss.
    TOP_K_PROBS = 5

    # Per-prediction "why": top SHAP contributors toward the model's own
    # guess for each sample game, not just global feature importance.
    # Explains ONE specific held-out game's verdict, e.g. "this particular
    # game's think-time rhythm is what pulled it toward player X" — a
    # different, more concrete question than "what does the model lean on
    # in general" (feature_importance below already answers that one).
    # Deliberately explains the FINAL model trained on the full cohort
    # (standard practice for interpretability), not one of the five CV
    # fold models that actually scored this game — the prediction/accuracy
    # numbers everywhere else on this page are the properly held-out ones;
    # this is a separate, expected-to-differ-slightly artifact for "why,"
    # not "was it right."
    class_index = {p: j for j, p in enumerate(classes_order)}
    top_reasons_by_idx: dict[int, list[dict]] = {}
    # Pool-wide mean/std per feature, in NATURAL units (not the
    # standardized X used to fit logistic regression) — lets each
    # reported reason say what this player's actual value WAS and how it
    # compares to the rest of the pool ("2.3s, far slower than the
    # 0.8-1.5s most players show"), not just which signal mattered and by
    # how much. Computed once here, reused for every sample game below.
    feature_pool_mean = np.nanmean(X_raw, axis=0)
    feature_pool_std = np.nanstd(X_raw, axis=0)
    try:
        import shap
        if model_name in ("gradient_boosting", "lightgbm"):
            tree_model = hgb if model_name == "gradient_boosting" else lgbm
            tree_model.fit(X_raw, y, sample_weight=sample_weight)
            explainer = shap.TreeExplainer(tree_model)
            shap_vals = explainer.shap_values(X_raw[sample_idx])
            # Binary cohorts (e.g. "primary") return a 2D array — SHAP's
            # convention there is "push toward classes_order[1]" only, not
            # one slice per class the way 3+ classes returns (n, features,
            # n_classes). Normalize both to "positive = toward this row's
            # OWN predicted class" so the UI never needs to know which case
            # it's in.
            binary = shap_vals.ndim == 2
            for pos, i in enumerate(sample_idx):
                if binary:
                    sign = 1.0 if class_index[pred[i]] == 1 else -1.0
                    contribs = shap_vals[pos, :] * sign
                else:
                    contribs = shap_vals[pos, :, class_index[pred[i]]]
                top_j = np.argsort(-np.abs(contribs))[:5]
                top_reasons_by_idx[i] = [
                    {
                        "feature": feature_cols[j],
                        "contribution": _f(contribs[j]),
                        "value": _f(X_raw[i, j]),
                        "pool_mean": _f(feature_pool_mean[j]),
                        "pool_std": _f(feature_pool_std[j]),
                    }
                    for j in top_j
                ]
        else:
            coefs_arr = lr.coef_  # lr.fit(X, y) already ran above
            for i in sample_idx:
                row_coef = coefs_arr[class_index[pred[i]]] if coefs_arr.shape[0] > 1 else coefs_arr[0]
                contribs = row_coef * X[i]
                top_j = np.argsort(-np.abs(contribs))[:5]
                top_reasons_by_idx[i] = [
                    {
                        "feature": feature_cols[j],
                        "contribution": _f(contribs[j]),
                        "value": _f(X_raw[i, j]),
                        "pool_mean": _f(feature_pool_mean[j]),
                        "pool_std": _f(feature_pool_std[j]),
                    }
                    for j in top_j
                ]
    except Exception as e:
        print(f"    [{cohort_name}] per-prediction reasoning skipped ({type(e).__name__}: {e})")

    results = []
    for i in sample_idx:
        full_p_map = {classes_order[j]: _f(proba[i, j]) for j in range(len(classes_order))}
        top_players = sorted(full_p_map, key=lambda p: -full_p_map[p])[:TOP_K_PROBS]
        keep = set(top_players) | {y[i], pred[i]}
        p_map = {p: full_p_map[p] for p in keep}
        results.append({
            "game_id": per_game.loc[i, "game_id"],
            "true_player": y[i],
            "predicted_player": pred[i],
            "correct": bool(pred[i] == y[i]),
            "n_plies": int(per_game.loc[i, "n_plies"]),
            "probabilities": p_map,
            "confidence": _f(max(full_p_map.values())),
            "top_reasons": top_reasons_by_idx.get(i, []),
        })
    results.sort(key=lambda r: -(r["confidence"] or 0))

    return {
        "cohort": cohort_name,
        "feature_mode": feature_mode,
        "mode": "bullet",
        "n_games_total": n,
        "class_counts": {p: int((y == p).sum()) for p in unique_players},
        "overall_accuracy": _f((pred == y).mean()),
        "balanced_accuracy": balanced_accuracy,
        "per_class_recall": per_class_recall,
        "confusion": confusion,
        "majority_baseline": _f(max((y == p).mean() for p in unique_players)),
        "chance_baseline": _f(1.0 / len(unique_players)),
        "model_used": model_name,
        "model_comparison": {"logistic_regression": _f(lr_bal), "gradient_boosting": _f(hgb_bal), "lightgbm": _f(lgbm_bal)},
        "rank_k_accuracy": rank_k_accuracy,
        "multi_game_accuracy": multi_game_accuracy,
        "multi_game_accuracy_weighted": multi_game_accuracy_weighted,
        "multi_game_accuracy_session_selected": multi_game_accuracy_session_selected,
        "correctness_patterns": correctness_patterns,
        "sample_games": results,
        "features_used": feature_cols,
        "feature_importance": [{"feature": f, "coef": _f(c)} for f, c in coefs],
        "coef_is_signed": bool(lr.coef_.shape[0] == 1),
    }


def _reconstruct_bridge_move(fen_from: str, target_piece_placement: str) -> tuple[str | None, str | None]:
    """The tracked player's own per-ply rows skip the opponent's moves
    entirely — this recovers them EXACTLY, not a guess: given the FEN
    right after one of the tracked player's own moves and the target
    piece-placement of the position right before their NEXT own move,
    there is exactly one legal move that bridges them (chess positions
    are deterministic), found by brute-force trying every legal move
    from the first position. Only compares the piece-placement field —
    move counters/clocks in our own FEN snapshots don't need to match.
    Cheap: bullet positions have a small branching factor and this only
    runs for a handful of hand-picked narrated games, not the full pool.
    """
    import chess
    board = chess.Board(fen_from)
    for move in board.legal_moves:
        san = board.san(move)
        board.push(move)
        if board.board_fen() == target_piece_placement:
            return san, board.fen()
        board.pop()
    return None, None


def _build_full_movelist(game_df: pd.DataFrame) -> list[dict]:
    """Reconstructs the FULL interleaved move list (both colors) for one
    game from a per-player-filtered features table that only has the
    tracked player's own plies. Opponent moves are bridged via
    `_reconstruct_bridge_move`; if the tracked player is Black, White's
    move 1 is recovered the same way, bridging from the true starting
    position. Falls back to own-moves-only (silently) if a bridge move
    can't be found — shouldn't happen on clean data, but a missing
    opponent move is far less bad than crashing the whole export.
    """
    import chess
    rows = game_df.sort_values("ply").to_dict("records")
    if not rows:
        return []
    full: list[dict] = []
    prev_fen_after: str | None = None
    # A running 0-indexed ply counter we maintain ourselves, rather than
    # trusting the source "ply" column — that column only numbers the
    # tracked player's OWN plies, with no value at all for the
    # reconstructed opponent moves now interleaved in. move_number =
    # ply//2 + 1, white/black = ply even/odd, is standard chess numbering
    # and self-consistent across both own and opponent entries this way.
    running_ply = 0
    for row in rows:
        fen_before = row["fen_before"]
        target_placement = fen_before.split(" ")[0]
        if prev_fen_after is None:
            board_check = chess.Board(fen_before)
            if not board_check.turn:  # Black to move here => White already played move 1
                san, _ = _reconstruct_bridge_move(chess.STARTING_FEN, target_placement)
                if san:
                    full.append({
                        "san": san, "is_own": False, "think_time": None, "fen_before": chess.STARTING_FEN,
                        "ply": running_ply, "move_number": running_ply // 2 + 1,
                    })
                    running_ply += 1
        elif prev_fen_after.split(" ")[0] != target_placement:
            san, _ = _reconstruct_bridge_move(prev_fen_after, target_placement)
            if san:
                full.append({
                    "san": san, "is_own": False, "think_time": None, "fen_before": prev_fen_after,
                    "ply": running_ply, "move_number": running_ply // 2 + 1,
                })
                running_ply += 1

        board = chess.Board(fen_before)
        own_move = chess.Move.from_uci(row["move_uci"])
        own_san = board.san(own_move)
        board.push(own_move)
        full.append({
            "san": own_san,
            "is_own": True,
            "think_time": _f(row["think_time"]),
            "fen_before": fen_before,
            "ply": running_ply,
            "move_number": running_ply // 2 + 1,
            "is_mistake": bool(row.get("is_mistake", False)),
            "cp_loss": _f(row.get("cp_loss")),
            "gap_1_2": _f(row.get("gap_1_2")),
            "n_captures_available": _f(row.get("n_captures_available")),
            "clock_frac_remaining": _f(row.get("clock_frac_remaining")),
        })
        running_ply += 1
        prev_fen_after = board.fen()
    return full


def _build_narrated_games(sample_games: list[dict], player_stems: dict[str, str], n_sample: int = 40, seed: int = 0, correct_only: bool = False) -> list[dict]:
    """Full move-by-move replay data for a subset of the already-computed
    sample games — not live, not for every game (that's the full pool,
    hundreds of games; this is a display-sized sample). Cheap (re-reads
    just those games' own per-ply rows, not the full pool) and
    deliberately scoped that way: the dashboard's "why did it decide
    that" panel wants a real chessboard + move list with think-times,
    which needs fen_before/move_san/think_time per ply — data that
    build_identification() doesn't keep around per-game (only the
    per-game AGGREGATE feature vector), so this re-reads it targeted,
    after the fact, only for the games actually chosen for display.

    correct_only=True restricts to predictions the model got right —
    an explicit choice, not a silent one: the honest single-game number
    (~25%) is already stated plainly elsewhere on the page, so this
    panel's job is showing what correct reasoning actually looks like
    across as many real examples as the sample pool has (currently
    capped by how many of the 126 sample_games happen to be correct —
    getting more headroom here would need keeping more than one sample
    game per player upstream, not done yet). Earlier iterations of this
    function debated random-vs-curated at length (see PROJECT_LOG.md) —
    landed on explicit curation being fine as long as it's labeled as
    such on the page, which it is.
    """
    rng = random.Random(seed)
    pool = [g for g in sample_games if g["correct"]] if correct_only else list(sample_games)
    rng.shuffle(pool)
    picked = pool[:n_sample]

    narrated = []
    for g in picked:
        player = g["true_player"]
        stem = player_stems.get(player)
        if not stem:
            continue
        df = pd.read_parquet(FEATURES_DIR / f"{stem}_features.parquet")
        game_df = df[df["game_id"] == g["game_id"]].sort_values("ply")
        if game_df.empty:
            continue

        moves = _build_full_movelist(game_df)  # interleaved, both colors — see docstring
        own_think_times = np.array([m["think_time"] for m in moves if m["is_own"] and m["think_time"] is not None])
        tt_mean = own_think_times.mean() if len(own_think_times) else 0.0
        tt_std = own_think_times.std() if len(own_think_times) else 0.0

        for m in moves:
            m["key_moment"] = None
            m["key_reason"] = None
            if not m["is_own"]:
                continue  # only the tracked player's own moves have think_time/is_mistake to flag
            tt = m["think_time"]
            z = (tt - tt_mean) / tt_std if tt_std > 0 and tt is not None else 0.0
            # is_mistake is cp_loss > 100, and cp_loss itself measures the
            # gap between two INDEPENDENT engine searches at consecutive
            # plies, not loss relative to this position's own best-move
            # PV (see PROJECT_LOG.md "Phase 2, v0" for the full
            # investigation) — usually a real eval swing, but can spike
            # near forced mates from search instability alone. Phrased as
            # "the position's evaluation swung" rather than an unqualified
            # "you blundered" to stay honest about what's actually known.
            if m["is_mistake"]:
                m["key_moment"] = "mistake"
                cp = m["cp_loss"]
                m["key_reason"] = (
                    f"The position's evaluation swung by about {int(cp)} centipawns after this move."
                    if cp is not None else "The position's evaluation swung sharply after this move."
                )
            elif z > 1.5:
                m["key_moment"] = "long_think"
                gap, n_capt = m["gap_1_2"], m["n_captures_available"]
                context = ""
                if gap is not None and gap < 30:
                    context = " — a sharp position where the top choices were close in value."
                elif n_capt is not None and n_capt >= 3:
                    context = f" — {int(n_capt)} captures were on the table."
                m["key_reason"] = f"Took {tt:.1f}s here, {z:.1f}x longer than their average for this game{context}"

        # Point some top_reasons at an actual position in THIS game, not
        # just an abstract number — e.g. post_mistake_think_delta is a
        # whole-game average, but a viewer wants to see the ACTUAL
        # mistake and how long they took to recover from it. "Next own
        # move" is no longer just index+1 now that opponent moves are
        # interleaved — search forward for the next is_own entry. Only
        # attempted for features with a natural single-moment story;
        # whole-game statistical properties (think_time_std, rhythm_
        # autocorr, tempo_match_corr, session-level context) have no one
        # ply to point to and are left as a plain described number.
        top_reasons = [dict(r) for r in g.get("top_reasons", [])]  # copy — don't mutate the shared sample
        for reason in top_reasons:
            feat = reason["feature"]
            if feat == "post_mistake_think_delta":
                best = None
                for mi in range(len(moves) - 1):
                    if not (moves[mi]["is_own"] and moves[mi]["is_mistake"]):
                        continue
                    nxt = next((k for k in range(mi + 1, len(moves)) if moves[k]["is_own"]), None)
                    if nxt is None or moves[nxt]["think_time"] is None:
                        continue
                    delta = moves[nxt]["think_time"] - tt_mean
                    if best is None or abs(delta) > abs(best[2]):
                        best = (mi, nxt, delta)
                if best:
                    reason["linked_mistake_idx"] = best[0]
                    reason["linked_move_idx"] = best[1]
                    reason["linked_delta"] = _f(best[2])
            elif feat == "low_time_think_ratio":
                candidates = [
                    (i, m["clock_frac_remaining"]) for i, m in enumerate(moves)
                    if m["is_own"] and m["clock_frac_remaining"] is not None
                ]
                if candidates:
                    idx, _ = min(candidates, key=lambda t: t[1])
                    reason["linked_move_idx"] = idx

        narrated.append({
            "game_id": g["game_id"],
            "true_player": g["true_player"],
            "predicted_player": g["predicted_player"],
            "correct": g["correct"],
            "confidence": g["confidence"],
            "top_reasons": top_reasons,
            "moves": moves,
        })
        print(f"    narrated game {g['game_id']} ({player}, {'correct' if g['correct'] else 'wrong'}): {len(moves)} moves")
    return narrated


def main() -> None:
    out = {"players": {}, "identification": {}}
    for player, modes in DATASETS.items():
        out["players"][player] = {}
        for mode, stem in modes.items():
            print(f"Building profile: {player} / {mode}")
            out["players"][player][mode] = build_profile(stem, player=player, mode=mode)

    gc.collect()  # profile-building is done; free that before the heavier identification pass
    for cohort_name, player_stems in IDENTIFICATION_COHORTS.items():
        print(f"Building identification demo: cohort={cohort_name} (full)")
        out["identification"][cohort_name] = build_identification(cohort_name, player_stems, feature_mode="full")
        gc.collect()
        biometric_name = f"{cohort_name}_biometric"
        print(f"Building identification demo: cohort={biometric_name} (biometric-only)")
        out["identification"][biometric_name] = build_identification(biometric_name, player_stems, feature_mode="biometric")
        gc.collect()

    # Biometrics-only, deliberately — the "why did it decide that" demo
    # should showcase what pure clock/timing/session habits can do on
    # their own, not lean on move-choice signals (maia_prob_played,
    # cp_loss) the full model also has access to. Reasoning bars for
    # these games will only ever cite biometric features as a result.
    print("Building narrated games (chessboard replay data) from the pool's biometrics-only sample...")
    out["narrated_games"] = _build_narrated_games(
        out["identification"]["pool_biometric"]["sample_games"], IDENTIFICATION_COHORTS["pool"]
    )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f"Wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
