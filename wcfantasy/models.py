"""Data models: players, fixtures, squad state, config."""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def norm_name(s: str | None) -> str:
    """ascii-fold + lowercase for fuzzy player matching."""
    return unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower().strip()


@dataclass
class Player:
    # straight from play.fifa.com/json/fantasy/players.json
    id: int
    first_name: str
    last_name: str
    known_name: str | None
    squad_id: int
    position: str          # GK/DEF/MID/FWD
    price: float
    status: str            # "playing" | "eliminated"
    match_status: str | None   # "start" | "sub" | "not_in_squad" | None (FIFA's lineup signal)
    ownership: float       # percentSelected
    round_points: dict     # {round_id(str): pts}
    total_points: int
    avg_points: float
    form: float
    # enriched
    country: str = "?"     # team name
    country_abbr: str = "?"
    goals: int = 0         # aggregated from rounds.json scorer feed
    assists: int = 0
    news: dict = field(default_factory=dict)   # {"status": "out|doubt|fit", "note": ..., "p_play": ...}

    @property
    def name(self) -> str:
        return self.known_name or f"{self.first_name} {self.last_name}".strip()

    @property
    def alive(self) -> bool:
        return self.status == "playing"

    @classmethod
    def from_feed(cls, d: dict) -> "Player":
        st = d.get("stats") or {}
        return cls(
            id=d["id"],
            first_name=d.get("firstName") or "",
            last_name=d.get("lastName") or "",
            known_name=d.get("knownName"),
            squad_id=d["squadId"],
            position=d["position"],
            price=float(d["price"]),
            status=d.get("status") or "?",
            match_status=d.get("matchStatus"),
            ownership=float(d.get("percentSelected") or 0.0),
            round_points={str(k): v for k, v in (st.get("roundPoints") or {}).items()},
            total_points=int(st.get("totalPoints") or 0),
            avg_points=float(st.get("avgPoints") or 0.0),
            form=float(st.get("form") or 0.0),
        )


@dataclass
class Fixture:
    id: int
    round_id: int
    stage: str             # GROUP/R32/R16/QF/SF/F
    date: str              # ISO
    status: str            # scheduled/playing/complete
    home_id: int
    away_id: int
    home: str
    away: str
    home_abbr: str
    away_abbr: str
    home_score: int | None
    away_score: int | None

    def involves(self, squad_id: int) -> bool:
        return squad_id in (self.home_id, self.away_id)

    def opponent_of(self, squad_id: int) -> int | None:
        if squad_id == self.home_id:
            return self.away_id
        if squad_id == self.away_id:
            return self.home_id
        return None


class GameState:
    """Everything loaded: player pool, fixtures, rounds, team map, my squad."""

    def __init__(self, players: dict[int, Player], fixtures: list[Fixture],
                 rounds: list[dict], team_map: dict[int, tuple[str, str]]):
        self.players = players
        self.fixtures = fixtures
        self.rounds = rounds
        self.team_map = team_map

    # ---- rounds ----
    def round_by_stage(self, stage: str) -> dict | None:
        for r in self.rounds:
            if r["stage"] == stage:
                return r
        return None

    def current_round(self) -> dict:
        """The round being played, else the next scheduled one."""
        for r in self.rounds:
            if r["status"] == "playing":
                return r
        for r in self.rounds:
            if r["status"] == "scheduled":
                return r
        return self.rounds[-1]

    def next_scheduled_round(self) -> dict | None:
        for r in self.rounds:
            if r["status"] == "scheduled":
                return r
        return None

    def fixtures_for_round(self, round_id: int) -> list[Fixture]:
        return [f for f in self.fixtures if f.round_id == round_id]

    def fixture_for_team(self, squad_id: int, round_id: int) -> Fixture | None:
        for f in self.fixtures_for_round(round_id):
            if f.involves(squad_id):
                return f
        return None

    # ---- players ----
    def find_player(self, query: str) -> list[Player]:
        q = norm_name(query)
        return [p for p in self.players.values()
                if q in norm_name(f"{p.first_name} {p.last_name}") or q in norm_name(p.known_name)]


# ------------------------------------------------------------------ squad IO

def load_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)


def load_config(data_dir: Path = DATA_DIR) -> dict:
    cfg = load_json(data_dir / "config.json", {}) or {}
    # defaults
    cfg.setdefault("weights", {})
    w = cfg["weights"]
    w.setdefault("form_component", 0.55)     # weight of form-based EP
    w.setdefault("stat_component", 0.45)     # weight of stat-model EP
    w.setdefault("base_mu", 1.30)            # avg goals per team per KO match
    w.setdefault("elo_scale", 1000.0)        # goals multiplier: 10^(diff/scale)
    w.setdefault("form_opponent_pull", 0.35) # how much opponent strength scales form
    cfg.setdefault("beam_width", 48)
    cfg.setdefault("swap_candidates", 14)    # top-N replacement candidates per out-player
    cfg.setdefault("max_extra_transfers", 2) # evaluate up to free+N transfers (-3 each)
    return cfg
