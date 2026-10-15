"""Offline regressions for sanitized records and per-group /verify metrics."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
import geetest_records as records
import geetest_verify as verify


SECRET = "PRIVATE_SENTINEL_DO_NOT_PERSIST"


def png(size, color):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


class RecordTests(unittest.TestCase):
    def test_allowlist_preserves_pixels_but_never_copies_prediction_to_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            record = records.AttemptRecord(directory, {"model": "example-model", "cookie": SECRET})
            record.stage("download")
            record.expect_images("imgs", ["ques[0]", "ques[1]"])
            image = png((70, 40), "orange")
            record.save_image("imgs", image)
            record.save_image("ques[0]", png((20, 20), "red"))
            record.stage("locate")
            record.predictions([{"index": 1, "found": True, "center": [8, 9],
                                 "box": [3, 4, 13, 14], "note": SECRET,
                                 "raw": SECRET, "candidate_source": SECRET,
                                 "candidate_verified": True, "token": SECRET}],
                               {"enabled": True, "reason": SECRET, "scores": [[0.5, 0.8]]})
            record.data["server"] = records.safe_server({
                "status": "success", "data": {"result": "fail", "seccode": SECRET},
                "raw": SECRET}, submitted=True)
            record.error(ValueError(SECRET))
            record.finish()
            prediction_text = (record.path / "prediction.json").read_text(encoding="utf-8")
            truth_text = (record.path / "truth.json").read_text(encoding="utf-8")
            self.assertNotIn(SECRET, prediction_text + truth_text)
            prediction, truth = json.loads(prediction_text), json.loads(truth_text)
            self.assertEqual(prediction["error"], {"type": "ValueError"})
            self.assertEqual(prediction["stage"], "locate")
            self.assertEqual(prediction["targets"][1]["available"], False)
            self.assertTrue(truth["needs_annotation"])
            self.assertEqual(len(truth["targets"]), 2)
            self.assertTrue(all(t["box"] is None and t["center"] is None for t in truth["targets"]))
            with Image.open(record.path / "main.png") as saved:
                self.assertEqual(saved.size, (70, 40))
                self.assertEqual(saved.tobytes(), Image.open(io.BytesIO(image)).tobytes())

    def test_unknown_server_text_and_nonfinite_coordinates_are_removed(self):
        self.assertEqual(records.safe_server({"status": SECRET, "data": {"result": SECRET}}),
                         {"submitted": False, "status": "other", "result": "other"})
        cleaned = records.safe_results([{"found": True, "box": [0, 0, float("nan"), 1],
                                         "center": [0, float("inf")]}])
        self.assertIsNone(cleaned[0]["box"])
        self.assertIsNone(cleaned[0]["center"])


class FullFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.main = png((100, 70), "white")
        self.targets = [("ques[0]", png((15, 15), "red")),
                        ("ques[1]", png((15, 15), "green"))]
        self.data = {"captcha_type": "icon", "imgs": "main.png", "ques": ["a.png", "b.png"],
                     "payload": SECRET, "process_token": SECRET, "lot_number": SECRET,
                     "pow_detail": {"bits": 0}}
        self.fake_load = SimpleNamespace(
            DEFAULT_HEADERS={}, DEFAULT_COOKIE=SECRET,
            DEFAULT_PARAMS={"captcha_id": "example", "challenge": "fixture-challenge"},
            prepare_load_params=lambda params: dict(params),
            build_load_url=lambda params: "https://example.invalid/load",
            http_get=lambda *a: (200, "application/json", b"{}"),
            unwrap_jsonp=lambda *a: {"status": "success", "data": self.data},
            collect_image_paths=lambda data: [("imgs", "main.png"),
                                              ("ques[0]", "a.png"), ("ques[1]", "b.png")])
        self.fake_solver = SimpleNamespace(
            MODEL="example-model", split_images=self.split_images, load_rgb=self.load_rgb,
            validate_vlm_config=lambda: None,
            prepare_main=lambda blob, scale: self.load_rgb(blob).resize((int(100 * scale), int(70 * scale))),
            prep_icon=self.load_rgb, data_url=lambda image: "data:image/png;base64,OFFLINE",
            locate_all=self.locate_all)
        self.args = SimpleNamespace(record_dir=str(self.directory), benchmark=2, attempts=5,
                                    timeout=10, lang="zho", cookie=None)
        self.patches = [patch.object(verify, "gl", self.fake_load),
                        patch.dict(sys.modules, {"geetest_solve": self.fake_solver}),
                        patch.object(verify.time, "sleep", return_value=None),
                        patch.object(verify, "build_pow", return_value={"pow_msg": SECRET, "pow_sign": SECRET}),
                        patch.object(verify, "build_w", return_value={"w": SECRET, "key": SECRET,
                                                                     "plaintext": SECRET})]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def load_rgb(blob):
        return Image.open(io.BytesIO(blob)).convert("RGB")

    def split_images(self, data, headers, timeout, image_callback=None):
        image_callback("imgs", self.main)
        for role, blob in self.targets:
            image_callback(role, blob)
        return "imgs", self.main, self.targets, {}, "icon"

    def locate_all(self, main, url, sent_size, icons, **kwargs):
        # The image artifacts must already exist when recognition begins.
        paths = sorted(self.directory.glob("*/prediction.json"))
        latest = paths[-1].parent
        self.assertTrue((latest / "main.png").exists())
        self.assertTrue((latest / "target_1.png").exists())
        self.assertTrue((latest / "target_2.png").exists())
        self.assertEqual(sent_size, (200, 140))
        self.assertTrue(kwargs["candidate_verify"])
        return ([{"index": 1, "found": True, "center": [20, 20], "box": [10, 10, 30, 30],
                  "verified": True, "note": SECRET},
                 {"index": 2, "found": True, "center": [75, 45], "box": [65, 35, 85, 55],
                  "verified": True}], None, [], None)

    def run_flow(self, responses):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(verify, "submit_verify", side_effect=responses) as submit:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = verify.full_flow(self.args)
        self.assertNotIn(SECRET, stdout.getvalue() + stderr.getvalue())
        return code, json.loads(stdout.getvalue()), submit.call_count

    def test_benchmark_keeps_running_after_success_and_counts_rejection(self):
        code, summary, calls = self.run_flow([
            {"status": "success", "data": {"result": "success", "seccode": SECRET}},
            {"status": "success", "data": {"result": "fail", "error_body": SECRET}}])
        self.assertEqual((code, calls), (3, 2))
        self.assertEqual((summary["success"], summary["total"], summary["submitted"]), (1, 2, 2))
        self.assertEqual((summary["success_per_total"], summary["success_per_submitted"]), (0.5, 0.5))
        self.assertEqual((summary["errors"], summary["server_rejected"]), (0, 1))
        paths = sorted(self.directory.glob("*/prediction.json"))
        self.assertEqual(len(paths), 2)
        for path in paths:
            self.assertNotIn(SECRET, path.read_text(encoding="utf-8"))
            self.assertTrue(json.loads((path.parent / "truth.json").read_text())["needs_annotation"])

    def test_download_exception_retains_partial_image_and_group_denominator(self):
        original = self.fake_solver.split_images
        calls = 0

        def split(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                kwargs["image_callback"]("imgs", self.main)
                raise TimeoutError(SECRET)
            return original(*args, **kwargs)

        self.fake_solver.split_images = split
        code, summary, submitted = self.run_flow([{"status": "success", "data": {"result": "success"}}])
        self.assertEqual((code, submitted), (1, 1))
        self.assertEqual((summary["total"], summary["success"], summary["not_submitted"], summary["errors"]),
                         (2, 1, 1, 1))
        self.assertEqual(summary["success_per_total"], 0.5)
        self.assertEqual(summary["success_per_submitted"], 1.0)
        final_path = sorted(self.directory.glob("*/prediction.json"))[-1]
        failed = json.loads(final_path.read_text())
        self.assertEqual(failed["stage"], "download")
        self.assertEqual(failed["error"], {"type": "TimeoutError"})
        self.assertTrue((final_path.parent / "main.png").exists())
        self.assertEqual(len(failed["targets"]), 2)

    def test_missing_target_never_submits_or_disappears_from_denominator(self):
        self.targets = self.targets[:1]
        self.args.benchmark = 1
        code, summary, calls = self.run_flow([])
        self.assertEqual(calls, 0)
        self.assertEqual((summary["total"], summary["success"], summary["not_submitted"]), (1, 0, 1))
        prediction = json.loads(next(self.directory.glob("*/prediction.json")).read_text())
        self.assertEqual(prediction["outcome"], "missing_images")
        self.assertEqual([t["available"] for t in prediction["targets"]], [True, False])

    def test_normal_attempts_still_stop_at_first_success(self):
        self.args.benchmark = 0
        code, summary, calls = self.run_flow([{"status": "success", "data": {"result": "success"}}])
        self.assertEqual((code, calls, summary["total"]), (0, 1, 1))
        self.assertEqual(summary["mode"], "attempts")

    def test_submission_exception_is_counted_without_exception_message(self):
        self.args.benchmark = 1
        code, summary, calls = self.run_flow([TimeoutError(SECRET)])
        self.assertEqual((code, calls, summary["total"], summary["submitted"], summary["errors"]),
                         (1, 1, 1, 1, 1))
        prediction = json.loads(next(self.directory.glob("*/prediction.json")).read_text())
        self.assertEqual(prediction["stage"], "submit")
        self.assertEqual(prediction["error"], {"type": "TimeoutError"})

    def test_record_flush_failure_before_submission_is_not_submitted(self):
        self.args.benchmark = 1
        original = records.AttemptRecord.flush
        def flush(record):
            if record.data["stage"] == "submit":
                raise OSError("fixture write failure")
            return original(record)
        with patch.object(records.AttemptRecord, "flush", flush):
            code, summary, calls = self.run_flow([])
        self.assertEqual((code, calls, summary["submitted"], summary["total"]), (1, 0, 0, 1))


if __name__ == "__main__":
    unittest.main()
