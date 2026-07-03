"""Predicted-vs-actual score tracking.

Every `recommend`/`auto` run while a round is still open logs a snapshot of
YOUR saved lineup (squad.json) with its predicted EP, plus the model's best
plan EP. Once the round completes, `settle()` fills in the actual fantasy
points from FIFA's own roundPoints feed so you can judge the model.

Caveat: actual score is computed as sum(roundPoints of your XI) with the
captain doubled (VC doubled instead if the captain blanked). Auto-subs and
manual in-round subs are NOT simulated, so it can differ a little from the
official number on your team page — the drift itself is worth seeing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import DATA_DIR, GameState, load_json, save_json

PRED_FILE = "predictions.json"


def _starters(sq: dict) -> list[int]:
    bench = set(sq.get("bench_order", [])) | {sq.get("bench_gk")}
    return [pid for pid in sq["player_ids"] if pid not in bench]


def log_prediction(state: GameState, sq: dict, ep: dict, rec: dict,
                   data_dir: Path = DATA_DIR) -> None:
    """Snapshot prediction for the round being recommended (only while open)."""
    rnd = rec["round"]
    if rnd["status"] != "scheduled":       # round locked/underway: freeze the log
        return
    preds = load_json(data_dir / PRED_FILE, {}) or {}
    xi = _starters(sq)
    entry = {
        "stage": rnd["stage"],
        "deadline": rnd["startDate"],
        "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "xi_ids": xi,
        "captain_id": sq.get("captain_id"),
        "vice_id": sq.get("vice_id"),
        "player_ep": {str(pid): ep.get(pid, {}).get("ep", 0.0) for pid in sq["player_ids"]},
        "player_names": {str(pid): state.players[pid].name for pid in sq["player_ids"]},
        "predicted_my_xi_ep": round(
            sum(ep.get(pid, {}).get("ep", 0.0) for pid in xi)
            + ep.get(sq.get("captain_id"), {}).get("ep", 0.0), 1),
        "recommended_plan_net_ep": rec["plans"][0].net_ep if rec["plans"] else None,
    }
    prev = preds.get(str(rnd["id"])) or {}
    if "actual" in prev:                   # already settled — never overwrite
        return
    preds[str(rnd["id"])] = entry
    save_json(data_dir / PRED_FILE, preds)


def settle(state: GameState, data_dir: Path = DATA_DIR) -> list[str]:
    """Fill in actuals for completed rounds. Returns newly settled summaries."""
    preds = load_json(data_dir / PRED_FILE, {}) or {}
    news = []
    complete = {str(r["id"]) for r in state.rounds if r["status"] == "complete"}
    for rid, entry in preds.items():
        if rid not in complete or "actual" in entry:
            continue
        pts = {}
        for pid in entry["xi_ids"]:
            p = state.players.get(pid)
            pts[pid] = (p.round_points.get(rid) if p else None)
        base = sum(v or 0 for v in pts.values())
        cap, vice = entry.get("captain_id"), entry.get("vice_id")
        cap_pts = pts.get(cap)
        if cap_pts is not None:
            double, dbl_id = cap_pts, cap
        else:                               # captain blanked -> VC doubled
            double, dbl_id = pts.get(vice) or 0, vice
        actual = base + double
        entry["actual"] = {
            "my_xi_points": actual,
            "doubled_player": entry["player_names"].get(str(dbl_id), str(dbl_id)),
            "per_player": {entry["player_names"].get(str(k), str(k)): v for k, v in pts.items()},
            "settled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        diff = actual - entry["predicted_my_xi_ep"]
        news.append(f"{entry['stage']}: predicted {entry['predicted_my_xi_ep']:.1f}, "
                    f"actual {actual} ({diff:+.1f})")
    if news:
        save_json(data_dir / PRED_FILE, preds)
    return news


def comparison_table(data_dir: Path = DATA_DIR) -> str:
    preds = load_json(data_dir / PRED_FILE, {}) or {}
    if not preds:
        return "No predictions logged yet."
    lines = [f"{'round':<6} {'predicted':>9} {'actual':>7} {'diff':>7}  doubled"]
    tot_p = tot_a = n = 0
    for rid in sorted(preds, key=int):
        e = preds[rid]
        a = e.get("actual")
        if a:
            diff = a["my_xi_points"] - e["predicted_my_xi_ep"]
            lines.append(f"{e['stage']:<6} {e['predicted_my_xi_ep']:>9.1f} {a['my_xi_points']:>7} "
                         f"{diff:>+7.1f}  {a['doubled_player']}")
            tot_p += e["predicted_my_xi_ep"]; tot_a += a["my_xi_points"]; n += 1
        else:
            lines.append(f"{e['stage']:<6} {e['predicted_my_xi_ep']:>9.1f} {'—':>7} {'—':>7}  (pending)")
    if n:
        lines.append(f"{'TOTAL':<6} {tot_p:>9.1f} {tot_a:>7} {tot_a - tot_p:>+7.1f}  ({n} settled)")
    lines.append("note: actual = your logged XI w/ captain double; auto/manual subs not simulated")
    return "\n".join(lines)
