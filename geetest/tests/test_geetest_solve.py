"""Production solver regressions; no API calls or live challenges."""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import geetest_solve as gs


class SolverRegressionTests(unittest.TestCase):
    def setUp(self):
        offline = patch.object(gs, "chat", side_effect=AssertionError("unexpected network call in offline test"))
        offline.start()
        self.addCleanup(offline.stop)

    def test_normalized_coordinates_ignore_reported_pixel_size(self):
        self.assertEqual(gs.to_center_pixels([.5, .5], (600, 400), (300, 200), [1000, 1000]), [150, 100])
        self.assertEqual(gs.to_pixels([.1, .2, .3, .4], (600, 400), (300, 200), [1000, 1000]), [30, 40, 90, 80])
        self.assertEqual(gs.to_pixels([20, 40, 60, 80], (300, 200), (300, 200), [600, 400]), [10, 20, 30, 40])

    def test_nonfinite_coordinate_is_rejected(self):
        for bad in (float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                gs.to_pixels([bad, 2, 30, 40], (300, 200), (300, 200), None)

    def test_preprocessing_preserves_aspect_ratio(self):
        im = Image.new("RGB", (300, 200))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        self.assertEqual(gs.prepare_main(buf.getvalue()).size, (600, 400))
        self.assertEqual(gs.upscale_to(Image.new("RGB", (90, 40)), 256).size, (630, 280))

    def test_candidate_response_must_name_existing_id_and_clear_match(self):
        candidates = [{"id": 1, "box": [10, 10, 50, 50]}]
        for response in ({"candidate_id": 99, "match": "clear"},
                         {"candidate_id": 1, "match": "uncertain"},
                         {"candidate_id": True, "match": "clear"}):
            with patch.object(gs, "chat", return_value=json.dumps(response)):
                choice = gs._choose_candidate(Image.new("RGB", (20, 20)), candidates, "panel", "main")
                self.assertIsNone(choice["candidate_id"])

    def test_clipped_background_cannot_supply_geometry_rescue_evidence(self):
        shapes = [{"box": (94, 172, 36, 28)}, {"box": (179, 4, 49, 48)}]
        candidates = [{"box": [94, 172, 130, 200]}, {"box": [179, 4, 228, 52]}]
        self.assertEqual(gs._scores_for_candidates(candidates, shapes, [[.9], [.6]], (300, 200)),
                         [[None], [.6]])

    def test_excluded_edge_is_not_independent_runner_up_evidence(self):
        from geetest_rescue import geometry_rescue
        candidates = [{"id": 1, "box": [10, 10, 40, 40], "center": [25, 25]},
                      {"id": 2, "box": [90, 70, 120, 100], "center": [105, 85]}]
        shapes = [{"box": (10, 10, 30, 30)}, {"box": (90, 70, 30, 30)}]
        results = [{"index": 1, "found": False, "center": None, "box": None}]
        scores = gs._scores_for_candidates(candidates, shapes, [[.7], [.1]], (150, 100))
        self.assertEqual(geometry_rescue(results, candidates, scores), [])
        self.assertFalse(results[0]["found"])

    def test_target_input_order_survives_candidate_order(self):
        main = Image.new("RGB", (200, 100), "white")
        icons = [Image.new("RGB", (20, 20), (2, 0, 0)), Image.new("RGB", (20, 20), (1, 0, 0))]
        shapes = [{"box": (10, 10, 30, 30)}, {"box": (110, 10, 30, 30)}]
        def choose(icon, *_):
            return {"candidate_id": icon.getpixel((0, 0))[0], "candidate_verified": True, "elapsed_ms": 1}
        with patch.object(gs, "cv_extract_shapes", return_value=shapes), patch("geetest_vision.score_matrix", return_value=[[1, 0], [0, 1]]), patch.object(gs, "_choose_candidate", side_effect=choose):
            results, clicks, warnings, _ = gs.locate_all(main, "main", main.size, icons, workers=1)
        self.assertEqual(clicks, [[125, 25], [25, 25]])
        self.assertEqual([r["index"] for r in results], [1, 2])
        self.assertTrue(all(r["candidate_verified"] for r in results))
        self.assertEqual(warnings, [])

    def test_conflicting_choices_are_not_forced_into_distinct_answers(self):
        main = Image.new("RGB", (200, 100), "white")
        icons = [Image.new("RGB", (20, 20)) for _ in range(2)]
        shapes = [{"box": (10, 10, 30, 30)}, {"box": (110, 10, 30, 30)}]
        choice = {"candidate_id": 1, "candidate_verified": True, "elapsed_ms": 1}
        def locate(*args, **kwargs):
            idx = args[4]
            box = [10, 10, 40, 40] if idx == 1 else [110, 10, 140, 40]
            return {"index": idx, "box": box, "center": gs.mid(box), "found": True,
                    "verified": None, "note": "", "elapsed_ms": 1}
        with patch.object(gs, "cv_extract_shapes", return_value=shapes), patch("geetest_vision.score_matrix", return_value=[[1, 0], [0, 1]]), patch.object(gs, "_choose_candidate", return_value=choice), patch.object(gs, "locate_one", side_effect=locate):
            results, _, warnings, _ = gs.locate_all(main, "main", main.size, icons, strict=True, workers=1,
                                                    geometric_rescue=False)
        self.assertFalse(any(r["found"] for r in results))
        self.assertFalse(any(r["verified"] for r in results))
        self.assertTrue(any("几乎重合" in w for w in warnings))

    def test_partial_download_cannot_silently_shorten_target_order(self):
        data = {"imgs": "main.jpg", "ques": ["t0.png", "t1.png"], "captcha_type": "icon"}
        saved = []
        def download(path, *_):
            if path == "t0.png":
                raise TimeoutError("private URL should not be printed")
            return "local", b"fixture"
        with patch.object(gs, "download", side_effect=download):
            with self.assertRaisesRegex(RuntimeError, "不完整"):
                gs.split_images(data, {}, 1, image_callback=lambda role, blob: saved.append(role))
        self.assertEqual(saved, ["imgs", "ques[1]"])

    def test_cv_recovery_of_missing_center_updates_found(self):
        main = Image.new("RGB", (200, 100), "white")
        icons = [Image.new("RGB", (20, 20)) for _ in range(2)]
        shapes = [{"box": (10, 10, 30, 30), "center": [25, 25]},
                  {"box": (110, 10, 30, 30), "center": [125, 25]}]
        results = [{"index": 1, "found": True, "box": [10, 10, 40, 40], "center": [25, 25], "note": ""},
                   {"index": 2, "found": False, "box": None, "center": None, "note": ""}]
        with patch.object(gs, "cv_extract_shapes", return_value=shapes), patch.object(gs, "_cv_icon_mask", return_value=True), patch("geetest_vision.score_matrix", return_value=[[1.0, 0.0], [0.0, 1.0]]):
            info = gs.cv_reconcile(main, icons, results, apply=True)
        self.assertTrue(info["applied"])
        self.assertTrue(results[1]["found"])
        self.assertEqual(results[1]["center"], [125, 25])
        self.assertIsNone(results[1]["verified"])


if __name__ == "__main__":
    unittest.main()
