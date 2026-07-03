"""Data layer.

Sources (all free, no auth):
- play.fifa.com/json/fantasy/players.json  — prices, ownership, per-round fantasy pts, form, status
- play.fifa.com/json/fantasy/rounds.json   — rounds, deadlines, fixtures, scores, scorers/assists
- site.api.espn.com .../soccer/fifa.world  — scoreboard + per-match lineups (live mode)

Your own team can't be read without a FIFA login, so it lives in data/squad.json
and you keep it in sync after applying transfers (``squad`` CLI command helps).
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from .models import DATA_DIR, Fixture, GameState, Player, load_json, save_json

FIFA_FEEDS = {
    "players.json": "https://play.fifa.com/json/fantasy/players.json",
    "rounds.json": "https://play.fifa.com/json/fantasy/rounds.json",
}
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) wc-fantasy-helper/1.0"}


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def refresh(data_dir: Path = DATA_DIR, max_age_min: float = 20, force: bool = False) -> list[str]:
    """Re-download FIFA feeds if stale. Returns list of refreshed files."""
    refreshed = []
    for fname, url in FIFA_FEEDS.items():
        path = data_dir / fname
        age_ok = path.exists() and (time.time() - path.stat().st_mtime) < max_age_min * 60
        if age_ok and not force:
            continue
        try:
            raw = _get(url)
            json.loads(raw)  # validate before overwrite
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            refreshed.append(fname)
        except Exception as e:  # keep stale cache on failure
            print(f"[fetch] WARNING: {fname} refresh failed ({e}); using cached copy")
    return refreshed


def _build_fixtures(rounds: list[dict]) -> list[Fixture]:
    out = []
    for r in rounds:
        for m in r.get("tournaments") or []:
            out.append(Fixture(
                id=m["id"], round_id=r["id"], stage=r["stage"], date=m["date"],
                status=m["status"], home_id=m["homeSquadId"], away_id=m["awaySquadId"],
                home=m["homeSquadName"], away=m["awaySquadName"],
                home_abbr=m["homeSquadAbbr"], away_abbr=m["awaySquadAbbr"],
                home_score=m.get("homeScore"), away_score=m.get("awayScore"),
            ))
    return out


def _team_map(rounds: list[dict]) -> dict[int, tuple[str, str]]:
    tm = {}
    for r in rounds:
        for m in r.get("tournaments") or []:
            tm[m["homeSquadId"]] = (m["homeSquadName"], m["homeSquadAbbr"])
            tm[m["awaySquadId"]] = (m["awaySquadName"], m["awaySquadAbbr"])
    return tm


def _aggregate_goal_involvements(rounds: list[dict]) -> dict[int, dict]:
    """playerId -> {goals, assists} from the scorer feed embedded in fixtures."""
    agg: dict[int, dict] = {}
    for r in rounds:
        for m in r.get("tournaments") or []:
            for side in ("homeGoalScorersAssists", "awayGoalScorersAssists"):
                for ev in m.get(side) or []:
                    if ev.get("isOwnGoal"):
                        continue
                    pid = ev.get("playerId")
                    if pid:
                        agg.setdefault(pid, {"goals": 0, "assists": 0})["goals"] += 1
                    aid = ev.get("assistId")
                    if aid:
                        agg.setdefault(aid, {"goals": 0, "assists": 0})["assists"] += 1
    return agg


def load_state(data_dir: Path = DATA_DIR, max_age_min: float = 20, offline: bool = False) -> GameState:
    if not offline:
        refresh(data_dir, max_age_min=max_age_min)
    players_raw = load_json(data_dir / "players.json", [])
    rounds = load_json(data_dir / "rounds.json", [])
    tm = _team_map(rounds)
    involvements = _aggregate_goal_involvements(rounds)
    news = load_json(data_dir / "news.json", {}) or {}
    news_by_id = {int(k): v for k, v in (news.get("players") or {}).items()}

    players: dict[int, Player] = {}
    for d in players_raw:
        p = Player.from_feed(d)
        p.country, p.country_abbr = tm.get(p.squad_id, ("?", "?"))
        inv = involvements.get(p.id) or {}
        p.goals, p.assists = inv.get("goals", 0), inv.get("assists", 0)
        p.news = news_by_id.get(p.id, {})
        players[p.id] = p

    return GameState(players, _build_fixtures(rounds), rounds, tm)


# ------------------------------------------------------------------- ESPN

def espn_scoreboard(dates: str | None = None) -> dict:
    """dates: YYYYMMDD (optional). Free, no key."""
    url = f"{ESPN_BASE}/scoreboard" + (f"?dates={dates}" if dates else "")
    return json.loads(_get(url))


def espn_lineups(event_id: str) -> dict:
    return json.loads(_get(f"{ESPN_BASE}/summary?event={event_id}"))
