"""Live matchday mode: catch starters who didn't play and suggest manual subs.

Rules recap: during a round you may swap OUT a starter whose match has FINISHED
for a bench player whose match has NOT started. But any manual change disables
auto-subs and the vice-captain fallback for the round — the tool nets that out.
"""
from __future__ import annotations

from pathlib import Path

from .engine import STAGE_TO_KEY, load_my_squad
from .fetch import load_state
from .models import DATA_DIR, load_config
from . import predict


def live_check(data_dir: Path = DATA_DIR, offline: bool = False) -> str:
    cfg = load_config(data_dir)
    state = load_state(data_dir, max_age_min=3, offline=offline)  # tight cache during matches
    rnd = state.current_round()
    if rnd["status"] != "playing":
        return f"No round in play (current: {rnd['stage']} is {rnd['status']}). Nothing to do."
    round_key = STAGE_TO_KEY.get(rnd["stage"], rnd["stage"])
    rid = str(rnd["id"])

    sq = load_my_squad(state, data_dir)
    squad = {pid: state.players[pid] for pid in sq["player_ids"]}
    starters = [p for pid, p in squad.items()
                if pid not in set(sq["bench_order"]) and pid != sq.get("bench_gk")]
    bench = [squad[pid] for pid in sq["bench_order"]]

    ep = predict.score_pool(state, rnd["id"], cfg, data_dir)
    lines = [f"=== LIVE check — {rnd['stage']} ==="]
    problems = []
    for p in starters:
        fx = state.fixture_for_team(p.squad_id, rnd["id"])
        if fx is None:
            continue
        pts = p.round_points.get(rid)
        if fx.status == "complete" and (pts in (None, 0)) and p.match_status in ("not_in_squad", None, "sub"):
            problems.append(p)
            lines.append(f"  !! {p.name} ({p.country_abbr}) match FINISHED with {pts or 0} pts "
                         f"and lineup status '{p.match_status}' — likely didn't play.")
    if not problems:
        lines.append("  All starters fine so far (played, playing, or still to play).")
        return "\n".join(lines)

    # candidate bench swaps: bench players whose match hasn't started
    usable = []
    for b in bench:
        fx = state.fixture_for_team(b.squad_id, rnd["id"])
        if fx is not None and fx.status == "scheduled":
            usable.append((b, ep.get(b.id, {}).get("ep", 0.0), fx))
    usable.sort(key=lambda t: -t[1])

    lines.append("")
    if usable:
        lines.append("  Manual-sub options (bench player whose match hasn't kicked off):")
        for b, e, fx in usable:
            lines.append(f"    -> IN {b.name} ({b.country_abbr}, EP {e:.1f}, plays {fx.home_abbr}-{fx.away_abbr} {fx.date})")
        lines.append("")
        lines.append("  CAUTION: a manual sub disables AUTO-subs and the VC fallback for this round.")
        lines.append("  Auto-sub would fix a 0-minute starter for free IF you make no manual changes —")
        lines.append("  only sub manually when the auto-sub would pick a worse player than the option above,")
        lines.append("  or when your captain is the one who didn't play (VC fallback still needs no manual change).")
    else:
        lines.append("  No bench player still has an unplayed match — auto-subs (if untouched) are your fallback.")
    return "\n".join(lines)
