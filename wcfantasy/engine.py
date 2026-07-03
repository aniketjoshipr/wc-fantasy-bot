"""Glue: load state -> score -> optimize -> recommendation dict."""
from __future__ import annotations

from pathlib import Path

from . import optimize, predict, rules
from .fetch import load_state
from .models import DATA_DIR, load_config, load_json

STAGE_TO_KEY = {"R32": "R32", "R16": "R16", "QF": "QF", "SF": "SF", "F": "FINAL"}


def load_my_squad(state, data_dir: Path = DATA_DIR) -> dict:
    sq = load_json(data_dir / "squad.json")
    if not sq:
        raise SystemExit("data/squad.json missing — seed it with your 15 player ids")
    missing = [pid for pid in sq["player_ids"] if pid not in state.players]
    if missing:
        raise SystemExit(f"squad ids not in players feed: {missing}")
    return sq


def recommend(data_dir: Path = DATA_DIR, offline: bool = False, stage: str | None = None) -> tuple[dict, object]:
    cfg = load_config(data_dir)
    state = load_state(data_dir, offline=offline)
    rnd = state.round_by_stage(stage) if stage else state.next_scheduled_round() or state.current_round()
    round_key = STAGE_TO_KEY.get(rnd["stage"], rnd["stage"])

    sq = load_my_squad(state, data_dir)
    squad = [state.players[pid] for pid in sq["player_ids"]]
    bank = round(rules.BUDGET - sum(p.price for p in squad), 1)

    ep = predict.score_pool(state, rnd["id"], cfg, data_dir)
    plans = optimize.search_transfers(state, squad, ep, round_key, bank, cfg)

    # wildcard comparison: unlimited transfers
    wildcard_plan = None
    if sq.get("boosters_available", {}).get("wildcard"):
        wc_plans = optimize.search_transfers(state, squad, ep, round_key, bank, cfg, free_override=None)
        wildcard_plan = wc_plans[0] if wc_plans else None

    boosters = optimize.booster_advice(state, squad, ep, round_key,
                                       sq.get("boosters_available", {}), plans[0], wildcard_plan)

    problems = rules.validate_squad(squad, round_key)
    note = (f"form({cfg['weights']['form_component']}) + stats({cfg['weights']['stat_component']}), "
            f"Elo-Poisson opponent model; edit data/elo.json + data/news.json to steer it.")
    rec = {
        "round": rnd, "round_key": round_key,
        "free_transfers": rules.FREE_TRANSFERS[round_key],
        "squad": squad, "bank": bank, "ep": ep,
        "plans": plans, "wildcard_plan": wildcard_plan,
        "boosters": boosters, "problems": problems,
        "model_note": note,
    }
    return rec, state
