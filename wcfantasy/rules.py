"""Game rules for FIFA World Cup Fantasy 2026 (play.fifa.com/fantasy).

All constants cross-verified against Yahoo Sports + Ingenuity Fantasy guides
(the official FAQ is a client-rendered SPA). If FIFA's in-app FAQ disagrees,
trust the app and fix the numbers here.
"""
from __future__ import annotations

# ---------------------------------------------------------------- positions
GK, DEF, MID, FWD = "GK", "DEF", "MID", "FWD"
POSITIONS = (GK, DEF, MID, FWD)

SQUAD_SHAPE = {GK: 2, DEF: 5, MID: 5, FWD: 3}   # 15 players
SQUAD_SIZE = 15
XI_SIZE = 11

# Valid starting-XI formations: (DEF, MID, FWD); always exactly 1 GK.
FORMATIONS = [(4, 4, 2), (4, 3, 3), (4, 5, 1), (3, 4, 3),
              (3, 5, 2), (5, 4, 1), (5, 3, 2)]

BUDGET = 105.0            # $m from Round of 32 onward (was 100.0 group stage)

# ------------------------------------------------------------------- rounds
# Knockout round keys, in order.
ROUNDS = ["R32", "R16", "QF", "SF", "FINAL"]

FREE_TRANSFERS = {"R32": None, "R16": 4, "QF": 4, "SF": 5, "FINAL": 6}  # None = unlimited
EXTRA_TRANSFER_COST = -3   # points per transfer beyond the free allocation

MAX_PER_COUNTRY = {"R32": 3, "R16": 4, "QF": 5, "SF": 6, "FINAL": 8}

# ------------------------------------------------------------------ scoring
CAPTAIN_MULTIPLIER = 2

SCORING = {
    "appearance": 1,          # any minutes played
    "assist": 3,
    "penalty_won": 2,
    "penalty_conceded": -1,
    "yellow": -1,
    "red": -2,
    "own_goal": -2,
    "goal": {GK: 9, DEF: 7, MID: 6, FWD: 5},
    "clean_sheet": {GK: 5, DEF: 5, MID: 1, FWD: 0},   # requires 60+ min
    "goal_conceded_after_first": {GK: -1, DEF: -1, MID: 0, FWD: 0},
    "penalty_save": 3,        # GK
    "per_3_saves": 1,         # GK
    "per_3_tackles": 1,       # MID
    "per_2_chances_created": 1,  # MID
    "per_2_shots_on_target": 1,  # FWD
    "direct_free_kick_goal_bonus": 1,
    # +2 if player scores 4+ pts in a match AND is owned by <5% of managers
    "differential_bonus": 2,
    "differential_ownership_max": 5.0,   # percent
    "differential_min_points": 4,
}

# ----------------------------------------------------------------- boosters
BOOSTERS = {
    "wildcard":       "Unlimited free transfers this round (not usable in R32).",
    "12th_man":       "Extra player this round; scores points; no captain/sub/limits.",
    "max_captain":    "Captaincy auto-assigned to your highest scorer this round.",
    "qualification":  "+2 per starter (1+ min) whose country advances (R32 onward).",
    "cs_shield":      "Mystery booster: clean sheet survives the first goal conceded.",
}
ONE_BOOSTER_PER_ROUND = True


def valid_formation(n_def: int, n_mid: int, n_fwd: int) -> bool:
    return (n_def, n_mid, n_fwd) in FORMATIONS


def transfer_hit(n_transfers: int, round_key: str) -> int:
    """Points penalty for making n_transfers in the given round (0 or negative)."""
    free = FREE_TRANSFERS[round_key]
    if free is None or n_transfers <= free:
        return 0
    return EXTRA_TRANSFER_COST * (n_transfers - free)


def validate_squad(players: list, round_key: str) -> list[str]:
    """Return a list of rule-violation strings for a 15-player squad (empty = valid)."""
    problems = []
    if len(players) != SQUAD_SIZE:
        problems.append(f"squad has {len(players)} players, needs {SQUAD_SIZE}")
    for pos, need in SQUAD_SHAPE.items():
        have = sum(1 for p in players if p.position == pos)
        if have != need:
            problems.append(f"{pos}: have {have}, need {need}")
    cost = sum(p.price for p in players)
    if cost > BUDGET + 1e-9:
        problems.append(f"cost ${cost:.1f}m exceeds budget ${BUDGET:.1f}m")
    cap = MAX_PER_COUNTRY[round_key]
    from collections import Counter
    for country, n in Counter(p.country_abbr for p in players).items():
        if n > cap:
            problems.append(f"{n} players from {country} (max {cap} in {round_key})")
    return problems
