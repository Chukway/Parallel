import os
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

import config
from arcane_catalog import ArcaneCatalog, ArcaneInfo, ArcaneStock
from arcane_scanner import (
    ArcaneCardSlicer,
    ArcaneCollectionSlicer,
    ArcaneMatcher,
    ArcaneScanner,
    ArcaneTile,
    apply_arcane_changes,
    apply_arcane_rank_changes,
    build_arcane_changes,
    build_arcane_rank_changes,
    format_arcane_changes,
    format_arcane_rank_changes,
    looks_like_collection_screen,
    read_arcane_rank,
)
from inventory_scanner import InventoryItem
from pillar2_ocr import OCRResult

GOLD = (80, 170, 215)  # BGR
NAMES = [
    "Secondary Merciless", "Secondary Deadhead", "Secondary Dexterity", "Primary Merciless",
    "Cascadia Overcharge", "Molt Efficiency", "Arcane Energize", "Arcane Grace",
]


def _catalog(tmp: str) -> ArcaneCatalog:
    catalog = ArcaneCatalog(Path(tmp) / "cat.json")
    catalog.refresh(fetch=lambda: [
        ArcaneInfo(n.lower().replace(" ", "_"), n, "id", 5) for n in NAMES
    ])
    return catalog


def _roi_frame() -> np.ndarray:
    _x, _y, width, height = config.ROI_PRESETS["inventory_sell"]
    return np.full((height, width, 3), 18, dtype=np.uint8)


def _draw_card(frame, slicer, col, row, name_gold=True, digit_blocks=(8, 8)):
    """Gold check circle + digit blocks in the badge area and a gold name bar in the label strip."""
    left = col * slicer.stride_x
    top = slicer._card_y_for_row(row)
    frame[top + 8: top + 34, left + 4: left + 30] = GOLD
    x = left + 38
    for width in digit_blocks:
        frame[top + 8: top + 34, x: x + width] = GOLD
        x += width + 3
    if name_gold:
        y = top + slicer.label_y + 10
        frame[y: y + 12, left + 20: left + 150] = GOLD


class FakeOCR:
    """Returns scripted names for label strips and counts for badge crops, in call order."""

    def __init__(self, names, counts):
        self.names = list(names)
        self.counts = list(counts)

    def _next_name(self):
        return [OCRResult(self.names.pop(0), 0.95)] if self.names else []

    def read_batch(self, frames, batch_size=16):
        return [self._next_name() for _ in frames]

    def read_regions(self, image, regions):
        return self._next_name()

    def read(self, frame):
        return self._next_name()

    def read_whole(self, image, allowlist=None, confidence_threshold=0.0):
        count = self.counts.pop(0) if self.counts else None
        return [OCRResult(str(count), 0.9)] if count else []


class ArcaneSlicerTests(unittest.TestCase):
    def test_label_strip_sits_above_the_rank_diamonds(self):
        frame = _roi_frame()
        slicer = ArcaneCardSlicer()
        slicer.configure_for_frame(frame)
        cells = slicer.slice_labels(frame)
        self.assertEqual(len(cells), 18)                       # 6 columns x 3 rows
        self.assertEqual(slicer.label_y, config.ARCANE_LABEL_Y_OFFSET)
        self.assertEqual(slicer.label_h, config.ARCANE_LABEL_HEIGHT)
        self.assertLessEqual(slicer.label_y + slicer.label_h, 167)   # diamonds start about 168 (1080p units)
        self.assertEqual(cells[0][1].shape[0], config.ARCANE_LABEL_HEIGHT)

    def test_prime_slicer_label_geometry_is_unchanged(self):
        from inventory_scanner import CardSlicer
        slicer = CardSlicer()
        slicer.configure_for_frame(_roi_frame())
        self.assertEqual(slicer.label_y, config.INVENTORY_LABEL_Y_OFFSET)


class ArcaneMatcherTests(unittest.TestCase):
    def test_noisy_ocr_still_finds_the_right_arcane(self):
        with tempfile.TemporaryDirectory() as tmp:
            matcher = ArcaneMatcher(_catalog(tmp))
            self.assertEqual(matcher.match("Secondary Merciless").url_name, "secondary_merciless")
            self.assertEqual(matcher.match("Secondry Mercilss").url_name, "secondary_merciless")
            self.assertEqual(matcher.match("Cascadia Overcharge").display_name, "Cascadia Overcharge")

    def test_name_without_arcane_prefix_is_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ArcaneMatcher(_catalog(tmp)).match("Energize").url_name, "arcane_energize")

    def test_garbage_matches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(ArcaneMatcher(_catalog(tmp)).match("Zzqx Vvbn"))


class ArcaneScanFrameTests(unittest.TestCase):
    def test_scan_reads_names_and_counts_and_ignores_empty_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            scanner = ArcaneScanner(_catalog(tmp), enable_capture=False)
            scanner.slicer.detect_vertical_shift = lambda frame: 0
            frame = _roi_frame()
            scanner.slicer.configure_for_frame(frame)
            for col, row in ((0, 0), (1, 0), (2, 1)):
                _draw_card(frame, scanner.slicer, col, row)
            counts = [None] * 18
            counts[0], counts[1], counts[8] = 70, 66, 28      # row-major cell order
            scanner._ocr = FakeOCR(
                ["Secondary Merciless", "Secondary Deadhead", "Secondary Dexterity"], counts
            )
            result = scanner.scan_frame(frame)
            found = {i.url_name: (i.quantity, i.grid_position) for i in result.items}
            self.assertEqual(found, {
                "secondary_merciless": (70, (0, 0)),
                "secondary_deadhead": (66, (1, 0)),
                "secondary_dexterity": (28, (2, 1)),
            })


class ArcaneReviewTests(unittest.TestCase):
    def _item(self, name, qty, score=100):
        return InventoryItem(name, name.lower().replace(" ", "_"), qty, score, name, (0, 0))

    def test_counts_replace_rank_zero_stock_and_rescan_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            stock = ArcaneStock(Path(tmp) / "stock.json")
            stock.add("secondary_merciless", 0, 3)
            stock.add("secondary_merciless", 5, 2)          # a ranked copy entered by hand stays
            items = [self._item("Secondary Merciless", 70), self._item("Molt Efficiency", 26, 80)]
            changes = build_arcane_changes(items, stock)
            self.assertEqual([(c.name, c.current, c.scanned) for c in changes],
                             [("Molt Efficiency", 0, 26), ("Secondary Merciless", 3, 70)])
            text = format_arcane_changes(changes, unreadable=2)
            self.assertIn("Secondary Merciless: 3 -> 70", text)
            self.assertIn("(check name)", text)             # score below 85
            self.assertIn("2 slot(s) could not be read", text)
            apply_arcane_changes(stock, changes)
            apply_arcane_changes(stock, build_arcane_changes(items, stock))
            rows = {(r.slug, r.rank): r.quantity for r in ArcaneStock(Path(tmp) / "stock.json").sorted_rows()}
            self.assertEqual(rows, {
                ("secondary_merciless", 0): 70,
                ("secondary_merciless", 5): 2,
                ("molt_efficiency", 0): 26,
            })


# ──────────────────────────────────────────────
# ARCANES (collection) screen
# ──────────────────────────────────────────────

SCREEN_W, SCREEN_H = 2000, 1250     # the 16:10 capture the grid fractions were measured on


def _draw_diamonds(image, centre_x, centre_y, filled, scale=1.0):
    """Five rank diamonds like the game draws them: bright outline, gold dot when filled."""
    for k in range(5):
        cx = int(round(centre_x + (k - 2) * 21.5 * scale))
        tip = int(round(8 * scale))
        pts = np.array([[cx - tip, centre_y], [cx, centre_y - tip], [cx + tip, centre_y], [cx, centre_y + tip]])
        cv2.polylines(image, [pts], True, (95, 165, 190) if k < filled else (60, 60, 60), 2)
        if k < filled:
            cv2.circle(image, (cx, centre_y), max(2, int(round(3 * scale))), (110, 190, 215), -1)


def _collection_frame(tiles):
    """Full-screen frame with {(col, row): rank} tiles: gold name bar + diamonds in the real positions."""
    frame = np.full((SCREEN_H, SCREEN_W, 3), 14, dtype=np.uint8)
    slicer = ArcaneCollectionSlicer()
    for (col, row), rank in tiles.items():
        x, y, w, h = slicer.tile_rect(col, row, frame.shape)
        ny = y + int(h * 0.7)
        frame[ny: ny + 14, x + 20: x + 150] = GOLD
        _draw_diamonds(frame, x + w // 2, y + int(round(h * config.ARCANE_TILE_DIAMOND_Y)), rank)
    return frame


class ArcaneRankReaderTests(unittest.TestCase):
    def _strip(self, filled, width=177):
        scale = width / 177.0
        strip = np.full((int(26 * scale) | 1, width, 3), 14, dtype=np.uint8)
        _draw_diamonds(strip, width // 2, strip.shape[0] // 2, filled, scale)
        return strip

    def test_every_rank_zero_to_five(self):
        for filled in range(6):
            self.assertEqual(read_arcane_rank(self._strip(filled)), filled)

    def test_scales_with_resolution(self):
        for width in (127, 255):
            self.assertEqual(read_arcane_rank(self._strip(3, width)), 3)

    def test_vertical_drift_of_a_couple_of_pixels_is_tolerated(self):
        self.assertEqual(read_arcane_rank(np.roll(self._strip(2), 2, axis=0)), 2)

    def test_no_diamond_row_is_not_a_tile(self):
        self.assertIsNone(read_arcane_rank(np.full((26, 177, 3), 14, dtype=np.uint8)))
        self.assertIsNone(read_arcane_rank(np.zeros((0, 0, 3), dtype=np.uint8)))
        self.assertIsNone(read_arcane_rank(np.full((26, 177, 3), 200, dtype=np.uint8)))   # bright, no outlines


class ArcaneCollectionSlicerTests(unittest.TestCase):
    def test_ranks_are_read_per_slot_and_empty_slots_are_skipped(self):
        frame = _collection_frame({(0, 0): 5, (1, 0): 4, (4, 1): 0, (2, 3): 2})
        slicer = ArcaneCollectionSlicer()
        self.assertEqual(slicer.read_tile_ranks(frame), {(0, 0): 5, (1, 0): 4, (4, 1): 0, (2, 3): 2})
        cells = slicer.slice_labels(frame)
        self.assertEqual([pos for pos, _img in cells], [(0, 0), (1, 0), (4, 1), (2, 3)])
        self.assertEqual(slicer.slice_quantity_badges(frame), [])

    def test_geometry_scales_with_the_frame(self):
        slicer = ArcaneCollectionSlicer()
        for width, height in ((1000, 625), (2880, 1800)):
            slicer.configure_for_frame(np.zeros((height, width, 3), dtype=np.uint8))
            x, y, _w, _h = slicer.tile_rect(1, 1)
            self.assertAlmostEqual(x / width, config.ARCANE_GRID_ORIGIN[0] + config.ARCANE_GRID_STRIDE[0], places=2)
            self.assertAlmostEqual(y / height, config.ARCANE_GRID_ORIGIN[1] + config.ARCANE_GRID_STRIDE[1], places=2)

    def test_layout_detection(self):
        self.assertTrue(looks_like_collection_screen(_collection_frame({(0, 0): 5, (1, 0): 1, (2, 0): 0})))
        self.assertFalse(looks_like_collection_screen(_roi_frame()))
        self.assertFalse(looks_like_collection_screen(np.full((SCREEN_H, SCREEN_W, 3), 14, dtype=np.uint8)))


class ArcaneCollectionScanTests(unittest.TestCase):
    def test_identical_tiles_are_kept_and_grouped_by_arcane_and_rank(self):
        with tempfile.TemporaryDirectory() as tmp:
            scanner = ArcaneScanner(_catalog(tmp), enable_capture=False)
            frame = _collection_frame({(0, 0): 5, (1, 0): 5, (2, 0): 3, (0, 1): 5})
            scanner._ocr = FakeOCR(
                ["Secondary Merciless", "Secondary Merciless", "Secondary Merciless", "Molt Efficiency"], []
            )
            result = scanner.scan_collection_frame(frame)
            self.assertEqual(result.layout, "collection")
            self.assertEqual(len(result.tiles), 4)           # duplicates are not merged away
            changes = build_arcane_rank_changes(result.tiles, ArcaneStock(Path(tmp) / "s.json"))
            self.assertEqual([(c.name, c.rank, c.seen) for c in changes], [
                ("Molt Efficiency", 5, 1), ("Secondary Merciless", 5, 2), ("Secondary Merciless", 3, 1),
            ])


class ArcaneRankReviewTests(unittest.TestCase):
    @staticmethod
    def _tiles(*pairs):
        return [ArcaneTile(name.lower().replace(" ", "_"), name, rank, 100, (i, 0))
                for i, (name, rank) in enumerate(pairs)]

    def test_counts_are_raised_to_max_of_stock_and_seen_and_rescans_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            stock = ArcaneStock(Path(tmp) / "stock.json")
            stock.add("secondary_merciless", 5, 3)
            stock.add("secondary_merciless", 0, 4)            # unranked stock is left alone
            screen = self._tiles(("Secondary Merciless", 5), ("Secondary Merciless", 5),
                                 ("Molt Efficiency", 4))
            changes = build_arcane_rank_changes(screen, stock)
            text = format_arcane_rank_changes(changes, unreadable=1)
            self.assertIn("Secondary Merciless, rank 5 (max): 2 seen, owned 3 (unchanged)", text)
            self.assertIn("Molt Efficiency, rank 4: 1 seen, owned 0 -> 1", text)
            self.assertIn("1 tile(s) could not be read", text)
            self.assertEqual(apply_arcane_rank_changes(stock, changes), 1)
            # an overlapping rescan with more copies in view: raised once, never added twice
            more = self._tiles(("Secondary Merciless", 5), ("Secondary Merciless", 5),
                               ("Secondary Merciless", 5), ("Secondary Merciless", 5), ("Molt Efficiency", 4))
            apply_arcane_rank_changes(stock, build_arcane_rank_changes(more, stock))
            apply_arcane_rank_changes(stock, build_arcane_rank_changes(more, stock))
            rows = {(r.slug, r.rank): r.quantity for r in ArcaneStock(Path(tmp) / "stock.json").sorted_rows()}
            self.assertEqual(rows, {
                ("molt_efficiency", 4): 1, ("secondary_merciless", 0): 4, ("secondary_merciless", 5): 4,
            })


# ──────────────────────────────────────────────
# Reference screenshots (optional: skipped when the files are not on this machine)
# ──────────────────────────────────────────────

# Reference screenshots are not part of the repository: point ARCANE_REF_DIR at a folder holding them
# (22.webp, 23.webp, 24.webp, 25.webp) to run the image tests; otherwise those tests skip.
REF_DIR = Path(os.environ.get("ARCANE_REF_DIR", "")) if os.environ.get("ARCANE_REF_DIR") else Path("__no_reference_images__")
# crop file -> tile origin inside the crop, in pixels. The crops come from a 2880x1800 capture whose
# first tile sits at (144, 298), so they are pasted back at that spot to rebuild a full-screen frame.
REF_CROPS = {"22": (37, 35), "23": (91, 67), "24": (130, 115)}


def _ref_screen(name):
    path = REF_DIR / f"{name}.webp"
    if not path.exists():
        return None
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or name == "25":
        return image
    ox, oy = REF_CROPS[name]
    canvas = np.zeros((1800, 2880, 3), dtype=np.uint8)
    x, y = 144 - ox, 298 - oy
    canvas[y: y + image.shape[0], x: x + image.shape[1]] = image
    return canvas


class ArcaneReferenceScreenshotTests(unittest.TestCase):
    def test_full_screen_ranks(self):
        frame = _ref_screen("25")
        if frame is None:
            self.skipTest("reference screenshot not available")
        self.assertTrue(looks_like_collection_screen(frame))
        ranks = ArcaneCollectionSlicer().read_tile_ranks(frame)
        self.assertEqual(len(ranks), 20)
        for row in range(3):
            self.assertEqual([ranks[(col, row)] for col in range(5)], [5] * 5)
        self.assertEqual([ranks[(col, 3)] for col in range(5)], [4, 4, 2, 1, 1])

    def test_cropped_screens_are_all_max_rank(self):
        for name in REF_CROPS:
            frame = _ref_screen(name)
            if frame is None:
                self.skipTest("reference screenshot not available")
            self.assertTrue(looks_like_collection_screen(frame), name)
            ranks = ArcaneCollectionSlicer().read_tile_ranks(frame)
            self.assertEqual(len(ranks), 15 if name == "24" else 20, name)
            self.assertEqual(set(ranks.values()), {5}, name)


if __name__ == "__main__":
    unittest.main()
