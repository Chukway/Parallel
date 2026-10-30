import unittest

import numpy as np

from inventory_scanner import _quantity_digit_crop

GOLD = (80, 170, 215)  # BGR


def _badge(digit_blocks, lines_after=()):
    image = np.full((44, 92, 3), (40, 30, 20), dtype=np.uint8)
    image[8:34, 4:30] = GOLD            # check circle (solid block stands in for the ring)
    x = 38
    for width in digit_blocks:
        image[8:34, x:x + width] = GOLD
        x += width + 3
    for column in lines_after:
        image[:, column] = (150, 140, 130)  # grey card-art line
    return image


class QuantityDigitCropTests(unittest.TestCase):
    def test_crop_starts_after_circle_and_ignores_card_art_lines(self):
        crop = _quantity_digit_crop(_badge([8], lines_after=(60, 66, 72)))
        self.assertLess(crop.shape[1], 24)
        self.assertTrue((crop[:, :, 2] > 150).any())

    def test_two_digits_are_kept(self):
        crop = _quantity_digit_crop(_badge([8, 8], lines_after=(70, 76)))
        self.assertGreaterEqual(crop.shape[1], 19)

    def test_blank_badge_falls_back_without_error(self):
        blank = np.zeros((44, 92, 3), dtype=np.uint8)
        self.assertGreater(_quantity_digit_crop(blank).size, 0)


if __name__ == "__main__":
    unittest.main()
