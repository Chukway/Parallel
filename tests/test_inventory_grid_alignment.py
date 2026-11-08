import unittest

import numpy as np

import config
from inventory_scanner import CardSlicer


def _grid_frame(offset: int, rows: int = 4) -> np.ndarray:
    """A 1080p-sized inventory ROI: lighter card blocks on a dark background."""
    _x, _y, width, height = config.ROI_PRESETS["inventory_sell"]
    frame = np.full((height, width, 3), 18, dtype=np.uint8)
    rng = np.random.default_rng(3)
    for row in range(rows):
        for col in range(config.INVENTORY_GRID_COLS):
            left = col * config.INVENTORY_CARD_STRIDE_X
            top = row * config.INVENTORY_CARD_STRIDE_Y + offset
            if top < 0 or top + config.INVENTORY_CARD_HEIGHT > height:
                continue
            block = rng.integers(30, 90, (config.INVENTORY_CARD_HEIGHT, config.INVENTORY_CARD_WIDTH, 3))
            frame[top: top + config.INVENTORY_CARD_HEIGHT, left: left + config.INVENTORY_CARD_WIDTH] = block
    return frame


class VerticalShiftTests(unittest.TestCase):
    def _shift(self, offset: int) -> int:
        frame = _grid_frame(offset)
        slicer = CardSlicer()
        slicer.configure_for_frame(frame)
        return slicer.detect_vertical_shift(frame)

    def test_lower_grid_is_measured_relative_to_the_aligned_one(self):
        aligned = self._shift(0)
        self.assertAlmostEqual(self._shift(30) - aligned, 30, delta=2)
        self.assertAlmostEqual(self._shift(21) - aligned, 21, delta=2)

    def test_higher_grid_gives_a_negative_shift(self):
        self.assertLess(self._shift(-12), self._shift(0))

    def test_blank_frame_gives_no_shift(self):
        _x, _y, width, height = config.ROI_PRESETS["inventory_sell"]
        frame = np.full((height, width, 3), 18, dtype=np.uint8)
        slicer = CardSlicer()
        slicer.configure_for_frame(frame)
        self.assertEqual(slicer.detect_vertical_shift(frame), 0)


if __name__ == "__main__":
    unittest.main()
