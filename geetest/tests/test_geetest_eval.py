# -*- coding: utf-8 -*-
"""离线回归：定位错误、遗漏、顺序与无真值不能误计为成功。"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import _driver
from eval_groups import rescore_report, score_group, summarize_groups
from eval_captcha_records import evaluate_records, load_records, replay_records


def target(index, box, annotated=True):
    return {"index": index, "box": box, "needs_annotation": not annotated}


def prediction(index, box, center=None):
    return {"index": index, "found": True, "box": box,
            "center": center or [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]}


def record(name, result="fail", submitted=True, targets=None, truth=None):
    targets = targets if targets is not None else [{"index": 1, "file": "target_1.png"}]
    return {"name": name, "prediction": {
                "targets": targets, "results": [prediction(1, [0, 0, 10, 10])],
                "server": {"submitted": submitted,
                           "status": "success" if result == "success" else "fail",
                           "result": result}},
            "truth": truth or {"needs_annotation": True,
                               "targets": [target(1, None, False)]},
            "prediction_error": None, "truth_error": None}


class EvaluationTests(unittest.TestCase):
    def test_found_but_zero_iou_is_wrong(self):
        report = summarize_groups([score_group(
            [target(1, [0, 0, 10, 10])], [prediction(1, [100, 100, 110, 110])], "g1")])
        self.assertEqual(report["found_rate"], 1)
        self.assertEqual(report["target_accuracy"], 0)
        self.assertEqual(report["group_accuracy"], 0)
        self.assertEqual(report["wrong"], 1)
        self.assertEqual(report["fails"], 1)
        self.assertEqual(report["iou"], 0)
        self.assertEqual(report["max"], 100)

    def test_missing_results_keep_full_denominator(self):
        report = summarize_groups([score_group(
            [target(i, [0, 0, 10, 10]) for i in (1, 2, 3)],
            [prediction(1, [0, 0, 10, 10])], "g1")])
        self.assertEqual(report["n"], 3)
        self.assertEqual(report["found"], 1)
        self.assertEqual(report["missing"], 2)
        self.assertEqual(report["fails"], 2)
        self.assertEqual(report["target_accuracy"], 0.333333)
        self.assertEqual(report["group_accuracy"], 0)

    def test_explicit_invalid_click_is_not_replaced_by_box_center(self):
        report = summarize_groups([score_group(
            [target(1, [0, 0, 10, 10])],
            [{"index": 1, "found": True, "box": [0, 0, 10, 10], "center": None}], "g1")])
        self.assertEqual(report["found"], 1)
        self.assertEqual(report["invalid"], 1)
        self.assertEqual(report["correct"], 0)

    def test_wrong_order_is_not_repaired_by_sorting(self):
        report = summarize_groups([score_group(
            [target(1, [0, 0, 10, 10]), target(2, [20, 20, 30, 30])],
            [prediction(2, [0, 0, 10, 10]), prediction(1, [20, 20, 30, 30])], "g1")])
        self.assertEqual(report["found"], 2)
        self.assertEqual(report["iou"], 1)
        self.assertEqual(report["order_errors"], 2)
        self.assertEqual(report["correct"], 0)
        self.assertEqual(report["group_accuracy"], 0)

    def test_unknown_truth_never_becomes_correct(self):
        report = summarize_groups([score_group(
            [target(1, None, False)], [prediction(1, [0, 0, 10, 10])], "g1",
            require_manual=True)])
        self.assertIsNone(report["target_accuracy"])
        self.assertIsNone(report["group_accuracy"])
        self.assertIsNone(report["rows"][0]["correct"])
        self.assertEqual(report["unknown_targets"], 1)
        self.assertEqual(report["annotation_coverage"], 0)
        self.assertEqual(report["correct"], 0)

    def test_saved_box_requires_explicit_manual_annotation(self):
        draft = {"targets": [{"index": 1, "box": [0, 0, 10, 10]}]}
        item = record("draft", truth=draft)
        report = evaluate_records([item])
        self.assertIsNone(report["target_accuracy"])
        draft["targets"][0]["needs_annotation"] = False
        report = evaluate_records([item])
        self.assertEqual(report["target_accuracy"], 1)
        self.assertEqual(report["annotation_coverage"], 1)

    def test_server_rate_includes_unsubmitted_and_broken_records(self):
        items = [record("pass", "success"), record("fail"), record("skip", None, False),
                 {"name": "broken", "prediction": {}, "truth": {},
                  "prediction_error": "JSONDecodeError", "truth_error": "FileNotFoundError"}]
        report = evaluate_records(items)
        self.assertEqual(report["groups"], 4)
        self.assertEqual(report["server_total"], 4)
        self.assertEqual(report["server_pass_rate"], 0.25)
        self.assertEqual(report["submission_coverage"], 0.5)
        self.assertEqual(report["server_result_coverage"], 0.5)
        self.assertEqual(report["target_count_unknown_groups"], 1)
        self.assertIsNone(report["target_accuracy"])

    def test_status_or_found_alone_does_not_prove_server_success(self):
        item = record("fail", "fail")
        item["prediction"]["server"]["status"] = "success"
        self.assertEqual(evaluate_records([item])["server_pass_rate"], 0)

    def test_replay_never_reuses_historical_server_success(self):
        item = record("pass", "success")
        report = evaluate_records([item], {"pass": {"results": []}})
        self.assertIsNone(report["server_pass_rate"])
        self.assertEqual(report["original_server"]["server_pass_rate"], 1)
        self.assertEqual(report["submitted"], 0)
        self.assertEqual(report["missing"], 1)

    def test_load_records_keeps_directory_without_prediction(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "failed").mkdir()
            records = load_records(root)
            report = evaluate_records(records)
            self.assertEqual(report["groups"], 1)
            self.assertEqual(report["record_errors"], 1)
            self.assertEqual(report["server_total"], 1)
            self.assertEqual(report["server_pass_rate"], 0)

    def test_malformed_truth_in_valid_json_keeps_mixed_batch_and_marks_unknown(self):
        bad_truths = [
            {"targets": [target([1], [0, 0, 10, 10])]},
            {"targets": [target({}, [0, 0, 10, 10])]},
            {"targets": [target(True, [0, 0, 10, 10])]},
            {"targets": [target("1", [0, 0, 10, 10])]},
            {"targets": [target(1, [0, 0, 10, 10]), target(1, [0, 0, 10, 10])]},
            {"targets": [target(1, [0, 0, 10, 10]), None]},
            {"targets": {"index": 1}},
            {"targets": [target(1, [0, 0, "bad", 10])]},
        ]
        for bad_truth in bad_truths:
            with self.subTest(truth=bad_truth), tempfile.TemporaryDirectory() as name:
                good = record("good", "success", truth={"targets": [target(1, [0, 0, 10, 10])]})
                bad = record("bad", "fail", truth=bad_truth)
                for item in (good, bad):
                    directory = Path(name) / item["name"]
                    directory.mkdir()
                    for filename, value in (("prediction.json", item["prediction"]),
                                            ("truth.json", item["truth"])):
                        (directory / filename).write_text(json.dumps(value), encoding="utf-8")
                report = evaluate_records(load_records(name))
                self.assertEqual(report["groups"], 2)
                self.assertEqual(report["server_total"], 2)
                self.assertEqual(report["server_pass_rate"], 0.5)
                self.assertEqual(report["truth_errors"], 1)
                self.assertEqual(report["correct"], 1)
                self.assertEqual(report["unknown_targets"], 1)
                bad_score = next(group for group in report["group_rows"] if group["group"] == "bad")
                self.assertIn("truth_error", bad_score)
                self.assertIsNone(bad_score["rows"][0]["correct"])

    def test_replay_handles_bad_target_metadata_and_preserves_candidate_diagnostics(self):
        good_truth = {"targets": [target(1, [0, 0, 10, 10])]}
        good = record("good", truth=good_truth)
        bad_truth = record("bad_truth", truth={"targets": [target([1], [0, 0, 10, 10])]})
        bad_prediction = record("bad_prediction", truth=good_truth,
                                targets=[{"index": [], "file": "target_1.png"}])
        result = prediction(1, [0, 0, 10, 10])
        diagnostics = {"candidate_id": 3, "candidate_source": "edge",
                       "candidate_verified": True, "verification_method": "candidate_choice",
                       "previous_center": [20, 20], "previous_box": [15, 15, 25, 25],
                       "cv_score": 0.9, "cv_margin": 0.3}
        result.update(diagnostics)
        result["raw_response"] = "must not be copied"
        with patch("eval_captcha_records._image_path", return_value="fixture.png"), \
                patch("_driver.locate_group", return_value=[result]) as locate:
            outcomes = replay_records([bad_truth, bad_prediction, good], geometric_rescue=False)
        self.assertEqual(locate.call_count, 2)
        self.assertFalse(locate.call_args.kwargs["geometric_rescue"])
        self.assertEqual(outcomes["bad_prediction"]["error"], "ValueError")
        self.assertNotIn("raw_response", outcomes["good"]["results"][0])
        for key, value in diagnostics.items():
            self.assertEqual(outcomes["good"]["results"][0][key], value)
        report = evaluate_records([bad_truth, bad_prediction, good], outcomes)
        self.assertEqual(report["groups"], 3)
        self.assertEqual(report["truth_errors"], 1)
        self.assertEqual(report["record_errors"], 1)
        self.assertEqual(report["correct"], 1)

    def test_rescore_recovers_missing_targets_from_existing_truth(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name) / "g01"
            directory.mkdir()
            truth = {"targets": [
                {"index": 1, "glyph": "one", "box": [0, 0, 10, 10]},
                {"index": 2, "glyph": "two", "box": [20, 20, 30, 30]},
            ]}
            (directory / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
            old = {"model": "test", "groups": 1, "n": 1, "fails": 0, "rows": [
                {"group": "g01.one", "found": True, "box": [100, 100, 110, 110],
                 "truth": [0, 0, 10, 10]},
            ]}
            report = rescore_report(old, name)
            self.assertTrue(report["denominator_complete"])
            self.assertEqual(report["n"], 2)
            self.assertEqual(report["wrong"], 1)
            self.assertEqual(report["missing"], 1)
            self.assertEqual(report["fails"], 2)

    def test_driver_calls_production_pipeline_and_forwards_ablation_options(self):
        orig = object()
        sent = types.SimpleNamespace(size=(600, 400))
        fake = types.SimpleNamespace(
            MODEL="original", load_rgb=Mock(return_value=orig),
            prepare_main=Mock(return_value=sent), prep_icon=Mock(return_value="icon"),
            data_url=Mock(return_value="data:image/png;base64,fixture"),
            locate_all=Mock(return_value=([prediction(1, [0, 0, 10, 10])], [], [], {})),
        )
        with patch.dict(sys.modules, {"geetest_solve": fake}):
            results = _driver.locate_group("main.png", ["target.png"], model="chosen",
                                           verify=True, cross_check=False,
                                           candidate_verify=False, main_scale=1.0,
                                           geometric_rescue=False)
        fake.prepare_main.assert_called_once_with("main.png", scale=1.0)
        self.assertEqual(fake.locate_all.call_args.kwargs,
                         {"verify": True, "cross_check": False, "candidate_verify": False,
                          "geometric_rescue": False})
        self.assertIs(fake.locate_all.call_args.args[0], orig)
        self.assertEqual(fake.MODEL, "original")
        self.assertEqual(results[0]["target"], "target.png")


if __name__ == "__main__":
    unittest.main()
