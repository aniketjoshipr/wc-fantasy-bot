"""Rendering & notifications: terminal report, HTML dashboard, Telegram."""
from __future__ import annotations

import html
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BOLD, DIM, GRN, YEL, RED, CYN, END = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[31m", "\033[36m", "\033[0m"


def _p(state, pid):
    return state.players[pid]


def fmt_player(p, ep_map, mark="") -> str:
    b = ep_map.get(p.id, {})
    ep = b.get("ep", 0.0)
    warn = ""
    if (p.news or {}).get("status") in ("out", "doubt"):
        warn = f" {RED}[{p.news['status'].upper()}]{END}"
    return (f"{p.position:>3} {p.name:<24.24} {p.country_abbr} ${p.price:<4} "
            f"EP {GRN}{ep:>4.1f}{END} vs {b.get('opp','?'):<3} own {p.ownership:>4.1f}%{warn}{mark}")


def render_cli(rec: dict, state) -> str:
    """rec: output of engine.recommend()."""
    ep = rec["ep"]
    lines = []
    rnd = rec["round"]
    lines.append(f"{BOLD}{CYN}=== FIFA Fantasy — {rnd['stage']} recommendation ==={END}")
    lines.append(f"Deadline (first kickoff): {YEL}{rnd['startDate']}{END}   free transfers: {rec['free_transfers']}")
    lines.append("")

    lines.append(f"{BOLD}-- Current squad (EP this round) --{END}")
    for p in sorted(rec["squad"], key=lambda p: ("GK DEF MID FWD".split().index(p.position), -ep.get(p.id, {}).get("ep", 0))):
        dead = f" {RED}ELIMINATED{END}" if not p.alive else ""
        lines.append("  " + fmt_player(p, ep) + dead)
    lines.append("")

    lines.append(f"{BOLD}-- Transfer plans (net EP = XI+captain EP + hit + 0.15x bench) --{END}")
    for i, pl in enumerate(rec["plans"][:5]):
        outs = ", ".join(_p(state, o).name for o in pl.out_ids) or "—"
        ins = ", ".join(_p(state, o).name for o in pl.in_ids) or "—"
        tag = f"{GRN}<< RECOMMENDED{END}" if i == 0 else ""
        lines.append(f"  [{i}] {len(pl.out_ids)} transfer(s), hit {pl.hit:+d}, net EP {BOLD}{pl.net_ep:5.1f}{END}  bank ${pl.bank_after:.1f}m {tag}")
        if pl.out_ids:
            lines.append(f"       OUT: {RED}{outs}{END}")
            lines.append(f"       IN : {GRN}{ins}{END}")
    lines.append("")

    best = rec["plans"][0]
    xi = best.xi
    lines.append(f"{BOLD}-- Best XI after plan [0] — {xi.formation[0]}-{xi.formation[1]}-{xi.formation[2]} --{END}")
    for p in xi.starters:
        mark = ""
        if p.id == xi.captain.id:
            mark = f" {YEL}(C){END}"
        elif p.id == xi.vice.id:
            mark = f" {YEL}(VC){END}"
        lines.append("  " + fmt_player(p, ep, mark))
    lines.append(f"  {DIM}bench: " + ", ".join(f"{i+1}.{b.name}" for i, b in enumerate(xi.bench))
                 + (f"  GK: {xi.bench_gk.name}" if xi.bench_gk else "") + END)
    lines.append("")

    lines.append(f"{BOLD}-- Booster advice --{END}")
    for a in rec["boosters"]:
        lines.append(f"  * {a}")
    lines.append("")
    lines.append(f"{DIM}Model: {rec['model_note']}{END}")
    return "\n".join(lines)


def strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


# ------------------------------------------------------------------ dashboard

def render_html(rec: dict, state, out_path: Path) -> Path:
    ep = rec["ep"]
    rnd = rec["round"]
    best = rec["plans"][0]

    def row(p, mark=""):
        b = ep.get(p.id, {})
        news = (p.news or {}).get("status", "")
        cls = {"out": "out", "doubt": "doubt"}.get(news, "")
        return (f"<tr class='{cls}'><td>{p.position}</td><td>{html.escape(p.name)}{mark}</td>"
                f"<td>{p.country_abbr}</td><td>${p.price}</td><td>{b.get('ep', 0):.1f}</td>"
                f"<td>{b.get('opp', '?')}</td><td>{p.ownership:.1f}%</td>"
                f"<td>{html.escape((p.news or {}).get('note', ''))}</td></tr>")

    plans_html = ""
    for i, pl in enumerate(rec["plans"][:5]):
        outs = ", ".join(html.escape(_p(state, o).name) for o in pl.out_ids) or "&mdash;"
        ins = ", ".join(html.escape(_p(state, o).name) for o in pl.in_ids) or "&mdash;"
        plans_html += (f"<tr{' class=best' if i == 0 else ''}><td>{i}</td><td>{len(pl.out_ids)}</td>"
                       f"<td>{pl.hit:+d}</td><td><b>{pl.net_ep:.1f}</b></td>"
                       f"<td class='out-td'>{outs}</td><td class='in-td'>{ins}</td><td>${pl.bank_after:.1f}m</td></tr>")

    xi = best.xi
    xi_rows = "".join(row(p, " <b>(C)</b>" if p.id == xi.captain.id else (" (VC)" if p.id == xi.vice.id else ""))
                      for p in xi.starters)
    squad_rows = "".join(row(p) for p in sorted(rec["squad"], key=lambda p: -ep.get(p.id, {}).get("ep", 0)))
    boosters = "".join(f"<li>{html.escape(a)}</li>" for a in rec["boosters"])
    top_pool = sorted((p for p in state.players.values() if p.alive),
                      key=lambda p: -ep.get(p.id, {}).get("ep", 0))[:25]
    pool_rows = "".join(row(p) for p in top_pool)

    doc = f"""<title>WC Fantasy — {rnd['stage']}</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:1000px;margin:1rem auto;padding:0 1rem}}
 table{{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem;font-size:.9rem}}
 td,th{{border-bottom:1px solid #8884;padding:.25rem .5rem;text-align:left}}
 .best{{background:#2e7d3222}} .out{{opacity:.45;text-decoration:line-through}} .doubt{{background:#ff980022}}
 .out-td{{color:#c62828}} .in-td{{color:#2e7d32}} h2{{margin-top:1.5rem}}
 .deadline{{background:#c6282822;padding:.5rem 1rem;border-radius:8px;display:inline-block}}
</style>
<h1>FIFA Fantasy — {rnd['stage']} plan</h1>
<p class=deadline><b>Deadline (first kickoff):</b> {rnd['startDate']} &nbsp;|&nbsp; free transfers: {rec['free_transfers']}</p>
<h2>Transfer plans</h2>
<table><tr><th>#</th><th>Transfers</th><th>Hit</th><th>Net EP</th><th>Out</th><th>In</th><th>Bank</th></tr>{plans_html}</table>
<h2>Best XI (plan 0) — {xi.formation[0]}-{xi.formation[1]}-{xi.formation[2]}</h2>
<table><tr><th>Pos</th><th>Player</th><th>Team</th><th>Price</th><th>EP</th><th>Opp</th><th>Own</th><th>News</th></tr>{xi_rows}</table>
<p><b>Bench:</b> {", ".join(f"{i+1}. {html.escape(b.name)}" for i, b in enumerate(xi.bench))}
 {('&nbsp; GK: ' + html.escape(xi.bench_gk.name)) if xi.bench_gk else ''}</p>
<h2>Booster advice</h2><ul>{boosters}</ul>
<h2>Current squad EP</h2>
<table><tr><th>Pos</th><th>Player</th><th>Team</th><th>Price</th><th>EP</th><th>Opp</th><th>Own</th><th>News</th></tr>{squad_rows}</table>
<h2>Top 25 of the whole pool</h2>
<table><tr><th>Pos</th><th>Player</th><th>Team</th><th>Price</th><th>EP</th><th>Opp</th><th>Own</th><th>News</th></tr>{pool_rows}</table>
<p><small>Generated {datetime.now(timezone.utc).isoformat(timespec='minutes')} — {html.escape(rec['model_note'])}</small></p>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc)
    return out_path


# ------------------------------------------------------------------ email

def email_send(subject: str, text: str, html: str | None = None) -> bool:
    """Send via any SMTP relay (defaults tuned for Brevo's free tier).

    Env (see ~/.config/wcfantasy.env):
      SMTP_HOST (default smtp-relay.brevo.com), SMTP_PORT (587),
      SMTP_USER, SMTP_PASSWORD, EMAIL_FROM (a Brevo-verified sender), EMAIL_TO.
    Port 465 uses implicit SSL; anything else uses STARTTLS.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    host = os.environ.get("SMTP_HOST", "smtp-relay.brevo.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("EMAIL_FROM", user)
    to = os.environ.get("EMAIL_TO", sender)
    if not user or not pw or not to:
        print("[email] SMTP_USER / SMTP_PASSWORD / EMAIL_TO not set — skipping")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, sender, to
    msg.attach(MIMEText(strip_ansi(text), "plain"))
    if html:
        msg.attach(MIMEText(html, "html"))
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        with server as s:
            s.login(user, pw)
            s.sendmail(sender, [to], msg.as_string())
        return True
    except Exception as e:
        print(f"[email] send failed: {e}")
        return False


# ------------------------------------------------------------------ archive

def save_report(text: str, stage: str) -> Path:
    """Archive every generated recommendation under reports/ (plain text)."""
    reports = Path(__file__).resolve().parent.parent / "reports"
    reports.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
    path = reports / f"{stage}-{stamp}.txt"
    path.write_text(strip_ansi(text))
    (reports / "latest.txt").write_text(strip_ansi(text))
    return path


# ------------------------------------------------------------------ telegram

def telegram_send(text: str) -> bool:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000]}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        print(f"[telegram] send failed: {e}")
        return False
