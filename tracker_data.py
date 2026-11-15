"""Data and logic behind the Trackers pages (Live, Daily, Weekly, Archimedea, News, Arbitration).

Nothing here touches Qt, so it is all testable on its own.

* ``WorldState``            the game's live state through warframestat.us, cached on disk so the pages
                            still show the last known data (and keep counting) when you are offline
* ``roll_cycle``            works out where a day/night style cycle is now from its last known state
* ``ArbitrationSchedule``   the bundled hourly Arbitration schedule with tier ratings
* ``TrackerState``          which cards are shown, their names, tick boxes and reset periods
* view helpers              turn the raw worldstate into small dicts the pages can draw
"""
from __future__ import annotations

import bisect
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import requests

import config

WORLDSTATE_URL = "https://api.warframestat.us/pc"
UTC = timezone.utc

# ── cards ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CardDef:
    id: str
    title: str
    page: str
    default_on: bool = True


CARDS: tuple[CardDef, ...] = (
    CardDef("cycles", "World cycles", "live"),
    CardDef("arbitration", "Arbitration", "live"),
    CardDef("baro", "Void Trader", "live"),
    CardDef("fissures", "Void Fissures", "live"),
    CardDef("bounties", "Bounties", "live", False),
    CardDef("darvo", "Darvo's deals", "live", False),
    CardDef("alerts", "Alerts", "live", False),
    CardDef("events", "Events", "live", False),
    CardDef("invasions", "Invasions", "live", False),
    CardDef("storms", "Void Storms (Railjack)", "live", False),
    CardDef("incursions", "Steel Path Incursions", "live", False),
    CardDef("sortie", "Sortie", "daily"),
    CardDef("daily_ticks", "Daily tick list", "daily"),
    CardDef("circuit", "Duviri Circuit rotation", "weekly"),
    CardDef("archon", "Archon Hunt", "weekly"),
    CardDef("steelpath", "Steel Path reward", "weekly"),
    CardDef("weekly_ticks", "Weekly tick list", "weekly"),
    CardDef("eta", "Elite Temporal Archimedea (ETA)", "archimedea"),
    CardDef("eda", "Elite Deep Archimedea (EDA)", "archimedea"),
    CardDef("news", "News", "news"),
    CardDef("nightwave", "Nightwave", "news"),
)
CARD_BY_ID = {card.id: card for card in CARDS}

PAGE_TITLES = {   # in sidebar order
    "live": "Live", "daily": "Daily", "weekly": "Weekly",
    "archimedea": "Archimedea", "arbitration": "Arbitration", "news": "News",
}

DAILY_TICKS = (
    ("sortie", "Sortie"),
    ("syndicates", "Syndicate standing"),
    ("nightwave_daily", "Daily Nightwave acts"),
    ("login", "Login reward"),
)
WEEKLY_TICKS = (
    ("archon", "Archon Hunt"),
    ("circuit_normal", "Duviri Circuit (normal)"),
    ("circuit_steel", "Duviri Circuit (Steel Path)"),
    ("nightwave_weekly", "Nightwave weeklies"),
    ("steel_honors", "Steel Path Honors"),
    ("eta", "Elite Temporal Archimedea (ETA)"),
    ("eda", "Elite Deep Archimedea (EDA)"),
    ("kahl", "Kahl's Garrison"),
    ("netracells", "Netracells"),
)

DEFAULT_OPTIONS = {
    "tray": True,                  # close button hides to the tray instead of quitting
    "notify_arbitration": True,
    "notify_tiers": ["S", "A"],
    "notify_cetus": False,
    "notify_weekly": False,
    "notify_sound": True,
}

# ── time helpers ────────────────────────────────────────────────────


def now_utc() -> datetime:
    return datetime.now(UTC)


def parse_time(value) -> datetime | None:
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.year < 1980:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def fmt_span(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s" if minutes < 10 else f"{minutes}m"
    return f"{secs}s"


def period_key(kind: str, now: datetime) -> str:
    """Id of the current daily or weekly period. Both reset at 00:00 UTC, weeks on Monday."""
    day = now.astimezone(UTC).date()
    if kind == "weekly":
        day -= timedelta(days=day.weekday())
    return day.isoformat()


def next_reset(kind: str, now: datetime) -> datetime:
    today = datetime.combine(now.astimezone(UTC).date(), datetime.min.time(), tzinfo=UTC)
    if kind == "weekly":
        return today + timedelta(days=7 - today.weekday())
    return today + timedelta(days=1)


# ── cycles that keep running without a connection ───────────────────

CYCLE_PATTERNS: dict[str, list[tuple[str, int]]] = {
    "cetus": [("day", 6000), ("night", 3000)],
    "cambion": [("fass", 6000), ("vome", 3000)],
    "vallis": [("warm", 400), ("cold", 1200)],
    "earth": [("day", 14400), ("night", 14400)],
    "duviri": [("joy", 7200), ("anger", 7200), ("envy", 7200), ("sorrow", 7200), ("fear", 7200)],
}


def roll_cycle(pattern: list[tuple[str, int]], state: str, expiry: datetime, now: datetime) -> tuple[str, datetime]:
    """Move a cycle forward to ``now`` from a known (state, expiry). Returns (state, expiry)."""
    names = [name for name, _ in pattern]
    if state not in names or now < expiry:
        return state, expiry
    total = sum(seconds for _, seconds in pattern)
    skip = int((now - expiry).total_seconds() // total)
    expiry += timedelta(seconds=skip * total)
    index = names.index(state)
    while expiry <= now:
        index = (index + 1) % len(pattern)
        expiry += timedelta(seconds=pattern[index][1])
    return names[index], expiry


# ── live worldstate ─────────────────────────────────────────────────


class WorldState:
    """The game's current state, cached to disk."""

    def __init__(self, path: Path | None = None):
        self.path = path or (config.USER_DATA_DIR / "tracker_worldstate.json")
        self.data: dict = {}
        self.fetched: float = 0.0
        self.error: str = ""
        self._load()

    def _load(self):
        try:
            cached = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = cached["data"]
            self.fetched = float(cached["fetched"])
        except (OSError, ValueError, KeyError, TypeError):
            self.data, self.fetched = {}, 0.0

    def fetch(self) -> bool:
        """Download fresh data. Keeps the old data (and says why) when it fails."""
        try:
            response = requests.get(
                WORLDSTATE_URL, headers={"User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}"}, timeout=25,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or "cetusCycle" not in data:
                raise ValueError("unexpected worldstate response")
        except Exception as e:
            self.error = str(e)
            return False
        self.data, self.fetched, self.error = data, time.time(), ""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"fetched": self.fetched, "data": data}), encoding="utf-8")
        except OSError as e:
            print(f"[Trackers] Could not save worldstate cache: {e}")
        return True

    def age_seconds(self) -> float:
        return time.time() - self.fetched if self.fetched else float("inf")

    def get(self, key: str, default=None):
        return self.data.get(key, default)


# ── view helpers (raw worldstate -> small dicts the pages draw) ─────

CYCLE_LABELS = {
    "cetus": ("Cetus", "cetusCycle"),
    "vallis": ("Orb Vallis", "vallisCycle"),
    "cambion": ("Cambion Drift", "cambionCycle"),
    "earth": ("Earth", "earthCycle"),
    "duviri": ("Duviri", "duviriCycle"),
}
CYCLE_NEXT = {
    "cetus": "night", "vallis": "cold", "cambion": "vome", "earth": "night",
}


def cycles_view(ws: WorldState, now: datetime) -> list[dict]:
    rows = []
    for key, (label, field) in CYCLE_LABELS.items():
        raw = ws.get(field)
        if not isinstance(raw, dict):
            continue
        state = str(raw.get("state") or "").lower()
        if not state and "isDay" in raw:
            state = "day" if raw["isDay"] else "night"
        if not state and "isWarm" in raw:
            state = "warm" if raw["isWarm"] else "cold"
        expiry = parse_time(raw.get("expiry"))
        if not state or expiry is None:
            continue
        state, expiry = roll_cycle(CYCLE_PATTERNS[key], state, expiry, now)
        pattern = CYCLE_PATTERNS[key]
        names = [name for name, _ in pattern]
        following = names[(names.index(state) + 1) % len(names)] if state in names else ""
        rows.append({"key": key, "name": label, "state": state, "next": following, "expiry": expiry})
    return rows


def _tier_rank(entry: dict) -> int:
    return int(entry.get("tierNum") or 0)


def fissures_view(ws: WorldState, now: datetime) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {"normal": [], "steel": [], "storm": []}
    for raw in ws.get("fissures", []) or []:
        expiry = parse_time(raw.get("expiry"))
        if expiry is None or expiry <= now:
            continue
        row = {
            "tier": raw.get("tier", ""), "tier_num": _tier_rank(raw),
            "mission": raw.get("missionType", ""), "node": raw.get("node", ""),
            "enemy": raw.get("enemy", ""), "expiry": expiry,
        }
        groups["storm" if raw.get("isStorm") else "steel" if raw.get("isHard") else "normal"].append(row)
    for rows in groups.values():
        rows.sort(key=lambda r: (r["tier_num"], r["expiry"]))
    return groups


def baro_view(ws: WorldState) -> dict | None:
    raw = None
    traders = ws.get("voidTraders")
    if isinstance(traders, list) and traders:
        raw = traders[0]
    elif isinstance(ws.get("voidTrader"), dict):
        raw = ws.get("voidTrader")
    if not raw:
        return None
    return {
        "name": raw.get("character") or "Baro Ki'Teer", "location": raw.get("location") or "an unknown relay",
        "start": parse_time(raw.get("activation")), "end": parse_time(raw.get("expiry")),
    }


def sortie_view(ws: WorldState, now: datetime) -> dict | None:
    raw = ws.get("sortie")
    if not isinstance(raw, dict):
        return None
    expiry = parse_time(raw.get("expiry"))
    return {
        "boss": raw.get("boss") or "", "faction": raw.get("faction") or "", "expiry": expiry,
        "missions": [
            {"type": v.get("missionType", ""), "node": v.get("node", ""), "modifier": v.get("modifier", ""),
             "detail": v.get("modifierDescription", "")}
            for v in raw.get("variants", []) or []
        ],
    }


def archon_view(ws: WorldState) -> dict | None:
    raw = ws.get("archonHunt")
    if not isinstance(raw, dict):
        return None
    return {
        "boss": raw.get("boss") or "", "expiry": parse_time(raw.get("expiry")),
        "missions": [{"type": m.get("type", ""), "node": m.get("node", "")} for m in raw.get("missions", []) or []],
    }


def steelpath_view(ws: WorldState) -> dict | None:
    raw = ws.get("steelPath")
    if not isinstance(raw, dict):
        return None
    reward = raw.get("currentReward") or {}
    return {
        "reward": reward.get("name", ""), "cost": reward.get("cost"), "expiry": parse_time(raw.get("expiry")),
        "rotation": [r.get("name", "") for r in raw.get("rotation", []) or []],
        "incursions": raw.get("incursions") if isinstance(raw.get("incursions"), list) else [],
    }


def circuit_view(ws: WorldState) -> dict | None:
    raw = ws.get("duviriCycle")
    if not isinstance(raw, dict):
        return None
    groups = {c.get("category"): list(c.get("choices") or []) for c in raw.get("choices", []) or []}
    return {"warframes": groups.get("normal", []), "incarnon": groups.get("hard", [])}


ARCHIMEDEA_NAMES = {"CT_LAB": "Deep Archimedea", "CT_HEX": "Temporal Archimedea"}


def archimedea_view(ws: WorldState) -> dict[str, dict]:
    """{"eda": {...}, "eta": {...}}. The elite versions add the ``isHard`` risks to what the normal ones have."""
    out: dict[str, dict] = {}
    for raw in ws.get("archimedeas", []) or []:
        type_key = str(raw.get("typeKey") or raw.get("type") or "").replace(" ", "")
        key = "eda" if type_key.endswith("LAB") else "eta" if type_key.endswith("HEX") else ""
        if not key:
            continue
        missions = []
        for mission in raw.get("missions", []) or []:
            deviation = mission.get("deviation") or {}
            missions.append({
                "faction": mission.get("faction", ""), "type": mission.get("missionType", ""),
                "deviation": deviation.get("name", ""), "deviation_text": deviation.get("description", ""),
                "risks": [
                    {"name": r.get("name", ""), "text": r.get("description", ""), "elite": bool(r.get("isHard"))}
                    for r in mission.get("risks", []) or []
                ],
            })
        out[key] = {
            "name": ARCHIMEDEA_NAMES.get(type_key, type_key), "expiry": parse_time(raw.get("expiry")),
            "missions": missions,
            "modifiers": [{"name": m.get("name", ""), "text": m.get("description", "")}
                          for m in raw.get("personalModifiers", []) or []],
        }
    return out


def nightwave_view(ws: WorldState, now: datetime) -> dict | None:
    raw = ws.get("nightwave")
    if not isinstance(raw, dict):
        return None
    acts = {"daily": [], "weekly": [], "elite": []}
    for challenge in raw.get("activeChallenges", []) or []:
        expiry = parse_time(challenge.get("expiry"))
        if expiry is not None and expiry <= now:
            continue
        kind = "daily" if challenge.get("isDaily") else "elite" if challenge.get("isElite") else "weekly"
        acts[kind].append({
            "title": challenge.get("title", ""), "desc": challenge.get("desc", ""),
            "standing": challenge.get("reputation"), "expiry": expiry,
        })
    return {"season": raw.get("season"), "acts": acts}


def news_view(ws: WorldState, limit: int = 12) -> list[dict]:
    rows = []
    for raw in ws.get("news", []) or []:
        date = parse_time(raw.get("date"))
        if date is None or not raw.get("message"):
            continue
        rows.append({"message": raw["message"], "link": raw.get("link") or "", "date": date})
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows[:limit]


def bounties_view(ws: WorldState, now: datetime) -> list[dict]:
    rows = []
    for raw in ws.get("syndicateMissions", []) or []:
        expiry = parse_time(raw.get("expiry"))
        jobs = raw.get("jobs") or []
        if not jobs or (expiry is not None and expiry <= now):
            continue
        rows.append({"syndicate": raw.get("syndicate", ""), "expiry": expiry, "jobs": len(jobs)})
    return rows


def darvo_view(ws: WorldState, now: datetime) -> list[dict]:
    rows = []
    for raw in ws.get("dailyDeals", []) or []:
        expiry = parse_time(raw.get("expiry"))
        if expiry is not None and expiry <= now:
            continue
        rows.append({"item": raw.get("item", ""), "price": raw.get("salePrice"), "was": raw.get("originalPrice"),
                     "left": max(0, int(raw.get("total") or 0) - int(raw.get("sold") or 0)), "expiry": expiry})
    return rows


def alerts_view(ws: WorldState, now: datetime) -> list[dict]:
    rows = []
    for raw in ws.get("alerts", []) or []:
        expiry = parse_time(raw.get("expiry"))
        if expiry is not None and expiry <= now:
            continue
        mission = raw.get("mission") or {}
        rows.append({"text": f"{mission.get('type', '')} · {mission.get('node', '')}", "expiry": expiry})
    return rows


def events_view(ws: WorldState, now: datetime) -> list[dict]:
    rows = []
    for raw in ws.get("events", []) or []:
        expiry = parse_time(raw.get("expiry"))
        if expiry is not None and expiry <= now:
            continue
        rows.append({"text": raw.get("description", ""), "expiry": expiry})
    return rows


def invasions_view(ws: WorldState) -> list[dict]:
    rows = []
    for raw in ws.get("invasions", []) or []:
        if raw.get("completed"):
            continue
        rows.append({"node": raw.get("node", ""), "text": raw.get("desc", ""), "progress": raw.get("completion")})
    return rows


# ── Arbitration ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Arbitration:
    start: datetime
    node: str
    name: str
    mission: str
    faction: str
    tier: str

    @property
    def end(self) -> datetime:
        return self.start + timedelta(hours=1)


class ArbitrationSchedule:
    """The hourly Arbitration schedule, shipped with the app so it works offline."""

    def __init__(self, schedule: Path | None = None, nodes: Path | None = None, tiers: Path | None = None):
        schedule = schedule or config.resource_path("tracker_arbitration_schedule.txt")
        nodes = nodes or config.resource_path("tracker_sol_nodes.json")
        tiers = tiers or config.resource_path("tracker_arbitration_tiers.json")
        self._times: list[int] = []
        self._nodes: list[str] = []
        try:
            for line in schedule.read_text(encoding="ascii").splitlines():
                stamp, _, node = line.partition(",")
                if stamp.isdigit() and node:
                    self._times.append(int(stamp))
                    self._nodes.append(node.strip())
        except OSError as e:
            print(f"[Trackers] Arbitration schedule missing: {e}")
        try:
            self._node_info = json.loads(nodes.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._node_info = {}
        try:
            self._tiers = dict(json.loads(tiers.read_text(encoding="utf-8")).get("tiers", {}))
        except (OSError, ValueError):
            self._tiers = {}

    def __len__(self) -> int:
        return len(self._times)

    def tier_of(self, node: str) -> str:
        return self._tiers.get(node, "F")

    def _entry(self, index: int) -> Arbitration:
        node = self._nodes[index]
        info = self._node_info.get(node) or {}
        return Arbitration(
            start=datetime.fromtimestamp(self._times[index], UTC), node=node,
            name=info.get("value") or node, mission=info.get("type") or "Unknown",
            faction=info.get("enemy") or "", tier=self.tier_of(node),
        )

    def _index_at(self, now: datetime) -> int:
        return bisect.bisect_right(self._times, int(now.timestamp())) - 1

    def current(self, now: datetime) -> Arbitration | None:
        index = self._index_at(now)
        if index < 0 or int(now.timestamp()) >= self._times[index] + 3600:
            return None
        return self._entry(index)

    def upcoming(self, now: datetime, count: int) -> list[Arbitration]:
        first = self._index_at(now) + 1
        return [self._entry(i) for i in range(max(0, first), min(len(self._times), first + count))]

    def window(
        self, now: datetime, hours: int, missions: Iterable[str] | None = None,
        tiers: Iterable[str] | None = None, factions: Iterable[str] | None = None,
    ) -> list[Arbitration]:
        """Everything from the current hour to ``hours`` ahead, optionally filtered."""
        missions = {m.lower() for m in missions or ()}
        tiers = {t.upper() for t in tiers or ()}
        factions = {f.lower() for f in factions or ()}
        end = int((now + timedelta(hours=hours)).timestamp())
        start = max(0, self._index_at(now))
        out = []
        for index in range(start, len(self._times)):
            if self._times[index] > end:
                break
            entry = self._entry(index)
            if missions and entry.mission.lower() not in missions:
                continue
            if tiers and entry.tier not in tiers:
                continue
            if factions and entry.faction.lower() not in factions:
                continue
            out.append(entry)
        return out

    def mission_types(self) -> list[str]:
        return sorted({(info.get("type") or "") for node in set(self._nodes) for info in [self._node_info.get(node) or {}] if info.get("type")})

    def factions(self) -> list[str]:
        return sorted({(info.get("enemy") or "") for node in set(self._nodes) for info in [self._node_info.get(node) or {}] if info.get("enemy")})


_SHARED_SCHEDULE: ArbitrationSchedule | None = None


def shared_schedule() -> ArbitrationSchedule:
    """The bundled schedule, read once per run."""
    global _SHARED_SCHEDULE
    if _SHARED_SCHEDULE is None:
        _SHARED_SCHEDULE = ArbitrationSchedule()
    return _SHARED_SCHEDULE


# ── saved choices and tick boxes ────────────────────────────────────


class TrackerState:
    """Card visibility, custom names, options and tick boxes. Saved to ``trackers.json``."""

    def __init__(self, path: Path | None = None):
        self.path = path or (config.USER_DATA_DIR / "trackers.json")
        self.enabled: dict[str, bool] = {}
        self.titles: dict[str, str] = {}
        self.ticks: dict[str, str] = {}
        self.options: dict = dict(DEFAULT_OPTIONS)
        self.notified: dict[str, str] = {}
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        self.enabled = {k: bool(v) for k, v in dict(raw.get("enabled") or {}).items() if k in CARD_BY_ID}
        self.titles = {k: str(v) for k, v in dict(raw.get("titles") or {}).items() if str(v).strip()}
        self.ticks = {str(k): str(v) for k, v in dict(raw.get("ticks") or {}).items()}
        self.options = {**DEFAULT_OPTIONS, **dict(raw.get("options") or {})}
        self.notified = {str(k): str(v) for k, v in dict(raw.get("notified") or {}).items()}

    def save(self):
        payload = {"enabled": self.enabled, "titles": self.titles, "ticks": self.ticks,
                   "options": self.options, "notified": self.notified}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        except OSError as e:
            print(f"[Trackers] Could not save settings: {e}")

    # cards
    def is_on(self, card_id: str) -> bool:
        return self.enabled.get(card_id, CARD_BY_ID[card_id].default_on)

    def set_on(self, card_id: str, value: bool):
        self.enabled[card_id] = bool(value)
        self.save()

    def title(self, card_id: str) -> str:
        return self.titles.get(card_id) or CARD_BY_ID[card_id].title

    def set_title(self, card_id: str, text: str):
        text = text.strip()
        if text and text != CARD_BY_ID[card_id].title:
            self.titles[card_id] = text
        else:
            self.titles.pop(card_id, None)
        self.save()

    def page_title(self, page: str) -> str:
        return self.titles.get(f"page:{page}") or PAGE_TITLES[page]

    def set_page_title(self, page: str, text: str):
        text = text.strip()
        if text and text != PAGE_TITLES[page]:
            self.titles[f"page:{page}"] = text
        else:
            self.titles.pop(f"page:{page}", None)
        self.save()

    # options
    def option(self, name: str):
        return self.options.get(name, DEFAULT_OPTIONS.get(name))

    def set_option(self, name: str, value):
        self.options[name] = value
        self.save()

    # ticks
    def is_ticked(self, tick_id: str, kind: str, now: datetime | None = None) -> bool:
        return self.ticks.get(f"{kind}:{tick_id}") == period_key(kind, now or now_utc())

    def set_tick(self, tick_id: str, kind: str, value: bool, now: datetime | None = None):
        key = f"{kind}:{tick_id}"
        if value:
            self.ticks[key] = period_key(kind, now or now_utc())
        else:
            self.ticks.pop(key, None)
        self.save()

    def prune_ticks(self, now: datetime | None = None):
        """Forget ticks from earlier periods so the file does not grow forever."""
        now = now or now_utc()
        stale = [k for k, v in self.ticks.items() if v != period_key(k.split(":", 1)[0], now)]
        for key in stale:
            del self.ticks[key]
        if stale:
            self.save()


# ── notifications ───────────────────────────────────────────────────


def due_notifications(
    state: TrackerState, schedule: ArbitrationSchedule, ws: WorldState, now: datetime,
) -> list[tuple[str, str]]:
    """Messages that should pop up now as (title, text). Each fires once; ``state.notified`` remembers."""
    out: list[tuple[str, str]] = []
    changed = False

    if state.option("notify_arbitration"):
        current = schedule.current(now)
        tiers = {str(t).upper() for t in state.option("notify_tiers") or []}
        if current is not None and current.tier in tiers:
            stamp = str(int(current.start.timestamp()))
            if state.notified.get("arbitration") != stamp:
                state.notified["arbitration"] = stamp
                changed = True
                if (now - current.start).total_seconds() < 600:  # not for one that started long ago
                    out.append((f"Arbitration · tier {current.tier}", f"{current.name} · {current.mission} · {current.faction}"))

    if state.option("notify_cetus"):
        for row in cycles_view(ws, now):
            if row["key"] != "cetus":
                continue
            marker = f"{row['state']}:{int(row['expiry'].timestamp())}"
            if state.notified.get("cetus") != marker:
                first_time = "cetus" not in state.notified
                state.notified["cetus"] = marker
                changed = True
                if not first_time:
                    out.append(("Cetus", f"It is now {row['state']} on the Plains of Eidolon."))

    if state.option("notify_weekly"):
        reset = next_reset("weekly", now)
        key = period_key("weekly", now)
        if 0 < (reset - now).total_seconds() <= 3600 and state.notified.get("weekly") != key:
            state.notified["weekly"] = key
            changed = True
            out.append(("Weekly reset soon", "Weeklies reset in under an hour."))

    if changed:
        state.save()
    return out
