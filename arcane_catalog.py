"""Arcane catalog and the user's arcane stock (per rank).

Arcanes are rankable on warframe.market: every sell order carries a rank and
prices differ strongly between rank 0 and max rank. The catalog comes from the
public ``/v2/items`` list (tag ``arcane_enhancement``) and is cached on disk.
The stock is a small JSON file keyed by (arcane, rank).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

import config

ARCANE_TAG = "arcane_enhancement"
CATALOG_TTL_DAYS = 14


@dataclass
class ArcaneInfo:
    slug: str
    name: str
    item_id: str
    max_rank: int


@dataclass
class ArcaneStockRow:
    slug: str
    rank: int
    quantity: int


def stock_key(slug: str, rank: int) -> str:
    return f"{slug}@{int(rank)}"


def _atomic_write(path: Path, payload: object):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class ArcaneCatalog:
    def __init__(self, path: Path | None = None):
        self.path = path or (config.USER_DATA_DIR / "arcanes_cache.json")
        self._lock = threading.RLock()
        self.fetched = 0.0
        self.arcanes: dict[str, ArcaneInfo] = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.fetched = float(data.get("fetched") or 0)
            self.arcanes = {
                slug: ArcaneInfo(slug, raw["name"], raw.get("item_id", ""), int(raw.get("max_rank") or 0))
                for slug, raw in (data.get("arcanes") or {}).items()
            }
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.fetched, self.arcanes = 0.0, {}

    def is_stale(self) -> bool:
        return not self.arcanes or time.time() - self.fetched > CATALOG_TTL_DAYS * 86400

    def names(self) -> dict[str, str]:
        """Display name -> slug."""
        with self._lock:
            return {info.name: slug for slug, info in self.arcanes.items()}

    def info(self, slug: str) -> ArcaneInfo | None:
        with self._lock:
            return self.arcanes.get(slug)

    def refresh(self, fetch=None):
        arcanes = (fetch or fetch_arcanes)()
        if not arcanes:
            return
        with self._lock:
            self.arcanes = {a.slug: a for a in arcanes}
            self.fetched = time.time()
            _atomic_write(self.path, {
                "fetched": self.fetched,
                "arcanes": {
                    a.slug: {"name": a.name, "item_id": a.item_id, "max_rank": a.max_rank}
                    for a in arcanes
                },
            })


class ArcaneStock:
    def __init__(self, path: Path | None = None):
        self.path = path or (config.USER_DATA_DIR / "arcane_stock.json")
        self._lock = threading.RLock()
        self.rows: dict[str, ArcaneStockRow] = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for raw in data.get("rows", []):
                row = ArcaneStockRow(str(raw["slug"]), max(0, int(raw["rank"])), max(0, int(raw["quantity"])))
                if row.quantity > 0:
                    self.rows[stock_key(row.slug, row.rank)] = row
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.rows = {}

    def _save(self):
        try:
            _atomic_write(self.path, {"rows": [r.__dict__ for r in self.sorted_rows()]})
        except OSError as e:
            print(f"[Arcanes] Could not save {self.path}: {e}")

    def sorted_rows(self) -> list[ArcaneStockRow]:
        with self._lock:
            return sorted(self.rows.values(), key=lambda r: (r.slug, r.rank))

    def set_quantity(self, slug: str, rank: int, quantity: int):
        """Set the owned quantity for one rank; zero removes the row."""
        with self._lock:
            key = stock_key(slug, rank)
            if quantity <= 0:
                self.rows.pop(key, None)
            else:
                self.rows[key] = ArcaneStockRow(slug, int(rank), int(quantity))
            self._save()

    def add(self, slug: str, rank: int, quantity: int = 1):
        with self._lock:
            current = self.rows.get(stock_key(slug, rank))
            self.set_quantity(slug, rank, (current.quantity if current else 0) + max(1, int(quantity)))


def fetch_arcanes() -> list[ArcaneInfo]:
    response = requests.get(
        f"{config.WFM_API_BASE_V2}/items",
        headers={
            "Accept": "application/json",
            "Language": getattr(config, "WFM_LANGUAGE", "en"),
            "Platform": getattr(config, "WFM_PLATFORM", "pc"),
            "User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}",
        },
        timeout=30,
    )
    response.raise_for_status()
    language = getattr(config, "WFM_LANGUAGE", "en")
    arcanes = []
    for item in response.json().get("data") or []:
        if ARCANE_TAG not in (item.get("tags") or []):
            continue
        i18n = item.get("i18n") or {}
        name = (i18n.get(language) or next(iter(i18n.values()), {})).get("name") or item["slug"]
        arcanes.append(ArcaneInfo(item["slug"], name, item.get("id", ""), int(item.get("maxRank") or 0)))
    return arcanes
