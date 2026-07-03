"""CLI.

  python3 -m wcfantasy recommend [--stage R16] [--offline] [--notify] [--html]
  python3 -m wcfantasy live [--notify]
  python3 -m wcfantasy squad [--apply-plan N] [--set-captain NAME]
  python3 -m wcfantasy players <query>       # look up ids/EP for any player
  python3 -m wcfantasy fetch                 # force-refresh feeds
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import auto, engine, fetch, live, predict, report, tracker
from .models import DATA_DIR, load_config, load_json, save_json

DASH = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"


def cmd_recommend(args):
    rec, state = engine.recommend(stage=args.stage, offline=args.offline)
    tracker.log_prediction(state, load_json(DATA_DIR / "squad.json"), rec["ep"], rec)
    out = report.render_cli(rec, state)
    print(out)
    report.save_report(out, rec["round"]["stage"])
    if rec["problems"]:
        print("\n!! SQUAD RULE PROBLEMS:", *rec["problems"], sep="\n  - ")
    if args.html or True:  # dashboard is cheap — always write it
        p = report.render_html(rec, state, DASH)
        print(f"\ndashboard: file://{p}")
    if args.notify:
        best = rec["plans"][0]
        outs = ", ".join(state.players[o].name for o in best.out_ids) or "none"
        ins = ", ".join(state.players[o].name for o in best.in_ids) or "none"
        msg = (f"WC Fantasy {rec['round']['stage']} — deadline {rec['round']['startDate']}\n"
               f"Best plan ({len(best.out_ids)} transfers, hit {best.hit}): OUT {outs} | IN {ins}\n"
               f"Captain: {best.xi.captain.name} (VC {best.xi.vice.name}) — net EP {best.net_ep}")
        sent = report.telegram_send(msg)
        print(f"telegram: {'sent' if sent else 'not configured/failed'}")


def cmd_live(args):
    out = live.live_check()
    print(out)
    if args.notify and "!!" in out:
        report.telegram_send(report.strip_ansi(out))


def cmd_fetch(_args):
    refreshed = fetch.refresh(force=True)
    print("refreshed:", refreshed or "nothing")


def cmd_players(args):
    cfg = load_config()
    state = fetch.load_state(offline=True)
    rnd = state.next_scheduled_round() or state.current_round()
    ep = None
    for p in state.find_player(" ".join(args.query)):
        if ep is None:
            ep = predict.score_pool(state, rnd["id"], cfg)
        b = ep.get(p.id, {})
        print(f"id={p.id:5d} {p.position:>3} {p.name:<26.26} {p.country_abbr} ${p.price:<5} "
              f"EP {b.get('ep', 0):4.1f} form {p.form:4.1f} own {p.ownership:4.1f}% "
              f"status={p.status}/{p.match_status} g{p.goals} a{p.assists}")


def cmd_squad(args):
    state = fetch.load_state(offline=True)
    sq = load_json(DATA_DIR / "squad.json")
    if args.apply_plan is not None:
        rec, state = engine.recommend(offline=True)
        plan = rec["plans"][args.apply_plan]
        ids = [pid for pid in sq["player_ids"] if pid not in plan.out_ids] + plan.in_ids
        sq["player_ids"] = ids
        xi = plan.xi
        sq["captain_id"], sq["vice_id"] = xi.captain.id, xi.vice.id
        sq["bench_order"] = [p.id for p in xi.bench]
        if xi.bench_gk:
            sq["bench_gk"] = xi.bench_gk.id
        save_json(DATA_DIR / "squad.json", sq)
        print(f"squad.json updated with plan {args.apply_plan} "
              f"({len(plan.in_ids)} transfers). NOW APPLY THE SAME IN THE APP.")
        return
    if args.set_captain:
        hits = [p for p in state.find_player(args.set_captain) if p.id in sq["player_ids"]]
        if len(hits) != 1:
            sys.exit(f"need exactly 1 match in your squad, got {[p.name for p in hits]}")
        sq["captain_id"] = hits[0].id
        save_json(DATA_DIR / "squad.json", sq)
        print(f"captain -> {hits[0].name}")
        return
    for pid in sq["player_ids"]:
        p = state.players[pid]
        tags = []
        if pid == sq.get("captain_id"):
            tags.append("C")
        if pid == sq.get("vice_id"):
            tags.append("VC")
        if pid in sq.get("bench_order", []) or pid == sq.get("bench_gk"):
            tags.append("bench")
        print(f"id={p.id:5d} {p.position:>3} {p.name:<26.26} {p.country_abbr} ${p.price:<5} "
              f"{p.status:<10} {' '.join(tags)}")


def cmd_auto(args):
    import os
    force = args.force or os.environ.get("WCF_FORCE", "").lower() in ("1", "true")
    auto.run(email=not args.no_email, force=force)


def cmd_compare(_args):
    state = fetch.load_state(offline=True)
    newly = tracker.settle(state)
    if newly:
        print("newly settled:", *newly, sep="\n  ")
    print(tracker.comparison_table())


def cmd_email_test(_args):
    ok = report.email_send("WC Fantasy — test email",
                           "If you can read this, email alerts are working.")
    print("email:", "sent" if ok else
          "FAILED (check SMTP_USER/SMTP_PASSWORD/EMAIL_TO in ~/.config/wcfantasy.env)")


def main():
    ap = argparse.ArgumentParser(prog="wcfantasy")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("auto", help="cron entrypoint: acts only when a deadline/live event is near")
    a.add_argument("--no-email", action="store_true")
    a.add_argument("--force", action="store_true",
                   help="send a fresh recommendation now, even if already alerted")
    a.set_defaults(fn=cmd_auto)

    c = sub.add_parser("compare", help="predicted vs actual score per round")
    c.set_defaults(fn=cmd_compare)

    e = sub.add_parser("email-test", help="send a test email")
    e.set_defaults(fn=cmd_email_test)

    r = sub.add_parser("recommend", help="pre-deadline transfer/captain/booster plan")
    r.add_argument("--stage", help="R16/QF/SF/F (default: next scheduled round)")
    r.add_argument("--offline", action="store_true", help="use cached feeds")
    r.add_argument("--notify", action="store_true", help="push summary to Telegram")
    r.add_argument("--html", action="store_true", help="(always on) write dashboard/index.html")
    r.set_defaults(fn=cmd_recommend)

    l_ = sub.add_parser("live", help="matchday check: starters who didn't play, sub options")
    l_.add_argument("--notify", action="store_true")
    l_.set_defaults(fn=cmd_live)

    f = sub.add_parser("fetch", help="force refresh FIFA feeds")
    f.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("players", help="search the player pool")
    p.add_argument("query", nargs="+")
    p.set_defaults(fn=cmd_players)

    s = sub.add_parser("squad", help="show or update your saved squad")
    s.add_argument("--apply-plan", type=int, help="apply transfer plan N to squad.json")
    s.add_argument("--set-captain")
    s.set_defaults(fn=cmd_squad)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
