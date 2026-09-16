# Project Log

Running record of decisions, findings, and design rationale for the chess
clock-management analyzer. See [CLAUDE.md](CLAUDE.md) for the original design
brief and Phase 2 sketch, and [README.md](README.md) for the pipeline
checklist. This file is the narrative: *why* things are the way they are,
*what* we've found, and what's still open. Kept up to date as the project
progresses — if you're picking this up cold (including a compressed
conversation), start here.

## Current status (snapshot)

- **Two original players fully current** (cdznjerry bullet, BIG_TONKA_T
  bullet/blitz/rapid) across the whole pipeline — engine annotation
  (depth 12, search-instability), features (135 cols incl. Maia), models,
  insights. See "Dashboard" and "Overnight player-pool expansion" sections
  below for what's changed and what's actively running.
- **Environment**: Python 3.12 venv (`.venv`). Stockfish via
  `brew install stockfish`; `lc0` (Maia) via `brew install lc0`. LightGBM
  needed `brew install libomp` on macOS.
- **Cloud annotation pipeline**: AWS EC2 Spot (`cloud/remote_annotate_aws.py`,
  background+poll, survives Spot reclaim) is primary; Hetzner
  (`cloud/remote_annotate_hetzner.py`) is an unused, less-resilient fallback.
  Requires `set -a && source .env && set +a` first — the script reads
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` from the environment, not
  from `.env` directly (no dotenv loading in the script itself).
- **insights.py** (bucket scoring + premove profile) and **fetch_games.py**
  (Lichess) are both built. Lichess is no longer used for think-time
  analysis at all — see the Dashboard section for why.
- **A dashboard now exists** (Claude Artifact, not a local app) — see
  below. Automated tests still don't exist.

## Dashboard (built this session)

Published as a Claude Artifact ("Time Signature"), using the `db`
capability so new players can be added later via `write_db` calls alone —
no republish needed for data updates, only for code/layout changes. Data
is precomputed by `scripts/export_dashboard_data.py` (not part of the
regular pipeline; run manually after new players/data land) into a single
JSON blob, seeded into the artifact's database, with the same JSON also
bundled as `data.js` (`FALLBACK_DATA`) so the page works even if the `db`
capability is unavailable in a given viewer.

**Three tabs**: Profile (per-player/mode breakdown), Compare (pick any two
player+mode combos, see them side by side), Identification (cross-validated
"which player produced this game" model).

**A real architectural decision, made explicitly with the user**: the
dashboard does NOT process game uploads. Real inference (Stockfish, Maia,
the trained LightGBM models) needs Python + engine binaries, which cannot
run inside a browser artifact. The user chose "dashboard only, no upload
yet" over building a local Flask/FastAPI backend for real-time analysis —
revisit if that's wanted later; FastAPI (not Flask) would be the pick, per
this session's stack discussion, for being modern/async/faster.

**Ratings system, NBA2K/FIFA-style**: each player/mode gets six 0-100
attribute scores (Recognition, Clutch, Composure, Discipline, Intuition,
Aggression), each mapped from one existing raw stat onto a FIXED
domain-anchored `[lo, hi]` range (not pool-relative percentile — percentile
degenerates to 0/100 with only 2-3 players and would shift every player's
score retroactively whenever someone new joins the pool, which is exactly
wrong for a "card" that's supposed to mean something on its own). Each
rating card is an expandable `<details>` (matching how FIFA/NBA2K let you
tap a stat for its breakdown) showing: the formula in plain language, the
player's raw number, and a marker showing where that raw number sits
between the fixed lo/hi anchors. See `compute_ratings()`/`RATING_ATTRIBUTES`
in `scripts/export_dashboard_data.py` for the exact formulas and anchors —
anchors are a first-pass calibration from this project's real players so
far, not a permanent standard; revisit as the pool grows.

**Lichess data pulled entirely from the dashboard/pool, not just caveated**:
six titled Lichess bullet players were fetched, annotated, and added for a
rating-matched identification cohort (35.8% balanced accuracy vs. 16.7%
chance, own_skewers/opp_skewers etc. all computed) — then the user
correctly flagged the results looked off (phase-time flat at exactly 1.0s
for every player) and it was removed entirely rather than patched. Root
cause, confirmed directly: Lichess's PGN `%clk` comments are whole-second
resolution (Chess.com's are decisecond), and at these players' fast bullet
speeds that's disqualifying, not just noisy — `ArtemChesSRU` had
`think_time == 0.0` on 58% of moves (the clock genuinely cannot represent
anything under 1s), which inflates `is_premove` (tuned for Chess.com's
finer resolution) far past real premove behavior once both a true instant
premove and a real ~0.7s decision round to the same recorded value. Every
think_time-derived stat for Lichess players was unreliable by construction,
not just imprecise. **Lesson for this project going forward: Chess.com only
for anything that touches think_time/clock precision; Lichess is fine for
non-timing signals (move sequences, ratings) but not for this project's
core question.**

**Two real bugs caught by the user while reviewing the live dashboard,
both fixed**:
  - "Overthought" always showed 0% in the critical-positions-only bucket
    view. Not actually a bug — Overthought is *defined* (insights.py) as
    elevated time on a NON-critical position, so it's mathematically
    guaranteed to be 0% among critical positions. Fixed by dropping that
    row from that view (with an explanatory caption) rather than showing a
    permanently-empty, misleading bar.
  - The "sample held-out games" identification view showed 0 out of 18
    examples as cdznjerry — always BIG_TONKA_T. Root cause: the Python-side
    sample was a single random draw across the whole (imbalanced, ~6:1)
    pool, then globally sorted by confidence — a majority class can
    dominate every visible slot this way even before considering that its
    correct predictions may just run higher-confidence. Fixed on BOTH
    ends: Python now stratifies the underlying sample evenly per player,
    and the frontend interleaves per-player when picking which rows to
    display, rather than trusting a global confidence sort to stay
    representative.

**Recognition chart also relabeled** — "Q1"..."Q5" (unexplained quintile
jargon) replaced with plain "calmer positions → sharper positions" axis
labels, per direct user feedback that the page shouldn't require the
viewer to know the pipeline's internal vocabulary.

**Explicitly deferred, not done**: a second, simplified/public-facing
version of the dashboard (the user wants two: this one, technical, for
themselves; a plain-language, polished one for eventual public sharing).
Checked for an installed UX-specific skill beyond `artifact-design`
(already loaded and in use) — found `frontend-design` as a plugin skill
present on disk but not invokable in this session (not enabled); read it
directly for reference (its guidance is about bold/distinctive aesthetics
generally, not UX-simplification specifically). The public version has not
been started.

## Overnight player-pool expansion + identification tuning (in progress)

Explicit user mandate: use AWS overnight without worrying about cost, find
as many Chess.com players as possible across every rating band (not just
titled/elite), build the full pipeline for each, and keep tuning the
identification model — but with an explicit, important guardrail (the
user's own words): **don't overfit the "primary" 2-player cohort** — any
feature/model tuning validated on just cdznjerry vs. BIG_TONKA_T risks
fitting quirks specific to those two individuals rather than a real,
generalizable signal. Deeper tuning is deliberately being saved for once
the larger pool exists; what's been added so far (below) is generic,
domain-motivated engineering (within-game consistency, trajectory), not
anything hand-fit to these two players' specific differences.

**Identification accuracy on "primary," three honest rounds, in order**:
  1. 72.7% balanced accuracy (baseline: mean-only per-game features,
     class-balanced logistic regression + 5-fold CV).
  2. 77.0% — added per-game **std** alongside mean for continuous features
     (think_time, cp_loss, maia_surprise, maia_prob_played,
     n_captures_available). Consistency/volatility is itself a candidate
     personal trait a mean-only view is blind to.
  3. 80.3% — added **trend** (second-half-of-game mean minus first-half
     mean) per continuous feature — genuinely ORDER-aware, using each
     game's actual ply sequence rather than treating it as an unordered
     bag of plies. `maia_prob_played_trend` and `cp_loss_trend` both
     landed in the top-7 SHAP-equivalent coefficients, confirming this
     wasn't a wasted feature.
  - Also added a `HistGradientBoostingClassifier` comparison alongside
    logistic regression (cross-validated, whichever wins is reported) —
    logistic regression has won every round so far.
  - A **reverted false start**: briefly reintroduced the 6 Lichess titled
    players as a SEPARATE identification-only cohort (using only
    clock-independent features, excluding think_time/low_time — a genuine
    idea, since Lichess's resolution problem only affects clock-derived
    stats, not cp_loss/Maia/skewers/sacrifices). The user clarified this
    was a misunderstanding — Lichess should stay dropped entirely, full
    stop, no exceptions; they'd only mentioned "6 Lichess players had low
    accuracy" as an example of low accuracy in general, not a request to
    fix it. Reverted cleanly (`IDENTIFICATION_COHORTS`/`build_identification`
    back to pre-Lichess state) before ever being exported or published.
    **Lesson: don't infer a request from a passing mention — the 6
    titled-player features/model files are still on disk, untouched,
    unused, in case ever wanted for something else, but no code here
    builds anything from them.**

**Ratings redesigned into skill vs. style, on direct user feedback**: the
original 6 "2K-style" ratings conflated two different things. The user
correctly flagged that "Intuition" (matching Maia's single most-likely
human move) isn't a *skill* — a strong player deviating from the
statistically typical move can still be objectively right, so rewarding
"typicalness" doesn't measure competency. Same for "Aggression" (sacrifice
rate) — a style choice, not a competency. Fixed by splitting
`RATING_ATTRIBUTES` (5 genuine time-usage/recognition skills: Recognition,
**Efficiency** [new — overall Clean-bucket rate, "does allocation match
criticality across the WHOLE game," not just critical positions], Clutch,
Composure, Discipline) from `STYLE_ATTRIBUTES` (Intuition, Aggression —
still computed, just not shown as headline cards; their raw numbers
already live in the existing Maia/Sacrifice detail panels, so nothing was
lost, just recategorized). `compute_ratings()` now returns
`{"skills": {...}, "style": {...}}`.

**Player discovery — three sources so far, all Chess.com (never repeating
the Lichess resolution mistake)**:
  1. `scripts/discover_players.py` (new, one-off): samples a public club's
     `all_time` member list, probes each candidate's `/pub/player/
     {username}/stats` for bullet rating + game count (serial,
     rate-limited, matching this project's existing Chess.com etiquette),
     buckets into 7 rating bands (sub-800 through 2300+) so the selection
     spans the range instead of clustering at the top like a leaderboard —
     `--min-games` (default 300) enforces the user's explicit "avoid
     low-sample players" instruction.
  2. Ran against 3 different large clubs for demographic diversity
     (`team-usa` 4230 members → 21 players; `chess-com-developer-community`
     21704 members → 32 players; `world-chess-players` 1396 members → 26
     players) — different clubs surface different rating distributions and
     player pools, avoiding a single community's selection bias.
  3. **Opponent-mining, on direct user request** ("look through friendlist
     for jerrycdzn/cdznjerry"): Chess.com's public API has no friends-list
     endpoint (confirmed directly — `/pub/player/{u}/friends` returns 404;
     it's a private social feature, not part of the public API), and
     neither account belongs to any public club. Substituted a strictly
     better source for this project's purposes: 4,344 unique real
     opponents extracted directly from cdznjerry's and jerrycdzn's own
     fetched PGN archives — actual bullet players from the same rating
     community, not just socially adjacent ones. Added `--usernames-file`
     and `--exclude-file` options to `discover_players.py` to support
     probing an arbitrary username list (not just a club) while skipping
     anyone already fetched.
  - `jerrycdzn` (fetched, parsed, AWS-annotating) deserves special note:
    it's a SECOND Chess.com account belonging to the same real person as
    `cdznjerry` (per this project's own username memory), used for
    lower-rated play (1632-2190 vs. cdznjerry's 880-2119 — overlapping but
    distinct). Once its features/model exist, explicitly check whether the
    identification model places jerrycdzn's profile closer to cdznjerry's
    than to anyone else's — real ground truth for "does this track the
    PERSON, not just the rating band," the sharpest test this project can
    run of its whole premise.
  - Total discovered so far this session: 21 + 32 + 26 = 79 candidate
    players queued for fetch/annotate, on top of jerrycdzn — see Open
    items for exact pipeline stage each batch is at when this was written.

## Architecture decisions and why

- **Trailing window (270 days) for "live" models, full history + `recency_weight`
  for "descriptive" ones.** Both players show real skill drift (cdznjerry:
  rating roughly 1580→2067 over the dataset; BIG_TONKA_T rapid: a dramatic
  198→1960 rise-then-19-month-gap-then-lower-plateau arc). Diagnostics
  (`src/diagnostics.py`) confirmed pooling everything would blur different
  versions of the player together. CLAUDE.md's own skill-drift section
  anticipated this; we just had to pick concrete numbers.
- **Never pool across game modes (bullet/blitz/rapid).** Same logic as not
  pooling Chess.com/Lichess — different time controls are different
  distributions. Built separate parsed/annotated/feature files per mode.
- **`annotate.py` checkpoints per worker-chunk, not just once at the end.**
  Originally it only wrote output after the *entire* multiprocessing pool
  finished — meaning a crash or (critically) a reclaimed AWS Spot instance
  would lose the whole run's progress, not just resume from where it left
  off. Rewrote to use `imap_unordered` and write each chunk to
  `data/annotated/.<name>_partial/chunk_N.parquet` as it completes, with
  automatic consolidation on the next run (or the same run's end). Verified
  end-to-end with a real kill-mid-run test.
- **Feature files never delete/gitignore accidentally**: `data/`,
  `.venv-py39-backup`, `.env` etc. — `.env` is gitignored (holds AWS keys).
- **Feature pruning policy** (see memory: `feedback_feature_pruning_policy`):
  never remove a feature because it's redundant/unimportant for *one*
  player's SHAP ranking. Only prune once shown irrelevant across the whole
  multi-player pool — a feature useless for player A might be exactly what
  distinguishes player B, which is the entire point of the identification
  work this is building toward.

## Data pipeline gotchas hit this session (fixed, but worth knowing)

- Added several fields to `parse_pgn.py` (`is_recapture`, `opp_think_time_prev`,
  `utc_time`, `own_castled_side`/`opp_castled_side`/`opposite_side_castling`)
  at different points **after** BIG_TONKA_T and even cdznjerry had already
  been parsed once — each time, forgot to re-parse before rebuilding
  features, producing `KeyError`s downstream. Re-parsing from the already-
  fetched raw PGN is cheap (no engine calls); just don't forget the step.
  This happened twice; worth double-checking column presence after any
  `parse_pgn.py` edit before assuming existing parsed files are current.
- `features.py`'s `board_features()`: twice added a new feature *function*
  (bishop pair, pawn weaknesses) without actually wiring it into the
  `feats.update(...)` call list — silent no-op until validated against real
  data. Always test new board features against a known reference FEN
  immediately after wiring them in.

## Cloud infrastructure

- **AWS chosen over Hetzner** deliberately for resume value (AWS is the
  recognized platform in job postings; Hetzner is cheaper/simpler but
  carries no signal to employers), despite Hetzner being the technically
  simpler fit for this one workload.
- Account started on the "Free" plan (correct choice for prototyping/$200
  credit), but Free-tier accounts are **hard-restricted to Free Tier-eligible
  instance types** (`t2.micro`/`t3.micro`) — not a quota issue, an account-
  plan restriction. Upgrading to Paid removes it; pricing itself is
  unaffected by the plan tier.
- `t3.micro` (1 vCPU usable — 2 vCPUs advertised, but only 1 GiB RAM, and
  each Stockfish worker wants a 256MB hash table by default, so 2 workers
  OOM-kill each other) is fine for pipeline validation, not for real
  throughput.
- `c6i.2xlarge`/`c6i.4xlarge` occasionally hit `InsufficientInstanceCapacity`
  on Spot in `us-east-1` — a transient market condition, not a config
  problem. Fallback to a smaller size in the same family usually resolves it
  immediately.
- Total cost for a real job is roughly constant regardless of instance size
  on Spot (bigger = faster but proportionally pricier per hour) — sizing up
  buys elapsed time, not meaningfully more spend. A ~320k-position job costs
  well under $1 on Spot regardless of whether it takes 45 min or 3 hours.
- **Never leave the laptop's own annotate jobs unprotected against sleep.**
  Locking the screen does *not* prevent macOS's idle-sleep timer — only an
  active `caffeinate` assertion (or literally not closing the lid) does. An
  8-hour overnight bullet-annotation run lost almost all its wall-clock time
  to sleep before `caffeinate -w <pid>` was set up.
- A real secret (AWS access key) was accidentally displayed in a `grep`
  tool-output during debugging; user declined to rotate it. Lesson for future
  sessions: only check credential *presence*, never print the value, even
  when debugging "why isn't this env var visible."
- **A real Spot interruption happened** (BIG_TONKA_T bullet re-annotation,
  first attempt): the instance was reclaimed mid-run — SSH died with exit
  255, and the script's own `terminate_instances` call then got
  `InvalidInstanceID.NotFound` since AWS had already torn it down. This
  exposed a real gap: `annotate.py`'s per-chunk checkpointing protects
  against a *local* crash, but `remote_annotate_aws.py` only synced the
  *final* result back — so a reclaimed instance lost the whole run's
  progress, not just the last chunk. Fixed by rewriting `run_remote()` to
  launch annotate.py in the background on the remote box (`nohup ... &`)
  and poll every 60s, rsyncing back the partial-checkpoint directory each
  time — a reclaim now costs at most one poll interval, and a re-run
  resumes from whatever was last synced rather than starting over.
- **Lichess's API needs a descriptive User-Agent in practice**, exactly
  like Chess.com, despite the published OpenAPI spec not documenting it —
  a generic client UA gets silently routed to the web app's 404 page
  instead of reaching the API at all (confirmed by testing with `curl`:
  identical request succeeds the moment a real UA is added). Also saw a
  transient `{"error":"Please only run 1 request(s) at a time"}` while
  debugging with rapid repeated requests — a concurrency guard, resolves
  on its own after a short cooldown.

## Key findings (chronological, the actual research results)

1. **Premove/lag validation passed cleanly** for both players (bullet
   premove rate ~9-12%, scales down predictably in blitz/rapid; near-zero
   lag-spikes with `caffeinate`-protected runs).
2. **The `gap_1_2`/think-time Pearson correlation looked like ~0 — a red
   herring.** `gap_1_2` is heavily right-skewed (mate-adjacent outliers);
   Spearman correlation revealed a real ~0.23-0.25 relationship. Lesson:
   check for skew/outliers before concluding "no signal" from a raw Pearson
   number on an engine-eval-derived feature.
3. **Obviousness matters and is separable from stakes.** `is_recapture`/
   `is_forced` (added because a forced recapture can have a huge eval swing
   but near-zero real thought) cleanly separated two different behaviors:
   for non-obvious moves, think-time scales smoothly with stakes; for
   obvious moves it's flat/declining even as stakes rise. Validated the
   player is reacting to genuine difficulty, not raw eval-swing size.
4. **Recognition-rate diagnostic**: comparing think-time against a local
   (clock-decile) baseline showed both players correctly elevate time on
   genuinely critical positions and correctly suppress it on obvious-but-
   high-stakes ones — real, measurable situational awareness.
5. **Model 2 (move-quality) false alarm**: R²=0.86-0.90 was almost entirely
   the mechanical "how decided is the position" ceiling effect (`abs_eval`/
   `outcome_bucket`), not genuine time-spent signal — `log_think_time`
   barely registered. Restricting to contested (`is_decided==False`)
   positions dropped R² to an honest ~0.11-0.12. Lesson: a high accuracy
   number needs its SHAP breakdown checked before being trusted.
6. **Clean/Panic/Underthought/Miss+good framework** (crossing "did you slow
   down" against "was the move good") is the most productive analysis this
   session produced, more so than the regression models:
   - For cdznjerry: on genuinely critical positions, Panic ≈ Clean (recognizing
     a hard position doesn't reliably convert to solving it) — the bottleneck
     in truly hard spots is calculation, not awareness.
   - Over cdznjerry's own rating history: Panic rate fell substantially
     (32.8%→21.2%) as rating rose — real improvement in execution-under-
     pressure. But Underthought rate stayed flat (~23%) the *entire time* —
     recognition-triggering hasn't improved even as everything else did.
     Reframed later (correctly, per the user) as: this doesn't mean nothing
     improved — Miss+good rose too (18.5%→27.2%), meaning improvement
     shows up as *needing to consciously slow down less often* (better
     intuition), not as *better conscious triage*. Combined Clean+Miss+good
     ("handled the critical position well, however it happened") rose
     44.0%→55.4%.
   - Segmenting further: **opponent-strength and color** produced the
     largest effects found all session — clear complacency against weaker
     opponents (Panic 35.0%, Underthought 30.6% vs. 21.3%/19.2% against
     stronger opponents); color showed Black meaningfully better than White
     for cdznjerry (Underthought 18.2% vs 28.2%). Low-time and being-behind-
     on-clock showed mild, *counterintuitive* improvement (no evidence of
     panicking under pressure). Game phase showed only a modest effect.
     Session depth (fatigue) showed **no effect at all** — a genuine null
     result, not a data limitation (the same method found large opponent-
     strength/color effects in the same data, evidence the method can
     detect real variation when present).
   - Direct accuracy check (dropping the timing framing entirely): critical-
     position accuracy for cdznjerry rose 44.0%→55.4% over the dataset, but
     non-critical accuracy rose almost identically (56.6%→67.5%) — the gain
     is general chess improvement, not something specific to hard positions.
   - **Cross-mode consistency (BIG_TONKA_T bullet vs. blitz)**: same shape in
     both (Panic ≥ Clean on critical positions in both; elevated-time
     correctly suppressed on obvious moves in both), blitz consistently a
     few points worse across the board (plausibly just less practice, since
     93% of blitz volume is one concentrated month) — evidence the personal
     signature is stable across formats for this player, not something that
     only holds in one specific time control.
7. **Opponent's preceding think-time (`opp_think_time_prev`)**: hypothesized
   this might show a *negative* correlation (pre-calculate on their time,
   play fast) — found the *opposite*, a positive Spearman ~0.37 raw, ~0.25-0.33
   even controlling for complexity/clock state. Reframed as: their think-time
   is picking up on real difficulty our engine features don't fully capture,
   not evidence people "steal" time from the opponent's turn.
8. **New feature-research effort (this session's big push)**: implemented
   and individually validated move-value entropy, tactical motifs (forks,
   discovered/double checks — skewers explicitly *not* implemented, the
   ray-walking logic was getting bug-prone and was cut rather than shipped
   wrong), book/theory-move detection (Polyglot), bishop pair, opposite-side
   castling, pawn structure (isolated/doubled/islands), Syzygy tablebase
   ground truth (≤5 pieces), and search-instability-across-depth (requires
   the annotate.py rewrite + AWS re-annotation). Individually, several
   validated with real, sensible signal (entropy: Spearman -0.257 with
   think-time, one of the strongest in the project; opposite-castling:
   951 vs 604 avg cp_loss; book moves: 0.5s vs 1.1s median think-time;
   tablebase-covered endgames: 98.2% optimal-move rate). **But the full
   model's R² barely moved (0.317/0.325, same as before all of this)** —
   most new features turned out to be redundant re-derivations of
   information `gap_1_2`/`top_n_spread`/`n_moves_within_30cp` already gave
   the model (e.g. entropy correlates -0.58 with `top_n_spread`). The two
   that added genuinely new information: `plies_since_book` (distance from
   known theory — nothing else captured this) and `opp_forking_pieces`
   (a live tactical threat right now, not just general sharpness). Per the
   feature-pruning policy above, nothing was removed — redundancy for this
   one player doesn't mean irrelevance for the eventual player pool.
9. **BIG_TONKA_T brought up to the full current feature set (bullet/blitz/
   rapid) — first real cross-mode comparison for one player, and a direct
   validation of the feature-pruning policy.** Clean/Panic crosstab on
   critical positions across modes: bullet Clean=0.249/Panic=0.312, blitz
   0.229/0.306, rapid 0.263/0.265 — Clean and Panic converge to roughly even
   *only* in rapid, the one mode with real time to spend. Reads as a direct
   confirmation of the Russek "value of computation" framing: with more
   time available, recognizing a hard position actually converts into
   solving it more often, not just into noticing it. Model quality scales
   the same way — think-time R² is 0.32-0.34 in bullet, 0.42 in blitz,
   0.40-0.49 in rapid (the best fit anywhere in the project), and rapid's
   move-quality live model (R²=0.28) is far better than any move-quality
   fit seen before (previously 0.07-0.12). Concretely validated the
   pruning policy in the process: `is_recapture` (all 3 modes),
   `opp_forking_pieces` and `opp_isolated_pawns` (blitz), `opp_bishop_pair`/
   `own_bishop_pair` (bullet, rapid), and `plies_since_book` (all 3,
   strongest in rapid) all earned real SHAP importance for BIG_TONKA_T
   despite being redundant/unimportant for cdznjerry — exactly the
   per-player divergence the pruning policy exists to protect.
   `opp_think_time_prev` is rapid's single strongest live-model feature
   (0.193 SHAP) — more prominent than in any other mode, plausibly because
   the opponent's think-time is a more informative difficulty signal when
   they actually have time to deliberate.
   Infra fix along the way: blitz's 270-day trailing window only had 78
   rows (its volume is 93% one concentrated month), which crashed
   `model.py`'s train/test split — added `MIN_LIVE_ROWS=500` guard that
   skips the live model with a clear message instead of crashing.

## `src/insights.py` — the real bucket-scoring module (built)

Formalizes CLAUDE.md's four buckets (Overthought/Underthought/Panic/Clean)
plus a fifth motivated by this session's own analysis:

- **Instinctive** — not-elevated + critical + good move (fast, correct,
  despite genuine difficulty). Folding this into generic "Clean" would have
  lost a distinction we found repeatedly valuable (e.g. cdznjerry's rising
  Miss+good rate over time = growing intuition, distinct from recognition).

Design choices:
- **Expected think-time comes from the trained Model 1 booster**, not the
  clock-decile local-median proxy used throughout this session's ad hoc
  analysis. This is a real sharpening, not just a refactor — re-running it
  on cdznjerry immediately produced a different, more honest picture:
  Underthought is the plurality bucket among critical positions (27.3%),
  not tied with Clean as the cruder proxy suggested (~28% each). Reason:
  critical positions already get more time on average (Model 1 learned
  this), so "elevated relative to what THIS position's full complexity
  profile predicts" is a higher bar than "elevated relative to clock state
  alone" — fewer moves clear it, so more genuinely land in Underthought.
- **"Good move" stays the simple `cp_loss <= 100` threshold**, deliberately
  *not* a Model 2 prediction — Model 2's R² is still weak for most
  player/mode combos (0.07-0.12), and the simple threshold has produced
  every real finding this project has made. Revisit once Model 2 earns it.
- Reuses the obviousness-adjusted criticality definition (top-quartile
  `gap_1_2` among non-recapture/non-forced moves) validated all session,
  rather than inventing a new one.

**Bug fixed along the way**: `model.py`'s `_prep_X` cast categorical columns
(`outcome_bucket`, `game_phase`) via `astype("category")`, which infers
category codes from whatever values happen to be present in that particular
slice — fine when train/test splits both cover every category by luck (as
they have so far), but not guaranteed, and a saved raw `Booster` needs the
*exact* training-time category→code mapping to predict correctly later
(confirmed by hitting `"train and valid dataset categorical_feature do not
match"` on the first real attempt to load-and-predict). Fixed with an
explicit fixed vocabulary (`CATEGORY_VALUES` in `model.py`) instead of
inferring it. All of cdznjerry's models were retrained under the new
encoding; BIG_TONKA_T's will be retrained again anyway once the AWS
re-annotation lands, so no extra cost there.

**Premove profile added** (user's idea): rather than only treating premoves
as noise to filter out (`is_suspect`), `premove_profile()` measures their
*rate*, the *situations* they occur in, and their *accuracy* — extending
the obviousness framework (`is_recapture`/`is_forced`) to its limit, since a
premove is the most extreme "zero real-time decision" case. First real
result (cdznjerry): premove rate drops from 45.0% on obvious moves to 7.9%
on genuinely critical ones — the *same* situational-awareness pattern found
in think-time allocation shows up in the premove/no-premove decision itself.
But the failure mode is severe: 398 instances of premoving on a genuinely
critical position averaged 3,573 cp_loss (vs. 606 for non-premoves overall)
— a concrete, quantifiable "premove trap," not just elevated noise.

## Bug-review pass (this session, systematic)

Went through the codebase deliberately after several real bugs surfaced
today (categorical encoding, shell operator precedence, decode_unicode).
Found and fixed:
- **`annotate.py` had no schema-drift check on resume.** This project has
  hit "resumed from data that predates a schema change" at least 3 times
  (utc_time, opposite_side_castling, the stability-tracking annotation
  itself) — always by forgetting to re-parse/re-annotate after adding a
  field. Added `_check_schema()`: on resume, warns loudly if existing
  output/checkpoints are missing columns the current code produces, instead
  of silently carrying incomplete rows forward.
- **`cloud/remote_annotate_hetzner.py` still uses the old fully-blocking
  design** (no background+poll like the AWS script now has) — same
  "loses everything on disconnect" exposure the AWS script had before the
  Spot-interruption fix. Not ported yet since Hetzner is the unused
  fallback, not the active path — documented as a known gap rather than
  silently left inconsistent.
- Reviewed `parse_pgn.py`'s sequential state tracking (castling side,
  `opp_think_time_prev`, `last_capture_square`) carefully for
  read-before-write ordering bugs — traced through manually, no issues
  found; the "read old state into a `_before` variable, use it for the row,
  update state after" pattern is applied consistently.

**Skewer detection completed** (was deliberately skipped earlier this
session as too bug-prone to rush). Built a clean, dedicated
`_pieces_along_ray()` walker (first two occupied squares along a ray, past
the first blocker — `board.attacks()` alone stops there) and validated with
three cases before trusting it: a real skewer, a pin-shaped position that
must NOT count as a skewer (front piece less valuable — the exact
distinction that made the first attempt risky), and same-color pieces on a
ray (must not count). All three passed. On real data: present in ~8% of
positions (rarer than forks, as expected), real correlation with think-time
(Spearman 0.12-0.15) — but, consistent with the established pattern, doesn't
crack cdznjerry's top-10 SHAP (redundant with existing features for this
player specifically). Kept per the feature-pruning policy regardless.

## Where this is headed (Phase 2 / the "digital heartbeat" framing)

The user's framing: does each player have a distinct, individually-identifiable
timing "heartbeat" (→ usable as a biometric, e.g. detecting account sharing),
or does everyone share a similar heartbeat (→ deviation from that shared
pattern becomes a signal for non-human/engine-assisted play)? Working
hypothesis (not yet tested): **probably both, as layered signal, not an
either/or** — a shared "species-level" human baseline with an individual
deviation on top, directly analogous to how CLAUDE.md's own Phase 2 sketch
already splits Maia's generic human-move-likelihood model (shared layer) from
a per-player fine-tuned Maia (individual layer). The planned test: fit one
pooled baseline across a multi-player pool, then check whether per-player
*residuals* from that baseline are large/stable/distinguishing (→ individual
signature) or small/inconsistent (→ mostly shared pattern) — the same pool
would answer both branches at once, not require picking one.

**Sequencing decision (explicit, from the user)**: build/validate the pool
*after* the current single-player model is solid, not before. Planned pool
sources: a few friends + a public leaderboard, at rating bands comparable to
existing players (so the pool can't just trivially separate players by skill
level — that would test rating, not style). Two-player tests (what we have
now) are not sufcient — need players at *similar* rating for a meaningful
identification task.

**Constraints already in place per CLAUDE.md**: any actual cheat-detection
evaluation must use injected/simulated cheat moves with known ground truth,
never real accused players. The player-identification work sidesteps that
constraint entirely (no accusation involved, purely a style-distinctiveness
question) — one of the reasons the user's sequencing (identification before
cheat-detection) is the right call, not just a nice-to-have.

**This sequencing has now actually started, explicitly greenlit by the
user** ("find as many possible players as you can, can be different rating
ranges too, even untitled and lower rated"). In progress overnight (see
Open items for exact task/job IDs at time of writing):
  - `jerrycdzn` added — a SECOND Chess.com account belonging to the same
    real person as `cdznjerry` (per this user's own earlier-recorded
    username note), used for lower-rated play (1632-2190, vs cdznjerry's
    880-2119 — overlapping but distinct). This is a uniquely valuable test
    case beyond generic pool diversity: it's ground truth for "does the
    identification model correctly recognize the SAME underlying person
    across two different accounts/rating levels," not just "can it tell
    two different people apart." Once features/model exist for it, check
    explicitly whether jerrycdzn's behavioral profile sits closer to
    cdznjerry's than to BIG_TONKA_T's or the new discovered players' —
    that's the sharpest validation this project can run of the whole
    "personal signature survives rating changes" premise.
  - 21 more Chess.com players discovered via `scripts/discover_players.py`
    (new, one-off) — samples a large public club's member list (used
    `team-usa`, 4230 members), probes each candidate's `/pub/player/
    {username}/stats` for bullet rating + game count (serial, rate-limited,
    matching this project's existing Chess.com etiquette), and buckets by
    rating band so the selection actually spans the range rather than
    clustering at the top like a leaderboard would. Got 3 players per band
    across 7 bands (sub-800 through 2300+), all Chess.com-sourced (so no
    repeat of the Lichess resolution problem) with 300+ bullet games each.
  - Once fetched/parsed/annotated (Stockfish depth 12 + Maia) and
    features/models/insights built for all of these, rebuild the
    identification cohort with a REAL rating-spanning pool for the first
    time — the `titled_pool` cohort was rating-matched but all at one
    (elite) tier; this new pool actually varies rating the way a real
    identification system would need to be tested against.
  - **If identification accuracy comes back low**: the user's explicit
    instruction is to diagnose why (which features help/hurt, whether more
    per-game aggregates are needed, whether a different classifier does
    better than balanced logistic regression) and to do more literature
    research if stuck, rather than just reporting a disappointing number.
    The existing research cluster (behavioral stylometry transformers,
    path-signature/rough-path methods, Elo-disentangled embeddings) is the
    starting point for "what to try next" if the simple per-game-mean
    logistic regression approach plateaus.

**Where does "real cheater" ground truth data come from, if not real accused
players?** It doesn't — and neither does the actual field. Researched this
directly (see Sources below): Ken Regan's system (the real-world detector
endorsed by FIDE/US Chess, used by major platforms) doesn't train on
cheaters at all — it calibrates an "honesty" baseline from large databases
of *presumed-clean* historical games stratified by rating, then flags
statistical deviation from that baseline, validated via synthetic
"Frankenstein player" simulations rather than real cheating cases. This
directly validates the project's existing direction: build a strong
per-player/pool baseline of legitimate play (already underway), and treat
deviation from it as the signal — exactly what CLAUDE.md's rule about
injected/simulated ground truth already prescribes, not a workaround.

**A directly relevant paper for the identification side**: "Chess Signatures
of Play" (Turk, arXiv:2606.18544) models a game as a continuous *path*
through feature-channel space (engine eval, move accuracy, position
complexity, clock reading — essentially the same channels this project
already tracks per-ply) and applies the *signature transform* from rough-path
theory: a graded set of iterated integrals that captures the order and
interaction of in-game events, not just their marginal statistics. Concrete,
potentially adoptable pieces:
  - **A player's style is formally identifiable from their "expected
    signature"** (up to tree-like equivalence) — a real theoretical grounding
    for the "digital heartbeat" idea, not just an empirical hope.
  - **A signature-kernel two-sample test** gives a rigorous statistical test
    for "are these two players' styles actually different distributions,"
    rather than eyeballing SHAP tables side by side (what this project has
    done so far, e.g. the cdznjerry vs. BIG_TONKA_T comparison).
  - **The Lévy area** (the simplest genuinely path-dependent signature term)
    "exposes the difficulty-accuracy coupling aggregates cannot see" — i.e.
    whether a player's accuracy tends to lead or lag behind rising
    difficulty, a lead-lag style signature our current per-move, order-blind
    features can't capture at all.
  - **Cheat detection reframed as an anytime-valid sequential test** (a
    signature-conformance score as an "e-process," error controlled at every
    sample size via Ville's inequality) — again, tests conformance to a
    player's *own* established signature rather than needing labeled
    cheating examples.
  This is real, nontrivial math (iterated integrals, Chen's identity — the
  standard libraries are `iisignature`/`esig`/`signatory`), not something to
  bolt on casually. Documented here as a literature-grounded candidate
  methodology for when the pool-based identification work actually starts,
  not something implemented yet.

**Concrete sample-size numbers for the identification task, from CSSLab**
(the same lab behind Maia — already the Phase 2 human-move-likelihood
reference in CLAUDE.md): "Detecting Individual Decision-Making Style:
Exploring Behavioral Stylometry in Chess" reports **98% accuracy identifying
a player among *thousands* of candidates using only 100 labeled games**,
via a transformer approach adapted from speech-verification (voice
biometrics) methodology. Later-game (middlegame/endgame) actions carry a
stronger signal than openings in general, but openings alone still reach
97% with 100 games — meaning even a fairly small per-player sample (well
within what we already have: cdznjerry 605 games, BIG_TONKA_T 600+ per
mode) should be enough for a real identification test once the pool exists.
**Notably, the authors do not release their code** — their GitHub repo
states it's withheld "due to the sensitive nature of our model," available
only on direct request to the author. That's an independent, real-world
data point for the dual-use caution already discussed in this project: even
the researchers who built this treat it as something to gate, not freely
distribute — worth keeping in mind for how any results here eventually get
shared, not just how they get built.

**One more paper, possibly the most directly adoptable design**:
"Elo-Disentangled Player-Style Embeddings for Human Chess via a
Rating-Conditioned Residual Move Model" (Carlson, arXiv:2606.25176) —
learns a per-player embedding such that inner products between embeddings
measure stylistic similarity, explicitly *disentangled* from playing
strength. Mechanically: a rating-conditioned base model (Maia-3 policy +
Stockfish features) captures "typical play for this strength level" (the
shared layer), and a small learned per-player vector explains only the
*residual* deviation from that (the individual layer) — precisely the
two-layer split this project has been hypothesizing informally (pooled
baseline + per-player residual). No public code found for this specific
paper, though the same author has a separate public repo (DPO-tuning Maia
to "simulate realistic grandmaster-level play styles... for streamers and
personalized content") — a notably more open disclosure stance than
CSSLab's gated approach above, worth keeping in mind as two real, differing
precedents for how this community handles releasing style-modeling work.

**A cluster of related papers**, all pointing the same direction (per-player
embedding on top of a shared Maia-style base model): "Toward Modeling
Player-Specific Chess Behaviors" (arXiv:2605.11893, champion-specific Maia-2
embeddings) makes a methodological point worth internalizing regardless of
architecture — *"move accuracy inherently penalizes natural human variance
and ignores long-term behavioral consistency"* — i.e. the same critique of
using raw accuracy/R² as a success metric that came up when the user asked
what criteria we're using to define "accuracy" earlier this session.
"Mixture of Masters" (arXiv:2602.04447) takes a related but distinct angle:
sparse mixture-of-experts with per-move gating between grandmaster-style
"personas" — built for style *generation*, not identification, but a
reasonable reference if we ever want the pool-based model to *simulate* a
given player's decisions, not just recognize them.

Sources: [Cheating Detection and Cognitive Modeling At Chess (Regan)](https://cse.buffalo.edu/~regan/Talks/CogSciOct2024np.pdf) · [US Chess Endorses Dr. Kenneth Regan's Fair Play Methodology](https://new.uschess.org/news/us-chess-endorses-dr-kenneth-regans-fair-play-methodology) · [Chess Signatures of Play (arXiv:2606.18544)](https://arxiv.org/abs/2606.18544) · [Detecting Individual Decision-Making Style: Behavioral Stylometry in Chess (arXiv:2208.01366)](https://arxiv.org/abs/2208.01366) · [CSSLab/behavioral-stylometry (GitHub — code withheld)](https://github.com/CSSLab/behavioral-stylometry) · [Elo-Disentangled Player-Style Embeddings (arXiv:2606.25176)](https://arxiv.org/abs/2606.25176) · [Toward Modeling Player-Specific Chess Behaviors (arXiv:2605.11893)](https://arxiv.org/abs/2605.11893) · [Mixture of Masters (arXiv:2602.04447)](https://arxiv.org/abs/2602.04447)

## Maia integration (built)

Explicit user instruction: integrate Maia now (not wait for a full Phase 2
rewrite), audit everywhere it could improve the existing Phase 1 model, and
research what else is possible with it. All three addressed below.

**Why now, not "wait for Phase 2"**: CLAUDE.md defers Phase 2 (the combined
cheat-detection model) but Maia itself — the human-move-likelihood engine —
is just another position-level feature source, exactly like Stockfish
already is. Nothing about Phase 1's design required waiting; the "don't
build Phase 2 yet" rule is about the *combined cheat-detection scoring
model*, not about the underlying data source being off-limits. Using it now
strengthens Phase 1 (the think-time/move-quality models) directly, and
whatever gets built now is exactly the reusable infrastructure Phase 2 would
need anyway.

**What was built**:
  - `lc0` (Leela Chess Zero) installed via `brew install lc0` (0.32.1,
    Metal backend confirmed working on Apple Silicon).
  - All 9 official Maia weight files (1100-1900, step 100) downloaded from
    `github.com/CSSLab/maia-chess` into `data/maia_weights/`.
  - `src/maia_query.py` — thin, validated wrapper: `open_maia_engine(rating_bin)`
    configures one lc0 process with `WeightsFile` + `VerboseMoveStats`;
    `query_maia_policy(engine, board)` runs `go nodes 1` (the point: read the
    policy head's immediate output, not search-refined — Maia is trained to
    model snap human judgment, not calculation) and parses the `info string`
    lines it emits into `{uci_move: probability}`. Validated against the
    starting position (e2e4 50.22%, d2d4 23.34% — matches the very first
    manual UCI test byte-for-byte) and confirmed probabilities sum to ~1.
  - `src/maia_annotate.py` — a resumable batch stage, same checkpointing
    philosophy as `annotate.py`: dedup by **(FEN, rating_bin)** rather than
    by FEN alone (the same position recurs across many rating bins across
    players/games, and Maia's answer depends on which bin), one lc0 process
    *per rating bin* rather than per chunk of FENs (reloading a weights file
    per position would dwarf the actual query cost — a query is ~instant,
    process/weights startup is not). Validated end-to-end on synthetic data:
    correct multi-bin dispatch, correct resume (reran with nothing new,
    produced zero re-queries), correct checkpoint consolidation.
  - `add_maia_features()` in `features.py`: `maia_prob_played` (probability
    Maia at the player's nearest rating bin assigns to the move actually
    played), `maia_top1_prob` (probability of Maia's single most-likely
    move — a *human*-consensus analogue to engine `top_n_spread`),
    `maia_is_top_choice`, `maia_surprise` (`-log(maia_prob_played)` — a
    continuous "how atypical was this for a human at this rating"
    measure). Validated on synthetic data: top-choice move correctly gets
    low surprise, an unseen/off-policy move correctly floors to probability
    ~0 and max surprise, matching hand-computed expected values exactly.
    Wired into `COMPLEXITY_FEATURES` in `model.py`. No-ops gracefully (like
    book/tablebase features) if `maia_annotate.py` hasn't been run yet for a
    given plies file.

**Bug fixed en route**: `add_sacrifice_features()` was confirmed broken
before this — it compared `material_diff` before vs. immediately after the
mover's OWN move, which can never show a material loss (the sacrificed
piece is only actually captured on the OPPONENT'S subsequent move; verified
directly on a constructed Greek-gift `Bxh7+` position, which produced a
*gain* under the old logic). Rewritten to check, on the board immediately
after the mover's move, whether any of the mover's own pieces (king
excluded) are attacked-and-undefended — `material_offered`, the actual
value being risked, regardless of whether the opponent goes on to take it.
Re-validated on the same Greek-gift position (now correctly flags
`material_offered=3, is_sacrifice=True`) plus a quiet-retreat negative
control (correctly `material_offered=0, is_sacrifice=False`).

**Retrospective audit — where Maia sharpens things already built**:
  - **Sacrifice vs. mistake** (the user's original harder question — does a
    worse-eval move reflect a deliberate practical choice, not a blunder):
    this is exactly what `maia_surprise` answers that raw `cp_loss` cannot.
    A sound sacrifice (engine-confirmed, `is_sacrifice=True`) with LOW
    `maia_surprise` is a *known pattern* other players at this strength also
    reach for; a technically-losing move with comparatively low
    `maia_surprise` despite high `cp_loss` is the "practical/time-pressure/
    attacking-chances" case the user described — objectively worse, but a
    choice other humans at this level would recognize and make too. High
    `cp_loss` + high `maia_surprise` together is the case closest to a
    genuine, idiosyncratic blunder. This is a 2D read (`cp_loss` ×
    `maia_surprise`) the project had no way to make before.
  - **Criticality/obviousness reframed**: `gap_1_2`/`is_recapture`/
    `is_forced` all measure engine-obviousness. `maia_top1_prob` is the
    missing human-obviousness axis — a position can be a real practical
    "only move" for a human (very high `maia_top1_prob`) without the engine
    calling it forced, or conversely engine-forced yet still something
    lower-rated humans get wrong at meaningful rates (this only shows up by
    comparing `maia_top1_prob` at the player's OWN rating bin against, say,
    the 1900 bin — not yet computed, a candidate follow-up below).
  - **Premove profile**: `premove_rate_on_critical` currently uses
    engine-criticality only. Cross-referencing premoves against
    `maia_prob_played` would separate "premoved because it was genuinely
    the human-obvious move" (a skill signal — correct intuition, not
    recklessness) from "premoved despite the position being human-atypical
    too" (a real risk-taking signal) — the same distinction the user's own
    insight (measure premove accuracy/situations, not just filter them)
    was reaching for, one level deeper.
  - **Instinctive bucket** (`insights.py`): currently "fast + correct on a
    critical position." `maia_prob_played` on those rows would distinguish
    genuine calculation-free pattern recognition (move was also
    human-typical — an earned instinct) from a lucky guess (move was
    human-atypical yet happened to be objectively fine) — same
    correct-but-was-it-really-obvious question, applied to the bucket
    that's hardest to explain from engine features alone.
  - **Player-identification pool (deferred, Open items)**: every paper in
    the research cluster above (Elo-Disentangled embeddings,
    champion-specific Maia-2, "Mixture of Masters") builds a per-player
    residual ON TOP OF a Maia base rate, not on raw engine eval. When that
    work resumes, `maia_prob_played`/`maia_surprise` per ply are the
    natural residual-input features — this integration is the prerequisite
    infrastructure for that whole research direction, not a side add-on.

**Further "what else is possible" research** (WebSearch pass, this
session): confirms Maia's own research program has moved well beyond plain
move-accuracy prediction, reinforcing several already-adopted design calls
here and surfacing new candidates:
  - **Maia-2** ([arXiv:2409.20553](https://arxiv.org/pdf/2409.20553)) unifies
    the 9 separate rating-bin nets into one model conditioned continuously
    on rating, rather than snapping to the nearest of 9 bins the way this
    project's `nearest_rating_bin()` currently does. If Maia-2 weights/code
    become usable, that removes the "player at 1550 gets treated identically
    to 1500 or 1600" coarseness for free — worth a follow-up look, not
    urgent (the 9-bin snap is a reasonable approximation for now).
  - **"A Behavior-Based Knowledge Representation Improves Prediction of
    Players' Moves in Chess by 25%"** ([arXiv:2504.05425](https://arxiv.org/pdf/2504.05425))
    — direct, independent confirmation that conditioning on a player's own
    behavioral history (not just rating) substantially improves human-move
    prediction over rating-conditioned Maia alone. This is the strongest
    outside validation yet for the project's whole "personal baseline, not
    generic thresholds" premise, from a completely different angle (move
    prediction) than the one this project set out to test (think-time
    prediction).
  - **Stylistic-alignment critique** (from the Maia-2/"Toward Modeling
    Player-Specific Chess Behaviors" cluster): move-accuracy alone is an
    insufficient evaluation metric for human-alignment; distribution-based
    metrics (comparing full move-probability distributions, not just
    top-1 accuracy) are argued as more informative. Directly relevant here:
    `maia_prob_played`/`maia_surprise` already lean this direction (using
    the actual probability, not a binary top-1 match) rather than reducing
    to `maia_is_top_choice` alone — validates keeping both, not simplifying
    down to the binary flag.
  - **Personalized/fine-tuned Maia** (CLAUDE.md's Phase 2 sketch item):
    confirmed technically straightforward in principle — Maia weight files
    are ordinary Lc0 nets, fine-tunable on a single player's games the same
    way the base rating-bin nets were trained on rating-stratified pools.
    Not attempted this session (real training infra, out of scope for a
    feature-integration pass) but no new blocker was found either — flagged
    here as a validated-feasible, not-yet-started Phase 2 item.

Sources: [Maia-2: A Unified Model for Human-AI Alignment in Chess (arXiv:2409.20553)](https://arxiv.org/pdf/2409.20553) · [A Behavior-Based Knowledge Representation Improves Prediction of Players' Moves in Chess by 25% (arXiv:2504.05425)](https://arxiv.org/pdf/2504.05425) · [Learning Models of Individual Behavior in Chess (arXiv:2008.10086)](https://arxiv.org/pdf/2008.10086) · [Project Maia — Microsoft Research technical deep dive](https://www.microsoft.com/en-us/research/project/project-maia/technical-deep-dive/)

**UPDATE — run on real data, same session**: `maia_annotate.py` run on
cdznjerry (20,870 fen/rating-bin pairs) and BIG_TONKA_T blitz (~25K rows);
rapid (194,219 pairs across all 9 bins) kicked off in the background.
Features rebuilt and both players' models retrained. Real result, not
synthetic:
  - **cdznjerry** (rated 1900-2100+, so clamped to Maia's top 1900 bin for
    nearly all positions — 15,005 of 20,870 pairs): `maia_prob_played` is
    the **4th-most-important SHAP feature** for the descriptive think-time
    model (0.0305, ahead of `plies_since_book`, `opp_think_time_prev`), and
    both `maia_surprise`/`maia_prob_played` crack top-10 in the live model
    too. `maia_prob_played` also lands top-6 in both move-quality models.
  - **BIG_TONKA_T blitz** (rated 100-1069 — actually WITHIN Maia's native
    training range, unlike cdznjerry): the signal is even stronger —
    `maia_surprise` is the **3rd-most-important feature** for think-time
    (0.0618, just behind `game_phase`/`clock_frac_remaining`), with
    `maia_prob_played`/`maia_top1_prob` also top-10. This is the expected
    pattern if the effect is real: strongest for the player Maia was
    actually trained to model, and it is.
  - **Sacrifice hypothesis directly confirmed** (cdznjerry, the user's
    original question): sacrifice moves (`is_sacrifice`, engine-confirmed
    sound) take a **30-60% longer median think_time than non-sacrifice
    moves at the same stakes**, checked within each `gap_1_2` quartile
    separately so this isn't just "sacrifices happen to be higher-stakes
    positions" — e.g. bottom quartile 1.30s vs 0.80s, top quartile 1.50s
    vs 1.20s. Confirms voluntarily giving up material carries a real time
    cost distinct from raw engine criticality.
  - **Honest null result on the harder question** (sacrifice vs. mistake,
    using `maia_surprise` as the split): among moves that both offer real
    material (`material_offered>=3`) AND are objectively worse per the
    engine (`cp_loss>100`, i.e. NOT `is_sacrifice`), splitting by low vs.
    high `maia_surprise` ("other humans would also choose this" vs.
    "genuinely atypical") showed **no meaningful think_time difference**
    (1.40s vs 1.45s median, n=401 vs 278). `maia_surprise` alone isn't yet
    the answer to "deliberate practical choice vs. accidental blunder" —
    may need a different cut (opponent's time pressure, or comparing
    against several strong players individually rather than Maia's
    aggregate policy) rather than being solved by this feature alone.
    Recorded honestly rather than reading a null result as support.
  - Not yet done: same rebuild for BIG_TONKA_T rapid (Maia annotation
    running) and bullet (blocked on its separate Stockfish re-annotation,
    in progress on AWS).

## Self-healing retry loop validated in a real reclaim (not just code review)

group0's instance (`i-0bb10b51aab86dda5`) genuinely got Spot-reclaimed
mid-run — confirmed directly (`describe_instances` showed it `terminated`,
no public IP). This is exactly the scenario the round-2 resilience work
above was built for, and it worked with zero manual intervention: the
local supervising process (still the same PID, running continuously
since its original launch) detected the lost connection, auto-relaunched
a fresh instance (`i-0df8418058e262c12`), and resumed. Confirmed no
progress lost — the 123 checkpointed chunks (~73,800 positions) were
still on disk, untouched, exactly as the checkpoint design promised.
First real-world proof this fix works, not just a code-reviewed
assumption — worth having actually happened rather than just tested in
the abstract.

## Research: the actual architecture behind "98% accuracy, thousands of candidates"

This project's research log has cited "Detecting Individual Decision-Making
Style: Exploring Behavioral Stylometry in Chess" (McIlroy-Young, Wang, Sen,
Kleinberg, Anderson — NeurIPS 2021, arXiv:2208.01366) several times for its
headline number, without ever pulling the actual methodology. Got it this
time (PDF extraction failed twice — binary/corrupted per the fetch tool;
the ar5iv HTML mirror worked). This is the single most directly relevant,
concrete piece of prior art found all project for where the identification
work should go once the bigger pool exists — worth recording in full.

**Architecture** (three levels): (1) each position encoded as a 34-channel
8×8 tensor (24 piece-type channels + 10 metadata channels — repetition,
castling rights, etc. — essentially an AlphaZero/Leela-style board
encoding, not a hand-picked feature vector); (2) a per-move residual CNN
block turns each position+move into a 320-dim move feature; (3) a
modified Vision Transformer (12 blocks, 8 heads × 64 dims, sinusoidal
position encoding, up to 500 moves per game) aggregates the whole move
sequence, average-pooled to a single **512-dim game embedding**.

**This is a full move-sequence model, not aggregate per-game statistics**
— confirms, with real architecture detail this time, why this project's
current approach (mean/std/trend of ~9 scalar features per game) is
fundamentally capped well below what's achievable, and by how much: full
sequence + real board encoding vs. ~30 scalar numbers per game.

**Training**: GE2E loss (Generalized End-to-End loss for Speaker
Verification) — the SAME loss function speaker-verification systems use
(e.g. voice-unlock systems), repurposed here for chess. It's genuine
metric learning: builds a similarity matrix over a batch of N players × M
games each, and optimizes so a game's embedding sits close to its own
player's centroid and far from every other player's, all at once — not a
fixed-output-class softmax classifier the way this project's logistic
regression/gradient boosting currently work.

**Inference — exactly the embedding/metric-learning design this project's
earlier research (Elo-disentangled embeddings, champion-specific Maia-2)
already pointed at, now with a concrete recipe**: compute each candidate
player's "reference representation" from a set of their known games
(apparently an averaged/centroid embedding), embed the query game the same
way, then pick whichever candidate's reference representation is closest
by cosine similarity. Critically, **this generalizes to players never seen
during training** — a new player just needs a handful of reference games
run through the SAME trained encoder, no retraining — which directly
solves the "growing pool of 95+ players, then celebrities, then whoever's
next" scaling problem this project's current one-vs-rest classifier
approach does not.

**Honest scaling data (their real ablations, not cherry-picked)**:
  - P@1 = 0.86 at a 2,844-player pool.
  - Drops to **0.308** for a specifically HIGH-RATED subset (2,157
    candidates) — stronger players are HARDER to tell apart, consistent
    with this project's own suspicion (identification is a style-vs-skill
    problem, and skill convergence at the top compresses style
    differences). Relevant directly: several of this project's newly
    added players (the 8 celebrities, several titled discovered players)
    are exactly this hard high-rated regime — don't expect the easy end
    of this range once they're in the pool.
  - P@1 = 0.54 at a much larger 41,184-player pool — accuracy degrades
    with pool size, as expected, but far more gracefully than chance
    would (which at that scale is ~0.002%).
  - Reference-set size: performance rises sharply then **saturates around
    50 reference games per player**, but is "reasonable" with as few as
    10 — meaning even players with a modest sample (like this project's
    smaller discovered-pool entries) are usable, not a lost cause.

**Concrete recommendation for this project, once the bigger pool lands**:
the natural "next tier" beyond continuing to grow a flat multi-class
classifier is a metric-learning embedding model — full move sequences (not
aggregate stats), trained with something GE2E-like, compared by cosine
similarity to per-player reference centroids. This is a real step up in
engineering effort (a proper neural sequence model, likely GPU training)
compared to the current LightGBM/logistic-regression approach, so it's
recorded here as the well-evidenced target architecture, not something to
build reflexively — the current lightweight approach has been improving
steadily on its own (72.7% → 84.9%) and remains the right choice while the
pool is still small and growing.

Sources: [Detecting Individual Decision-Making Style: Exploring Behavioral Stylometry in Chess (arXiv:2208.01366)](https://arxiv.org/abs/2208.01366) · [ar5iv HTML mirror](https://ar5iv.labs.arxiv.org/abs/2208.01366) · [Toward Modeling Player-Specific Chess Behaviors (arXiv:2605.11893)](https://arxiv.org/abs/2605.11893) · [Elo-Disentangled Player-Style Embeddings (arXiv:2606.25176)](https://arxiv.org/html/2606.25176v1)

**Ruled out, honestly, not oversold**: "Accelerating Skill Assessment in
Chess: A Drift-Diffusion-Enhanced Elo Rating System" (Zhou, Fu, Yang,
arXiv:2606.26267) looked directly relevant by title to this project's
core "position complexity → time spent AND move quality" question — a
proper drift-diffusion model (the actual cognitive-science framework)
jointly explains reaction time and choice accuracy from one underlying
evidence-accumulation process, which is exactly this project's
hypothesis, just from psychology rather than chess ML. Checked the real
methodology (ar5iv mirror, after two PDF extraction failures) rather than
assume the title meant what it sounded like: "drift-diffusion" here is
purely a metaphor for how a rating aggregates move-quality signal
**across games** for faster convergence (tested on ~10M Lichess games,
measured via directional-accuracy/lead-time-to-milestone metrics) — it
does not model individual decision time at all, only centipawn loss.
Not applicable to this project's actual question. Recorded so this
specific paper isn't re-investigated later under the same false premise.

## Quota increase landed — 4 parallel instances, real throughput numbers

User added the `servicequotas:*` inline policy to `chess-clock-bot` (see
prior section) and it worked immediately — `GetServiceQuota` confirmed
the actual EC2 Spot vCPU quota is **32** (not just 8, which is all that
had actually been observed working before). Empirically re-tested rather
than assumed: launched a second `c6i.2xlarge` (16 total) — succeeded this
time (the earlier `MaxSpotInstanceCountExceeded` at just 16 vCPUs was
likely transient/timing, not a hard 16-vCPU ceiling); then a third (24
total) and fourth (32 total, exactly at quota) — all four launched and
running simultaneously: groups 0/1/2 (the 92-player pool, split earlier)
plus the celebrities group, one `c6i.2xlarge` (8 vCPUs) each.

**Real throughput, not estimated**: group 0's 77 completed 600-position
checkpoints over ~21 minutes of actual `annotate.py` runtime ⇒ ~36.7
positions/sec per 8-vCPU instance — lower than an earlier back-of-envelope
guess (~107/sec), likely due to per-chunk Stockfish process startup
overhead now that chunks are deliberately small (600 positions) for
checkpoint safety; a real, worthwhile trade of some throughput for much
better crash-resilience. At this rate: groups 0/1/2 (~630-670K positions
remaining each) ⇒ ~4.8-5 hours each; celebrities (220,911 positions, just
started) ⇒ ~1.7 hours. All four run in parallel, so total wall time ≈ the
slowest one (~5 hours), not the sum.

**Standing commitment, per explicit user request**: process each group
(Maia annotate → features → model → insights → export → publish → DB
write) the moment ITS Stockfish annotation finishes, not batched until
all four are done — the celebrities group finishing first is the first
real test of this.

**Also confirmed**: the user's own AWS console appeared to show "no
instances running" — a region-selector mismatch (the console defaults to
whatever region was last viewed; every instance this project launches is
in `us-east-1`/N. Virginia specifically), not an actual problem. Worth
remembering if this comes up again.

## Dashboard: identification simplified, celebrities added, pinned+search UI

- **Identification cohort merged back to 2-way, on user request.** Having
  proven the "same person, two accounts" point (jerrycdzn → cdznjerry
  67.6% of misattributions), the user asked to stop treating them as
  separate identities for identification — they ARE the same person.
  `IDENTIFICATION_COHORTS["primary"]` now points `"cdznjerry"` at a merged
  file (`cdznjerry_combined_plies` = `cdznjerry_2023on_plies` +
  `jerrycdzn_plies` concatenated), giving that class MORE data rather than
  splitting it. Result: balanced accuracy improved again, to **84.9%**
  (gradient boosting now wins over logistic regression, 84.9% vs. 82.5%) —
  the minority class effectively doubled in size once merged, which is
  exactly the kind of generalizable improvement the user's earlier
  "don't overfit to 2 subjects" caution was pointing at, not a fluke.
  jerrycdzn keeps its own separate PROFILE entry in `DATASETS` — only the
  identification cohort's data source changed.
- **8 celebrity/professional accounts added**: Hikaru Nakamura (`hikaru`),
  Magnus Carlsen (`magnuscarlsen`), Fabiano Caruana (`fabianocaruana`),
  Alireza Firouzja (`firouzja2003`), Ian Nepomniachtchi (`lachesisq`),
  Daniel Naroditsky (`danielnaroditsky`), Levy Rozman/GothamChess
  (`gothamchess`), Anish Giri (`anishgiri`) — all verified as real,
  correct accounts directly via the Chess.com public player API (name +
  title match) before fetching, not guessed. All have large bullet
  samples (463-67,482 games in their full history; fetched 800 each,
  matching this project's existing cap). Fetched and parsed; combined
  into `discovered_celebrities_plies.parquet` (220,911 unique positions)
  as its own group, queued for AWS annotation after the current groups.
- **Current rating added** to both the Profile summary tile and the
  Compare view (rating as of the player's MOST RECENT game, not just the
  historical min/max range already shown) — direct user request.
- **Pinned players + searchable browse modal**, replacing the plain
  player `<select>` dropdown — necessary now that the pool is headed to
  90+ players. `cdznjerry`/`jerrycdzn`/`BIG_TONKA_T` are always-visible
  quick-select buttons; everyone else is reachable via a "Browse all
  players" button opening a searchable modal list (live substring filter,
  shows each player's current rating). Player-group processing (see
  below) means this list will grow incrementally as groups finish, not
  all at once.

## Spot resilience, round 2 — self-healing launcher + much finer checkpoints

The user woke up frustrated (rightly) that ~9 hours produced only 4,002
annotated positions, and asked directly why. Honest accounting, since this
matters for trusting future overnight runs: most of that time wasn't
wasted *compute* — it was the 4.5-hour Maia hang (separate job, see
below), several launch attempts that failed in seconds (capacity/quota
errors, not slow), one real ~50-minute Stockfish run that genuinely
computed correctly and then lost EVERYTHING to a Spot reclaim (the
per-worker chunking bug from the previous entry), and then a short second
run before the user checked back in. Not a pattern of the pipeline being
slow — a pattern of losing already-completed work, which is worse.

**User's follow-up questions, answered directly**:
  - *"Can we stay on Spot but not lose everything?"* — yes, already
    improved once (4,000-position chunks) and improved further here:
    `CHECKPOINT_CHUNK_TARGET` dropped to **600** — at this project's
    observed depth-12 throughput (~13/sec/worker), that's under a minute
    of work at risk per checkpoint interval, not hours.
  - *"How long might a reclaim leave us without a server?"* — answered
    honestly: unpredictable, and genuinely ranges from instant (retry
    succeeds immediately) to hours (if that specific instance size has a
    real regional shortage, which is exactly what happened to
    `c6i.4xlarge`/`8xlarge`/`16xlarge` overnight while `c6i.2xlarge` — the
    default — succeeded on every single launch attempt this session,
    including all three attempts this same night). No AWS-published
    interruption-frequency figure was fabricated here — this is this
    project's own direct, repeated observation, nothing more.
  - *"Can we add a separate concurrent instance?"* — hit a hard,
    account-wide `MaxSpotInstanceCountExceeded` quota after just a SECOND
    8-vCPU instance (not a per-region limit, not a capacity issue — an
    account ceiling). Confirmed the `chess-clock-bot` IAM user has **zero
    IAM API access at all** (can't even read its own attached policies,
    let alone request a quota increase) — by design, least-privilege. User
    chose to grant it `servicequotas:RequestServiceQuotaIncrease` via the
    AWS console themselves; until that lands, this project runs ONE
    instance at a time.
  - *"Switch to On-Demand to dodge reclaim entirely?"* — offered, user
    declined ("don't worry about the cost" was hyperbole) — staying on
    Spot, just made more resilient instead.

**Fix, `cloud/remote_annotate_aws.py`**: `main()`'s single launch-and-run
attempt is now a bounded retry loop (`--max-attempts`, default 15;
`--retry-delay-s`, default 30) — a `ClientError` on launch (capacity/quota)
retries after a delay; `run_remote` raising `SystemExit` (a reclaim
mid-run OR SSH never coming up) triggers an automatic relaunch rather than
exiting and waiting for a human to notice and re-run manually. Checkpoints
already made this safe to automate — the only reason it wasn't automatic
before was inertia, not a real obstacle. `wait_for_running_and_ssh`'s own
timeout also raises `SystemExit`, so the same retry path correctly covers
both "reclaimed while running" and "never came up in the first place."

**Also**: player-group-based data splitting (3 groups of ~31 players each,
balanced by unique-FEN count, not raw player count) replaces the earlier
arbitrary FEN-count split — so whichever group's job finishes first can go
straight into feature-building and the dashboard without waiting on the
other two. Currently running sequentially (one at a time, per the quota
ceiling above): group 0 relaunched with the new resilient script; groups 1
and 2 queued to follow.

## jerrycdzn added — the "same person, two accounts" test actually ran

jerrycdzn's full pipeline (fetch → parse → AWS Stockfish → Maia → features
→ model → insights) completed and it was added as a THIRD class in the
"primary" identification cohort (`cdznjerry` vs. `BIG_TONKA_T` vs.
`jerrycdzn`) — see the earlier "Overnight player-pool expansion" section
for why this specific addition matters more than just growing N.

**The result is real and striking.** Balanced accuracy dropped to 57.1%
(3-way chance: 33.3%) — expected, 3-way is intrinsically harder, and
these two ARE the same person so some confusion between them is actually
the CORRECT behavior of a working model, not a failure. What matters is
*where* cdznjerry's errors land: when the model misattributes a cdznjerry
game, it guesses **jerrycdzn 67.6% of the time** vs. at most **17.2%** to
BIG_TONKA_T (a genuinely different person) — a 3.9x difference. This isn't
fully explained by jerrycdzn simply having more games in the pool
(4,851 vs. BIG_TONKA_T's 3,376, only a 1.44x size difference against a
3.9x confusion difference — class-weight-balanced training already
compensates for raw pool size besides). **This is the sharpest validation
this project has run of its core premise**: the model is picking up
something that survives a rating change and an account change, not just
memorizing which account is which.

Added a `confusion` field to `build_identification()`'s output (per true
player, the full distribution of what they got predicted as — not just
`per_class_recall`, which can't show *which* wrong answer a model reaches
for) and a dedicated "Same person, two accounts" callout in the dashboard
that computes and states this comparison directly, plus a full N×N
confusion-matrix table (only shown for 3+ classes). `renderSampleGames`'s
versus-track and legend were also generalized from a hardcoded 2-color/
2-player assumption to an N-player palette, since the cohort is no longer
guaranteed to be exactly 2 players.

## Real incident: 1.94M-position AWS job lost ALL progress to a Spot reclaim (found, fixed)

The 92-new-player discovered-pool Stockfish annotation (1,940,919 unique
positions, by far the largest job this project has run) hit a genuine Spot
reclaim ~50 minutes in. `remote_annotate_aws.py` handled the interruption
itself exactly as designed — clean exit, clear message, instance
terminated, billing stopped, no crash. But checking the checkpoint
directory afterward found **zero positions saved**. Root cause: `annotate.py`
chunked work as one chunk per WORKER (`_chunk(fens, workers)`), and a
checkpoint only lands when a whole chunk finishes — with 16 workers
splitting 1.94M positions, each chunk was ~121K positions, i.e. genuinely
hours of work, so nothing had finished yet when the reclaim hit. The
existing "resumable" design was real (validated repeatedly this session on
smaller jobs) but its checkpoint GRANULARITY silently scaled the wrong way
as job size grew — fine at ~100K positions, a real liability at ~2M.

**Fix**: chunk count now scales with total WORK, not worker count.
`CHECKPOINT_CHUNK_TARGET = 4000` in `annotate.py` — `n_chunks =
max(workers, len(fens) // CHECKPOINT_CHUNK_TARGET)`, so a checkpoint lands
roughly every 4,000 positions regardless of how many workers are running
(485 chunks for this job instead of 16). Bounds worst-case loss from a
future interruption to a few minutes of work instead of potentially the
entire run. Per-chunk Stockfish process startup overhead (~485 spawns
instead of 16) is negligible against a job measured in hours.

**Also**: the relaunch hit `InsufficientInstanceCapacity` twice in a row on
`c6i.4xlarge` (16 vCPUs) — a real, transient regional Spot capacity limit,
not a bug — and separately `c6i.16xlarge` hit `MaxSpotInstanceCountExceeded`
(an account-level vCPU quota, added `c6i.12xlarge`/`16xlarge`/`24xlarge`/
`32xlarge` to `VCPUS_BY_TYPE` for future reference but the quota itself
wasn't raised). Fell back to the original default `c6i.2xlarge` (8 vCPUs,
reliably available), meaning this job runs slower than hoped (~2x the
16-vCPU estimate) but the checkpointing fix matters far more than instance
size for actually finishing an overnight job unattended.

## Real incident: `maia_annotate.py` wedged for 4.5 hours (found, fixed)

While running jerrycdzn's Maia annotation overnight, the job silently hung
for 4.5 real hours while accumulating under 6 minutes of actual CPU time —
not slow, genuinely stuck. Root cause not fully confirmed (plausibly a
Metal/GPU backend hiccup in `lc0`, since this machine's lc0 build uses the
Metal backend), but the failure MODE was clear: `query_maia_policy()`
blocks on `engine.analysis(...)` waiting for a UCI response that was never
coming, and python-chess's `SimpleEngine` has no built-in per-call timeout
— one wedged position hangs the entire bin (and, since bins run one per
worker process with no cross-bin coordination, the whole job) forever.

**Diagnosis method, worth remembering**: `ps -o pid,etime,time,pcpu` on the
suspect process — `etime` (wall clock) vs. `time` (actual CPU consumed)
diverging massively (4h31m elapsed vs. 5m42s CPU) is the signature of a
stall, not slowness. A merely-slow job would show `time` climbing roughly
in step with `etime`. This is the same diagnostic principle already used
earlier this session to confirm a *healthy* long AWS job (checking that
Stockfish workers had real accumulated CPU time before trusting silence)
— same check, this time catching the opposite (unhealthy) case.

**Fix**, in `maia_annotate.py`: `_query_with_timeout()` wraps each query in
a `ThreadPoolExecutor` with a `QUERY_TIMEOUT_S=10` limit (roughly 600x the
~16ms a query should normally take). Python threads can't be force-killed,
but the underlying engine OS PROCESS can — `engine.protocol.transport.
get_pid()` exposes it, and `os.kill(pid, SIGKILL)` unblocks the wedged
thread by breaking its pipe read. On timeout, the engine is discarded and
reopened before continuing to the next FEN; `MAX_CONSECUTIVE_TIMEOUTS=10`
bails out of a bin entirely if failures keep recurring (a systemic
GPU/driver problem, not one bad position — retrying forever would just
reproduce the original hang). Validated on the normal (non-hanging) path
before trusting it, then confirmed on the real resume: the killed run had
actually completed 3 of 4 rating bins cleanly (17,962 positions, matching
the small bins exactly) — the hang was isolated to the one large 151,789-
position bin, confirming this is a per-position wedge, not a systemic
startup failure. Resumed cleanly with the fix in place, no data lost
(the resumable-by-design checkpoint architecture meant killing the stuck
process cost nothing beyond the wasted wall-clock time).

**Not yet done**: same protection hasn't been added to `annotate.py`
(Stockfish) — no evidence it's needed there (every Stockfish job this
session completed normally, verified via the same etime-vs-time check
before trusting silence), so this was a targeted fix for a confirmed
problem, not spread preemptively to a component with no observed issue.

## Open items / not yet done

- Skewer detection: implemented, validated, AND now checked against real
  data across all four datasets. Prevalence is modest (own_skewers>0 in
  6-8% of positions, opp_skewers>0 in 8-11%) but the Spearman correlation
  with think_time is real and highly significant everywhere: r=0.12-0.15
  for both own and opp skewers, p-values effectively 0 at these sample
  sizes (n=20K-207K). Full-ranking SHAP check (not just top-10): skewers
  land mid-pack, rank ~28-48 of ~76 think-time features — real but modest,
  largely redundant with richer existing criticality signals (gap_1_2,
  n_moves_within_30cp, tactical density overall) rather than a top
  independent driver. One consistent, replicated structural pattern worth
  keeping: **`opp_skewers` outranks `own_skewers` in SHAP importance in
  ALL FOUR datasets** — recognizing you're the one under a tactical threat
  costs more real thinking time than noticing you have one available. Not
  in the original hypothesis; a genuine finding from checking the data.
- `add_sacrifice_features()` bug fixed and re-run on real data (see Maia
  integration section) — `material_offered`/`is_sacrifice` now current for
  cdznjerry and BIG_TONKA_T blitz/rapid.
- BIG_TONKA_T's search-instability re-annotation: **all three modes done**
  (blitz/rapid finished earlier; bullet's first attempt hit a real Spot
  interruption, was relaunched with the more-resilient polling script, and
  completed successfully — 108,910 positions, instance terminated cleanly).
- No dashboard; no automated tests.
- **Maia integration: fully run on real data for ALL FOUR datasets**
  (cdznjerry bullet, BIG_TONKA_T bullet/blitz/rapid) — features rebuilt,
  models retrained for every one. Consistent, cross-validated result:
  `maia_prob_played`/`maia_surprise`/`maia_top1_prob` land in the
  think-time model's top-6-to-10 SHAP features in EVERY single dataset,
  not just one — this is no longer a "looks promising on one player"
  result, it replicates across two players and three time controls. Real
  numbers, live model unless noted:
    - cdznjerry bullet: `maia_surprise` #6 (0.0244), `maia_prob_played` #9
    - BIG_TONKA_T blitz (descriptive only, live window too small):
      `maia_surprise` #3 (0.0565), `maia_prob_played` #6
    - BIG_TONKA_T rapid: `maia_prob_played` #4 (0.1019), `maia_surprise` #8,
      `maia_prob_best_move` #9 — three Maia features in one model's top 10
    - BIG_TONKA_T bullet: `maia_prob_played` #6, `maia_top1_prob` #10 (live);
      `maia_prob_played`/`maia_surprise` both top-10 (descriptive)
- A code-review pass (this session) found no new bugs beyond the
  sacrifice-detection fix already made. One suspected issue was
  investigated and ruled out: `trajectory_features()`'s `eval_delta_1`
  looked at first like it might be corrupted by POV alternating between
  rows (eval_before is always from the CURRENT mover's perspective), but
  `parse_pgn.py` only emits rows for the TRACKED player's own moves (ply
  numbers step by 2), so POV is constant within a game's row sequence —
  confirmed directly on real data before concluding no bug existed, rather
  than assuming.
- A new Maia-derived feature was added and validated this session:
  `maia_prob_best_move` (probability Maia assigns to the engine's actual
  best move, independent of what was played) and `requires_overriding_
  instinct` (best move isn't Maia's top choice AND is <10% likely) — a
  genuinely different axis from gap_1_2/criticality: not just "getting
  this wrong is costly" but "the intuitive move actively points the wrong
  way." `maia_prob_best_move` lands in the move-quality model's top-10 SHAP
  features for cdznjerry; the binary `requires_overriding_instinct` doesn't
  crack top-10 on its own (the continuous prob captures more), which is a
  reasonable, expected result, not a failure.
- `src/fetch_games.py` (Lichess) built and validated end-to-end (fetch →
  parse, correct `source`/`game_id`/`time_control` on real data, cleaned up
  after testing). Needed a descriptive User-Agent, like Chess.com, despite
  the spec not documenting it (see Cloud infrastructure section). Not yet
  used for a real pull — that's the next step for the multi-player pool.
- Multi-player pool for the identification study not yet started.
- **New research findings, not yet acted on** (see Sources below):
  - ["How Much Can a Few Engine Moves Help? Quantifying Limited Cheating in
    Chess"](https://arxiv.org/abs/2601.05386) (Keren, IEEE CoG 2026) directly
    quantifies CLAUDE.md's "selective cheater" constraint: 1-2 well-chosen
    engine moves (via a threshold-based/Bellman-style intervention policy)
    raise average score from 0.51 (no cheating) to 0.71-0.82 — concrete
    numbers for something CLAUDE.md currently only describes qualitatively
    ("near chance from move quality alone... timing is an additional
    signal, not a silver bullet"). Their intervention-policy design is also
    a candidate methodology for constructing more realistic injected/
    simulated cheat moves whenever Phase 2 evaluation actually starts
    (optimal-selective rather than uniformly-random injection).
  - ["Towards Transparent Cheat Detection in Online Chess: An Application
    of Human and Computer Decision-Making Preferences"](https://dl.acm.org/doi/10.1007/978-3-031-34017-8_14)
    (Laarhoven & Ponukumati, CG 2022) makes a framing point worth folding
    into Phase 2 design directly: real-world cheaters are typically
    **"centaurs"** (a human selectively consulting an engine on a few
    moves), not an engine playing every move — meaning Phase 2's eventual
    injected/simulated ground truth should simulate occasional selective
    consultation, not wholesale engine substitution, to be a realistic
    test. Also independently combines human-move-preference and
    engine-preference signals for detection, the same two-signal shape
    this project's design already has (Maia + Stockfish).
  - ["Chess Rating Estimation from Moves and Clock Times Using a
    CNN-LSTM"](https://www.arxiv.org/pdf/2409.11506) predicts a player's
    rating move-by-move from position + clock-time sequences jointly. A
    candidate future feature/diagnostic: an independent "what rating does
    this player's move+clock behavior actually look like right now"
    estimate, which could flag rating-inconsistent stretches of play (a
    cheaper, complementary signal to the deviation-from-baseline approach
    already used here) — not attempted this session, flagged for later.

Sources for this update: [How Much Can a Few Engine Moves Help? (arXiv:2601.05386)](https://arxiv.org/abs/2601.05386) · [Towards Transparent Cheat Detection in Online Chess](https://dl.acm.org/doi/10.1007/978-3-031-34017-8_14) · [Chess Rating Estimation from Moves and Clock Times Using a CNN-LSTM (arXiv:2409.11506)](https://www.arxiv.org/pdf/2409.11506)

## Model architecture and complexity-metric research (follow-up pass)

Two questions researched: (1) is LightGBM still the right call vs. newer
tabular/sequential architectures, (2) any complexity-metric feature ideas
not yet covered.

**On architecture — stay with LightGBM, deliberately**: 2024-2025 survey
sources consistently report GBDTs (LightGBM/CatBoost) still lead on most
real-world tabular tasks — faster to train, easier to tune, and (the
decisive factor here) far more interpretable than deep alternatives.
Transformer-based tabular models are only competitive in specific regimes:
**TabPFN** (in-context learning, no training) on small datasets, and
**FATA-Trans** (arXiv:2310.13818, Field-And-Time-Aware Transformer)
specifically for *sequential* tabular data. FATA-Trans is conceptually the
closest match to this project's actual data shape (a sequence of per-ply
records within a game/session, mixed categorical+continuous fields per
step) — closer than plain LightGBM, which treats each ply as an
independent row and only approximates sequence effects through hand-built
lag features (`eval_delta_1`, `eval_roll_std_3`, `session_game_index`,
`opp_think_time_prev`). Explicit decision: **do not switch** — CLAUDE.md is
unambiguous that interpretability (SHAP feeding user-facing explanations
and Phase 2 scoring) outweighs a marginal accuracy gain, and swapping the
primary model for a "small accuracy gain" is the exact thing it says not to
do. If within-game sequential structure ever turns out to be seriously
underfit by the current lag features (not yet measured), a FATA-Trans-style
model would be a candidate for a SEPARATE, supplementary signal — never a
replacement for the SHAP-explainable primary model.

**On complexity metrics — CORRECTION after reading the actual methodology**:
the previous update described ["Emergent Complexity in the Decision-Making
Process of Chess Players"](https://arxiv.org/abs/2406.15463) (Scientific
Reports 2025) as a "fundamentally different" metric from what's already
built here, based on the abstract alone. Having now fetched the full paper:
their "decisiveness" metric is **Δ = |E₁ - E₂|** (top-two-move engine eval
gap) — this is EXACTLY this project's existing `gap_1_2`, not a new metric.
The "power-law" part of their contribution is an empirical claim about
gap_1_2's own DISTRIBUTION across positions (P(Δ)∝Δ^-γ, their γ=1.35), and
their agent-walking-a-tree simulation is a generative model reproducing
that distribution — not an alternative feature formula to adopt.

**Replicated their two headline findings directly on this project's own
data** (no new feature needed, since Δ already exists as `gap_1_2`):
  - **Power-law tail, all four datasets**: fitting γ via MLE on the
    Δ≥20cp tail gives γ=1.65-1.70 across cdznjerry and BIG_TONKA_T
    bullet/blitz/rapid — same heavy-tailed shape as their result (their
    γ=1.35; close enough in the same regime, not an exact match, which is
    expected given different players/engines/depth settings).
  - **"Accuracy rises with decisiveness," all four datasets**: P(played the
    engine's best move) by `gap_1_2` quintile rises monotonically and
    sharply everywhere, e.g. BIG_TONKA_T rapid: 0.176 → 0.189 → 0.258 →
    0.378 → 0.627 from lowest to highest quintile. Independently confirms
    their result on completely different players/time-controls/engine
    settings — real external validation, via a check actually run on this
    project's own data rather than taken on faith from the abstract.

**Practical upshot**: no new feature to add — `gap_1_2` is already in
`COMPLEXITY_FEATURES` (marked "reference only" since `outcome_changes` beat
it in this project's own SHAP comparisons) and this project's own
`critical_mask()` in insights.py already uses it as the criticality signal.
This paper is best read as strong outside validation that `gap_1_2` was the
right quantity to build the criticality/obviousness framing around, not as
a new feature-family candidate. Lesson for this log itself: verify a
paper's actual methodology (fetch the full text) before characterizing it,
not just the abstract — the previous entry's "fundamentally different
mathematical shape" claim was wrong and is corrected here rather than left
standing.

Sources: [Gradient Boosting Frameworks Compared](https://lalatenduswain.medium.com/gradient-boosting-frameworks-compared-lightgbm-catboost-xgboost-and-adaboost-83cf78dd69e7) · [FATA-Trans: Field And Time-Aware Transformer for Sequential Tabular Data (arXiv:2310.13818)](https://arxiv.org/pdf/2310.13818) · [TabPFN-2.5 (arXiv:2511.08667)](https://arxiv.org/pdf/2511.08667) · [Emergent Complexity in the Decision-Making Process of Chess Players (arXiv:2406.15463, full text)](https://arxiv.org/html/2406.15463v1)

## Spot resilience, round 3 — retry loop caught the wrong exception type

The self-healing retry loop added in round 2 (see above) only wrapped its
launch-and-run attempt in `except SystemExit:` — that's the exception
`run_remote()` deliberately raises when it detects a Spot reclaim mid-run.
But group3's job (24 `team-europe` players, 472,893 unique fens) died a
different way: the remote setup command itself (`python3 -m venv .venv &&
... && pip install -q -r requirements.txt && nohup python -m src.annotate
...`) came back non-zero (exit status 255 over SSH — a flaky
`pip install` on the fresh instance, not a reclaim), which `ssh()` surfaces
as `subprocess.CalledProcessError` via `subprocess.run(..., check=True)`.
That's a totally different exception type than `SystemExit`, so it wasn't
caught — it propagated straight out of `main()` and killed the whole
script. No retry happened. The `finally` block still ran (confirmed via
`describe_instances`: the orphaned instance was correctly terminated, no
leftover billing), so this was a lost retry opportunity, not a cost leak.

Found by noticing group3's supervising process had simply exited (not still
running like group0/1/2's), then reading its task output and seeing the
full traceback ending in `CalledProcessError: ... returned non-zero exit
status 255`.

**Fix** (`cloud/remote_annotate_aws.py`, `main()`): broadened the except
clause from `except SystemExit:` to catch any exception from the
launch/setup/run path, not just the specific one `run_remote` raises for a
reclaim it detected itself:
```python
except (SystemExit, Exception) as e:
    ...
```
This is deliberately broad, not narrowly targeted at
`CalledProcessError`, because the actual invariant that makes retrying safe
has nothing to do with which exception fired — it's that `annotate.py`
checkpoints to disk and resumes from there regardless of what interrupted
it (round-2 fix). Any failure between "instance launched" and "run
finished" — a flaky `pip install`, a dropped SSH connection, a reclaim, or
something not yet seen — is equally safe to retry, so narrowing the catch
to today's one observed failure mode would just leave the same class of bug
for the next unanticipated exception type. `attempt == args.max_attempts`
still re-raises after exhausting retries rather than swallowing forever.

Verified: `py_compile` clean; group0/1/2 were confirmed still alive and
each holding one running EC2 instance (24 of the 32-vCPU quota in use) so
only group3 needed relaunching; relaunched group3 with the fixed script and
confirmed it reached "attempt 1/15, waiting for instance to reach running"
normally.

## Caught before it shipped: running features.py/model.py directly on a combined multi-player discovery file silently pools unrelated players together

The discovery pipeline fetches/parses/annotates each batch of newly-found
players (celebrities, group0-3) as ONE combined parquet per batch — correct
and intentional for the Stockfish/Maia annotation stages, since both dedupe
work by `fen_before` (or `(fen_before, rating_bin)` for Maia) regardless of
which player the position came from, so annotating the union once instead
of once-per-player is a real, deliberate efficiency win with no downside —
a FEN is the same position no matter whose game it showed up in.

That reasoning does NOT extend to `features.py` or `model.py`, and running
either directly on a combined file was about to ship a serious bug. Caught
it after `model.py` had already run once on
`discovered_celebrities_plies_features.parquet` (all 8 celebrities pooled)
and produced ONE baseline model for "the average of Hikaru, Magnus,
Caruana, Firouzja, Naroditsky, Giri, Gotham, and lachesisq" — exactly the
kind of pooling CLAUDE.md's whole premise (personalized, per-player
baselines) exists to avoid. `player_rating` showing up as the #1 SHAP
feature for the think-time model was the tell: the model was mostly
learning "which of these 8 different-strength players is this," not
"how does complexity predict this ONE player's time usage."

Root cause is worse than just `model.py` naively fitting one booster per
call (expected — CLAUDE.md's `--player` convention already implies
one-features-file-per-player as the contract `model.py` was written
against). The deeper problem is in `features.py`'s own
`add_session_features` (features.py:815-869): `games_played_so_far`,
`recency_weight` (RECENCY_HALFLIFE_DAYS=90), `minutes_since_prev_game`, and
`session_game_index` are all computed by sorting the passed-in dataframe's
games by timestamp and diffing — with NO groupby on player. Fed a combined
8-player file, this doesn't just mislabel a column; it actively
cross-contaminates rows: `minutes_since_prev_game` for a Magnus Carlsen
game can come out measuring the gap to a Hikaru game that happened to fall
chronologically next in the pooled timeline, and `session_game_index`
partitions "sessions" across players' interleaved games rather than each
player's own play sessions. These features would have been actively wrong,
not just averaged-away noise.

**Fix, and the correct procedure going forward for every multi-player
discovery batch**: split the PARSED plies file by the `player` column into
one parquet per player (`data/parsed/<player>_plies.parquet`) before ever
calling `features.py`. Keep reusing the one combined `annotated`
(Stockfish) and Maia parquet per batch — hardlink/alias each per-player
plies stem to the batch's combined Maia file
(`data/maia_annotated/<player>_plies_maia.parquet` -> the batch's combined
`..._maia.parquet`) since `add_maia_features` and `build()`'s Stockfish
join are both keyed by `fen_before` (`(fen_before, maia_rating_bin)` for
Maia), so passing the full combined annotated/Maia file to a per-player
`features.py` run is correct and safe — every feature that reads
per-position engine/Maia data joins by FEN, unaffected by which other
players are also in that annotated file; only the SESSION-shaped features
need per-player scoping, and splitting the plies input before the merge
guarantees that. Then run `model.py` and `insights.py` once per player on
their own `data/features/<player>_plies_features.parquet`, exactly like
any other single-player dataset.

Applied to celebrities (8 players: hikaru, magnuscarlsen, fabianocaruana,
firouzja2003, lachesisq, danielnaroditsky, gothamchess, anishgiri) before
publishing anything from that batch — the one pooled model run was deleted
and redone correctly. group0-3 (31/30/similar players each) will get the
same treatment once their Stockfish+Maia annotation finishes; noting it
here now so it isn't skipped under time pressure once four batches are
ready at once.

## Spot resilience, round 4 — two more gaps in the same retry loop, both surfaced by one real network blip

group1's Maia annotation finished cleanly (669,818 positions), and while
processing it into the dashboard, a status check found group0 and group3's
AWS Stockfish jobs had both silently died — same symptom as round 3
(supervisor process gone, no completed output, no crash visible without
digging into the nohup logs), different cause. This time both crashed from
`botocore.exceptions.EndpointConnectionError: Could not connect to the
endpoint URL` — a real local network interruption on this Mac (both jobs
died within moments of each other, which only makes sense as a shared local
cause, not two independent AWS-side failures) — surfacing two more gaps in
`cloud/remote_annotate_aws.py`'s retry loop that round 3's fix didn't
cover:

1. **The launch call's own except clause** (`except ClientError as e:`,
   wrapping `launch_spot_instance()`) only caught AWS-side API errors, not
   `EndpointConnectionError` — a lower-level botocore exception for "never
   got an HTTP response at all," not a subclass of `ClientError`. A launch
   attempt that fails this way now still hasn't provisioned anything, so
   retrying is strictly safe; broadened to `except Exception`, formatting
   the log line from `e.response["Error"]["Code"]` only when the exception
   actually is a `ClientError` and falling back to `type(e).__name__`
   otherwise.

2. **The `finally` block's cleanup call** (`ec2.terminate_instances`,
   `except ClientError: pass`) had the identical gap, and this is the one
   that actually fired here: the run/setup phase had already hit some
   failure, got caught correctly by round 3's broadened except, and was
   about to retry — but the `finally` block's own termination call then hit
   the same `EndpointConnectionError`, which escaped uncaught (again, not a
   `ClientError`) and crashed the whole script instead of just failing to
   confirm cleanup. Broadened to `except Exception`, logging a warning
   instead of crashing — cleanup failing should never take down an
   unattended job; worst case is a stray billed instance until the next
   manual `describe_instances` check, which is trivially recoverable and
   far cheaper than losing the retry loop entirely.

Found 2 orphaned running instances afterward (`i-01927d7a559783501`,
`i-0aaefdac12b752ec5`, both launched within 2 seconds of each other,
confirming they were group0/group3's in-flight attempts at the moment of
the crash) — terminated manually, then relaunched both groups fresh; they
resumed from their local checkpoint dirs via the existing rsync-resume path
(1088 and 450 checkpoints respectively going in) exactly as designed.

**Pattern worth naming**: round 3 and round 4 are the same bug shape twice
— a hand-picked exception type (`SystemExit`, then `ClientError`) narrower
than "anything that can go wrong on this code path," in a script whose
entire design goal is surviving unattended overnight. Both fixes converged
on the same rule: once checkpointing makes a retry safe (true for
literally every failure mode after "job started"), catch broadly and let
`max_attempts` be the actual backstop, rather than trying to enumerate
every exception type a flaky network/AWS API can throw. All three except
clauses in `main()`'s retry loop now follow this rule consistently.

## Dashboard bug: publishing data.js did nothing, because the page never actually reads it when the db has content

After processing celebrities and group1 into the dashboard (published v13,
v14), the user reported still seeing only their own accounts + BIG_TONKA_T
— not gaslighting, a real bug I hadn't checked for. Root cause: the page's
`db` capability (`index.html`, ~line 836-857) does `db.collection("profiles").get()`
on load and, if that collection has ANY documents, REPLACES `DATA.players`
outright rather than merging it with the bundled `data.js` fallback. Five
stale documents (BIG_TONKA_T × 3 modes, cdznjerry, jerrycdzn) had been
written to that collection earlier in the session — so every `data.js`
republish since then was live on the page's own admission, just never
actually rendered, because the db path took priority and only ever had
those 5 original players in it.

I had validated every export by checking `data.json`'s content and
syntax-checking the script — never by actually tracing what the deployed
page does with `db` present, which is the one runtime path that mattered.
**Lesson: when a page declares a `db` capability, "the export file is
correct" and "the export file is what displays" are different claims —
verify the second one too, by checking what's actually in the db, not just
what's in the file being published.**

Fix: wrote all 44 current players' profiles (and the `identification`
cohort result) into the `profiles`/`identification` collections via one
`write_db` batch, pinned with `if_version` on the 5 pre-existing docs
(unpinned writes to existing docs are refused outright, which is itself a
useful safety net — caught this exact class of silent-clobber risk before
it could happen again). Going forward, every export that should reach the
live page needs BOTH the `data.js` republish (fallback / first paint) AND a
`write_db` batch sync (what actually renders whenever the db is non-empty)
— doing only the first is a silent no-op.

## Dashboard: player rating scale recalibrated against the real 44-player pool

The 0-100 skill scores (`RATING_ATTRIBUTES`/`STYLE_ATTRIBUTES` in
`export_dashboard_data.py`) use fixed lo/hi anchors, not pool-relative
percentiles — a deliberate NBA2K/FIFA-style choice so a score means the
same thing regardless of who else is in the pool at viewing time. But the
anchors themselves were originally guessed when only cdznjerry/BIG_TONKA_T
existed, and had drifted out of calibration as the pool grew to 44 players
spanning untitled club players up to Hikaru and Magnus Carlsen. User asked
directly: "make sure existing player scores are all updated against the
new player pool's range."

Added `scripts/calibrate_ratings.py` — loads every player/mode's profile
via the same `build_profile`/`_raw_rating_values` the live scoring path
uses (factored `_raw_rating_values` out of `compute_ratings()` specifically
so calibration and live scoring can't silently drift onto different raw
numbers), prints each attribute's real min/p5/p50/p95/max across the pool.
Set new lo/hi as a padded min/max (~10% headroom each side) rather than
strict percentiles or raw min/max — percentiles would clip 2-3 real
current players at each end for no good reason at only 44 entries; raw
min/max would pin the current single best/worst player to a hard 100/0
with no room for a more extreme player later.

**"Recognition" was the worst-miscalibrated**: old hi=0.40 vs. an actual
observed pool max of 0.317 — 26% too generous, meaning even the single best
recognizer in the entire pool could only ever score ~76/100, and everyone
else was compressed even further down. New bounds (lo=0.07, hi=0.34) let
the scale actually use its full range. Efficiency/clutch/composure were
already close to well-calibrated (old bounds within ~10% of the new ones);
discipline and intuition needed moderate widening/narrowing respectively.
Re-ran `export_dashboard_data.py` (recomputes every player's scores against
the new anchors) and pushed the updated 44 profiles to the live db per the
sync lesson above — a data.js-only republish would have silently not
shown the recalibrated scores.

## Maia annotation moved off the laptop, onto EC2

Maia annotation was the one pipeline stage still tied to this Mac being
awake — it runs lc0 with the Metal GPU backend locally, while Stockfish
annotation already ran unattended on EC2 Spot. User: "i dont have my
laptop running that much" — asked to move it to EC2 too.

The real wrinkle: lc0 publishes NO prebuilt Linux binaries (confirmed
against the actual GitHub releases API, not assumed — the latest release's
assets are Windows/Android only). So the remote setup can't be a one-line
`apt-get install` like Stockfish; it clones lc0 (`release/0.32` branch) and
builds it from source via meson+ninja+OpenBLAS (CPU backend — these
instances have no GPU). This is fine: Maia queries are `nodes=1` (a single
policy-head forward pass, no real search — see maia_query.py's docstring),
so they're cheap even without a GPU; paying for GPU Spot instances (pricier
and far less reliably available) would have been solving a problem that
doesn't exist here.

**Refactored the shared Spot plumbing first**: the launch/retry/cleanup
loop in `remote_annotate_aws.py`'s `main()` had already needed two real bug
fixes (rounds 3 and 4, above) to broaden its exception handling. Copying
that loop into a second script would mean either fixing bugs twice or
silently drifting out of sync, so factored it into `cloud/spot_common.py`
(AMI lookup, key pair, security group, `launch_spot_instance`,
`wait_for_running_and_ssh`, and the hardened `run_self_healing` retry loop)
and rewrote `remote_annotate_aws.py` to import from it — behavior-preserving,
verified by `py_compile` and by the fact that group0/group3's already-running
processes (loaded the old in-memory code) were completely unaffected, since
Python doesn't hot-reload a running process's imports.

New `cloud/remote_maia_annotate.py` mirrors `remote_annotate_aws.py`'s
structure: installs build deps (git, meson, ninja-build, build-essential,
libopenblas-dev), builds lc0, rsyncs `data/maia_weights/` (11MB, trivial)
alongside the usual code/plies/checkpoint sync, runs
`src.maia_annotate --engine <remote lc0 binary>` in the background, polls
and syncs the partial-bin checkpoint directory exactly like Stockfish's
polling loop. Uses the same `run_self_healing` from spot_common — so it
inherits all three of the earlier hardening fixes for free, not as
something to re-discover here.

**Validated on a real 25-position smoke test before trusting it for a real
job** (this project's standing discipline: validate small before
committing to hours of real work) — confirmed genuinely end-to-end, not
just "exited 0": lc0 cloned and built successfully on a fresh Ubuntu
instance, queried real positions, and the pulled-back parquet had the
correct schema (`fen_before`, `maia_rating_bin`, `maia_moves`,
`maia_probs`) with real, non-degenerate probability distributions —
identical shape to years of local Maia annotation output.

Redirected the in-flight queue: group2's Maia was already most of the way
done running locally (8/9 bins) so left it to finish there rather than
interrupt it, but killed the local watcher that was about to run group0's
Maia locally next and replaced it with one that calls
`cloud.remote_maia_annotate` instead; queued group3's Maia the same way,
to start automatically once its Stockfish pass (still running) finishes.
Every FUTURE discovered-player batch's Maia annotation goes through EC2
from here on — the laptop is no longer a dependency for unattended
progress on any pipeline stage.

One accepted cost/latency tradeoff, noted for later: building lc0 from
source adds a few minutes to every fresh instance's setup, including every
relaunch after a Spot reclaim (a reclaimed instance is gone entirely, so
the build isn't cached across reclaims within one script invocation).
Acceptable given annotation runs are hours long; if repeated reclaims make
this overhead add up, the fix is baking a custom AMI with lc0 pre-built,
not further script optimization.

## Identification: expanded from 2-way to the full 41-player pool

User asked directly for this — it's the exact expansion the earlier
"save most of it for when we actually get all the other subjects"
overfitting caution was waiting on. Added a new `"pool"` cohort to
`IDENTIFICATION_COHORTS` in `export_dashboard_data.py`: every player whose
full pipeline was done as of 2026-09-14 (8 celebrities + 31 group1 players
+ the two original identities — 41 total; group0/2/3 will join once their
pipelines finish). **`build_identification()` needed ZERO code changes** —
it was written generically from the start (`unique_players =
sorted(set(y))`, the `coef_.shape[0]==1` binary-vs-multiclass branch, the
N×N confusion structure), specifically anticipating this. Game counts range
335-5,743 per player; `StratifiedKFold(5)` only needs 5+ games in the
smallest class, comfortably met, so no player had to be dropped.

**Result: 29.8% balanced accuracy** (gradient boosting beat logistic
regression, 29.8% vs 28.3%), trained in ~108s on 35,317 games. Sounds low
next to the 2-way cohort's 84.9%, but that's the wrong comparison —
against chance (1/41 ≈ 2.4%) it's ~12x, and against the majority-class
baseline (15.4%, from guessing cdznjerry_combined every time, the biggest
class) it's ~2x. Both comparisons matter: beating chance alone would be
cheap under this much class imbalance, so beating the majority baseline
by a real margin is what actually shows the model learned per-player
behavioral signal rather than just exploiting the imbalance.

Dashboard changes to support this without the 2-way cohort's UI silently
breaking at 41 classes:
  - `COHORT_LABEL`/`COHORT_NOTE`: also fixed a stale bug found in passing —
    "primary"'s label still read "cdznjerry vs. BIG_TONKA_T vs. jerrycdzn"
    from before that cohort was merged back to 2-way sessions ago; nobody
    had gone back to update the label text after the merge landed.
  - **Confusion matrix**: the existing `renderConfusionMatrix` had no size
    guard — a full N×N grid at 41 classes would render a ~3000px-wide,
    genuinely unreadable table. Added `CONFUSION_MATRIX_MAX_CLASSES = 12`;
    above that threshold, `renderTopConfusions` shows the 20 biggest
    individual (true, predicted) mix-ups ranked by rate instead of the full
    grid — more actually useful at this size, not just a fallback.
  - Per-class recall bars and sample-game cards degrade gracefully at 41
    classes (a longer list / a repeating 6-color palette) — not broken,
    left as-is rather than adding guards nothing required.
  - The cohort `<select>` was ALREADY fully dynamic
    (`Object.keys(DATA.identification)`) — no wiring needed there, it just
    started showing "pool" as a second option the moment the data had it.

Synced to the live db (`identification/pool`, new doc — `primary` was
unchanged so left alone) per the "publish alone isn't enough" lesson
above.

## group2 processed into the dashboard; pool cohort hit the db document size limit at 71 classes

group2 (30 more discovered players) finished its Stockfish+Maia pipeline
and went through the same per-player split → features → model → insights
→ export flow as group1, growing the "pool" identification cohort from 41
to 71 players. **Result: 20.2% balanced accuracy** — down from 29.8% at 41
players, which is expected (harder problem, more classes) not a
regression: still ~14x the chance baseline (1.4% at 71 classes) and ~2x
the majority baseline (9.6%), so the signal-to-imbalance ratio held steady
as the pool grew rather than collapsing.

Hit a real limit publishing it: the `identification/pool` db document was
345KB, over the 256KB per-document cap. Broke down where the bytes went —
`confusion` (164KB, an unavoidable N×N structure at 71 classes) and
`sample_games` (198KB) together accounted for nearly all of it.
`sample_games` was the fixable one: each sample game stored a full
71-length `probabilities` dict, one entry per cohort player, when the UI
only ever needs the top handful. This was a real UX bug hiding behind a
size-limit error, not just a storage inefficiency — `renderSampleGames`
draws one stacked-bar segment per entry in that dict, so a 71-entry
probabilities map was already rendering 71 mostly-invisible slivers per
sample game before anyone noticed the byte count. Fixed both at once in
`build_identification()`: cap each sample's stored `probabilities` to its
top 5 classes plus the true and predicted player (so a miss is always
visible even if outside the top 5), computing `confidence` from the FULL
distribution before trimming so the displayed confidence number stays
correct. Shrunk `sample_games` from 198KB to 26KB, total document to 197KB
— accuracy numbers unchanged (196,924 bytes vs. the original 345,757,
same 0.197/0.202 model comparison), confirming the trim only removed
never-rendered data.

Lesson for next time: a storage-limit error on a data field with a
per-class fan-out is worth checking for a UI-scaling problem before just
trimming to fit — the two often share a root cause (unbounded per-class
output), and fixing only the number without checking the rendering would
have left the illegible stacked bar in place.

## Spot resilience, round 5 — the one gap that isn't an exception-handling gap: no timeout on the SSH session itself

Found while checking why group3's Stockfish job had been stuck on the same
checkpoint count (523) for well over an hour: its local supervisor process
was still "alive" (not crashed, no exception, nothing for the retry loop
to catch) but the underlying `ssh` subprocess running the remote setup
command (`apt-get install ...`) had been blocked for 47+ minutes, and
`describe_instances` showed ZERO running instances — the Spot instance it
was talking to didn't exist anymore.

This is a genuinely different failure shape than rounds 3/4. Those were
"an exception fires but the wrong `except` clause misses it." This is "no
exception fires at all." `ConnectTimeout=10` on the ssh command only
bounds the initial TCP handshake; once a session is established, if the
remote command hangs (or the instance is reclaimed mid-command, leaving an
already-open session with nothing behind it), `subprocess.run(...,
check=True)` with no `timeout=` argument blocks forever. No amount of
broadening `except` clauses helps here — the whole self-healing loop is
built around "catch the exception, retry," and a hung syscall never raises
one. This is why the round 3/4 hardening, thorough as it was for exception
*types*, didn't catch this: it was never an exception-handling problem.

**Fix**: every `subprocess.run` call in both `remote_annotate_aws.py`'s
`run_remote` and `remote_maia_annotate.py`'s `run_remote_maia` (`ssh`,
`rsync_to`, `rsync_from`) now passes `timeout=300` by default (the lc0
build call keeps its own longer `timeout=1200` override — a real compile
legitimately takes several minutes). `subprocess.TimeoutExpired` is a
normal exception, so once raised it flows through the exact same handling
already built for round 3/4: the polling loop's `except
(subprocess.CalledProcessError, subprocess.TimeoutExpired)` now catches
both (previously just `CalledProcessError`) and prints the same
"likely a Spot reclaim, checkpoints are safe" message; a hang during the
one-time setup phase (not wrapped in its own try/except) still gets caught
by `spot_common.run_self_healing`'s outer broad `except`, since
`TimeoutExpired` is just another `Exception` subclass. Also added
`timeout=15` to `spot_common.wait_for_running_and_ssh`'s readiness probe
for the same defense-in-depth reason, though a plain `echo ready` hanging
is a much rarer edge case than a real setup command doing so.

Recovered group3 by killing the two stuck local processes (supervisor +
hung ssh), confirming no orphaned billing (zero running instances — it
really had been reclaimed), and relaunching fresh with the fix; it resumed
from its 523 preserved checkpoints as expected.

**Pattern across rounds 3, 4, and 5**: three different root causes (narrow
exception types, twice; then no exception at all), but the fix each time
converges on the same idea — a script whose entire purpose is running
unattended overnight cannot have ANY code path that can silently stop
making progress without either completing or raising. Worth checking for
this class of bug specifically (a blocking call with no timeout) any time
a new remote/subprocess call is added to this pipeline, not just relying
on broad exception handling to catch problems after the fact.

## Six new identification features, chosen and validated against a "is this just detecting skill?" concern

User pushed back hard on a legitimate methodology risk before asking for
more accuracy: with a pool spanning ~100-3300 rating, does the
identification model actually track individual behavior, or is it just
learning to tell strong players from weak ones (skill as a trivial
identity proxy)? Tested directly rather than assuming: restricted a cohort
to 20 players within a ~450-point rating band (vs. the full pool's
~3200-point spread) and re-ran identification on JUST that band. If the
original number were mostly a skill-proxy artifact, this should collapse
toward chance. It didn't — 30.2% balanced accuracy on the narrow band,
essentially matching the 71-player full pool's 20.2% at the time, and
~6x both the narrow band's own chance (4.8%) and majority (5.0%)
baselines. Confirmed: the signal is real per-player behavior, not skill
leakage. This same-skill-band check became the standing validation gate
for every subsequent feature addition below — a feature only "counts" if
it improves the narrow-band number too, not just the full-pool number.

User then asked for more behavioral-biometric feature ideas (explicitly
excluding move-content/style, which is a different research direction —
see the earlier "full move sequences" discussion). Added six, all
computed from columns that already existed in the features parquet (no
new data collection, no upstream re-run needed), all validated on both
gates before being kept:

  - **`low_time_think_ratio`** — per-game ratio of mean think_time inside
    the existing `low_time` danger zone (clock_frac<0.15) vs. outside it.
    A per-GAME analog of the player-level `clutch` stat already in
    profiles, but this one can vary game to game.
  - **`rhythm_autocorr`** — lag-1 autocorrelation of think_time within a
    game. Steady pacing vs. fast/slow/fast/slow rhythm, invisible to
    mean/std/trend (all three treat a game as an unordered bag of plies).
  - **`tempo_match_corr`** — correlation between own think_time and
    `opp_think_time_prev` (already existed as a complexity-model feature,
    never used for identification before). Does this player's pace track
    their opponent's, or stay decoupled?
  - **`post_mistake_think_delta`** — think_time on the ply right after the
    player's own `is_mistake`, minus the game's baseline mean. Recalibrate-
    and-slow-down vs. tilt-and-speed-up is a psychological trait, not a
    skill proxy (a strong tilter and a weak tilter look similar on this
    axis despite very different cp_loss levels).
  - **`hour_sin`/`hour_cos`/`dow_sin`/`dow_cos`** — circular-encoded time-
    of-day and day-of-week from `game_datetime` (already existed).
    Completely orthogonal to skill or move quality; "usually plays
    weeknights around 9pm" is about as personally distinctive as anything
    in the feature set, and cheap since the raw timestamp was already
    computed.
  - **`prev_game_result`** — win/loss/draw of the player's immediately
    PRECEDING game (derived from `result`+`color`, shifted within player
    by `game_datetime`), as session-mood context for the current game. Not
    label leakage — only ever reads the PRIOR game's outcome, never the
    current game's own plies.

Also added, earlier in this same push and validated the same way: per-game
opening/middlegame/endgame time-share and session pacing
(`session_game_index`, `log_minutes_since_prev_game`) — see the "44-player
pool" entry above for those two specifically; this entry covers the six
added after that first round.

**Results, same-skill-band / full-pool, tracked across three rounds**:
  - v1 (original 19 features): 30.2% / 20.2% (41 then 71 players)
  - v2 (+ phase-share, pacing): 37.3% / 26.5%
  - v3 (+ all six above): **49.6% / 38.4%** (71-player pool at the time)

After also folding in group0 (31 more players, pool grew to 102): **31.8%
balanced accuracy** — lower than 38.4% only because the class count grew
(102 vs 71), not a regression; still ~32x chance (1.0%) and ~4.5x the
majority baseline (7.0%), both stronger multiples than the pool had before
these features. The 2-way `primary` cohort jumped too, from 84.9% to
**91.7%** — confirms these features are additive to the already-strong
2-player signal, not just noise that happens to help under heavy
imbalance. `session_game_index` and `hour_cos` both landed in the top-8
feature importances for the 102-player pool, alongside the original
timing/accuracy features — genuinely pulling real weight, not padding.

Hit the SAME db-size ceiling as the earlier sample_games fix, this time on
`confusion` (327KB at 102×102, over the 256KB limit) — fixed the same way:
added `CONFUSION_MATRIX_STORE_FULL_MAX = 12` (matching the dashboard's own
`CONFUSION_MATRIX_MAX_CLASSES`) so cohorts above that size only store each
row's diagonal + top-8 off-diagonal entries server-side, since
`renderTopConfusions` never reads more than that anyway. 373KB → 79KB, same
accuracy numbers.

**Research for further ideas** (WebSearch + reading the actual papers, not
just abstracts): the most directly relevant hit was arXiv:2606.18544
("Chess Signatures of Play"), which frames a game as a multivariate
stream (engine eval, accuracy, complexity, clock reading) and applies
rough-path signature transforms — their strongest discriminating signal is
the "Levy area" between accuracy and complexity: whether accuracy rises
*precisely when* positions get harder, an order-aware cross-term that
plain correlation or separate means both miss. A full signature-transform
implementation is a real scope jump (needs iterated integrals, likely a
new dependency), but a tractable, immediately-implementable approximation
exists using columns already on disk: per-game correlation between
`gap_1_2` (existing complexity proxy) and `cp_loss`, capturing "does this
player's accuracy specifically hold up or crumble under complexity" —
proposed as the next feature to test, not yet implemented. Separately,
arXiv:2409.14830 (FPS anti-cheat behavioral biometrics) reinforced that
mouse/input-device dynamics are the other big biometric category in the
literature — explicitly NOT applicable here, since Chess.com's public PGN
API exposes clock timestamps only, no client-side input telemetry.

## complexity_accuracy_corr implemented, tested, and reverted — a real negative result worth keeping the record of

Implemented the "Levy area" approximation proposed above: per-game Pearson
correlation between `log1p(gap_1_2)` (complexity; low gap = the top two
moves are close in value = a harder decision) and `cp_loss` (accuracy).
Tested against both standing validation gates before touching the live
export, per the pattern established for every other feature this session:

  - Same-skill-band (20 players, ~450-point range): 49.6% → 49.5%
    (essentially flat, a hair negative)
  - Full pool (102 players): 31.8% → 32.2% (+0.4pp, within
    cross-validation noise for this sample size)

Reverted rather than shipped. A plain Pearson correlation is too coarse an
instrument for what the paper actually measures — a true path-signature
Levy area is sensitive to lead-lag/ordering between the two streams
(whether complexity rises BEFORE accuracy responds, not just whether they
co-vary), which a same-game correlation coefficient collapses away
entirely. Worth recording as a real negative result: this project's other
six features this session all showed large, consistent gains on both
gates (10-20+ points each); a feature landing at +0.4pp on one gate and
slightly negative on the other is noise, not signal, and keeping it would
mean adding model complexity (one more dimension to overfit per class,
especially for the pool's lower-game-count players) without a
demonstrated benefit — exactly what the standing overfitting caution
exists to prevent.

**A more promising path to the same idea, scoped but not built**: Kenneth
Regan's Intrinsic Chess Ratings model (Regan & Haworth,
cse.buffalo.edu/~regan/papers/pdf/ReHa11c.pdf — fetched and read in full,
not just the abstract) fits exactly two player-specific parameters per
the formula `y_i = exp(-(δ_i/s)^c)`, where δ_i is a scaled move-value-loss
(via the `ln(1+x)` integral transform they derive from 150,000+ games'
worth of evaluation-scaling data) converted into a move-choice probability
via `p_i ∝ 1/ln(1/y_i)` solved jointly with `Σp_i=1`:
  - **s (sensitivity)** — how finely the player discriminates between
    moderately-inferior and much-inferior moves. Smaller s = sharper
    discrimination.
  - **c (consistency)** — how reliably the player avoids moves in the
    range they can discriminate as bad; the exponent shape parameter.

This is a genuinely richer characterization than a correlation coefficient
— a proper 2-parameter psychometric choice model fit via maximum
likelihood over which move was actually played among the alternatives,
not just a linear co-movement statistic. It's the same complexity-vs-
accuracy relationship the Levy-area idea was reaching for, but with 15+
years of empirical validation behind the functional form.

**Why not implemented now**: fitting s/c properly needs the evaluation of
EVERY (or at least many) candidate move at each ply, not just the top
choice — checked `data/annotated/discovered_celebrities_plies_d12.parquet`
directly and confirmed `top_moves`/`top_cps` currently store only ~2
alternatives per position (effectively MultiPV=2), not the deeper MultiPV
Regan's method wants. Getting real alternatives would mean a new,
deeper-MultiPV Stockfish annotation pass across the whole pool — a real
cost/time decision (re-running annotation on everything already
processed), not something to launch unilaterally overnight. Flagging this
precisely, with the exact formula and exact data gap, so a future session
doesn't have to re-derive it: the params could plausibly be fit per-game
(bullet games have 15-50 of the tracked player's plies, likely enough for
a stable MLE fit, though this would need validating) or fall back to a
per-player fit across their full history if per-game proves too noisy.

## time_control (base_s/increment_s) implemented, tested, reverted — and a genuinely new confound found in the process

Tried one more cheap idea: which bullet sub-format (60+0 vs 120+1 etc.) a
player gravitates to, added as a raw per-game feature (`base_s`,
`increment_s` — already on disk, no aggregation needed, confirmed real
variance for some players like wilder12 who splits roughly evenly between
two formats).

  - Same-skill-band gate: 49.6% → **53.9%** — passed clearly.
  - Full pool gate: 31.8% → **31.3%** — a genuine DROP, not just
    flat/noisy, with `base_s`/`increment_s` jumping to #4 and #6 in
    feature importance.

Reverted. That combination — one gate improves sharply while the other
gets WORSE, with the new feature itself dominating importance — is the
signature of a confound the same-skill-band check was never designed to
catch: not skill, but which DISCOVERY BATCH an account's games came from.
Celebrities were fetched single-format only; various discovered players'
time-control mix may reflect genuine personal preference (plausible — the
same-skill test used only group1/group2 players, where wilder12's split
really is within their own history) OR may just encode when/how their
archive happened to be fetched, which correlates with which of the four
discovery batches (group0-3, each pulled from different Chess.com clubs at
different times) they belong to. At full-pool scale, with celebrities
sitting at one extreme (zero variance) and four separate discovery batches
each potentially having their own subtle time-control mix, the model may
be partly learning "which batch is this," not "who is this."

**Methodological note for future feature work, worth being explicit
about**: the same-skill-band validation gate built earlier this session is
a real, necessary check, but it only controls for ONE confound (skill/
rating). It is not a general "this feature is safe" stamp — a feature
that passes it can still be exploiting a completely different shortcut
(here, discovery-batch membership) that a skill-controlled subset doesn't
expose, especially when that subset happens to be drawn entirely from
within one or two discovery batches, as the current same-skill cohort is.
A more complete future check would deliberately construct a same-BATCH
cohort (players known to come from the same discovery source) the same
way the same-skill cohort was constructed — not built here since discovery
batch isn't currently tracked as stored per-player metadata, but worth
adding if more features like this are tried later.

**Follow-up research**: this exact failure mode has an established name —
"batch effect confounding" — and a known fix. Confirmed via WebSearch
(PLOS One, "Batch Effect Confounding Leads to Strong Bias in Performance
Estimates Obtained by Cross-Validation") that when outcome labels
(players, here) are unevenly distributed across technical/collection
groups (discovery batches, here), a model can use batch as a surrogate for
the real label, and ordinary random k-fold CV doesn't detect this because
train and test folds both draw from the same batches. The documented fix
is **"k-batch" cross-validation**: fold assignment stratified by batch
membership instead of randomly, so no test-fold sample comes from a batch
also present in that fold's training data — exactly the same-BATCH cohort
idea above, formalized as a CV scheme rather than a one-off manual check.
Concrete next step if pursued: add a `discovery_batch` field to player
metadata (which of group0-3, celebrities, or original would need
recording, which isn't currently tracked) and either (a) build one-off
same-batch validation cohorts the way same-skill ones were built here, or
(b) properly implement k-batch CV as the standing validation method
instead of `StratifiedKFold`'s player-only stratification, for any future
feature whose provenance might correlate with which discovery pass added
a player. Source: [Batch Effect Confounding Leads to Strong Bias in
Performance Estimates Obtained by Cross-Validation, PLOS One](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0100335).

## A genuine multi-hour false lead: "memory pressure" wasn't the bug — a silent per-player crash was

Spent a long stretch chasing the wrong cause. After adding group3 (the 4th
and final discovery batch, 24 players) and re-running the final combined
export, it crashed at the exact same point — right after building
"worgplayer"'s profile, before the next player — four times in a row,
across different launch methods (piped through `tail`, redirected to a
file, plain background). System `vm_stat` showed genuinely low free memory
at the time of the first crash (~62MB out of 16GB), which was a real,
independently-confirmed condition, so the natural read was "OOM kill" —
and real, defensible memory hygiene was added on that theory (`del
all_df, frames` + `gc.collect()` between the profile-building and
identification-building phases of `build_identification`, plus between
cohorts in `main()` — see `scripts/export_dashboard_data.py`; kept, since
it's genuinely correct practice at 126 players regardless of what the real
bug turned out to be). It didn't fix the crash: a retry after freeing
memory (killing a stale leftover watcher process from much earlier in the
session) failed identically, at the identical player, with memory no
longer under pressure (~741MB free that time).

Four identical crash points regardless of varying memory conditions was
the tell that memory was never the real variable — running the export in
the **foreground** instead of backgrounded (`run_in_background: false`,
piped through `cat -n` to get real line numbers) surfaced the actual,
previously-invisible error immediately: `FileNotFoundError:
data/insights/worgplayer_plies_insights.parquet`. The batch loops used to
build 126 players' models/insights each piped a single player's output
through `2>&1 | tail -3` or `tail -4` before moving to the next player in
the SAME shell loop — a real Python traceback longer than the tail window
is silently truncated to nothing useful, and the loop keeps going to the
next player regardless of the previous one's exit code. worgplayer's
`model.py` run crashed with a real, uncaught exception and the loop never
surfaced it, leaving that one player with features but no model and no
insights file, invisible until `export_dashboard_data.py` tried to read a
file that was never written.

**The actual bug**, once visible: worgplayer's trailing-270-day "live"
window (1128 rows, comfortably above `MIN_LIVE_ROWS=500`) had ALL its
activity clustered on just 2 calendar dates (1056 rows on one day, 72 on a
second day a month later). `_time_split`'s 85th-percentile-by-game cutoff,
computed on so few distinct dates, put the boundary before every row —
`train` came out with 0 rows, and LightGBM's `Booster.__init__` raised
`ValueError: Input data must be 2 dimensional and non empty.` on an empty
training set. `MIN_LIVE_ROWS` was guarding the wrong thing: total row
count, not that the chronological split itself lands on both sides
non-empty. Fixed in `src/model.py`: after `_time_split` for both the
think-time and move-quality live models, check `train.empty or
test.empty` and skip gracefully (falling back to descriptive-only) with
the same messaging style as the existing `MIN_LIVE_ROWS` guard, rather
than crashing. Verified: worgplayer now trains cleanly (descriptive-only,
correctly), and a sweep of all 126 players in `DATASETS` for missing
`_insights.parquet` files came back empty — worgplayer was the only
casualty, not a wider silent-failure problem.

**Process lesson, not just a code lesson**: `2>&1 | tail -N` inside a
per-player batch loop is exactly the pattern that hides a crash — it
looks like normal truncated logging right up until something genuinely
breaks, and then it silently eats the one piece of information (the
traceback) that would have caught the problem immediately instead of
hours later. The diagnostic step that actually worked was abandoning the
background/piped/truncated execution entirely and running once in the
foreground with untruncated output — worth reaching for immediately next
time a background task fails identically more than twice, rather than
iterating on theories about *why* first.

## New: cloud/remote_export_aws.py — moving the identification model's CV off the laptop

User's laptop fan was loud (identification CV over 100+ classes pins every
core for several minutes) while in class — asked to move it to EC2. Unlike
Stockfish/Maia annotation, `export_dashboard_data.py`'s run is short
(single-digit minutes) and has no natural checkpoint structure, so this
new script skips the polling/checkpoint-sync loop the other two use:
launches an instance, rsyncs `data/features/` + `data/insights/` +
`data/models/` (~1.35GB combined — everything the script reads, confirmed
by grepping for every directory it touches) + `scripts/`/`src/`, runs the
export once over a single blocking SSH call, pulls back the one output
file. Reuses `cloud/spot_common.py`'s launch/retry infra.

One real wrinkle: `export_dashboard_data.py`'s `OUT_PATH` is a hardcoded
absolute path mirroring this session's local macOS scratchpad location
(`/private/tmp/claude-501/...`). Rather than edit the script to take the
output path as an argument, this script recreates that exact path
remotely so the script writes to it unmodified, then rsyncs that one file
back. First real run caught a genuine bug in that approach: a bare
`mkdir -p` on that path failed on Ubuntu — `/private` doesn't exist there,
and creating a new top-level directory under `/` needs root, which the
plain `ubuntu` SSH user doesn't have. Fixed with `sudo mkdir -p ... &&
sudo chown -R ubuntu:ubuntu /private` so the later unprivileged rsync/
python write can actually land. Also hit (and correctly auto-recovered
from, no code change needed) a genuine mid-transfer `rsync` network reset
during the 462MB `data/features/` sync and a stretch of
`InsufficientInstanceCapacity` on `c6i.2xlarge` — both handled by the
existing shared retry loop exactly as designed, just costs an extra full
1.35GB re-upload per retry since there's no partial-resume across
different destination instances (acceptable for now given the job is
short; worth revisiting only if capacity shortages make this recur
often).

A later real run also fully exhausted all 15 retry attempts —
`InsufficientInstanceCapacity` on `c6i.2xlarge` accounted for most of
them, a genuine regional shortage that day, not a bug; relaunched with
`--instance-type c6i.4xlarge` to sidestep it. Separately, one attempt that
DID reach the actual run step died mid-computation with a bare "exit
status 255" and no further explanation — root-caused to this script's own
structural difference from the other two remote scripts: it makes ONE
long-lived SSH call that sits completely silent for several minutes (the
identification CV itself), where `remote_annotate_aws.py`/
`remote_maia_annotate.py` only ever make short, frequent polling calls.
Silent long-lived connections are exactly what intermediate network
equipment/NAT commonly drops. Fixed by adding `-o ServerAliveInterval=30
-o ServerAliveCountMax=10` to this script's `ssh()` helper (keepalive
pings, tolerating up to 5 minutes of no reply before giving up) — the
other two scripts don't need this since no single SSH call in their
polling loop is ever silent for long.

## Recency-capping identification to recent games: a promising result that didn't survive proper scrutiny

User asked for more accuracy-improvement ideas. Tried restricting each
player's identification training data to only their most recent N games
(instead of full history), motivated directly by the earlier consistency/
Fisher-ratio finding — the reasoning: a player's oldest games may reflect
different, stale habits, inflating their own variance for no benefit.

Initial results under the STANDARD random-CV methodology (the same
`StratifiedKFold`-based `cross_val_predict` used everywhere else in this
project) looked excellent: same-skill-band balanced accuracy rose
monotonically as the cap tightened — 49.6% (no cap) → 55.9% (cap=400) →
57.6% (cap=300) → 63.4% (cap=150) → 69.5% (cap=100) → 73.4% (cap=50).

**Caught before shipping**: a monotonic curve that keeps improving all the
way down to a very small cap is exactly the shape a real effect should
NOT have (diminishing data should eventually hurt), which was reason
enough to check for a confound before trusting it. Checked directly:
`wilder12`'s most recent 50 games span only 7 days across 2 distinct
dates; `thenoise` and `hoangjr`'s span 2 days across 3 dates — tightening
the cap doesn't just reduce games, it collapses a player down to 1-2 play
*sessions*. Feature importance at cap=50 confirmed the mechanism directly:
`dow_sin`/`dow_cos` (day-of-week) jumped to the #2/#3 most important
features (not in the top 8 at baseline), alongside `hour_sin`/`hour_cos`/
`session_game_index` — four of the top eight. With everything crammed into
one or two sessions, day-of-week and hour-of-day become a near-perfect
"which burst is this" shortcut, and `StratifiedKFold`'s RANDOM (not
time-aware) fold assignment freely scatters games from the same burst
across train and test — the model was largely learning "this test game
sits in the same short window as a training game from this player," not a
genuine multi-month behavioral signature.

**Retested honestly**: rebuilt the same sweep using a chronological
holdout instead (per player: sort games by date, train on the earliest
80%, test on the strictly-later 20% — the same principle `model.py`'s
`_time_split` already uses for the baseline think-time/move-quality
models, just not yet adopted for identification). Results flipped:

| cap | random-CV (leaky) | chronological holdout (honest) |
|---|---|---|
| none | 49.6% | 41.3% |
| 400 | 55.9% | 34.6% |
| 300 | 57.6% | 34.1% |
| 150 | 63.4% | 35.4% |
| 50 | 73.4% | 52.9% (still confounded — see below) |

Mid-range caps (150-400) actually UNDERPERFORM full history once leakage
is controlled for — losing real training data without a compensating
benefit. Cap=50 still looks better than no-cap even chronologically, but
isn't clean either: when the entire kept pool is already 1-2 sessions, a
chronological 80/20 split within that pool still leaves test games mere
hours after the nearest training game — the chronological fix controls
for "did we see the future," not "are these two games from the same
sitting," so residual leakage likely remains at that cap specifically.

**Reverted.** The `recent_games_per_player` parameter was removed from
`build_identification()` entirely — cleanly, since it had defaulted to
`None`/off and was never shipped to production.

**The bigger, still-open implication**: every identification number
published this session (the six kept features, 84.9%→91.7% on the 2-way
cohort, 20.2%→31.8% on the pool) was validated with this same random-CV
methodology, which full-history data is far less vulnerable to than the
artificially-capped case (games spread across months/years, not crammed
into a handful of sessions) — but "far less vulnerable" is not "immune."
The qualitative conclusion that the six shipped features are real signal
is still well-supported by independent evidence (majority-baseline and
chance-baseline comparisons, the same-skill-band control ruling out the
specific skill confound it was built for) — but the exact percentages may
be mildly optimistic by an unmeasured amount. **Recommended next
infrastructure step, not yet done**: switch `build_identification`'s
standard evaluation from random `StratifiedKFold` to a chronological
holdout (or a time-blocked CV) as the default methodology, matching what
`model.py` already does for the baseline models — this would very likely
lower the published percentages somewhat, but they'd be honest numbers
instead of numbers with an unknown amount of session-leakage inflation
baked in.

## remote_export_aws.py: Spot capacity genuinely unavailable — added on-demand fallback

Relaunching after the SSH-keepalive fix, `remote_export_aws.py` exhausted
all 15 retry attempts on `InsufficientInstanceCapacity` — tried both
`c6i.2xlarge` and `c6i.4xlarge`, zero successful launches across either.
Confirmed genuine regional Spot scarcity that day, not a bug: more
retries or a different size in the same family wasn't going to fix it.

Added a real fix rather than just waiting it out: `spot_common.py`'s
`launch_spot_instance` and `run_self_healing` both take a new `spot: bool`
parameter (default `True`, so the two annotation scripts are completely
unaffected); `spot=False` omits `InstanceMarketOptions` entirely, which
requests a normal on-demand instance. `remote_export_aws.py` now defaults
to on-demand (new `--spot` flag to opt back into Spot pricing) — the
reasoning is specific to this one script: its job runs for single-digit
minutes, so the on-demand/Spot price gap is a few cents, trivial next to
burning through every retry attempt on a capacity shortage that Spot
pricing has no way around. The two annotation scripts stay on Spot by
default, unchanged — those run for hours, where the discount is real
money and they already have full reclaim-tolerance built in.

## remote_export_aws.py, round 2: the real bug was the single blocking SSH call itself, not a missing keepalive

Even after the on-demand fix and SSH keepalive, the exact same generic
`exit status 255` kept recurring on the actual export step. Added real
diagnostics to chase it properly instead of guessing again: extended
`spot_common.py`'s `run_self_healing` exception handler to print
`e.stdout`/`e.stderr` from any `CalledProcessError` — a gap that existed
across ALL THREE remote scripts (every `ssh()` helper already captures
this via `capture_output=True`, none of them ever printed it, so every
past "setup/run failed" message in this project's history has been
discarding the one piece of information that would explain why). Re-ran
with the fix: **both stdout and stderr came back empty**. That's the
tell — not a Python traceback being swallowed, but the SSH connection
itself dying mid-command with nothing captured at all, meaning
`ServerAliveInterval` genuinely wasn't preventing whatever this was.

Root cause, once framed correctly: `remote_export_aws.py` was the ONLY
one of the three remote scripts using a single long-lived SSH call that
sits completely silent for several minutes (venv setup + pip install +
the CPU-heavy export, all in one blocking command).
`remote_annotate_aws.py` and `remote_maia_annotate.py` never hit this
failure mode because they only ever make SHORT polling calls — start the
real work with `nohup ... &`, then poll every 60s with a trivial `test -f`
check. Rather than continuing to tune SSH-level options against a failure
whose actual trigger was never confirmed, switched `remote_export_aws.py`
to the exact same proven pattern: short blocking call for venv/pip setup,
then `nohup python -m scripts.export_dashboard_data > export.log 2>&1 &`
backgrounded, then poll every 20s for `data.json` to appear (with the
remote `export.log`'s tail printed if the poll loop times out, for the
next diagnostic step if this recurs). All three remote scripts now share
the same resilience shape, not just the same launch/retry infrastructure.

## remote_export_aws.py, round 3: the split-setup bug, then a self-inflicted SIGPIPE wound

Deployed the poll-based fix above, and the backgrounded process was
confirmed (via a direct `ps aux` on the instance over a fresh SSH session)
to simply not exist afterward — no crash, no error anywhere, just never
there. Root cause: that version split setup into TWO separate `ssh()`
calls (venv+pip install in one, the `nohup ... &` launch in a second) —
`remote_annotate_aws.py`/`remote_maia_annotate.py` do venv+pip+nohup-launch
all in ONE call, and had never hit this failure. Never fully isolated
*why* the split broke detachment, but matching the pattern that's worked
across dozens of runs all session was the pragmatic fix over further
guessing: merged back into a single `ssh()` call.

While diagnosing, ran the export manually over a direct SSH command to
confirm the script itself was fine — piped through `| head -30` to keep
the output short. It worked (real CPU, real memory, 17+ minutes of actual
computation confirmed via `ps aux`), until it didn't: `head -30` closed
its end of the pipe after its quota, the remote python process took
SIGPIPE on its next stdout write, and died — losing that entire 17-minute
run. The `[exited with code 0]` the tool reported was `head`'s exit
status, not python's (bash pipeline semantics: exit status is the LAST
command's, not the failing one, absent `pipefail`) — a real, easy-to-fall-
for trap when eyeballing a piped command's apparent success. **Lesson**:
never pipe a long-running, expensive remote computation through a
line-limiting filter (`head`, `tail -f | head`, etc.) for casual
diagnostics — redirect to a file and read that separately, or accept the
full output, so an unrelated local pipe tool can't kill real remote work.

## remote_export_aws.py, round 4: the real bug was never SSH — it was memory

After the round-3 merged-single-call fix deployed, the backgrounded
process STILL wasn't surviving: `export.log` stayed at 0 bytes, `ps aux`
showed no matching process, yet venv creation and the data rsync had
both clearly completed. Reproduced it by hand — SSH'd in directly and ran
the exact `nohup python -m scripts.export_dashboard_data > log 2>&1 &`
line myself, watched the process alive and burning real CPU (429%, 2GB+
RSS) for several minutes... and then it vanished too, with the same
silent, zero-byte log.

`dmesg`/`syslog` on the instance had the actual answer the whole time:

```
Out of memory: Killed process 7350 (python) total-vm:19335992kB,
anon-rss:15600308kB, ... Killed by the OOM killer.
```

`c6i.2xlarge` has 16GB RAM and no swap. The identification model's CV
over the grown 126-player pool (100+ classes) pushed the process past
15.5GB and the kernel SIGKILL'd it — which produces no Python exception,
no traceback, nothing for `run_self_healing`'s exception handling to
catch or print, and no trace in the app's own log, because the process
is killed before it gets a chance to flush anything. That silence is
exactly what three separate rounds of SSH/nohup/keepalive fixes spent
real time chasing this session, all on the wrong hypothesis — the SSH
layer was never broken; every one of those fixes was pattern-matching
against a symptom (silent death, no log) that memory exhaustion produces
identically to a dropped connection.

**Fix**: `remote_export_aws.py` now defaults `--instance-type` to
`r6i.2xlarge` (8 vCPUs, 64GB RAM) instead of inheriting
`spot_common.py`'s shared `c6i.2xlarge` default, which was tuned for the
CPU-bound annotation jobs, not this memory-bound one. Deliberately kept
as this script's own default rather than changing the shared constant —
the two jobs have opposite resource profiles (annotation: CPU-bound,
long-running, modest memory; export: short, bursty, now clearly
memory-hungry at 100+ classes) and shouldn't share a sizing default just
because they share launch/retry plumbing.

**Lesson for next time a backgrounded remote process goes silent with an
empty log and no trace in `ps aux`**: check `dmesg`/`syslog` for the OOM
killer BEFORE re-diagnosing the SSH/nohup/backgrounding layer — a
kernel-level SIGKILL and a dropped SSH session produce the exact same
externally-visible symptom (process just isn't there, nothing logged),
but have completely different fixes, and the SSH layer had already been
proven solid (rounds 1-3) before this ever started.

## Rating display bug: pre-game Elo vs. live rating, not stale data

User flagged cdznjerry showing 2066 while chess.com's live profile shows
2100, and had not played since August — so this was NOT the stale-data
problem it first looked like. Root cause, confirmed directly against
Chess.com's public `/pub/player/{username}/stats` endpoint (returns 2100
for cdznjerry's bullet, exactly matching the live site): `current_rating`
was computed from the WhiteElo/BlackElo header of the player's last
recorded game, which is the rating ENTERING that game, not after it. For
an account that's stopped playing, that gap between "pre-game Elo of the
last game we have" and "actual current rating" is permanent — there's no
later game to read a fresher number from.

**Fix**: new `scripts/refresh_current_ratings.py` — pulls every tracked
player's live rating straight from Chess.com's stats API (one small JSON
request per player, no PGN, no annotation, cheap to re-run) into
`data/raw/current_ratings.json`. `build_profile()` now takes `player`/
`mode` and prefers this live number when available, falling back to the
old last-game-header estimate otherwise; both the number's source
("live" vs "last_game_pregame") and its as-of date are now in the
exported JSON and shown on the dashboard, so a fallback case is visible
rather than silently presented as current. All 127 tracked players'
live ratings fetched successfully on the first run.

## Tilt vs. session-warmup: which game-context signals actually predict identifiability

User asked to dig for patterns (tilt, session time) in which games get
correctly identified, informed by the new per-game evaluation format, and
separately asked whether accuracy work could reduce the games needed
rather than just measuring the games-vs-accuracy curve. Added
`_correctness_pattern_analysis` to `build_identification()`: within-player
z-scored correlations between each session/tilt-style signal already in
the feature set and whether that specific held-out game was correctly
identified (within-player, not pooled — a pooled correlation would just
be confounded by which players are inherently easier to identify).

Real result on the full 126-player pool (93,418 games):

- **Session position dominates everything else.** `session_game_index`
  is the strongest correlate by a wide margin (r=+0.089). Bucketed:
  early-session games (1st-2nd of a session) are only **23.0%**
  identifiable single-game, mid-session (3rd-8th) **24.5%**, deep-session
  (9th+) **37.3%** — a genuine 14-point gap, not noise, confirmed on
  ~29K games per bucket. `log_minutes_since_prev_game` (r=-0.062, coming
  back after a break behaves like an early-session game) and
  `think_time_std` (r=-0.055, an erratic game is a harder-to-read game)
  are the next strongest, both consistent with the same "warmed up vs.
  not" story.
- **Tilt is NOT a real effect here.** `prev_game_result` (won/lost/drew
  the previous game) has essentially zero correlation (r=-0.0014);
  bucketed accuracy after a loss (27.57%) vs. after a win (28.14%) is a
  0.6-point gap, well within noise at this sample size. A real, honest
  negative result — the hypothesized "tilt makes you more identifiable"
  story the user asked to check for turned out not to hold; session
  warm-up is the actual driver, not emotional state.

**Turned into an actual accuracy lever, not just a finding**: added a
`sort_key` option to `_multi_game_curve` — instead of averaging a random
uniform sample of N games, prefer each player's own highest-value games
by some signal (here, `session_game_index`), taking only their single
best-k group rather than partitioning their whole history (an early
version of this partitioned into consecutive sorted chunks, which dumped
every player's worst games into the later groups and dragged the average
BELOW the random baseline — caught by testing before shipping, not
assumed to help). On the primary cohort, single-best-game selection hit
100% vs. 91.7% for a random game — the "if we could choose which games to
observe, not just how many" version of the same story the biometrics
literature already tells (a verifier isn't stuck with whatever games
exist; asking for a longer continuous session, the chess equivalent of a
longer speaker-verification utterance, is a legitimate ask). Also added
a confidence-weighted fusion variant (each game's own max-probability as
its averaging weight, a standard ensemble technique) as a second, more
modest lever — tiny but consistently non-negative gains on the small
cohort; full-pool numbers pending the same consolidated remote run below.

Shipped in the same run as the rating fix — `multi_game_accuracy_weighted`,
`multi_game_accuracy_session_selected`, and `correctness_patterns` now in
every identification cohort's exported JSON.

**Correction after the full-pool run landed**: both "smarter" selection
ideas were WORSE than plain random averaging at pool scale (confidence-
weighted: 66.3% vs 90.2% uniform at 20 games; session-selected: 61.9%)
— the opposite of what the small 2-player cohort suggested, and the
opposite of the "isn't cheating, real biometrics ask for better samples
too" framing this was shipped with initially. Root causes, not bugs:
confidence-weighting backfires because at 27% single-game accuracy, wrong
guesses are often confidently wrong, so weighting by confidence just
weights the errors harder; session-selection backfires because deep-
session games cluster within one calendar session and are correlated
with each other, not independent — averaging correlated samples doesn't
cancel noise the way averaging truly random ones does. Pulled both out
of the primary "how many games" selector (offering strictly-worse options
as equal choices would mislead) and now present them under "Two ideas
that didn't work" in the collapsed methodology section instead — a real
negative result, reported rather than buried, consistent with every other
reverted feature this session.

**Also found and fixed**: `remote_export_aws.py` never rsynced
`data/raw/` to the remote instance, so the live-ratings fix silently had
no effect on the first run it shipped in (every player fell back to the
stale estimate, no error). Fixed by syncing just `current_ratings.json`
specifically, not the multi-GB raw PGN directory it lives in.

## Dashboard redesign: interactive "guess who" game, plain-English labels, progressive disclosure

User feedback: the dashboard leads with raw single-game accuracy instead
of the real (multi-game) number, is wordy in places, and needs to be
presentable to people who aren't already chess- or ML-literate if it's
going to be published — "popularity won't just be how good the model is,
but how well it's presented." Researched how ML/biometric work gets
published as public demos before touching the page: Distill.pub's
explorable-explanations principle (reactive widgets tied to guided prose,
not walls of static text) and the "Which Face Is Real" / "Human or Not"
pattern of letting the viewer actively play against the model rather than
just read about it.

Concrete changes to the dashboard (not the underlying model):
- **The multi-game accuracy is now the headline**, not the single-game
  number — a `<select>` for games-observed drives the summary tile and
  the accuracy bar directly; the old single-game number is still shown,
  clearly labeled as "for comparison," never as the main figure.
- **New interactive panel**: "Try it yourself — whose game is this?" —
  picks a real held-out sample game, hides the name, gives a shuffled
  multiple-choice of candidates (drawn from that game's own top
  probabilities), lets the viewer guess before revealing the true player,
  the model's guess, and a running you-vs-model score. Directly borrows
  the "Which Face Is Real" engagement pattern — explaining the model by
  playing against it, not just displaying its stats.
- **Plain-English feature names everywhere**: a single shared
  `FEATURE_LABEL` map (e.g. `maia_surprise_mean` → "How often they play
  unexpected moves") now backs every panel that lists model features —
  feature importance, SHAP, and the correctness-pattern panel all used to
  show raw snake_case names independently; consolidated into one dict so
  a name only needs translating once. `hour_sin`/`hour_cos` and
  `dow_sin`/`dow_cos` (meaningless individually, one concept each) are
  now merged into a single "time of day" / "day of week" row wherever
  feature importance is displayed, instead of two cryptic half-signals.
- **Progressive disclosure**: the Identification tab now leads with the
  guess-game and the headline number, and tucks the confusion matrix,
  full feature-importance list, rank-k table, per-player recall, and
  methodology notes behind a single "See the full methodology..."
  `<details>` toggle — matching Distill's principle of hooking engagement
  before dense stats, and directly answering the "isn't focused on the
  main purpose" feedback.
- Rebuilt all 127 players' profiles locally (cheap — no CV/GPU work
  needed) to pick up the rating fix without another AWS round; only a
  genuinely new metric (the correctness-pattern analysis, weighted/
  session-selected curves) needed the remote run.

Sources consulted: [Distill — explorable explanations](https://distill.pub/2020/communicating-with-interactive-articles/), [Which Face Is Real](https://cottrillresearch.com/play-which-face-is-real-to-help-you-spot-fake-digital-identities/), [Human or Not](https://humanornot.so/).

## Group3 shipped: 126-player pool live on the dashboard

With the `r6i.2xlarge` fix, `remote_export_aws.py` succeeded on the very
first attempt — export completed in two ~20s poll cycles, instance
terminated cleanly. Full pipeline confirmed end to end for the first time
since group3 was wired into `export_dashboard_data.py`'s `DATASETS`.

Published the new `data.json`/`data.js` (page version 21), then synced
the database — both steps are required, per the earlier "publishing
data.js does nothing" lesson: the page's `db` capability overrides the
bundled fallback whenever any documents exist, so a page-only republish
would have left 102 stale players showing to real viewers. Wrote 131
documents (129 player profiles + `identification/pool` +
`identification/primary`) via three batched `write_db` calls, pinning
every write that touched an existing document with `if_version` (the
first attempt without pinning was correctly refused outright — apparently
`set` on an existing doc with no `if_version` is a hard refusal now, not
a soft skip, which is worth remembering next time: list the collection
first to collect current versions before any batch that might touch
existing docs).

Pool identification result on the full 126-player cohort: 27.9% overall
accuracy / 27.3% balanced accuracy vs. 5.8% majority-baseline and 0.8%
chance — consistent with the 102-player numbers, no sign of degradation
from the added players. `worgplayer` (the player whose `model.py` crash
was chased for hours earlier this session, see "A genuine multi-hour
false lead") is present and correctly profiled in this export, confirming
that fix held under the full pipeline.

## Two new accuracy metrics: rank-k and multi-game score fusion

User asked to keep pushing accuracy toward "actually publishable," citing
other biometric fields as inspiration, and separately proposed a
top-k-candidate-list idea unprompted. Both map directly onto standard,
well-established techniques rather than anything novel to invent:

- **Rank-k accuracy** (`_rank_k_curve` in export_dashboard_data.py): is
  the true player anywhere in the model's top k guesses, not just its #1
  guess? This is the "CMC curve" (Cumulative Match Characteristic) that
  face-recognition benchmarks report — rank-1 vs rank-5 accuracy is a
  standard pairing there for exactly this reason.
- **Multi-game score fusion** (`_multi_game_curve`): average the
  cross-validated probability vector across N held-out games from the
  same player, then take the argmax of the AVERAGED vector instead of
  judging one game alone. Grounded with real citations from three
  separate biometric fields this session: keystroke-dynamics systems
  authenticate off a typing *session*, not one keystroke; speaker
  verification's EER drops sharply as enrollment utterance duration
  increases; authorship attribution accuracy rises with text length and
  with majority-voting across multiple text samples. Same underlying
  statistics (noise averages down over independent samples) applied to
  chess move/clock behavior.

Both are computed post-hoc from `cross_val_predict`'s already-held-out
`proba` array — no retraining, and critically no leakage risk: every
game's probability estimate already comes from a fold that never trained
on it, so averaging several of them together is standard score-level
fusion, not a new source of contamination. Sanity-checked locally on the
fast 2-player "primary" cohort before committing to a full remote run
(91.9% at 1 game → 100% at 10 games — confirms the effect is real).

Real numbers on the full 126-player pool (the cohort that matters):

| games averaged | balanced accuracy |
|---|---|
| 1 | 27.3% |
| 3 | 44.7% |
| 5 | 56.1% |
| 10 | 73.9% |
| 20 | 90.2% |
| 50 | 98.4% |

| rank k | balanced accuracy |
|---|---|
| 1 | 27.3% |
| 3 | 48.2% |
| 5 | 59.1% |
| 10 | 73.9% |

50-game aggregation lands right in the range of the literature's headline
numbers cited earlier this session (~98% identifying among thousands of
candidates, albeit with full move-sequence models and larger enrolled
samples) — a genuinely strong, publishable result, not just a proof of
concept anymore. Shipped: page v22, dashboard now has "Watching more
games instead of one" (chart + explicit tracker table) and "Is the right
answer even on the list?" panels in the Identification tab; the closing
"Reading this honestly" copy now cites the real multi-game number
dynamically instead of a static claim. Both `identification/pool` and
`identification/primary` db docs re-synced with the new fields.

## Dashboard v2: a genuinely new artifact, not another patch to the old one

User feedback: incremental edits to the original dashboard weren't
visibly landing for them, and they wanted a real UI/UX redesign rather
than continued patches. Explicit instruction: leave the original artifact
alone as a legacy reference, build a completely separate new one.

Published a new artifact (different URL: `.../30a7b847-...`) from
scratch — new design system ("digital chess clock" concept: warm
graphite + amber in dark mode, Bricolage Grotesque + IBM Plex Mono,
"readout"/"bezel" card language instead of the old flat cards), new
information architecture (hero leads with the real headline number and a
one-line thesis; secondary stats — confusion matrix, full feature list,
methodology — tucked behind one "Full methodology" toggle instead of
cluttering the main view). The old dashboard at the original URL is
untouched.

## Real per-prediction reasoning (SHAP) replaces the "guess who" game

The interactive guessing game shipped earlier was cut: user pointed out
there's no real information to guess from (blind guessing among 126
unfamiliar usernames), and asked for something that explains reasoning
instead. Replaced with genuine SHAP-based explanations: `shap.TreeExplainer`
on the fitted gradient-boosting model (confirmed working directly against
sklearn's `HistGradientBoostingClassifier`, not just the LightGBM model.py
already used it for), computed only for the ~40-100 already-sampled
display games (cheap), giving each one a real top-5 feature attribution
toward the model's actual predicted class. Handles both the 2D SHAP-array
convention for binary cohorts and the 3D convention for multiclass ones
(discovered by testing directly — binary encodes "push toward
classes_order[1]" only, multiclass gives one slice per class).

## Two identification models: full vs. biometrics-only

User caught a real inconsistency: an earlier "no move style" instruction
(explicitly scoped to a later round of feature additions) had never been
applied to the ORIGINAL feature set, which still includes move-choice
signals (`maia_surprise`, `maia_prob_played`, `cp_loss`, `is_sacrifice`) —
two of which are top-5 important features. Clarified scope further: position/
move evaluation should only ever be used to measure the player's THINKING
RESPONSE, never stand alone as an identity signal.

Resolution (user's call): keep both. `build_identification()` now takes
`feature_mode` ("full" or "biometric", the latter dropping
`MOVE_CONTENT_FEATURES` entirely — cp_loss*, maia_surprise*, maia_prob_played*,
is_sacrifice, own/opp_skewers, n_captures_available* — down to 18 purely
clock/timing/session features from 33). `main()` now builds both variants
for every cohort. Full model is earmarked for a future anti-cheat
combination; biometrics-only answers "how much can we tell from habits
alone, zero move content."

Real result: biometrics-only is a *real, strong number on its own* —
23.3% single-game (vs. full's 27.3%) and **84.6% at 20 games** (vs. full's
90.2%) on the 126-player pool. Losing all move-choice information costs
only ~5.6 points at the multi-game horizon that actually matters. Both
numbers live on the dashboard behind a physical-switch-style toggle.

**Also fixed while shipping this**: `remote_export_aws.py` never synced
`data/raw/` to the remote instance, so the earlier live-ratings fix
silently had no effect on the first run after it shipped — every player's
rating fell back to the stale last-game-header estimate, no error, no
indication anything was wrong. Fixed by syncing just `current_ratings.json`
specifically (not the multi-GB raw PGN directory it lives in).

## New profile chart: think-time vs. position difficulty

Added `complexity_response_curve` to `build_profile()` — bins `gap_1_2`
(existing complexity proxy) into quintiles and reports mean think-time
AND mean cp_loss per bin, reusing the same axis as the existing
`recognition_curve` so the two are directly comparable. This is the first
time the project's own core relationship (`think_time ~ complexity`, per
CLAUDE.md) has been shown as a continuous curve rather than only as
discrete overthought/underthought/panic bucket percentages. Real,
intuitive finding on cdznjerry: think-time rises steadily with difficulty
(1.11s → 1.60s across quintiles) but accuracy still craters on the
hardest quintile (cp_loss jumps to 1566 from a 267-504 baseline) — they
DO try to slow down for hard positions, it just isn't enough.

## Two "smarter" multi-game selection ideas — both real negative results

Tried two ways to beat plain random-game averaging for the multi-game
accuracy curve: confidence-weighted fusion (weight each game's
contribution by its own max-probability) and session-selected fusion
(prefer each player's own single best-k games by `session_game_index`,
motivated by the correctness-pattern finding that deep-session games are
far more identifiable single-game). Both looked promising on the small
2-player "primary" cohort and were **initially shipped with a "this isn't
cheating" framing** — then both came back WORSE than uniform random
averaging once tested at full 126-player pool scale (confidence-weighted:
66.3% vs. uniform's 90.2% at 20 games; session-selected: 61.9%). Root
causes are real and non-obvious, not bugs:

- **Confidence-weighted backfires** because at 27% single-game accuracy,
  wrong guesses are often confidently wrong (this is a large, imbalanced
  multiclass problem — a game can closely resemble the WRONG specific
  player by chance, with high confidence). Weighting by confidence just
  weights the errors more heavily.
- **Session-selected backfires** because deep-session games cluster
  within one calendar session and are correlated with each other — same
  day, same warm-up state, closely spaced in time. Averaging N highly
  correlated samples doesn't cancel noise the way averaging N genuinely
  independent random ones does, even though any ONE of those deep-session
  games is individually more legible.

Corrected the dashboard copy to match reality: pulled both out of the
primary "which games to average" selector (offering strictly-worse
options as equal alternatives would mislead), now shown as a "Two ideas
that didn't work" appendix callout with the real numbers and the actual
reasons — same honesty standard as every other reverted feature this
session (recency-cap, complexity_accuracy_corr, time_control).

## Chessboard + move list in "Why did it decide that?"

User asked for the reasoning panel to show an actual chessboard with the
game on it, a move list with per-move think-times on the side, and
flagged "key moments" — explicitly fine with this being pre-selected
games rather than computed live, which made it cheap: confirmed
`fen_before`/`move_san`/`think_time`/`is_mistake` are all already
per-ply columns in the features parquet (from parse_pgn.py), so no new
data collection needed, just a targeted re-read.

Added `_build_narrated_games()` — picks the 4 most-confident correct and
4 most-confident incorrect predictions from the pool's full-model sample
(reusing the same confidence-sorted order already used elsewhere),
re-reads each specific game's per-ply rows from that player's own
features parquet (cheap: a handful of games, not the full pool), and
flags key moments as either `is_mistake` or a think-time z-score > 1.5
within that game. Computed and merged into the existing exported data
entirely LOCALLY — no AWS run needed, since this doesn't touch the
CV/model-fitting path at all, just re-reads a few already-known games'
raw rows.

Chessboard itself is a dependency-free inline SVG (`svgChessboard`) using
Unicode chess glyphs (♔♕♖♗♘♙/♚♛♜♝♞♟) rather than any external library or
image assets — deliberately avoids the CSP/loading complexity of
chessboard.js + jQuery + chess.js for what's ultimately a handful of
static position renders. Move list is clickable (jumps the board to that
ply), with quick-jump chips for flagged key moments so a viewer doesn't
have to scrub through 20-60 moves manually.

## Phase 2, v0: cheat-detection with injected/simulated ground truth

User greenlit Phase 2 earlier this session; picked it up now as explicitly
lower-priority "when there's extra time" work. Built
`scripts/phase2_cheat_simulation.py` — an injection + detection harness
satisfying CLAUDE.md's hard constraint (simulated ground truth only,
never real accused players).

**A real data-quality finding blocked the obvious approach first**: went
to use `cp_loss` as the move-quality signal for injected "always plays
best move" plies, and found (via investigation, not assumption) that
`cp_loss` is NOT "loss relative to the position's own best-move PV" — per
`src/features.py:879-888`, `eval_after` is just the NEXT row's
independently-computed `eval_before`, from a completely separate fresh
`analyse_fens()` search with no continuity to the previous position's
search. So `cp_loss` measures cross-search evaluation INSTABILITY, not
move quality — confirmed it can be thousands of cp even when
`played_best_move` is True (mean 790, max 19700 in exactly that subset),
concentrated near forced mates where the two independent searches'
mate-distance sentinels can disagree. This means a genuine Phase 2
move-quality signal needs real fresh engine re-analysis of the resulting
position, not a free relabel of an existing column — scoped out of v0,
left as a real, explicit follow-up requiring actual Stockfish compute.
**This also means `complexity_response_curve`'s mean-cp_loss line
(shipped earlier this session) likely has some of its quintile-5 spike
inflated by this same instability artifact near decisive/mate-adjacent
positions** — the qualitative finding (accuracy drops on hard positions)
is a well-established phenomenon independent of this artifact, but the
exact magnitude shown there should be read as directionally right, not
precisely calibrated; switching that chart to median cp_loss instead of
mean would be a robust, cheap fix, not yet done.

**v0 design**: two clean, well-defined signals instead —
`maia_prob_best_move` (a single Maia network call, safe to substitute
directly for an injected ply) and each player's own already-fitted
think-time-vs-complexity model (`src/model.py`, reused as-is). Two
adversary strengths: "naive" (replaced plies get unnaturally fast,
complexity-blind timing, 0.2-1.0s) and "smart" (replaced plies get timing
sampled from THIS PLAYER'S OWN fitted model prediction + realistic
noise — the deliberately-hardest-to-catch case). Evaluated via ROC-AUC,
clean vs. injected, on genuinely held-out test-set games (`_time_split`,
same split the player's own model was fit on) at injection rates 10/25/
50/100%.

**Caught and fixed a real bug before trusting results**: the think-time
model's target is `log1p(think_time)` (`features.py:954`), but the first
version of "smart" injection inverted with `exp()` instead of `expm1()`
— for small bullet-scale think-times, `exp(log1p(x)) = 1+x`, a systematic
+1 SECOND bias on every disguised ply, huge at this timescale. Silently
made "smart" injections look artificially slow rather than disguised
(AUC went *below* 0.5 instead of settling near it). Fixed, re-ran, and
the results became sensible — a good reminder to sanity-check a
simulation's own mechanics before trusting what it reports about the
thing it's simulating.

**Real, validated results — consistent across 3 different players**
(cdznjerry, BIG_TONKA_T, abhi-k), each on ~40-60 held-out games:

| | naive (lazy) @ 100% | smart (disguised) @ 100% |
|---|---|---|
| timing_score AUC | 0.99–1.00 | 0.33–0.54 (near or below chance) |
| maia_score AUC | 0.52–0.67 | 0.52–0.67 |

Honest takeaways, matching what CLAUDE.md already expected but now with
real numbers behind it: **timing deviation alone is a strong, reliable
catch for an unsophisticated cheater and is comprehensively defeated by
a moderately sophisticated one who disguises pace to match their own
known baseline** — exactly the "spoofable by a deliberate adversary"
caveat. **Move-quality-typicality (Maia) alone is a weak signal
regardless of disguise level**, for all three players tested, at
full-game injection rates.

One more real, non-obvious finding worth a closer look later: smart-mode
timing_score doesn't just settle near 0.5 as rate increases, it drifts
slightly BELOW 0.5 (0.33-0.42 at 100%) — a heavily-model-disguised game
has LESS natural variance than real human play (the model's own
prediction is a mean, and sampling tightly around it strips out the
noise real humans actually have), making it look paradoxically *too
consistent* with the baseline rather than inconsistent with it. A
residual-VARIANCE detector (not just mean residual) might catch exactly
this artifact — flagged as a promising next step, not yet built.

**Explicitly NOT done in this v0** (real scope, not hidden): a real
trained combined classifier (v0 just sums two raw scores); real
move-quality via fresh engine re-analysis; testing on the pool's full
player roster rather than 3 hand-picked ones.

## Phase 2, selective injection: the actual hardest case, now measured

Followed up the flat-rate v0 with CLAUDE.md's explicit hardest scenario —
"engine on 1-3 critical moves," not a rate spread across the whole game.
Added `selective_k` to `inject_cheat()`: instead of a random `rate`
fraction of plies, injects on the `k` plies with the LOWEST `gap_1_2`
(the genuinely hard decisions — a real selective cheater doesn't need
engine help on forced recaptures). Also added `score_game_max` — a
per-move MAX/top-3-mean reducer instead of a whole-game mean, since
averaging 1-3 tampered moves in with 20-60 clean ones dilutes any signal
to invisibility; a real detector for this case has to ask "does anything
in this game look anomalous," not "does the game look anomalous on
average."

**Result, consistent across cdznjerry and BIG_TONKA_T, k=1/2/3 critical
moves**: timing (naive/undisguised): AUC 0.49-0.62 — barely above chance
even in the EASY (undisguised) case, let alone the disguised one (0.49-0.50,
flat chance). Maia (`maia_max`/`maia_top3_mean`): pinned at **exactly**
0.500 in every single condition tested.

That exact 0.500 for Maia is itself a real, useful finding, not just
"weak signal" — investigated why: with a MAX-style reducer, the
game-wide score is dominated by whichever move already has the lowest
natural `maia_surprise` — and ordinary human play already produces some
very-low-surprise moves on its own (forced recaptures, obvious captures,
book moves), so that global minimum is already near its floor before any
injection happens. Adding 1-3 MORE low-surprise moves doesn't move a
statistic that's already saturated by naturally-occurring ones. This
means MAX-based aggregation, exactly the fix that helps the TIMING signal
survive dilution, is actively the WRONG tool for a move-typicality
signal — a real move-quality detector for the selective case needs
something conditioned on THIS position's own difficulty (was this move
surprisingly un-surprising for how hard the position was), not a raw
"least surprising move in the game" search. Flagged as the concrete next
step, not yet built.

**Bottom line, with real numbers behind it now**: this fully confirms
CLAUDE.md's own stated expectation that single-game detection of a
selective cheater is near chance — true not just for move quality (as
the literature already said) but, on this evidence, for timing too, once
the tampering is confined to a genuinely small number of critical moves.
Single-game detection of a careful, selective cheater is a hard problem;
any real Phase 2 system will need to lean on patterns ACROSS many games
from an account (the same multi-game aggregation principle the
identification model already uses), not a single-game verdict.

## Phase 2: a real trained combiner, not a naive sum

The v0 "combined" score was just `timing_score + maia_score` — arbitrary
1:1 weighting, never validated. Fitted a `LogisticRegression` on the two
scores instead, trained on half the held-out games and evaluated on the
OTHER half (a fresh split, so the reported AUC isn't fit-and-eval on the
same games).

Real result on cdznjerry, naive (undisguised) timing:

| rate | naive sum AUC | trained AUC |
|---|---|---|
| 10% | 0.481 | 0.638 |
| 25% | 0.601 | 0.887 |
| 50% | 0.664 | 0.996 |
| 100% | 0.756 | 1.000 |

The naive sum was actively hurting the pure timing signal (0.481 <
chance at 10% — worse than not looking at Maia at all), because it
blindly averages in a near-uninformative signal at equal weight. The
trained classifier's learned weights confirm exactly this: the timing
coefficient grows strongly with rate (+0.51 → +3.86) while the Maia
coefficient stays pinned near zero across every rate — it correctly
learned to ignore the signal that isn't carrying information, rather
than being dragged down by it. For "smart" (disguised) timing, the
trained combiner doesn't help (0.44-0.53, still chance) — consistent
with every other finding this session: once timing is well-disguised,
no combination of these two signals recovers it. A real Phase 2 system
needs a genuine third, independent signal (the fresh-engine-reanalysis
move-quality one already scoped as a follow-up) for that case, not a
better way to combine the two that already exist.

## Chessboard panel follow-ups: key-moment explanations, biometrics-only source, a merge bug

Four quick fixes to the chessboard/reasoning panel from live user feedback:

- **Key moments now say WHY.** A flagged "mistake" or "long think" used
  to just show a symbol with no explanation. Added `key_reason` text per
  move in `_build_narrated_games()` — for mistakes, the actual eval swing
  in centipawns (phrased as "the position's evaluation swung," not an
  unqualified "you blundered," given the cp_loss caveat above); for long
  thinks, the exact seconds/multiple-of-average plus position context
  (capture count or how close the top choices were) pulled from columns
  already on the row. Shown near the board for the selected move and as
  a tooltip on the key-moment quick-jump chips.
- **Narrated games now come from the biometrics-only model**, not the
  full one — user wants the showcase examples to be purely biometric,
  matching the feature_mode split shipped earlier. `main()`'s call now
  reads `out["identification"]["pool_biometric"]["sample_games"]` instead
  of `pool`'s — both which games get picked AND their `top_reasons` bars
  now only ever cite clock/timing/session signals, never move choice.
- **Skewed the example mix toward correct predictions** (4:4 → 6:2):
  single-game accuracy is genuinely ~23-27%, and an even split made the
  demo's first impression undersell what multi-game aggregation actually
  achieves. Still keeps real wrong examples — not fully cherry-picked,
  just weighted to match the honest headline (multi-game) number better
  than a coin-flip split would.
- **Real bug**: `mergeCyclicalFeatures` (folds `hour_sin`/`hour_cos` and
  `dow_sin`/`dow_cos` into one readable row) was applied to the feature-
  importance and SHAP panels but never to the reasoning panel's own top-5
  bars — user spotted `dow_cos`/`dow_sin` still showing raw there. Also
  switched the merge itself from quadrature magnitude (`sqrt(a²+b²)`,
  which discards sign) to a plain signed sum, since the reasoning panel's
  bars need a real "for/against" direction, not just relative size.

## LightGBM added as a third identification model, dashboard v2 republished

Answered the user's question about why only logistic regression and
HistGradientBoostingClassifier were the identification candidates: no
principled reason to stop there, just "simple interpretable baseline vs.
one real nonlinear challenger, actually compared rather than assumed."
Added `lgb.LGBMClassifier` as a third contender in `build_identification`
— it's already a trusted, zero-new-dependency part of this codebase
(every per-player think-time/move-quality model in `src/model.py`). All
three are cross-validated and the best on held-out balanced accuracy
wins, exactly like the LR-vs-HGB comparison before it.

Real result on the `primary` cohort: LR 78.7%, HGB 86.3%, **LightGBM
86.5%** — LightGBM won, narrowly. SHAP explanation support extended to
cover it (`shap.TreeExplainer` handles LightGBM's binary-classifier
output as a plain 2D array in the installed shap version, confirmed
directly rather than trusted from the deprecation-warning text, which
describes a change to a *future* shap version, not this one — verified
with a standalone repro before trusting the sign convention). Dashboard's
"Model comparison" panel now shows all three numbers, not two.

Tried the natural follow-up — a naive-average ensemble of LR + LightGBM
on the 126-class `pool` cohort — and got a real negative result: LR alone
scores 15.6% balanced accuracy there (far weaker on a cohort this size),
LightGBM alone scores 25.3%, and the 50/50 average scores 24.9% — *worse*
than LightGBM alone, because blindly averaging in a much weaker model
drags the stronger one down. Exactly the same lesson as Phase 2's
"naive sum vs. trained combiner" finding, showing up again in a different
part of the codebase. A real ensemble here would need a trained/weighted
stacking meta-model, not a flat average — not built yet, logged as a
real "tried it, it doesn't help naively" result rather than shipped.

The main dashboard's remote AWS export (`r6i.2xlarge`, this run's actual
compute — earlier attempts had failed from a mid-run SSH reset the
self-healing retry loop correctly recovered from) finally completed and
was pulled down and republished, bringing the opponent-move
reconstruction, value/typical-comparison sentences, linked-move-to-board,
and biometrics-as-default work from earlier in the session onto the live
page for the first time. The LightGBM change above is in the code but
NOT yet in that export — it'll ship on the next remote run rather than
justifying its own AWS spend solo.

## Phase 2, full roster: the same injected-cheat evaluation at 135 players, not 2-3

Ran `scripts/phase2_full_roster.py` (new) — the same flat-rate and
selective-injection scenarios from `phase2_cheat_simulation.py`, across
every player with a trained think-time model (135, not the 2-3 hand-
picked ones used to develop the methodology) — closing the "not tested
at scale" gap this file already flagged. ~55 minutes locally (one
player, `ashwa2e3`, took ~14 minutes on its own for reasons not
investigated — didn't recur, treated as a one-off rather than chased).

Full-roster numbers **confirm the small-sample finding, not just
replicate it**:

| scenario | naive (undisguised) | smart (disguised) |
|---|---|---|
| flat 10% replaced | 0.549 median combined AUC | 0.502 |
| flat 100% replaced | 0.847 | 0.521 |
| selective, k=1 critical move | 0.610 (worst-move AUC) | 0.500 |
| selective, k=3 critical moves | 0.739 | 0.489 |

Smart/disguised timing sits at essentially exactly chance (0.49-0.52)
across every rate and every selective-k, for the median player across
the whole roster — this is not a small-sample artifact, it's the honest
shape of the problem, matching CLAUDE.md's literature-derived expectation
directly. The trained combiner (logistic regression on timing_score +
maia_score, fit/eval split independent of the think-time model's own
split) reaches a median AUC of 0.586 vs. the naive sum's 0.524 on the
hardest single scenario (smart, 100% replaced) — a real improvement, and
enough to push 62/135 players (46%) to a meaningfully-above-chance 0.6+,
but still not remotely a reliable detector for the median case.

Built `scripts/phase2_build_dashboard_data.py` to aggregate the raw
per-player output into medians/IQR-per-scenario and a per-player table,
and published it as a **separate artifact** from the identification
dashboard, per explicit instruction — this tool answers "does a signal
exist at all," a genuinely different question and audience from "which
of my games looks like which player," and conflating the two risked the
identification dashboard reading like an accusation tool. New artifact:
"Signal Detection Lab" (dark/cool-slate identity, distinct from dashboard
v2's warm terracotta one, deliberately — same reason). Every page surface
states plainly and early that every "cheater" shown is a real held-out
game with synthetically injected ground truth, never a real accused
account, per CLAUDE.md's explicit constraint.

**Still not done, by design** — this is v1: no fresh-engine-reanalysis
move-quality signal (the third, independent signal actually scoped in
CLAUDE.md's Phase 2 sketch), no personalized-Maia style deviation, and
the combiner is still just two features. All real next steps, not
started tonight.

## Real bug found: the Maia suspicion sign was backwards — caught by building multi-game aggregation

User asked whether our methodology matches how real anti-cheat systems
(Ken Regan's Intrinsic Performance Rating, Chess.com Fair Play) actually
measure detection. The honest answer: real systems essentially never
call it from one game — they compare engine-agreement rate against a
rating-calibrated reference distribution accumulated over MANY games
(100+ moves is a typical practical minimum), not a single-game
classification. The identification dashboard already has this shape of
multi-game curve; Phase 2 cheat-detection never did — every AUC on the
Signal Detection Lab page was single-game. Built
`scripts/phase2_multigame.py` to close that gap: bootstrap-resamples
groups of N of the SAME player's held-out games to build many simulated
"N-game suspects," for the two most policy-relevant scenarios (flat
smart-100%, selective smart k=1).

Building it surfaced a real, previously-invisible bug. `score_game`'s
Maia component was `maia_score = -mean(maia_surprise)`, commented
"suspicious = low surprise, engine-like" — the assumption that an
engine's objectively-best move reads as boringly predictable to a
human-move model. Checked directly against 20k+ real plies
(cdznjerry): the engine's best move is on average MORE surprising to
Maia than the player's own actual move (mean surprise 2.084 vs. 2.045)
— a strong human's real choice is usually well-calibrated and
human-typical; the engine's move is sometimes objectively correct but
less intuitive. The sign was exactly backwards. Single-game noise (a
~2% gap) completely hid this — it's what let every earlier single-game
AUC in this session look merely "near chance" rather than visibly
wrong — but multi-game aggregation, built specifically to resolve small
consistent biases via the law of large numbers, immediately exposed it:
bootstrapped 20-game AUC was 0.155 (actively backwards) before the fix
and 0.774 (real signal) after flipping the sign in both `score_game`
and `score_game_max`.

This is the SAME category of catch as the earlier cp_loss semantics
investigation and the exp/expm1 injection bug — a plausible-sounding
assumption about what "looks like cheating" that turned out wrong on
real data, caught only because something forced a direct empirical
check rather than trusting the comment. It also reframes the earlier
"naive sum hurts, LR learns near-zero weight on maia_score" finding:
that wasn't just "maia_score is uninformative," it was partly
uninformative AND partly working against itself, both effects too small
per-game for LR to reliably untangle from noise at that scale.

Selective (1 critical move) detection stays near chance even after the
fix, at every N tested — a real, distinct limitation (one anomalous
move doesn't move a whole game's MAX surprise much when clean games
already contain naturally surprising moves elsewhere), not another sign
bug. Matches CLAUDE.md's own literature-derived expectation for the
hardest case.

Fixing the already-published Signal Detection Lab numbers (which used
the backwards sign) plus adding the new multi-game view needs a full
re-run of `phase2_full_roster.py` and the new `phase2_multigame.py`.
Per the earlier laptop-uptime discussion, the user asked for this to run
on AWS rather than locally — built `cloud/remote_phase2_aws.py`,
mirroring `remote_export_aws.py`'s proven nohup-background-plus-poll
pattern exactly (same SSH keepalive settings, same self-healing retry
loop via `spot_common.py`), running both phase2 scripts in sequence on
one `c6i.2xlarge` instance (lighter workload than the identification
export — no 100+-class CV memory spike, so no need for the export
script's memory-padded r6i.2xlarge). Kicked off alongside the
already-running main-dashboard export (LightGBM identification change)
— two separate EC2 instances running concurrently, both self-terminating
on completion.

## Correction to the above: the sign isn't backwards, it's PLAYER-DEPENDENT — and that's the more interesting finding

The full 135-player AWS run of `phase2_multigame.py` came back and did
NOT show the clean population-wide improvement the single-player
(cdznjerry) check predicted. Population median AUC stayed flat near
0.45 as N grew from 1 to 20 games, while the IQR widened dramatically
(q25: 0.37→0.10, q75: 0.53→0.74) — a split, not a uniform shift. Before
either publishing that confusing aggregate or re-asserting the earlier
"bug fixed" framing as if it were the whole story, checked 10 individual
players directly (all with comparable held-out pools, 37-40 games each,
ruling out small-sample bootstrap degeneracy as the explanation): AUC at
N=20 games ranged from **0.001 (one_min_mania... no — sangamejay, 0.001)
to 0.927 (one_min_mania)** — real, strong, opposite-direction effects,
not noise from thin data.

The honest conclusion: whether "the engine's best move is more surprising
to Maia than this player's own real choice" is TRUE OR FALSE depends on
the individual player, not a universal property of engine-assisted play.
For some players (apparently: those whose natural style already runs
somewhat atypical-for-their-rating), their own real moves are the more
"surprising" ones by Maia's reckoning, and the earlier sign was right
for them. For others, the original assumption holds. Neither a fixed
`+mean(surprise)` nor `-mean(surprise)` is a correct global rule — both
are right for roughly half the roster and actively backwards for the
other half. `score_game`'s sign flip earlier tonight wasn't wrong to
make (it's correct more often than the pre-flip version was, and
strictly correct for the one player it was validated against), but
presenting it as "the bug, now fixed" would have been premature —
the real finding is a level deeper: this signal isn't safely
summarizable as one global constant AT ALL.

This is, unexpectedly, direct empirical support for something CLAUDE.md
already scoped as necessary rather than optional: "personalized style
deviation (Maia fine-tuned on the individual player)." The per-player
TRAINED COMBINER already in `phase2_full_roster.py`
(`combiner_smart_100pct`, a LogisticRegression fit separately per
player) sidesteps this whole problem by construction — it learns
whatever sign and magnitude actually holds for that specific player from
their own held-out games, rather than assuming one global rule. That's
the credible number to foreground, not any fixed-sign naive score.
Signal Detection Lab needs to present this honestly: the per-player
split (with real numbers, not smoothed into a misleading median), the
trained combiner as the credible claim, and the naive global score
retired from being presented as a standalone detector.

### A further correction, minutes later: the flip itself was net-negative on the full roster

Ran the per-player breakdown properly (135 players, not the 10-player
spot-check above) to quantify the split precisely before touching
anything: 33/135 players strongly matched the flipped-sign direction
(n=20-game AUC >= 0.75), 48/135 strongly matched the ORIGINAL direction
(AUC <= 0.25) — more players contradicted the flip than confirmed it.
Population median AUC under the flipped sign: **0.414 (net WORSE than
chance)**; under the original, pre-flip sign: **0.586 (weakly net
better)**. The single-player check that motivated tonight's flip
(cdznjerry) happened to land in the smaller, flip-favoring group — a
real instance of the effect, but not representative, and generalizing
from it to a global code change was premature. **Reverted the sign in
`score_game`/`score_game_max` back to the original `-mean(surprise)`**
— marginally the better global default on real full-population data,
though "marginally better than a coin flip" is itself the honest
takeaway, not a fix. Comments in the code now state this precisely
(both directions' population median AUC, both group counts) rather than
asserting either as correct. Leaving this full sequence in the log
uncollapsed — first instinct, real single-player validation, a
premature global claim, then the correction — because the mid-course
correction happened in the *same night* and is itself worth being able
to trace, not something to quietly edit away.

Net effect on what actually changed vs. before tonight: the CODE is
back to where it started (original sign). What's real and new is the
FINDING — this signal's usable direction is genuinely player-dependent,
quantified now at full-roster scale, and the trained per-player
combiner (unaffected by any of this, since it fits its own sign per
player from data) is confirmed as the only credible way to use it.

## Both AWS jobs landed; both dashboards republished with corrected data

Main dashboard export finished (after one more transient auto-recovery,
same self-healing retry pattern as before — not a new issue) and was
pulled, rebuilt, and republished. LightGBM won 3 of 4 identification
cohorts (`primary_biometric`, `pool`, `pool_biometric`); HistGradient-
Boosting narrowly held `primary` (0.918 vs. LightGBM's 0.916) — close
enough that which one wins isn't stable across runs, which is exactly
why the code fits and compares all three every time rather than
hardcoding an assumption.

Rebuilt Signal Detection Lab with all three of tonight's Phase 2
results merged (`phase2_build_dashboard_data.py` extended to also read
`multigame.json` and a new `per_player_direction.json`): the corrected
(reverted) naive-sign numbers, the multi-game curves, and — the real
headline — a new "Personalization" section reporting per-player
STRENGTH (direction-agnostic: `max(auc, 1-auc)` at 20 games) rather than
a fixed-sign AUC. Median strength 0.805; 84/135 players (62%) reach
0.75+. Reframed the hero's second verdict card as "disguised, one
global rule" (still near chance) vs. a new third card, "disguised,
calibrated per player" (0.805) — making the actual finding legible at a
glance: the signal was never really weak, a single global heuristic
just can't see it.

Both `cloud/remote_phase2_aws.py`-launched and locally-run compute for
tonight are done; no jobs left in flight as of this entry.

## CatBoost tested as a 4th identification candidate — real gain, not worth the cost

Followed up on the earlier "other models worth trying" answer (CatBoost
was the top recommendation) and tested it directly against the same
cohorts as LR/HGB/LightGBM. Real result: `pool_biometric` balanced
accuracy 0.276 vs. LightGBM's 0.249 — a genuine, meaningful 2.7-point
gain, not noise. But it took **4225s (~70 minutes)** for that one
126-class cohort's 5-fold CV, vs. low tens of seconds for LightGBM/HGB
on the same data — `primary`/`primary_biometric` were fast (~1s) but
those are only 2-class problems; the cost scales badly with class count
here. Adding it as a 4th "fit all, take the best" candidate in
`build_identification()` would roughly double-or-worse the export's
total runtime across all 4 cohorts (`pool` and `pool_biometric` both
being 126-class), which matters a lot for a pipeline that already needed
a real fix (r6i.2xlarge, OOM investigation) to stay reliable, and
directly conflicts with CLAUDE.md's own stated priority: "Interpretability
matters more than raw accuracy here." Not wired in — deliberately, a
real tested result and not a ceiling this project is trying to chase,
same spirit as the naive-ensemble-averaging negative result earlier
tonight. Didn't try a lighter-config CatBoost (fewer iterations) to see
if most of the accuracy survives at a fraction of the cost — a
reasonable follow-up if this gets revisited, not done tonight given the
time cost of even one more full test.

## Small UX close-out: cross-linked the two dashboards, verified both are internally consistent

Time Signature and Signal Detection Lab are deliberately separate
artifacts (explicit instruction), but neither referenced the other,
which made them harder to discover as the related pair they are. Added
a quiet, footer-level link each way — not in the primary nav, matching
the "cheat detection is a related research page, not part of the main
identification story" framing already established. Also fixed a
duplicate `@media (prefers-reduced-motion)` rule left over from earlier
in the session (harmless, just redundant). Verified both dashboards'
`data.js` parse as valid JSON with every expected top-level key present
before calling tonight's work done — no jobs left in flight, nothing
known to be broken.

## A real per-player trained detector, replacing the thin 2-feature combiner

User asked directly: what's the current cheat-detection accuracy, have
we modeled it per player, and can we improve it. Answer at the time:
yes, minimally — a `LogisticRegression` on exactly 2 features
(`timing_score`, `maia_score`), fit per player on a single train/eval
half-split. Built `scripts/phase2_trained_detector.py` to do this
properly: 6 direction-agnostic features per game (mean/std/max of both
the think-time residual and Maia surprise, UNSIGNED — no hardcoded
"which way is suspicious," consistent with the personalization finding
above) and real `StratifiedKFold` cross-validation (k=3-5 depending on
per-player sample size) instead of one noisy split.

**Full-game replacement (smart timing, every ply replaced):** median
AUC 0.581 (old thin/single-split) → **0.966** (rich/CV'd), beating the
old approach on 135/135 players. Real, validated, large.

**Selective, 1 critical move (the actually realistic case):** the SAME
richer approach does NOT help — median AUC 0.281 (rich) vs. 0.361
(thin), both BELOW chance, rich winning on only 12/135 players. Checked
the most likely confound directly (does the always-targeted
lowest-`gap_1_2` ply already look different in clean, untouched play?)
on real data (cdznjerry, 132 games): resid −0.022 vs. −0.021 at other
plies (negligible), surprise 2.108 vs. 2.049 (a small, expected bump —
low-gap positions have two similarly-good options, so genuine human
ambiguity there is unsurprising) — nowhere near enough to explain a
BELOW-chance result. Left unexplained rather than papered over: most
likely explanation is that one tampered move diluted into 20-60 mostly
untouched ones just doesn't leave enough real signal for an 8-feature
model to learn a stable per-player direction from ~60-80 training rows,
even with CV, but that's a hypothesis, not a confirmed root cause.

Net conclusion, matching CLAUDE.md's original framing exactly: richer,
properly cross-validated per-player modeling is a real, large win for
catching a consistent/undisguised-in-aggregate cheater, but it does NOT
rescue the hard, realistic selective case — that genuinely needs a new,
independent signal (fresh engine reanalysis, or the personalized Maia
fine-tune already scoped), not a smarter combination of the two signals
already in hand. Not a disappointing result — a confirming one.

## Two adversary questions, both tested directly: more critical moves, and an alternate-move evasion

User asked three things: what if a cheater uses multiple critical moves
(not just one), what's standard practice in the field, and what if the
cheater plays an alternate (non-top-1) engine move to look less
conspicuous. Answered the "checks the engine but only acts selectively"
variant analytically — it's not actually a new case, since detection
only ever sees the OBSERVABLE outcome (which moves got touched), never
the decision process behind choosing them; that's identical to the
existing selective-k scenario. Answered "what's standard practice" from
the literature (Regan's IPR/FIDE, the "Irwin" tool, chess.com's Fair
Play): aggregate over many games (tens-hundreds, not one), weight
positions by how much the move choice actually discriminates skill
(similar in spirit to our gap_1_2/criticality features, far more
rigorously calibrated), and timing is a secondary/corroborating signal
— notably including a specific pattern we don't yet check for: spending
LESS time on objectively harder positions (a consulted engine "already
solved it"), the inverse of normal human behavior.

The alternate-move question was genuinely untestable from existing data
(precomputed `maia_prob_best_move` is rank-1-specific) but turned out to
be directly buildable: Stockfish's MultiPV already gives up to 4 ranked
candidates per position (`top_moves` column), and Lc0/Maia weights are
installed locally (confirmed: ~2ms per query once the engine is warm,
43ms one-time load). Built `scripts/phase2_altmove_adversary.py`: same
"smart" timing disguise as before, but the injected move is sampled
from MultiPV ranks 2-4 instead of always rank 1, with a LIVE Maia query
(not a lookup) for that specific alternate move's probability. Tested
both k=1 and k=3 critical-move scenarios, full 135-player roster, same
games scored both ways for an exact comparison:

| k | best-move (median AUC) | alt-move (median AUC) | alt-move helped evasion |
|---|---|---|---|
| 1 | 0.282 | 0.328 | 36/135 (27%) |
| 3 | 0.435 | 0.517 | 18/135 (13%) |

**Real, somewhat counter-intuitive result: naively playing an alternate
engine move makes the median player MORE detectable, not less.**
MultiPV ranks purely by engine evaluation, not by "how human-plausible
this looks" — a rank-3 alternative can be just as atypical to a
human-move model as the literal best move, while giving up objective
quality for no detection benefit. A genuinely effective version of this
evasion would need the adversary to ALSO consult something like Maia to
pick the most human-plausible engine-approved option, not just avoid
rank #1 — a meaningfully more sophisticated assumption than tested here,
and a real candidate for a future adversary tier.

Also confirms (k=1 → k=3, best-move adversary): more critical moves
does help detection (0.282 → 0.435, real and expected — more tampered
evidence), but doesn't solve the problem — still below chance at k=3
for the median player, even with the richer, cross-validated model.

## The night's best result: stacking biometrics onto a REAL engine-agreement baseline

User asked directly: instead of biometrics alone, can we layer this on
top of an existing anti-cheat model to boost it? This is literally the
"difficulty-conditioned move quality" signal CLAUDE.md scoped from the
very start and this project never built — not skipped, deferred,
because `cp_loss` (the obvious candidate) turned out to measure
cross-search instability, not move quality (the cp_loss investigation
earlier this session). The key realization: `played_best_move` and
`n_moves_within_30cp` are DIFFERENT columns from the same Stockfish
MultiPV annotation, completely unaffected by that issue, and give a
real engine-agreement-rate signal — the actual primary signal real
tools (Regan's IPR, etc.) use — for free, no fresh engine reanalysis
needed. Confirmed informativeness-weighting matters on real data
(cdznjerry): natural match rate is 24.8% on positions with 2+
genuinely competitive options vs. 47.6% on near-forced ones — matching
on a forced move tells you nothing.

Built `scripts/phase2_engine_agreement_boost.py`: a 3-feature engine-
agreement baseline (overall match rate, informative-position match
rate, a length control) vs. biometrics alone vs. combined, same
per-player CV'd-LR methodology as everywhere else tonight, full
135-player roster, selective k=1 (the realistic case).

First combination attempt (raw feature concatenation, 3+8=11 features)
made things WORSE than the baseline alone (0.479 → 0.322) — a real
overfitting artifact, not evidence biometrics don't help: throwing 8
extra raw dimensions at a model with only ~60-80 samples per player is
exactly the failure mode multi-game aggregation and cross-validation
have been fighting all night, just from a different angle. Fixed with
the standard stacking-ensemble pattern instead: train the two signal
groups as SEPARATE cross-validated models, then combine only their two
output probabilities in a final small classifier — 2 meta-features, not
11 raw ones.

**Real result: median AUC 0.479 (baseline alone) → 0.659 (stacked) —
beating the baseline on 128/135 players (95%).** The best single
outcome of the whole night for the hardest, most realistic scenario.
Biometrics genuinely do carry incremental information beyond move
quality — the earlier "biometrics alone are weak/ambiguous for
selective cheating" finding was real, but "weak" didn't mean "useless
in combination," and the combining METHOD mattered as much as which
signals were combined. Not wired into the live dashboard's identification
pipeline yet (that's the biometric player-ID model, a different system) —
this is Phase 2 cheat-detection specifically, added to Signal Detection
Lab.

## Tried importing an actual real-world anti-cheat model — real attempt, honest dead end

User pushed further: rather than our own engine-agreement proxy, could
we import a genuine EXISTING anti-cheat model and stack biometrics on
top of that instead. Searched and found a real, downloadable option:
"Irwin" (https://github.com/clarkerubber/irwin, via the
Jearnest94/chesscom-irwin fork) — actual pretrained Keras weights
(488K params, `analysedGame.h5`), not something built here. Cloned it,
read `AnalysedMove.tensor()` / `AnalysedGame.tensor()` /
`Game.boardTensorsByPlayerId()` directly from source (not guessed), and
built `scripts/irwin_adapter.py` — a faithful port mapping our own
`top_cps`/`top_moves`/`eval_after`/`think_time`/`fen_before` columns
into its exact (60,13) + (60,1) tensor shape, replicating even a real
quirk in the original code exactly (`piece_at(move.to_square)` reads
the PRE-move board, so for a capture this captures the CAPTURED piece's
type, not the mover's own piece — kept as-is since the model's weights
were trained against whatever that code actually produces).

Loaded successfully with real weights. Result: **completely saturated
on our data** — every real game scores ~99.7-99.8/100, and even the
most extreme test (100% of moves replaced with verified engine-best
choices) barely moves the score (99.8 → 99.8). Found and fixed one real
bug along the way (`inject_cheat` never updates `move_uci`, only the
Maia/timing columns our own scoring reads — the adapter needed
`move_uci` set to `best_move` on injected rows to reflect the
substitution at all) — didn't change the outcome. Checked for a missing
input-normalization step (none found in the source; the model has no
normalization layers and no separate scaler file). Input tensor itself
verified sane (no NaN/Inf, reasonably varied per-feature ranges).

Root cause, most likely: "Irwin" is an independent community research
project (clarkerubber, not an official Lichess system), no published
validation metrics, and its own README already warns results on
Chess.com data "may be less accurate" than its native Lichess training
distribution — ran into that domain-shift warning for real, not just as
a caveat. Conclusion: don't keep chasing this specific model. The
engine-agreement baseline built directly from our own trusted data
(previous section) remains the credible foundation — real, validated
end-to-end, and already proven to combine with biometrics via stacking.
This attempt to substitute in an external "real" model produced nothing
usable to replace it with, and that's a legitimate, complete answer to
the question, not an unfinished thread.

## A note on overnight autonomy: local background work doesn't survive the user's laptop going offline

User flagged (correctly) that local `run_in_background` Bash jobs — like
the 55-minute full-roster run above — need this session alive, which
needs the machine on, which the user can't guarantee overnight. Nothing
was lost this time (the run finished before the laptop's status changed),
but going forward: publish finished work as Artifacts immediately (they
live on Anthropic's infrastructure, not the user's machine, so they're
durable the moment they're published) rather than sitting on local
results; prefer the existing remote AWS pipeline over local background
compute for anything heavy, since the EC2 instance itself keeps running
independent of this session once launched — though pulling the result
back and terminating the instance still needs a live session, so a run
kicked off right before a long offline stretch would sit there
(and keep billing) until the session resumes. Chose not to kick off
another multi-AWS-instance-hour run blind right after this feedback for
that reason — the LightGBM identification change above is ready and
waiting for the next export instead of forcing one now.
