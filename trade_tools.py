"""Trading helpers that sit on top of the warframe.market client.

Everything here is plain Python (no Qt) so it can be tested without a window:

* ``VaultedCatalog``      which items are vaulted (cached list from /v2/items)
* ``price_trend``         rising / falling / flat from recent sale statistics
* ``compare_set_vs_parts`` is a full set worth more than its parts sold singly?
* ``WatchStore``          price alerts (sell) and wishlist (buy) targets
* ``build_reprice_rows``  my listings that someone in game is undercutting
* ``sales_summary`` / ``sales_to_csv``   profit log helpers
* ``fetch_baro`` / ``baro_rows``         Baro Ki'Teer stock with market values

Nothing here writes to warframe.market except ``apply_reprice``, which the UI
only calls after the user ticks rows and confirms.
"""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import requests

import config

VAULTED_TTL_SECONDS = 24 * 3600
TREND_TTL_SECONDS = 30 * 60
TREND_FLAT_BAND = 0.08          # within +/-8% counts as steady
WATCH_ALERT_COOLDOWN_SECONDS = 6 * 3600
BARO_URL = "https://api.warframestat.us/pc/voidTrader"


def _headers() -> dict:
    return {
        "Accept": "application/json",
        "Language": getattr(config, "WFM_LANGUAGE", "en"),
        "Platform": getattr(config, "WFM_PLATFORM", "pc"),
        "Crossplay": "true",
        "User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}",
    }


def _write_json(path: Path, payload) -> None:
    tmp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as f:
            tmp_path = Path(f.name)
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp_path, path)
    except OSError as e:
        print(f"[Trade tools] Could not save {path}: {e}")
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


# ── vaulted ─────────────────────────────────────────────────────────

class VaultedCatalog:
    """Slugs of vaulted items, refreshed from warframe.market once a day."""

    def __init__(self, path: Path | None = None, fetch: Callable[[], list[dict]] | None = None):
        self.path = path or (config.USER_DATA_DIR / "vaulted_cache.json")
        self._fetch = fetch or self._fetch_items
        self._lock = threading.RLock()
        self.slugs: set[str] = set()
        self.fetched = 0.0
        self._load()

    @staticmethod
    def _fetch_items() -> list[dict]:
        response = requests.get(f"{config.WFM_API_BASE_V2}/items", headers=_headers(), timeout=20)
        response.raise_for_status()
        return response.json().get("data") or []

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.slugs = {str(s) for s in data.get("slugs", [])}
            self.fetched = float(data.get("fetched") or 0)
        except (OSError, ValueError, AttributeError):
            pass

    def refresh(self, force: bool = False) -> bool:
        with self._lock:
            if not force and self.slugs and time.time() - self.fetched < VAULTED_TTL_SECONDS:
                return False
        try:
            items = self._fetch()
        except Exception as e:
            print(f"[Trade tools] Vaulted list not refreshed: {e}")
            return False
        slugs = {str(i["slug"]) for i in items if i.get("vaulted") and i.get("slug")}
        if not slugs and not items:
            return False
        with self._lock:
            self.slugs = slugs
            self.fetched = time.time()
        _write_json(self.path, {"fetched": self.fetched, "slugs": sorted(slugs)})
        return True

    def is_vaulted(self, slug: str) -> bool:
        with self._lock:
            return slug in self.slugs


# ── trend ───────────────────────────────────────────────────────────

_trend_cache: dict[str, tuple[float, dict | None]] = {}


def _weighted(points: list[dict]) -> tuple[float, int]:
    volume = sum(max(0, int(p.get("volume") or 0)) for p in points)
    if volume <= 0:
        return 0.0, 0
    total = sum(float(p.get("avg_price") or 0) * max(0, int(p.get("volume") or 0)) for p in points)
    return total / volume, volume


def trend_from_statistics(payload: dict) -> dict | None:
    """Compare recent sale prices with the period before them.

    Uses the hourly 48h series when it has enough sales, else the daily series.
    Returns ``{"pct": float, "direction": "up"|"down"|"flat", "arrow": str}``.
    """
    closed = ((payload or {}).get("payload") or {}).get("statistics_closed") or {}
    hourly = sorted(closed.get("48hours") or [], key=lambda p: p.get("datetime", ""))
    daily = sorted(closed.get("90days") or [], key=lambda p: p.get("datetime", ""))
    recent = before = None
    if len(hourly) >= 6:
        half = len(hourly) // 2
        recent, before = _weighted(hourly[half:]), _weighted(hourly[:half])
    if recent is None or recent[1] < 2 or before[1] < 2:
        if len(daily) >= 5:
            recent, before = _weighted(daily[-3:]), _weighted(daily[-10:-3])
    if recent is None or recent[1] < 2 or before[1] < 2 or before[0] <= 0:
        return None
    pct = (recent[0] - before[0]) / before[0]
    if pct > TREND_FLAT_BAND:
        direction, arrow = "up", "▲"
    elif pct < -TREND_FLAT_BAND:
        direction, arrow = "down", "▼"
    else:
        direction, arrow = "flat", "►"
    return {"pct": round(pct * 100, 1), "direction": direction, "arrow": arrow}


def price_trend(slug: str, timeout: float = 4.0, fetch=None) -> dict | None:
    """Price direction for one item, cached for 30 minutes. None if unknown."""
    cached = _trend_cache.get(slug)
    if cached and time.time() - cached[0] < TREND_TTL_SECONDS:
        return cached[1]
    try:
        if fetch is not None:
            payload = fetch(slug)
        else:
            response = requests.get(
                f"{config.WFM_API_BASE_V1}/items/{slug}/statistics",
                headers=_headers(), timeout=timeout,
            )
            if response.status_code != 200:
                return None
            payload = response.json()
        result = trend_from_statistics(payload)
    except Exception as e:
        print(f"[Trade tools] Trend lookup failed for {slug}: {e}")
        return None
    _trend_cache[slug] = (time.time(), result)
    return result


def market_hints(slug: str, vaulted: VaultedCatalog | None, want_trend: bool = True) -> dict:
    """Small extras shown on the reward overlay."""
    return {
        "vaulted": bool(vaulted and vaulted.is_vaulted(slug)),
        "trend": price_trend(slug, timeout=2.5) if want_trend else None,
    }


# ── set versus parts ────────────────────────────────────────────────

@dataclass
class SetComparison:
    set_slug: str
    set_name: str
    set_price: int | None
    parts: list[dict] = field(default_factory=list)   # {slug,name,needed,price,owned}
    parts_total: int | None = None                    # None when any part had no price

    @property
    def verdict(self) -> str:
        if self.set_price is None or self.parts_total is None:
            return "Not enough price data."
        diff = self.set_price - self.parts_total
        if diff > 0:
            return f"Sell as a full set: +{diff}p over selling the parts one by one."
        if diff < 0:
            return f"Sell the parts separately: +{-diff}p over the set price."
        return "Equal value either way."


def compare_set_vs_parts(api, catalog, set_slug: str, owned: dict[str, int] | None = None) -> SetComparison | None:
    status = catalog.status_for(set_slug, owned or {})
    if status is None:
        return None

    def lowest(slug: str) -> int | None:
        try:
            return int(api.get_price_info(slug).lowest_sell_price)
        except Exception as e:
            print(f"[Trade tools] No price for {slug}: {e}")
            return None

    result = SetComparison(set_slug, status.set_name, lowest(set_slug))
    total = 0
    complete = True
    for part in status.parts:
        price = lowest(part.slug)
        result.parts.append({
            "slug": part.slug, "name": part.name, "needed": part.needed,
            "price": price, "owned": part.owned,
        })
        if price is None:
            complete = False
        else:
            total += price * part.needed
    result.parts_total = total if complete else None
    return result


def plan_set_combine(parts, listing_for: Callable[[str], object | None]) -> list[tuple[object, object, int]]:
    """Which part listings must shrink when one full set is listed instead of its parts.

    ``parts`` are SetPart-like (slug, name, needed, owned); ``listing_for(slug)`` returns the
    part's current sell listing or None. The set takes ``needed`` of each part, so a part
    listing may not promise more than ``owned - needed``. Returns (part, listing, new_quantity)
    for every listing that has to change; a new quantity of 0 means delete the listing.
    """
    changes = []
    for part in parts:
        listing = listing_for(part.slug)
        if listing is None:
            continue
        allowed = max(0, int(part.owned) - int(part.needed))
        if int(listing.quantity) > allowed:
            changes.append((part, listing, allowed))
    return changes


# ── price alerts + wishlist ─────────────────────────────────────────

@dataclass
class WatchEntry:
    slug: str
    name: str
    direction: str            # "below" = buy wishlist, "above" = sell alert
    target: int
    last_alert: float = 0.0
    last_price: int | None = None

    def hit(self, price: int) -> bool:
        return price <= self.target if self.direction == "below" else price >= self.target

    def describe(self) -> str:
        word = "at or under" if self.direction == "below" else "at or over"
        return f"{self.name}: {word} {self.target}p"


class WatchStore:
    """Persisted watch list. Entries are keyed by (slug, direction)."""

    def __init__(self, path: Path | None = None):
        self.path = path or (config.USER_DATA_DIR / "watchlist.json")
        self._lock = threading.RLock()
        self.entries: list[WatchEntry] = []
        self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for raw in data.get("entries", []):
            try:
                direction = raw["direction"] if raw.get("direction") in ("below", "above") else "below"
                self.entries.append(WatchEntry(
                    slug=str(raw["slug"]), name=str(raw.get("name") or raw["slug"]),
                    direction=direction, target=max(1, int(raw["target"])),
                    last_alert=float(raw.get("last_alert") or 0),
                    last_price=None if raw.get("last_price") is None else int(raw["last_price"]),
                ))
            except (KeyError, TypeError, ValueError):
                continue

    def save(self):
        with self._lock:
            _write_json(self.path, {"entries": [e.__dict__ for e in self.entries]})

    def set(self, slug: str, name: str, direction: str, target: int) -> WatchEntry:
        direction = direction if direction in ("below", "above") else "below"
        target = max(1, int(target))
        with self._lock:
            for entry in self.entries:
                if entry.slug == slug and entry.direction == direction:
                    entry.name, entry.target, entry.last_alert = name, target, 0.0
                    break
            else:
                entry = WatchEntry(slug, name, direction, target)
                self.entries.append(entry)
            self.save()
            return entry

    def remove(self, slug: str, direction: str) -> bool:
        with self._lock:
            before = len(self.entries)
            self.entries = [e for e in self.entries if not (e.slug == slug and e.direction == direction)]
            changed = len(self.entries) != before
            if changed:
                self.save()
            return changed

    def check(self, get_price: Callable[[str], int | None], now: float | None = None) -> list[WatchEntry]:
        """Look up each watched item; return entries that newly hit their target.

        ``get_price`` returns the lowest in-game sell price, or None when there is
        none. An entry is not reported again for six hours after an alert.
        """
        now = time.time() if now is None else now
        triggered: list[WatchEntry] = []
        with self._lock:
            entries = list(self.entries)
        prices: dict[str, int | None] = {}
        for entry in entries:
            if entry.slug not in prices:
                try:
                    prices[entry.slug] = get_price(entry.slug)
                except Exception as e:
                    print(f"[Watch] {entry.slug}: {e}")
                    prices[entry.slug] = None
            price = prices[entry.slug]
            entry.last_price = price
            if price is not None and entry.hit(price) and (
                not entry.last_alert or now - entry.last_alert >= WATCH_ALERT_COOLDOWN_SECONDS
            ):
                entry.last_alert = now
                triggered.append(entry)
        self.save()
        return triggered


def suggest_buy_price(lowest_sell: int | None, top_buy: int | None, avg_sell: float | None = None) -> int | None:
    """A buy price that is competitive but still under what sellers ask.

    One plat above the best current buy order if there is one, otherwise 80% of the
    lowest sell. Never at or above the lowest sell price.
    """
    if lowest_sell is None and top_buy is None and avg_sell is None:
        return None
    if top_buy:
        price = top_buy + 1
    elif lowest_sell:
        price = round(lowest_sell * 0.8)
    else:
        price = round((avg_sell or 2) * 0.8)
    if lowest_sell:
        price = min(price, lowest_sell - 1)
    return max(1, int(price))


def market_snapshot(api, slug: str) -> dict:
    """Current state of one item from players who are in game.

    Returns lowest_sell, avg_sell (of the 10 cheapest), top_buy and suggested_buy;
    a value is None when there are no matching orders.
    """
    orders = api.get_orders(slug)
    allowed = set(getattr(config, "PRICE_STATUS_FILTER", ("ingame",)))

    def live(kind: str) -> list[int]:
        return [
            int(o["platinum"]) for o in orders
            if (o.get("type") or o.get("order_type")) == kind
            and o.get("visible", True)
            and (o.get("user") or {}).get("status") in allowed
        ]

    sells, buys = sorted(live("sell")), live("buy")
    lowest = sells[0] if sells else None
    avg = round(sum(sells[:10]) / len(sells[:10]), 1) if sells else None
    top_buy = max(buys) if buys else None
    return {
        "lowest_sell": lowest, "avg_sell": avg, "top_buy": top_buy,
        "suggested_buy": suggest_buy_price(lowest, top_buy, avg),
    }


# ── repricing ───────────────────────────────────────────────────────

@dataclass
class RepriceRow:
    order_id: str
    slug: str
    name: str
    quantity: int
    visible: bool
    my_price: int
    market_low: int
    suggested: int
    rank: int = 0

    @property
    def undercut_by(self) -> int:
        return max(0, self.my_price - self.market_low)


def build_reprice_rows(
    api, names: dict[str, str] | None = None, progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[RepriceRow]:
    """Compare each of my sell listings with the lowest in-game price right now.

    Only listings where someone in game is cheaper are returned. Listings that
    already have the lowest price are left alone.
    """
    listings = api.get_existing_sell_listing_map()
    unique = {}
    for listing in listings.values():
        unique.setdefault(listing.order_id, listing)
    rows: list[RepriceRow] = []
    total = len(unique)
    for index, listing in enumerate(unique.values(), start=1):
        if should_stop is not None and should_stop():
            break
        if progress is not None:
            progress(index, total)
        try:
            info = api.get_price_info(listing.item_url_name, rank=listing.rank if listing.rank else None)
        except Exception:
            continue
        if info.lowest_sell_price >= listing.platinum:
            continue
        slug = listing.item_url_name
        rows.append(RepriceRow(
            order_id=listing.order_id, slug=slug,
            name=(names or {}).get(slug) or slug.replace("_", " ").title(),
            quantity=listing.quantity, visible=listing.visible,
            my_price=listing.platinum, market_low=info.lowest_sell_price,
            suggested=min(listing.platinum, max(1, int(info.suggested_listing_price))),
            rank=listing.rank,
        ))
    return sorted(rows, key=lambda r: r.undercut_by, reverse=True)


def apply_reprice(api, rows: Iterable[RepriceRow], delay: Callable[[], None] | None = None) -> tuple[int, list[str]]:
    """PATCH each row to its suggested price. Returns (done, error messages)."""
    done, errors = 0, []
    for row in rows:
        try:
            api.update_order_price(row.order_id, row.suggested, quantity=row.quantity, visible=row.visible)
            done += 1
        except Exception as e:
            errors.append(f"{row.name}: {e}")
        if delay is not None:
            delay()
    return done, errors


# ── sales log ───────────────────────────────────────────────────────

def sales_summary(sales: list[dict], now: float | None = None) -> dict:
    """Totals for the sales log. ``platinum`` per row is the whole sale (price x count)."""
    now = time.time() if now is None else now
    day, week = now - 86400, now - 7 * 86400
    per_item: dict[str, dict] = {}
    summary = {"sales": len(sales), "items": 0, "platinum": 0, "today": 0, "week": 0, "unpriced": 0}
    for row in sales:
        count = max(1, int(row.get("count") or 1))
        plat = max(0, int(row.get("platinum") or 0))
        summary["items"] += count
        summary["platinum"] += plat
        if not plat:
            summary["unpriced"] += 1
        at = float(row.get("at") or 0)
        if at >= day:
            summary["today"] += plat
        if at >= week:
            summary["week"] += plat
        entry = per_item.setdefault(row.get("url_name", ""), {
            "name": row.get("display_name") or row.get("url_name", ""), "count": 0, "platinum": 0,
        })
        entry["count"] += count
        entry["platinum"] += plat
    summary["top"] = sorted(per_item.values(), key=lambda e: e["platinum"], reverse=True)[:5]
    return summary


def sales_to_csv(sales: list[dict], path: Path) -> int:
    """Write the sales log to a CSV file. Returns the number of rows written."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Item", "Quantity", "Total platinum", "Each platinum"])
        for row in sales:
            count = max(1, int(row.get("count") or 1))
            plat = max(0, int(row.get("platinum") or 0))
            when = datetime.fromtimestamp(float(row.get("at") or 0)).strftime("%Y-%m-%d %H:%M")
            writer.writerow([when, row.get("display_name") or row.get("url_name"), count,
                             plat or "", round(plat / count, 1) if plat else ""])
    return len(sales)


# ── Baro Ki'Teer ────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def fetch_baro() -> dict:
    response = requests.get(BARO_URL, headers={"User-Agent": f"{config.APP_NAME}/{config.APP_VERSION}"}, timeout=20)
    response.raise_for_status()
    return response.json()


BARO_PRICE_REFRESH_SECONDS = 3600


def _baro_cache_path() -> Path:
    return config.USER_DATA_DIR / "baro_cache.json"


def save_baro_cache(data: dict, rows: list[dict], path: Path | None = None) -> None:
    """Remember Baro's stock so the page can open instantly next time."""
    path = path or _baro_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"saved": time.time(), "data": data, "rows": rows}), encoding="utf-8")
    except OSError as e:
        print(f"[Baro] Could not save cache: {e}")


def load_baro_cache(path: Path | None = None, now: datetime | None = None) -> dict | None:
    """Return {"data", "rows", "saved", "stale_prices"} while the cache still describes Baro's current
    visit (or his upcoming arrival), else None so the caller fetches fresh data."""
    path = path or _baro_cache_path()
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        data, rows, saved = cached["data"], cached["rows"], float(cached["saved"])
        start = datetime.fromisoformat(str(data["activation"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(data["expiry"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    now = now or datetime.now().astimezone()
    if now >= end:
        return None  # his visit is over
    if now >= start and not rows:
        return None  # cached before he arrived, so it has no stock yet
    return {"data": data, "rows": rows, "saved": saved,
            "stale_prices": (time.time() - saved) > BARO_PRICE_REFRESH_SECONDS}


def baro_status(data: dict, now: datetime | None = None) -> str:
    """One line: where Baro is and how long until he arrives or leaves."""
    def parse(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    now = now or datetime.now().astimezone()
    start, end = parse(data.get("activation", "")), parse(data.get("expiry", ""))
    location = data.get("location") or "an unknown relay"
    if start is None or end is None:
        return "Baro's schedule is unavailable."

    def span(delta) -> str:
        minutes = max(0, int(delta.total_seconds() // 60))
        days, rest = divmod(minutes, 1440)
        hours, mins = divmod(rest, 60)
        return f"{days}d {hours}h" if days else f"{hours}h {mins}m"

    if start <= now <= end:
        return f"Baro is at {location}. Leaves in {span(end - now)}."
    if now < start:
        return f"Baro arrives at {location} in {span(start - now)}."
    return "Baro has left. Check again later."


def baro_rows(
    data: dict, name_to_slug: dict[str, str], get_price: Callable[[str], int | None] | None = None,
    progress: Callable[[int, int], None] | None = None, should_stop: Callable[[], bool] | None = None,
) -> list[dict]:
    """Baro's stock with the market price of each tradable item.

    ``plat_per_100_ducats`` ranks which purchases turn ducats into the most
    platinum when sold on warframe.market.
    """
    lookup = {k.lower(): v for k, v in name_to_slug.items()}
    inventory = list(data.get("inventory") or [])
    rows: list[dict] = []
    for index, item in enumerate(inventory, start=1):
        if should_stop is not None and should_stop():
            break
        if progress is not None:
            progress(index, len(inventory))
        name = str(item.get("item") or "")
        ducats = int(item.get("ducats") or 0)
        credits = int(item.get("credits") or 0)
        slug = lookup.get(name.lower())
        if slug is None and (_slugify(name) in lookup.values()):
            slug = _slugify(name)
        price = None
        if slug and get_price is not None:
            try:
                price = get_price(slug)
            except Exception as e:
                print(f"[Baro] No price for {name}: {e}")
        rows.append({
            "name": name, "slug": slug, "ducats": ducats, "credits": credits, "price": price,
            "plat_per_100_ducats": round(price * 100 / ducats, 1) if price and ducats else None,
        })
    rows.sort(key=lambda r: (r["plat_per_100_ducats"] is None, -(r["plat_per_100_ducats"] or 0), r["name"]))
    return rows
