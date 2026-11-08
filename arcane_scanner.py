"""Arcane scan: reads arcane names, ranks and owned counts from the screen.

Two layouts are understood and picked automatically (config.ARCANE_SCAN_LAYOUT):

* Inventory > Sell > ARCANES: a 6-column card grid with a count badge per card. It reuses
  the prime scanner's capture, grid alignment, count-badge reading and OCR pipeline.
  Rank is not visible there, so counts are stored as rank 0 (ARCANE_RANK).
* The ARCANES (collection) screen: a 5-column tile grid where EVERY COPY IS ITS OWN TILE
  (no count badge) and the number of filled diamonds under the name is the rank. Each tile's
  name is OCR'd and its rank read, then identical (arcane, rank) tiles are grouped into counts.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import cv2
import numpy as np

import config
from arcane_catalog import ArcaneCatalog, ArcaneStock, stock_key
from inventory_scanner import CardSlicer, InventoryItem, InventoryScanner, InventoryScanResult
from pillar1_capture import FrameGrabber
from pillar2_ocr import FuzzyMatcher

ARCANE_RANK = 0   # Sell-tab scans cannot see rank, so those counts are stored as unranked stock


class ArcaneCardSlicer(CardSlicer):
    """Same grid as the prime scanner; only the name strip is placed for arcane cards."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_label_y = float(getattr(config, "ARCANE_LABEL_Y_OFFSET", 121))
        self.base_label_h = float(getattr(config, "ARCANE_LABEL_HEIGHT", 45))
        self._apply_scale(self._scale_x, self._scale_y)


# ──────────────────────────────────────────────
# Collection screen: rank reader and tile grid
# ──────────────────────────────────────────────

def _gray(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image


def _diamond_centres(width: int) -> list[float]:
    """x of each diamond centre inside a strip as wide as one tile."""
    pitch = float(getattr(config, "ARCANE_DIAMOND_PITCH", 0.1215))
    count = int(getattr(config, "ARCANE_DIAMOND_COUNT", 5))
    return [width * (0.5 + (k - (count - 1) / 2.0) * pitch) for k in range(count)]


def _has_outline(gray: np.ndarray, cx: float, cy: float, scale: float) -> bool:
    """True when a diamond outline is drawn here: bright left/right/top/bottom tips, dark corners.

    Filled and hollow diamonds both have the outline; a bare tile or other UI does not."""
    height, width = gray.shape[:2]
    tip, corner = 8.0 * scale, 7.0 * scale
    ring_min = float(getattr(config, "ARCANE_RANK_RING_MIN_GRAY", 40))

    def sample(x: float, y: float, reduce) -> float:
        xi, yi = int(round(x)), int(round(y))
        if not (0 <= xi < width and 0 <= yi < height):
            return 0.0
        return float(reduce(gray[max(0, yi - 1): yi + 2, max(0, xi - 1): xi + 2]))

    def peak(x: float, y: float) -> float:
        return sample(x, y, np.max)

    tips = [peak(cx - tip, cy), peak(cx + tip, cy), peak(cx, cy - tip), peak(cx, cy + tip)]
    corners = [sample(cx + sx * corner, cy + sy * corner, np.mean) for sx in (-1, 1) for sy in (-1, 1)]
    outside = [sample(cx - tip - 2.5 * scale, cy, np.mean), sample(cx + tip + 2.5 * scale, cy, np.mean)]
    dark = float(np.mean(corners + outside))
    return min(tips) >= ring_min and float(np.mean(tips)) >= dark + 20.0


def read_arcane_rank(strip: np.ndarray) -> int | None:
    """Rank (0-5) from one tile's diamond strip: the number of FILLED diamonds.

    `strip` is a crop as wide as the tile and vertically centred on the diamond row. Returns
    None when the strip does not show the diamond row (an empty grid slot, other UI). A filled
    diamond has a bright gold centre; a hollow one is only a dim outline, so the mean gray of a
    small centre patch separates them (calibration: ARCANE_RANK_* in config.py).
    """
    if strip is None or strip.size == 0:
        return None
    gray = _gray(strip)
    height, width = gray.shape[:2]
    scale = width / 177.0                       # the calibration tile is 177 px wide
    radius = max(1, int(round(float(getattr(config, "ARCANE_RANK_PATCH", 0.0113)) * width)))
    wobble = max(1, int(round(2 * scale)))      # tolerate a couple of pixels of vertical drift
    filled_min = float(getattr(config, "ARCANE_RANK_FILLED_MIN_GRAY", 70))
    centres = _diamond_centres(width)
    # Find the row the outlines actually sit on (closest to the strip centre wins ties).
    offsets = sorted(range(-wobble - 1, wobble + 2), key=abs)
    scored = [(sum(_has_outline(gray, cx, (height - 1) / 2.0 + dy, scale) for cx in centres), dy)
              for dy in offsets]
    outlines = max(count for count, _dy in scored)
    if outlines < int(getattr(config, "ARCANE_RANK_MIN_RINGS", 4)):
        return None
    cy = (height - 1) / 2.0 + next(dy for count, dy in scored if count == outlines)
    rank = 0
    for cx in centres:
        best = 0.0
        for dy in (-1, 0, 1):
            y, x = int(round(cy + dy)), int(round(cx))
            patch = gray[max(0, y - radius): y + radius + 1, max(0, x - radius): x + radius + 1]
            if patch.size:
                best = max(best, float(patch.mean()))
        if best >= filled_min:
            rank += 1
    return rank


class ArcaneCollectionSlicer:
    """Cuts the ARCANES screen (full-screen frame) into 5-column tiles.

    Everything is a fraction of the screen (ARCANE_GRID_* in config.py), so it scales with the
    capture size. It mimics the CardSlicer interface so the prime scanner's OCR pipeline can run
    on the name strips. Slots without a diamond row (empty space after the last tile) are
    skipped, and each tile's rank is kept in `tile_ranks` after slice_labels().
    """

    def __init__(self):
        self.label = "arcane collection"
        self.cols = int(getattr(config, "ARCANE_GRID_COLS", 5))
        self.rows = int(getattr(config, "ARCANE_GRID_ROWS", 4))
        self._frame_w = self._frame_h = 0
        self.tile_ranks: dict[tuple[int, int], int] = {}

    def configure_for_frame(self, frame: np.ndarray):
        self._frame_h, self._frame_w = frame.shape[:2]

    def detect_vertical_shift(self, frame: np.ndarray) -> int:
        return 0

    def slice_quantity_badges(self, frame: np.ndarray):
        return []                               # no count badges: each copy is a tile

    def tile_rect(self, col: int, row: int, frame_shape=None) -> tuple[int, int, int, int]:
        height, width = (frame_shape or (self._frame_h, self._frame_w))[:2]
        ox, oy = config.ARCANE_GRID_ORIGIN
        sx, sy = config.ARCANE_GRID_STRIDE
        tw, th = config.ARCANE_TILE_SIZE
        return (int(round((ox + col * sx) * width)), int(round((oy + row * sy) * height)),
                max(1, int(round(tw * width))), max(1, int(round(th * height))))

    def diamond_strip(self, frame: np.ndarray, col: int, row: int) -> np.ndarray:
        x, y, w, h = self.tile_rect(col, row, frame.shape)
        top = max(0, int(round(y + h * float(config.ARCANE_TILE_DIAMOND_Y))) - max(4, int(round(0.075 * h))))
        return frame[top: top + 2 * max(4, int(round(0.075 * h))) + 1, max(0, x): x + w]

    def name_strip(self, frame: np.ndarray, col: int, row: int) -> np.ndarray:
        x, y, w, h = self.tile_rect(col, row, frame.shape)
        y1, y2 = (y + int(round(h * f)) for f in config.ARCANE_TILE_NAME_Y)
        return frame[max(0, y1): y2, max(0, x): x + w]

    def read_tile_ranks(self, frame: np.ndarray) -> dict[tuple[int, int], int]:
        """(col, row) -> rank for every slot that holds a complete tile."""
        self.configure_for_frame(frame)
        ranks = {}
        for row in range(self.rows):
            for col in range(self.cols):
                x, y, w, h = self.tile_rect(col, row)
                if x + w > self._frame_w or y + h > self._frame_h:
                    continue                    # partial tile at the screen edge
                rank = read_arcane_rank(self.diamond_strip(frame, col, row))
                if rank is not None:
                    ranks[(col, row)] = rank
        return ranks

    def slice_labels(self, frame: np.ndarray):
        self.tile_ranks = self.read_tile_ranks(frame)
        order = sorted(self.tile_ranks, key=lambda pos: (pos[1], pos[0]))
        return [(pos, self.name_strip(frame, *pos)) for pos in order]


def looks_like_collection_screen(frame: np.ndarray) -> bool:
    """True when the ARCANES screen's diamond rows are where the collection grid expects them."""
    slicer = ArcaneCollectionSlicer()
    slicer.configure_for_frame(frame)
    found = sum(read_arcane_rank(slicer.diamond_strip(frame, col, 0)) is not None
                for col in range(slicer.cols))
    return found >= 2


class _ArcaneNameCache:
    """Minimal ItemsCache stand-in so FuzzyMatcher can match against arcane names."""

    def __init__(self, catalog: ArcaneCatalog):
        self._names = catalog.names()          # display name -> slug

    @property
    def names(self) -> list[str]:
        return list(self._names)

    def url_name(self, display_name: str):
        return self._names.get(display_name)


class ArcaneMatcher:
    """FuzzyMatcher over arcane names; retries with the 'Arcane ' prefix some names carry."""

    def __init__(self, catalog: ArcaneCatalog):
        self._inner = FuzzyMatcher(_ArcaneNameCache(catalog), prime_only=False)

    def match(self, text: str):
        best = self._inner.match(text)
        if best is not None and best.url_name:
            return best
        if text.strip().lower().startswith("arcane "):
            return None
        retry = self._inner.match(f"Arcane {text}")
        return retry if retry is not None and retry.url_name else None


@dataclass
class ArcaneTile:
    """One tile (= one owned copy) read from the collection screen."""
    slug: str
    name: str
    rank: int
    score: int
    position: tuple[int, int]


@dataclass
class ArcaneScanResult(InventoryScanResult):
    layout: str = "sell"                       # "sell" (counts, no rank) or "collection" (tiles + rank)
    tiles: list[ArcaneTile] = field(default_factory=list)


class ArcaneScanner(InventoryScanner):
    """Scans the visible arcane cards. `scan()` captures the screen, `scan_frame()` takes a Sell-tab
    image and `scan_collection_frame()` a full-screen ARCANES-screen image."""

    def __init__(self, catalog: ArcaneCatalog, enable_capture: bool = True,
                 ocr_context: str = "inventory_6x3"):
        # Deliberately not calling InventoryScanner.__init__: it would load the prime-part item list.
        self.ocr_context = str(ocr_context)
        self.grabber = FrameGrabber() if enable_capture else None
        if self.grabber is not None:
            self.grabber.set_roi_preset("inventory_sell")
        self.items_cache = None
        self.matcher = ArcaneMatcher(catalog)
        self.slicer = ArcaneCardSlicer(label="arcanes 6x3")
        self._ocr = None
        self._ocr_backend_name = str(
            getattr(config, "OCR_ENGINE_BY_CONTEXT", {}).get(ocr_context, config.OCR_ENGINE)
        )
        self._ocr_backend_key = self._ocr_backend_name.lower().replace("_", "").replace("-", "")
        self._uses_windows_ocr = self._ocr_backend_key.startswith("windows")
        self._uses_rapid_ocr = self._ocr_backend_key.startswith("rapid")
        self._fallback_ocr = None

    def close(self):
        if self.grabber is not None:
            self.grabber.close()
            self.grabber = None
        self._ocr = None
        self._fallback_ocr = None

    def scan(self):
        """Capture and scan. With layout "auto" the whole screen is grabbed once, then either the
        ARCANES screen is read (tiles + ranks) or the Sell-tab region is cut out of it."""
        layout = str(getattr(config, "ARCANE_SCAN_LAYOUT", "auto")).strip().lower()
        if self.grabber is None:
            raise RuntimeError("ArcaneScanner was created without capture. Use scan_frame(frame).")
        if layout == "sell":
            self.grabber.set_roi_preset("inventory_sell")
            return self._as_sell_result(super().scan())
        width, height = config.screen_size()
        self.grabber.set_roi(0, 0, width, height)
        try:
            frame = self.grabber.capture(preprocess=False)
        finally:
            self.grabber.set_roi_preset("inventory_sell")
        if frame is None:
            raise RuntimeError("Screen capture returned None. Is Warframe running?")
        if layout == "collection" or looks_like_collection_screen(frame):
            return self.scan_collection_frame(frame)
        x, y, w, h = config.scaled_roi_preset("inventory_sell")
        return self.scan_frame(frame[y: y + h, x: x + w])

    def scan_frame(self, frame: np.ndarray):
        return self._as_sell_result(super().scan_frame(frame))

    @staticmethod
    def _as_sell_result(result: InventoryScanResult) -> ArcaneScanResult:
        return ArcaneScanResult(**{k: getattr(result, k) for k in InventoryScanResult.__dataclass_fields__})

    def scan_collection_frame(self, frame: np.ndarray) -> ArcaneScanResult:
        """Read a full-screen image of the ARCANES screen: one item per tile, with its rank."""
        import time
        if frame is None or frame.size == 0:
            raise RuntimeError("scan_collection_frame() received an empty image.")
        if self._ocr is None:
            from pillar2_ocr import create_ocr_engine
            self._ocr = create_ocr_engine(self.ocr_context)
        started = time.time()
        collection = ArcaneCollectionSlicer()
        sell_slicer, self.slicer = self.slicer, collection
        try:
            base = self._scan_frame(frame, started, started, merge_duplicates=False)
        finally:
            self.slicer = sell_slicer
        tiles = [
            ArcaneTile(item.url_name, item.display_name, collection.tile_ranks[item.grid_position],
                       int(item.match_score), item.grid_position)
            for item in base.items if item.grid_position in collection.tile_ranks
        ]
        result = self._as_sell_result(base)
        result.layout, result.tiles = "collection", tiles
        return result


# ──────────────────────────────────────────────
# Review / apply helpers (pure, no UI)
# ──────────────────────────────────────────────

@dataclass
class ArcaneChange:
    slug: str
    name: str
    scanned: int      # count read from the card
    current: int      # rank-0 count already in the stock list
    score: int        # fuzzy name match confidence


def build_arcane_changes(items: list[InventoryItem], stock: ArcaneStock) -> list[ArcaneChange]:
    changes = []
    for item in items:
        current = stock.rows.get(stock_key(item.url_name, ARCANE_RANK))
        changes.append(ArcaneChange(
            slug=item.url_name,
            name=item.display_name,
            scanned=max(1, int(item.quantity)),
            current=current.quantity if current else 0,
            score=int(item.match_score),
        ))
    return sorted(changes, key=lambda c: c.name)


def format_arcane_changes(changes: list[ArcaneChange], unreadable: int = 0) -> str:
    lines = [f"Set your unranked arcane stock from the {len(changes)} card(s) read:", ""]
    for change in changes:
        flag = "   (check name)" if change.score < 85 else ""
        if change.current == change.scanned:
            lines.append(f"{change.name}: {change.scanned} (unchanged){flag}")
        else:
            lines.append(f"{change.name}: {change.current} -> {change.scanned}{flag}")
    if unreadable:
        lines += ["", f"{unreadable} slot(s) could not be read and are left alone."]
    lines += ["", "The Sell tab does not show rank, so these are stored as unranked. Each count "
              "replaces the stock count, so scanning the same screen twice is safe.",
              "Apply these counts?"]
    return "\n".join(lines)


def apply_arcane_changes(stock: ArcaneStock, changes: list[ArcaneChange]) -> int:
    """Write the confirmed counts (replace, not add) to the rank-0 stock rows."""
    for change in changes:
        stock.set_quantity(change.slug, ARCANE_RANK, change.scanned)
    return len(changes)


# ── Collection screen: counts per (arcane, rank) ──
#
# DOUBLE-COUNT RULE. Every copy is a tile, and the user scrolls and rescans, so consecutive
# screens overlap and the same tiles are seen again. Adding the counts would inflate the stock
# on every rescan, and "set to what is on this screen" would wipe copies that are scrolled out
# of view. So each (arcane, rank) is raised to MAX(stock, seen on this screen): rescanning the
# same or an overlapping screen is idempotent and a scan never lowers the stock. The trade-offs
# are that copies sold or used must be removed on the Arcanes page, and a group of identical
# tiles split across two non-overlapping screens counts as the larger half until a screen shows
# the whole group. The review text says all of this.

@dataclass
class ArcaneRankChange:
    slug: str
    name: str
    rank: int
    seen: int         # identical tiles on this screen
    current: int      # copies already in the stock at this rank
    score: int        # weakest name match among those tiles

    @property
    def new(self) -> int:
        return max(self.current, self.seen)


def build_arcane_rank_changes(tiles: list[ArcaneTile], stock: ArcaneStock) -> list[ArcaneRankChange]:
    seen: Counter = Counter()
    names: dict[str, str] = {}
    scores: dict[tuple[str, int], int] = {}
    for tile in tiles:
        key = (tile.slug, tile.rank)
        seen[key] += 1
        names[tile.slug] = tile.name
        scores[key] = min(scores.get(key, 100), tile.score)
    changes = []
    for (slug, rank), count in seen.items():
        current = stock.rows.get(stock_key(slug, rank))
        changes.append(ArcaneRankChange(slug, names[slug], rank, count,
                                        current.quantity if current else 0, scores[(slug, rank)]))
    return sorted(changes, key=lambda c: (c.name, -c.rank))


def format_arcane_rank_changes(changes: list[ArcaneRankChange], unreadable: int = 0) -> str:
    max_rank = int(getattr(config, "ARCANE_DIAMOND_COUNT", 5))
    copies = sum(c.seen for c in changes)
    lines = [f"{copies} arcane(s) read on this screen, {len(changes)} arcane/rank group(s):", ""]
    for c in changes:
        flag = "   (check name)" if c.score < 85 else ""
        label = f"{c.name}, rank {c.rank}" + (" (max)" if c.rank == max_rank else "")
        if c.new == c.current:
            lines.append(f"{label}: {c.seen} seen, owned {c.current} (unchanged){flag}")
        else:
            lines.append(f"{label}: {c.seen} seen, owned {c.current} -> {c.new}{flag}")
    if unreadable:
        lines += ["", f"{unreadable} tile(s) could not be read and are left alone."]
    lines += ["", "Each count is raised to the larger of your stock and what is seen here, so scrolling "
              "and rescanning overlapping screens never counts a copy twice, and a scan never lowers "
              "your stock (remove sold copies on the Arcanes page).",
              "Apply these counts?"]
    return "\n".join(lines)


def apply_arcane_rank_changes(stock: ArcaneStock, changes: list[ArcaneRankChange]) -> int:
    """Raise each (arcane, rank) to max(stock, seen); returns how many rows changed."""
    changed = 0
    for c in changes:
        if c.new != c.current:
            stock.set_quantity(c.slug, c.rank, c.new)
            changed += 1
    return changed
