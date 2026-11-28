"""Which spare Prime parts to trade to Baro Ki'Teer for ducats, and removing them afterwards.

A part is only recommended for ducats when it sells for no more than the
platinum limit set for its ducat value (Settings, ducat filter) and it is not
needed for a set the Item List can already complete.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

import config

DUCATS = "ducats"   # trade it to Baro
SELL = "sell"       # worth more as platinum
KEEP = "keep"       # part of a set you can complete
CHECK = "check"     # no market price: decide yourself


@dataclass
class DucatRow:
    url_name: str
    name: str
    owned: int
    ducats: int
    price: int | None
    verdict: str
    use: int            # copies recommended for ducats
    reason: str
    listed: int = 0     # copies currently listed on warframe.market

    @property
    def ducats_per_plat(self) -> float:
        return self.ducats / max(1, self.price or 0) if self.price is not None else 0.0


def plat_limit(ducats: int, rules: dict | None = None) -> int:
    """The most platinum a part may sell for and still be better spent as ducats."""
    rules = config.DUCAT_BULK_PLAT_VALUES if rules is None else rules
    table = {int(k): int(v) for k, v in dict(rules or {}).items()}
    if int(ducats) in table:
        return table[int(ducats)]
    return int(ducats) // 10


def reserved_for_sets(url_name: str, owned: dict[str, int], catalog) -> int:
    """Copies of this part that are tied up in sets the Item List can complete now."""
    if catalog is None:
        return 0
    if not catalog.is_known(url_name):
        catalog.refresh([url_name])
    set_slug = catalog.set_slug_for(url_name)
    if not set_slug or set_slug == url_name:
        return 0
    status = catalog.status_for(set_slug, owned)
    if status is None or status.buildable < 1:
        return 0
    needed = next((part.needed for part in status.parts if part.slug == url_name), 0)
    return needed * status.buildable


def assess(owned: int, ducats: int, price: int | None, reserved: int = 0,
           rules: dict | None = None) -> tuple[str, int, str]:
    """(verdict, copies to use for ducats, why)."""
    if price is None:
        return CHECK, 0, "No market price found. Check it yourself before using it."
    limit = plat_limit(ducats, rules)
    if price > limit:
        return SELL, 0, f"Sells for {price}p, above your {limit}p limit for {ducats} ducats."
    spare = max(0, owned - reserved)
    if spare < 1:
        return KEEP, 0, "Needed for a set you can complete."
    reason = f"{ducats} ducats; only sells for {price}p."
    if reserved:
        reason += f" {reserved} kept for your complete set."
    return DUCATS, spare, reason


def build_rows(store, get_ducats, get_price, catalog=None, progress=None, should_stop=None) -> list[DucatRow]:
    """One row per owned item that has a ducat value, best ducat picks first."""
    owned = {slug: int(item.quantity) for slug, item in list(store.items.items()) if item.quantity > 0}
    candidates = []
    for slug in sorted(owned):
        if slug.endswith("_set"):
            continue
        ducats = get_ducats(slug)
        if ducats:
            candidates.append((slug, int(ducats)))
    rows = []
    for done, (slug, ducats) in enumerate(candidates, 1):
        if should_stop is not None and should_stop():
            break
        try:
            price = get_price(slug)
            price = None if price is None or int(price) <= 0 else int(price)
        except Exception as e:
            print(f"[Ducats] No price for {slug}: {e}")
            price = None
        reserved = 0
        if price is not None and price <= plat_limit(ducats):
            try:
                reserved = reserved_for_sets(slug, owned, catalog)
            except Exception as e:
                print(f"[Ducats] Set check failed for {slug}: {e}")
                rows.append(DucatRow(slug, store.items[slug].display_name, owned[slug], ducats, price,
                                     CHECK, 0, "Could not check which set it belongs to."))
                if progress is not None:
                    progress(done, len(candidates))
                continue
        verdict, use, reason = assess(owned[slug], ducats, price, reserved)
        listing = getattr(store, "listings", {}).get(slug)
        rows.append(DucatRow(slug, store.items[slug].display_name, owned[slug], ducats, price,
                             verdict, use, reason, int(listing.quantity) if listing is not None else 0))
        if progress is not None:
            progress(done, len(candidates))
    order = {DUCATS: 0, CHECK: 1, KEEP: 2, SELL: 3}
    rows.sort(key=lambda r: (order[r.verdict], -r.ducats_per_plat, r.name))
    return rows


def remove_from_item_list(store, picks: list[tuple[str, int]]) -> int:
    """Take the chosen copies out of the Item List. Returns how many copies came out."""
    removed = 0
    for url_name, count in picks:
        item = store.items.get(url_name)
        take = min(int(count), int(item.quantity)) if item is not None else 0
        if take > 0 and store.take_item_quantity(url_name, take):
            removed += take
    return removed


def remove_from_listings(api, store, picks: list[tuple[str, int]], pause=None) -> dict[str, list[str]]:
    """Lower (or delete) the warframe.market sell listings by the chosen copies.

    Returns the url names under "lowered", "deleted", "not_listed" and "failed".
    """
    if not getattr(config, "ENABLE_LIVE_LISTING", False):
        raise RuntimeError("Live listing changes are turned off.")
    if pause is None:
        pause = lambda: time.sleep(random.uniform(
            config.LISTING_REQUEST_DELAY_MIN_SECONDS, config.LISTING_REQUEST_DELAY_MAX_SECONDS))
    result: dict[str, list[str]] = {"lowered": [], "deleted": [], "not_listed": [], "failed": []}
    api.ensure_item_id_manifest()
    existing = api.get_existing_sell_listing_map()
    changed = False
    for url_name, count in picks:
        try:
            listing = existing.get(api.get_item_id(url_name)) or existing.get(url_name)
            if listing is None:
                result["not_listed"].append(url_name)
                continue
            if changed:
                pause()
            remaining = int(listing.quantity) - int(count)
            if remaining <= 0:
                if not api.delete_order(listing.order_id):
                    result["failed"].append(url_name)
                    continue
                result["deleted"].append(url_name)
            else:
                api.update_order_price(listing.order_id, listing.platinum, quantity=remaining,
                                       visible=listing.visible)
                result["lowered"].append(url_name)
            changed = True
        except Exception as e:
            print(f"[Ducats] Listing change failed for {url_name}: {e}")
            result["failed"].append(url_name)
    if changed:
        try:
            store.replace_listings(api.get_existing_sell_listing_map())
        except Exception as e:
            print(f"[Ducats] Listings changed, but the local copy was not refreshed: {e}")
    return result
