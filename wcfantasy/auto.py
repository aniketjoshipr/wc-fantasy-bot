"""Self-scheduling entrypoint — run from cron every ~20 min; it decides what matters:

- ~30h before a round's deadline: full recommendation email ("plan your transfers")
- ~8h before: refresh news (headless claude, best-effort) + final recommendation email
- while a round is live: sub-alert email only when a starter's match ended without them playing
- after rounds complete: settle predicted-vs-actual and include it in the next email

State lives in data/state.json so nothing is sent twice.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import engine, live as live_mod, report, tracker
from .fetch import load_state
from .models import DATA_DIR, load_json, save_json

EARLY_H, FINAL_H = 30.0, 8.0
BASE = Path(__file__).resolve().parent.parent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hours_until(iso: str) -> float:
    return (datetime.fromisoformat(iso) - _now()).total_seconds() / 3600.0


def _update_news() -> None:
    script = BASE / "bin" / "update-news.sh"
    try:
        subprocess.run([str(script)], timeout=600, check=False)
    except Exception as e:
        print(f"[auto] news update skipped: {e}")


def _send_recommendation(stage_label: str, settled: list[str], email: bool) -> None:
    rec, state = engine.recommend(offline=True)   # feeds were just refreshed
    sq = load_json(DATA_DIR / "squad.json")
    tracker.log_prediction(state, sq, rec["ep"], rec)
    text = report.render_cli(rec, state)
    print(text)
    report.save_report(text, rec["round"]["stage"])
    html_path = report.render_html(rec, state, BASE / "dashboard" / "index.html")
    extra = ""
    if settled:
        extra = "\n\nPredicted vs actual (newly settled):\n  " + "\n  ".join(settled)
    extra += "\n\n" + tracker.comparison_table()
    if email:
        best = rec["plans"][0]
        subj = (f"WC Fantasy {rec['round']['stage']} [{stage_label}] — "
                f"{len(best.out_ids)} transfers, net EP {best.net_ep} — "
                f"deadline {rec['round']['startDate']}")
        report.email_send(subj, report.strip_ansi(text) + extra, html_path.read_text())
        report.telegram_send(report.strip_ansi(text)[:3500])


def run(email: bool = True) -> None:
    state = load_state(max_age_min=15)            # refreshes feeds if stale
    st = load_json(DATA_DIR / "state.json", {}) or {}
    settled = tracker.settle(state)
    if settled:
        print("[auto] settled:", *settled, sep="\n  ")

    # ---- pre-deadline recommendations ----
    rnd = state.next_scheduled_round()
    if rnd is not None:
        rid, h = str(rnd["id"]), _hours_until(rnd["startDate"])
        sent = st.setdefault("sent", {}).setdefault(rid, [])
        if 0 < h <= FINAL_H and "final" not in sent:
            print(f"[auto] {rnd['stage']} deadline in {h:.1f}h — FINAL recommendation")
            _update_news()
            _send_recommendation("FINAL CALL", settled, email)
            sent.append("final")
        elif 0 < h <= EARLY_H and "early" not in sent:
            print(f"[auto] {rnd['stage']} deadline in {h:.1f}h — early recommendation")
            _send_recommendation("early look", settled, email)
            sent.append("early")
        else:
            print(f"[auto] {rnd['stage']} deadline in {h:.1f}h — nothing due")

    # ---- live sub alerts ----
    playing = any(r["status"] == "playing" for r in state.rounds)
    if playing:
        out = live_mod.live_check(offline=True)
        if "!!" in out:
            sig = str(hash(out))
            if st.get("last_live_sig") != sig:
                print(out)
                if email:
                    report.email_send("WC Fantasy LIVE — starter didn't play", out)
                    report.telegram_send(report.strip_ansi(out))
                st["last_live_sig"] = sig
            else:
                print("[auto] live issue already alerted")
        else:
            print("[auto] live check clean")
    elif settled and email:
        report.email_send("WC Fantasy — round settled: predicted vs actual",
                          "\n".join(settled) + "\n\n" + tracker.comparison_table())

    save_json(DATA_DIR / "state.json", st)
