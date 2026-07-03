"""Optimizer: best XI + captain, transfer beam search, booster advisor."""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from . import rules
from .models import GameState, Player


@dataclass
class XI:
    formation: tuple            # (DEF, MID, FWD)
    starters: list              # [Player] 11, GK first
    bench: list                 # [Player] outfield bench in priority order
    bench_gk: Player
    captain: Player
    vice: Player
    ep: float                   # XI EP incl captain double


@dataclass
class TransferPlan:
    out_ids: list = field(default_factory=list)
    in_ids: list = field(default_factory=list)
    hit: int = 0                # 0 or negative
    xi: XI = None
    net_ep: float = 0.0         # xi.ep + hit
    bank_after: float = 0.0


def best_xi(squad: list[Player], ep: dict[int, dict]) -> XI:
    """Pick the highest-EP legal XI from a 15-man squad."""
    def e(p):  # noqa: E731
        return ep.get(p.id, {}).get("ep", 0.0)

    by_pos = {pos: sorted([p for p in squad if p.position == pos], key=e, reverse=True)
              for pos in rules.POSITIONS}
    best = None
    for d, m, f in rules.FORMATIONS:
        if len(by_pos["DEF"]) < d or len(by_pos["MID"]) < m or len(by_pos["FWD"]) < f:
            continue
        starters = [by_pos["GK"][0]] + by_pos["DEF"][:d] + by_pos["MID"][:m] + by_pos["FWD"][:f]
        total = sum(e(p) for p in starters)
        if best is None or total > best[0]:
            best = (total, (d, m, f), starters)
    total, formation, starters = best
    outfield = sorted(starters[1:], key=e, reverse=True)
    captain, vice = outfield[0], outfield[1]
    # captain doubles
    total += e(captain)
    bench = sorted([p for p in squad if p not in starters and p.position != "GK"], key=e, reverse=True)
    bench_gk = by_pos["GK"][1] if len(by_pos["GK"]) > 1 else None
    return XI(formation, starters, bench, bench_gk, captain, vice, round(total, 2))


def _country_counts(squad: list[Player]) -> dict[str, int]:
    c: dict[str, int] = {}
    for p in squad:
        c[p.country_abbr] = c.get(p.country_abbr, 0) + 1
    return c


def search_transfers(state: GameState, squad: list[Player], ep: dict[int, dict],
                     round_key: str, bank: float, cfg: dict,
                     free_override: int | None = None) -> list[TransferPlan]:
    """Beam search over transfer combinations. Returns plans ranked by net EP
    (includes the 0-transfer baseline)."""
    def e(p):  # noqa: E731
        return ep.get(p.id, {}).get("ep", 0.0)

    free = free_override if free_override is not None else rules.FREE_TRANSFERS[round_key]
    unlimited = free is None
    max_t = 15 if unlimited else min(free + cfg["max_extra_transfers"], 8)
    cap = rules.MAX_PER_COUNTRY[round_key]
    beam_width = cfg["beam_width"]
    n_cands = cfg["swap_candidates"]

    squad_ids = {p.id for p in squad}
    # replacement candidates per position: alive, not mine, best EP first
    pool = {pos: sorted([p for p in state.players.values()
                         if p.alive and p.position == pos and p.id not in squad_ids],
                        key=e, reverse=True)[: n_cands * 3]
            for pos in rules.POSITIONS}

    bench_w = cfg.get("bench_weight", 0.15)

    def plan_of(current: list[Player], outs: list[int], ins: list[int]) -> TransferPlan:
        n = len(outs)
        hit = 0 if unlimited else rules.transfer_hit(n, round_key)
        xi = best_xi(current, ep)
        bank_after = bank + sum(state.players[o].price for o in outs) - sum(state.players[i].price for i in ins)
        # bench players matter ~15%: auto-sub insurance + future rounds
        bench_ep = sum(e(b) for b in xi.bench) + (e(xi.bench_gk) if xi.bench_gk else 0.0)
        return TransferPlan(outs, ins, hit, xi, round(xi.ep + hit + bench_w * bench_ep, 2), round(bank_after, 2))

    baseline = plan_of(squad, [], [])
    beam = [(squad, [], [], bank)]
    results = {frozenset(): baseline}

    for _depth in range(max_t):
        nxt = []
        for cur_squad, outs, ins, cur_bank in beam:
            cur_counts = _country_counts(cur_squad)
            # heuristic: try removing the weakest players first
            for out_p in sorted(cur_squad, key=e)[:8]:
                if out_p.id in ins:
                    continue
                budget = cur_bank + out_p.price
                counts = dict(cur_counts)
                counts[out_p.country_abbr] -= 1
                tried = 0
                for in_p in pool[out_p.position]:
                    if in_p.id in {p.id for p in cur_squad}:
                        continue
                    if in_p.price > budget + 1e-9:
                        continue
                    if counts.get(in_p.country_abbr, 0) + 1 > cap:
                        continue
                    if e(in_p) <= e(out_p):
                        continue
                    new_squad = [p for p in cur_squad if p.id != out_p.id] + [in_p]
                    new_outs, new_ins = outs + [out_p.id], ins + [in_p.id]
                    key = frozenset(zip(sorted(new_outs), sorted(new_ins)))
                    if key not in results:
                        results[key] = plan_of(new_squad, new_outs, new_ins)
                        nxt.append((new_squad, new_outs, new_ins, cur_bank + out_p.price - in_p.price))
                    tried += 1
                    if tried >= n_cands:
                        break
        if not nxt:
            break
        nxt.sort(key=lambda s: results[frozenset(zip(sorted(s[1]), sorted(s[2])))].net_ep, reverse=True)
        beam = nxt[:beam_width]

    plans = sorted(results.values(), key=lambda pl: pl.net_ep, reverse=True)
    return plans


def booster_advice(state: GameState, squad: list[Player], ep: dict[int, dict],
                   round_key: str, boosters_available: dict, best_plan: TransferPlan,
                   wildcard_plan: TransferPlan | None) -> list[str]:
    """Heuristic booster recommendations with the numbers behind them."""
    def e(p):  # noqa: E731
        return ep.get(p.id, {}).get("ep", 0.0)

    advice = []
    xi = best_plan.xi

    if boosters_available.get("qualification"):
        ev = sum(ep.get(p.id, {}).get("advance_prob", 0.5) * ep.get(p.id, {}).get("p_play", 0) * 2
                 for p in xi.starters)
        advice.append(f"Qualification Booster EV this round: +{ev:.1f} pts "
                      f"(2 x P(advance) per starter). Rounds left shrink its ceiling — "
                      f"{'use it in ' + round_key if round_key in ('R16', 'QF') else 'consider soon'}.")

    if boosters_available.get("cs_shield"):
        defensive = [p for p in xi.starters if p.position in ("GK", "DEF")]
        ev = sum(ep.get(p.id, {}).get("mu_against", 1.3) for p in defensive)
        adds = sum((__import__('math').exp(-b) * b) * rules.SCORING["clean_sheet"][p.position]
                   for p, b in [(p, ep.get(p.id, {}).get("mu_against", 1.3)) for p in defensive])
        advice.append(f"Clean Sheet Shield EV: ~+{adds:.1f} pts across your {len(defensive)} "
                      f"GK/DEF starters (P(exactly 1 conceded) x CS pts). Best on a defense-heavy XI.")

    if boosters_available.get("max_captain") and round_key != "FINAL":
        top2 = sorted((e(p) for p in xi.starters), reverse=True)[:2]
        gap = top2[0] - top2[1] if len(top2) == 2 else 0
        advice.append(f"Maximum Captain: hold for the FINAL unless desperate — current captain "
                      f"choice is {'clear' if gap > 1.5 else 'close'} (EP gap {gap:.1f}).")
    if boosters_available.get("wildcard") and wildcard_plan:
        gain = max(0.0, wildcard_plan.net_ep - best_plan.net_ep)
        advice.append(f"Wildcard: unlimited rebuild would add ~{gain:.1f} EP over the best "
                      f"free-transfer plan this round. "
                      f"{'Worth it now.' if gain >= 6 else 'Hold (QF/SF often better after surprises).'}")
    if boosters_available.get("12th_man"):
        pool_best = max((p for p in state.players.values() if p.alive), key=e)
        advice.append(f"12th Man: best add would be {pool_best.name} (~{e(pool_best):.1f} EP). "
                      f"Usually strongest in SF/Final when your bench is thin.")
    advice.append("Reminder: only ONE booster per round.")
    return advice
