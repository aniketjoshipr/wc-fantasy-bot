"""Rule-based expected-points (EP) model for one round.

EP per player = blend of two signals, both adjusted for opponent strength (Elo):

1. FORM component — FIFA's own `form` (recent fantasy pts/round), scaled by how
   much easier/harder the next opponent is than an average one.
2. STAT component — built bottom-up from scoring rules:
   expected team goals (Elo -> Poisson) x player's goal/assist share, clean-sheet
   probability for defensive returns, GK saves, appearance pts, card risk,
   differential (<5% owned) bonus EV.

Everything is multiplied by p(plays), inferred from FIFA's matchStatus lineup
signal and overridden by data/news.json (injury/suspension/sentiment layer,
which the `update-news` job maintains).

Elo ratings live in data/elo.json (seeded from eloratings.net; editable).
"""
from __future__ import annotations

import math
from pathlib import Path

from . import rules
from .models import DATA_DIR, GameState, Player, load_json

# p(plays any minutes) and expected minutes by FIFA lineup signal
MATCH_STATUS_PRIOR = {
    "start": (0.92, 88),
    "sub": (0.60, 30),
    None: (0.40, 30),
    "not_in_squad": (0.08, 10),
}
NEWS_P_PLAY = {"out": 0.0, "doubt": 0.45, "fit": None, "nailed": 0.95}  # None = keep prior

CARD_RISK_EV = {"GK": -0.05, "DEF": -0.18, "MID": -0.15, "FWD": -0.12}
DEFAULT_ELO = 1750.0
MEAN_ELO_ALIVE = None  # computed per-call


def load_elo(data_dir: Path = DATA_DIR) -> dict[str, float]:
    """abbr -> elo."""
    return {k: float(v) for k, v in (load_json(data_dir / "elo.json", {}) or {}).items()
            if not k.startswith("_")}


def team_elo(elo: dict, abbr: str) -> float:
    return elo.get(abbr, DEFAULT_ELO)


def expected_goals(elo_for: float, elo_against: float, cfg: dict) -> float:
    """Poisson mean for a team in a knockout match, from Elo difference."""
    w = cfg["weights"]
    return w["base_mu"] * (10 ** ((elo_for - elo_against) / w["elo_scale"]))


def advance_prob(elo_for: float, elo_against: float) -> float:
    """P(team advances) — Elo expected score works well incl. pens coin-flippiness."""
    return 1.0 / (1.0 + 10 ** ((elo_against - elo_for) / 400.0))


def _poisson_pmf(k: int, mu: float) -> float:
    return math.exp(-mu) * mu ** k / math.factorial(k)


def _expected_conceded_after_first(mu: float) -> float:
    """E[max(X-1, 0)] for X ~ Poisson(mu)."""
    return sum(_poisson_pmf(k, mu) * (k - 1) for k in range(2, 12))


def p_play_and_minutes(p: Player) -> tuple[float, float, str]:
    """Returns (p_play, exp_minutes, reason)."""
    p_play, mins = MATCH_STATUS_PRIOR.get(p.match_status, (0.40, 30))
    reason = f"lineup:{p.match_status}"
    if p.match_status is None:
        # no lineup signal (match not imminent) — infer from form/output
        if p.form >= 4:
            p_play, mins = 0.87, 82
        elif p.form >= 2:
            p_play, mins = 0.55, 45
        reason = f"form-prior({p.form})"
    ns = (p.news or {}).get("status")
    if ns in NEWS_P_PLAY:
        override = NEWS_P_PLAY[ns]
        if ns == "out":
            return 0.0, 0.0, f"news:out ({p.news.get('note', '')})"
        if override is not None:
            p_play = override if ns == "doubt" else max(p_play, override)
            reason = f"news:{ns}"
    if "p_play" in (p.news or {}):
        p_play = float(p.news["p_play"])
        reason = "news:p_play"
    return p_play, mins, reason


def pending_context(state: GameState, squad_id: int, round_id: int, elo: dict) -> tuple[float, float, str]:
    """Team is alive but has no fixture in round_id yet (bracket slot TBD because
    an earlier match hasn't finished). Returns (p_reach, est_opp_elo, opp_label).

    p_reach: 1.0 if the team has already won through, else Elo advance prob of
    their unfinished current-round match. Opponent Elo is approximated as the
    p_reach-weighted mean of the other alive-but-undrawn teams — coarse, but the
    real fixture lands in the feed as soon as tonight's games end.
    """
    cur = state.current_round()
    p_reach, own_opp = 1.0, None
    fx = state.fixture_for_team(squad_id, cur["id"]) if cur["id"] != round_id else None
    if fx is not None and fx.status != "complete":
        own_opp = fx.opponent_of(squad_id)
        own_abbr = state.team_map.get(squad_id, ("?", "?"))[1]
        opp_abbr = state.team_map.get(own_opp, ("?", "?"))[1]
        p_reach = advance_prob(team_elo(elo, own_abbr), team_elo(elo, opp_abbr))

    drawn = {t for f in state.fixtures_for_round(round_id) for t in (f.home_id, f.away_id)}
    alive = {pl.squad_id for pl in state.players.values() if pl.alive}
    pool = alive - drawn - {squad_id} - ({own_opp} if own_opp else set())
    cand = []
    for tid in pool:
        abbr = state.team_map.get(tid, ("?", "?"))[1]
        tfx = state.fixture_for_team(tid, cur["id"])
        w = 1.0
        if tfx is not None and tfx.status != "complete":
            o = state.team_map.get(tfx.opponent_of(tid), ("?", "?"))[1]
            w = advance_prob(team_elo(elo, abbr), team_elo(elo, o))
        cand.append((team_elo(elo, abbr), w, abbr))
    if cand:
        est = sum(e * w for e, w, _ in cand) / max(1e-9, sum(w for _, w, _ in cand))
        label = "TBD(" + "/".join(a for _, _, a in sorted(cand, key=lambda c: -c[1])[:3]) + ")"
    else:
        est, label = sum(elo.values()) / max(1, len(elo)), "TBD"
    return p_reach, est, label


def expected_points(p: Player, state: GameState, elo: dict, round_id: int, cfg: dict) -> dict:
    """Full EP breakdown for player p in the given round. Returns dict with 'ep'."""
    if not p.alive:
        return {"ep": 0.0, "why": "eliminated", "p_play": 0.0}

    fx = state.fixture_for_team(p.squad_id, round_id)
    p_reach = 1.0
    if fx is None:
        p_reach, opp_elo_est, opp_abbr = pending_context(state, p.squad_id, round_id, elo)
        opp_id = None
    else:
        opp_id = fx.opponent_of(p.squad_id)
        opp_abbr = state.team_map.get(opp_id, ("?", "?"))[1]
        opp_elo_est = None
    e_for = team_elo(elo, p.country_abbr)
    e_opp = opp_elo_est if opp_elo_est is not None else team_elo(elo, opp_abbr)
    mu_for = expected_goals(e_for, e_opp, cfg)
    mu_against = expected_goals(e_opp, e_for, cfg)

    p_play, exp_min, availability = p_play_and_minutes(p)
    if p_play <= 0:
        return {"ep": 0.0, "why": availability, "p_play": 0.0, "opp": opp_abbr}

    w = cfg["weights"]
    played_60 = min(1.0, exp_min / 60.0)

    # ---------------- form component -----------------------------------
    # opponent factor: >1 vs weak teams, <1 vs strong; anchored on mean alive elo
    mean_elo = sum(team_elo(elo, a) for a in elo) / max(1, len(elo))
    opp_factor = 1.0 + w["form_opponent_pull"] * (mean_elo - e_opp) / 400.0
    form_ep = max(0.0, p.form) * opp_factor

    # ---------------- stat component ------------------------------------
    matches_played = max(1, len([v for v in p.round_points.values() if v is not None]))
    # smoothed per-match goal/assist rates (prior keeps unproven players sane)
    prior_g = {"GK": 0.0, "DEF": 0.05, "MID": 0.12, "FWD": 0.30}[p.position]
    prior_a = {"GK": 0.0, "DEF": 0.06, "MID": 0.15, "FWD": 0.15}[p.position]
    g_rate = (p.goals + prior_g * 2) / (matches_played + 2)
    a_rate = (p.assists + prior_a * 2) / (matches_played + 2)
    # scale by how attacking this fixture is vs tournament average (~1.3 goals)
    atk_scale = mu_for / w["base_mu"]

    ep_goals = g_rate * atk_scale * rules.SCORING["goal"][p.position]
    ep_assists = a_rate * atk_scale * rules.SCORING["assist"]
    cs_prob = math.exp(-mu_against)
    ep_cs = cs_prob * rules.SCORING["clean_sheet"][p.position] * played_60
    ep_concede = rules.SCORING["goal_conceded_after_first"][p.position] * _expected_conceded_after_first(mu_against)
    ep_saves = 0.0
    if p.position == "GK":
        exp_saves = mu_against * 2.0  # ~3 SoT per xG faced, ~2/3 saved
        ep_saves = exp_saves / 3.0 * rules.SCORING["per_3_saves"]
    ep_appear = rules.SCORING["appearance"]
    stat_ep = ep_goals + ep_assists + ep_cs + ep_concede + ep_saves + ep_appear + CARD_RISK_EV[p.position]

    # ---------------- blend + extras -------------------------------------
    ep = w["form_component"] * form_ep + w["stat_component"] * stat_ep
    ep *= p_play * p_reach

    diff_ev = 0.0
    if p.ownership < rules.SCORING["differential_ownership_max"]:
        p_haul = min(0.75, max(0.0, ep / 9.0))  # rough P(4+ pts)
        diff_ev = rules.SCORING["differential_bonus"] * p_haul
        ep += diff_ev

    return {
        "ep": round(ep, 2), "p_play": p_play, "p_reach": round(p_reach, 2), "availability": availability,
        "opp": opp_abbr, "mu_for": round(mu_for, 2), "mu_against": round(mu_against, 2),
        "cs_prob": round(cs_prob, 2), "form_ep": round(form_ep, 2), "stat_ep": round(stat_ep, 2),
        "diff_ev": round(diff_ev, 2),
        "advance_prob": round(advance_prob(e_for, e_opp), 2),
    }


def score_pool(state: GameState, round_id: int, cfg: dict, data_dir: Path = DATA_DIR) -> dict[int, dict]:
    """EP breakdown for every alive player. Returns {player_id: breakdown}."""
    elo = load_elo(data_dir)
    return {pid: expected_points(p, state, elo, round_id, cfg)
            for pid, p in state.players.items()}
