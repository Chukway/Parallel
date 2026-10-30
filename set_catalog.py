"""Set composition lookup and set-completion checks for the Inventory tab.

warframe.market's public ``/v2/item/{slug}/set`` endpoint lists every part of
a set with the quantity needed to build it. Results are cached on disk so each
set is fetched once; only sets the user actually holds parts of are requested.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import requests

import config


SET_REQUEST_DELAY = 0.4          # seconds between requests (API allows ~3/s)
SET_CACHE_TTL_DAYS = 30
NOT_A_SET = "-"                  # cache marker for items that belong to no set


@dataclass
class SetPart:
    slug: str
    name: str
    needed: int
    owned: int = 0

    @property
    def missing(self) -> int:
        return max(0, self.needed - self.owned)


@dataclass
class SetStatus:
    set_slug: str
    set_name: str
    parts: list[SetPart] = field(default_factory=list)
    set_owned: int = 0           # complete "<name> Set" items already held

    @property
    def buildable(self) -> int:
        """How many full sets the owned parts can make right now."""
        if not self.parts:
            return 0
        return min(part.owned // part.needed for part in self.parts if part.needed > 0)

    @property
    def parts_have(self) -> int:
        return sum(min(part.owned, part.needed) for part in self.parts)

    @property
    def parts_total(self) -> int:
        return sum(part.needed for part in self.parts)

    @property
    def missing_parts(self) -> list[SetPart]:
        return [part for part in self.parts if part.missing > 0]


class SetCatalog:
    def __init__(self, path: Path | None = None):
        self.path = path or (config.USER_DATA_DIR / "sets_cache.json")
        self._lock = threading.RLock()
        # set_slug -> {"name", "fetched", "parts": [{slug, name, needed}]}
        self.sets: dict[str, dict] = {}
        # any member slug (parts and the set itself) -> set_slug, or NOT_A_SET
        self.member_of: dict[str, str] = {}
        self.load()

    # ── persistence ──────────────────────────────────────────────
    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        with self._lock:
            self.sets = data.get("sets") or {}
            self.member_of = data.get("member_of") or {}

    def save(self):
        tmp_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = {"sets": self.sets, "member_of": self.member_of}
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=self.path.parent,
                    prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
                ) as f:
                    tmp_path = Path(f.name)
                    json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
                os.replace(tmp_path, self.path)
        except Exception as e:
            print(f"[Sets] Could not save {self.path}: {e}")
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    # ── lookups ──────────────────────────────────────────────────
    def set_slug_for(self, slug: str) -> str | None:
        with self._lock:
            target = self.member_of.get(slug)
        return None if target in (None, NOT_A_SET) else target

    def is_known(self, slug: str) -> bool:
        with self._lock:
            return slug in self.member_of

    def _is_stale(self, set_slug: str) -> bool:
        fetched = float((self.sets.get(set_slug) or {}).get("fetched") or 0)
        return time.time() - fetched > SET_CACHE_TTL_DAYS * 86400

    def status_for(self, set_slug: str, owned: dict[str, int]) -> SetStatus | None:
        with self._lock:
            entry = self.sets.get(set_slug)
            if not entry:
                return None
            parts = [
                SetPart(
                    slug=part["slug"],
                    name=part["name"],
                    needed=max(1, int(part["needed"])),
                    owned=max(0, int(owned.get(part["slug"], 0))),
                )
                for part in entry["parts"]
            ]
            return SetStatus(
                set_slug=set_slug,
                set_name=entry["name"],
                parts=parts,
                set_owned=max(0, int(owned.get(set_slug, 0))),
            )

    def all_statuses(self, owned: dict[str, int], only_owned: bool = True) -> list[SetStatus]:
        with self._lock:
            slugs = list(self.sets)
        result = []
        for set_slug in slugs:
            status = self.status_for(set_slug, owned)
            if status is None:
                continue
            if only_owned and not (status.set_owned or any(p.owned for p in status.parts)):
                continue
            result.append(status)
        return sorted(result, key=lambda s: s.set_name.lower())

    # ── fetching ─────────────────────────────────────────────────
    def refresh(
        self,
        slugs: Iterable[str],
        progress: Callable[[int, int], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        fetch: Callable[[str], dict | None] | None = None,
    ) -> int:
        """Fetch set data for slugs not yet cached. Returns the number requested."""
        fetch = fetch or fetch_set
        pending = []
        for slug in slugs:
            with self._lock:
                known = slug in self.member_of
                target = self.member_of.get(slug)
            if known and target == NOT_A_SET:
                continue
            if known and target in self.sets and not self._is_stale(target):
                continue
            pending.append(slug)
        done = 0
        covered: set[str] = set()
        for slug in pending:
            if should_stop is not None and should_stop():
                break
            if slug in covered:
                done += 1
                continue
            try:
                data = fetch(slug)
            except Exception as e:
                print(f"[Sets] {slug}: {e}")
                done += 1
                continue
            self._store_result(slug, data)
            for member in (data or {}).get("members", []):
                covered.add(member["slug"])
            done += 1
            if progress is not None:
                progress(done, len(pending))
            time.sleep(SET_REQUEST_DELAY)
        self.save()
        return len(pending)

    def _store_result(self, slug: str, data: dict | None):
        with self._lock:
            if not data or not data.get("set_slug") or len(data.get("members", [])) < 2:
                self.member_of[slug] = NOT_A_SET
                return
            set_slug = data["set_slug"]
            self.sets[set_slug] = {
                "name": data["set_name"],
                "fetched": time.time(),
                "parts": [
                    {"slug": m["slug"], "name": m["name"], "needed": m["needed"]}
                    for m in data["members"]
                    if m["slug"] != set_slug
                ],
            }
            self.member_of[set_slug] = set_slug
            for m in data["members"]:
                self.member_of[m["slug"]] = set_slug


class MasteryList:
    """The sets the user wants to build, persisted as a list of set slugs."""

    def __init__(self, path: Path | None = None):
        self.path = path or (config.USER_DATA_DIR / "mastery_list.json")
        self._lock = threading.RLock()
        self.slugs: list[str] = []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.slugs = [str(s) for s in data.get("sets", []) if s]
        except (OSError, ValueError, AttributeError):
            pass

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"sets": self.slugs}, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"[Sets] Could not save {self.path}: {e}")

    def add(self, set_slug: str) -> bool:
        with self._lock:
            if set_slug in self.slugs:
                return False
            self.slugs.append(set_slug)
            self._save()
            return True

    def remove(self, set_slug: str) -> bool:
        with self._lock:
            if set_slug not in self.slugs:
                return False
            self.slugs.remove(set_slug)
            self._save()
            return True


def known_set_names() -> dict[str, str]:
    """Display name -> slug for every set in the cached item list."""
    try:
        names = json.loads(config.ITEMS_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        name: slug
        for name, slug in names.items()
        if isinstance(slug, str) and slug.endswith("_set")
    }


def fetch_set(slug: str) -> dict | None:
    """Return {set_slug, set_name, members[{slug,name,needed}]} or None if not in a set."""
    response = requests.get(
        f"{config.WFM_API_BASE_V2}/item/{slug}/set",
        headers={
            "Accept": "application/json",
            "Language": getattr(config, "WFM_LANGUAGE", "en"),
            "Platform": getattr(config, "WFM_PLATFORM", "pc"),
            "User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}",
        },
        timeout=15,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    items = (response.json().get("data") or {}).get("items") or []
    set_slug = set_name = ""
    members = []
    language = getattr(config, "WFM_LANGUAGE", "en")
    for item in items:
        i18n = (item.get("i18n") or {})
        name = (i18n.get(language) or next(iter(i18n.values()), {})).get("name") or item["slug"]
        if item.get("setRoot"):
            set_slug, set_name = item["slug"], name
        members.append({
            "slug": item["slug"],
            "name": name,
            "needed": int(item.get("quantityInSet") or 1),
        })
    if not set_slug:
        return None
    return {"set_slug": set_slug, "set_name": set_name, "members": members}
