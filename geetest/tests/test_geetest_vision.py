"""Offline regressions for local shape proposals; no model or server requests."""

from pathlib import Path
import sys
import unittest

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py"))
import geetest_vision as vision


class VisionTests(unittest.TestCase):
    def test_normalization_keeps_holes_and_aspect(self):
        mask = np.zeros((80, 120), np.uint8)
        cv2.rectangle(mask, (10, 20), (109, 59), 1, -1)
        cv2.rectangle(mask, (30, 30), (89, 49), 0, -1)
        normalized, box = vision.normalize_mask(mask)
        self.assertEqual(box, (10, 20, 100, 40))
        self.assertFalse(normalized[64, 64])
        ys, xs = np.nonzero(normalized)
        self.assertAlmostEqual((xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1), 2.5, delta=.06)

    def test_ring_is_not_disk(self):
        disk = np.zeros((100, 100), np.uint8)
        cv2.circle(disk, (50, 50), 40, 1, -1)
        ring = disk.copy()
        cv2.circle(ring, (50, 50), 18, 0, -1)
        same = vision.match_score(ring, ring)
        different = vision.match_score(ring, disk)
        self.assertGreater(same, .95)
        self.assertLess(different, .80)
        self.assertGreater(same - different, .20)
        thin_ring = disk.copy()
        cv2.circle(thin_ring, (50, 50), 36, 0, -1)
        self.assertLess(vision.match_score(thin_ring, disk), .80)

    def test_rotation_recovers_asymmetric_shape(self):
        mask = np.zeros((100, 100), np.uint8)
        cv2.fillPoly(mask, [np.array([[18, 23], [80, 50], [18, 77], [33, 50]])], 1)
        matrix = cv2.getRotationMatrix2D((50, 50), 20, 1)
        rotated = cv2.warpAffine(mask, matrix, (100, 100), flags=cv2.INTER_NEAREST)
        self.assertGreater(vision.match_score(rotated, mask), .80)

    def test_transparent_icon_composited_before_threshold(self):
        arr = np.zeros((60, 60, 4), np.uint8)
        arr[10:50, 20:40, 3] = 255
        mask = vision.icon_mask(Image.fromarray(arr))
        self.assertIsNotNone(mask)
        ys, xs = np.nonzero(mask)
        self.assertAlmostEqual((xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1), .5, delta=.03)

    def test_saturated_background_does_not_swallow_colored_outline(self):
        image = Image.new("RGB", (320, 200), (240, 110, 60))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 155, 199), fill=(25, 170, 100))
        draw.ellipse((95, 65, 151, 121), outline=(140, 50, 210), width=3)
        shapes = vision.extract_shapes(image)
        self.assertTrue(any(vision._box_iou(shape["box"], (95, 65, 57, 57)) > .8 for shape in shapes))
        self.assertTrue(all(shape["box"][3] < 200 for shape in shapes))

    def test_multiple_synthetic_targets_are_recalled(self):
        # Entirely generated fixture: no downloaded challenge images or IDs.
        image = Image.new("RGB", (300, 200), (225, 225, 225))
        draw = ImageDraw.Draw(image)
        truth = [(35, 30, 45, 45), (120, 115, 49, 49), (215, 60, 43, 57)]
        draw.ellipse((35, 30, 79, 74), outline=(195, 40, 65), width=4)
        draw.rectangle((120, 115, 168, 163), outline=(40, 65, 195), width=4)
        draw.polygon([(215, 116), (236, 60), (257, 116)], fill=(40, 150, 70))
        shapes = vision.extract_shapes(image)
        for box in truth:
            with self.subTest(box=box):
                self.assertTrue(any(vision._box_iou(shape["box"], box) >= .5 for shape in shapes))


if __name__ == "__main__":
    unittest.main()
