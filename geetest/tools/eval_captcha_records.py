# -*- coding: utf-8 -*-
"""离线统计固定的验证码图片记录；只有 --replay 才调用视觉模型，永不提交 /verify。

    python tools/eval_captcha_records.py --record-dir samples/captcha_records
    python tools/eval_captcha_records.py --record-dir samples/captcha_records --out baseline.json
    python tools/eval_captcha_records.py --record-dir samples/captcha_records --replay \
        --no-cross-check --no-candidate-verify --main-scale 1 --out comparison.json

每组包含 prediction.json 和 truth.json。人工在 truth 的每个目标填写原图
box=[x1,y1,x2,y2]，并将该目标的 needs_annotation 改为 false，才计入离线准确率。
未标注不算成功；服务端通过率以所有记录为分母，包括未提交、出错与缺文件组。
replay 的预测仅写入 --out，不覆盖原预测；历史服务端结果不会冒充 replay 通过率。
"""
import argparse
import json
import math
from pathlib import Path

from eval_groups import _rate, _vector, score_group, summarize_groups, valid_box

RESULT_FIELDS = ("index", "role", "found", "center", "box", "bbox", "verified",
                 "agree_px", "elapsed_ms", "cv_source", "resampled",
                 "candidate_id", "candidate_source", "candidate_verified", "verification_method",
                 "previous_center", "previous_box", "cv_score", "cv_margin")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        return (data, None) if isinstance(data, dict) else ({}, "invalid_json_object")
    except (OSError, ValueError) as exc:
        return {}, type(exc).__name__


def load_records(record_dir):
    root = Path(record_dir)
    if not root.is_dir():
        raise ValueError("record directory does not exist")
    if (root / "prediction.json").exists() or (root / "truth.json").exists():
        directories = [root]
    else:
        # 缺 prediction.json 的组仍然保留，不能把失败样本从分母删掉。
        directories = sorted(path for path in root.iterdir() if path.is_dir())
    records = []
    for directory in directories:
        prediction, prediction_error = _read_json(directory / "prediction.json")
        truth, truth_error = _read_json(directory / "truth.json")
        records.append({"name": directory.name, "path": directory,
                        "prediction": prediction, "truth": truth,
                        "prediction_error": prediction_error, "truth_error": truth_error})
    return records


def _validated_targets(document, annotation=False):
    """先校验索引再用于 dict/set；一个坏条目使整份标注不可信。"""
    if not isinstance(document, dict) or not isinstance(document.get("targets"), list):
        return [], "invalid_targets"
    if annotation and "needs_annotation" in document and type(document["needs_annotation"]) is not bool:
        return [], "invalid_annotation_flag"
    seen = set()
    for item in document["targets"]:
        if not isinstance(item, dict):
            return [], "invalid_target_structure"
        index = item.get("index")
        if type(index) is not int or index < 1:
            return [], "invalid_target_index"
        if index in seen:
            return [], "duplicate_target_index"
        seen.add(index)
        if annotation:
            if "needs_annotation" in item and type(item["needs_annotation"]) is not bool:
                return [], "invalid_annotation_flag"
            if item.get("box") is not None and valid_box(item["box"]) is None:
                return [], "invalid_annotation_box"
            if item.get("center") is not None and _vector(item["center"], 2) is None:
                return [], "invalid_annotation_center"
    return document["targets"], None


def record_targets(record):
    prediction, truth = record["prediction"], record["truth"]
    saved, prediction_error = _validated_targets(prediction)
    truth_targets, truth_error = _validated_targets(truth, annotation=True)
    # 与文件读取错误分开存储，重复评测修好的标注时不残留上次的校验错误。
    record["_prediction_validation_error"] = prediction_error
    record["_truth_validation_error"] = truth_error
    truth_by_index = {item["index"]: item for item in truth_targets}
    base = list(saved)
    if not base:
        base = sorted(truth_targets, key=lambda item: item["index"])
    # truth 比 prediction 多出的目标不能静默丢掉。
    present = {item["index"] for item in base}
    base = base + [item for item in truth_targets
                   if item["index"] not in present]
    targets = []
    for position, item in enumerate(base, 1):
        index = item.get("index", position)
        annotation = truth_by_index.get(index, {}) if not prediction_error else {}
        targets.append({"index": index, "role": item.get("role"),
                        "file": item.get("file"),
                        "box": annotation.get("box"),
                        "center": annotation.get("center"),
                        "needs_annotation": (True if truth_error or prediction_error else annotation.get(
                            "needs_annotation", truth.get("needs_annotation", True)))})
    return targets


def _server_summary(records):
    submitted = observed = success = 0
    statuses = {}
    for record in records:
        server = record["prediction"].get("server")
        server = server if isinstance(server, dict) else {}
        did_submit = server.get("submitted") is True
        submitted += did_submit
        result = server.get("result")
        observed += did_submit and isinstance(result, str) and bool(result)
        passed = did_submit and server.get("status") == "success" and result == "success"
        success += passed
        # 保留状态分类，不复制任何可能带凭据/原始响应的错误内容。
        if record.get("prediction_error") or record.get("_prediction_validation_error"):
            status = "record_error"
        elif passed:
            status = "success"
        elif did_submit:
            status = "submitted_without_success"
        else:
            status = "not_submitted"
        statuses[status] = statuses.get(status, 0) + 1
    return {"server_success": success, "server_total": len(records),
            "server_pass_rate": _rate(success, len(records)),
            "submitted": submitted, "submission_coverage": _rate(submitted, len(records)),
            "server_observed": observed, "server_result_coverage": _rate(observed, len(records)),
            "server_pass_rate_submitted": _rate(success, submitted),
            "server_status_counts": statuses}


def evaluate_records(records, replay_results=None):
    scores = []
    for record in records:
        targets = record_targets(record)
        results = (replay_results.get(record["name"], {}).get("results", [])
                   if replay_results is not None else record["prediction"].get("results", []))
        score = score_group(targets, results, record["name"], require_manual=True)
        score["target_count_known"] = bool(targets)
        prediction_error = record.get("prediction_error") or record.get("_prediction_validation_error")
        truth_error = record.get("truth_error") or record.get("_truth_validation_error")
        if prediction_error:
            score["prediction_error"] = prediction_error
        if truth_error:
            score["truth_error"] = truth_error
        if replay_results is not None and replay_results.get(record["name"], {}).get("error"):
            score["replay_error"] = replay_results[record["name"]]["error"]
        scores.append(score)
    report = summarize_groups(scores)
    report.update({"schema_version": 1, "mode": "replay" if replay_results is not None else "offline",
                   "record_errors": sum("prediction_error" in score for score in scores),
                   "truth_errors": sum("truth_error" in score for score in scores),
                   "target_count_unknown_groups": sum(not score["target_count_known"] for score in scores),
                   "target_inventory_coverage": _rate(sum(score["target_count_known"] for score in scores),
                                                      len(scores)),
                   "server_note": "success requires recorded submission, status=success and result=success; "
                                  "all records remain in the denominator"})
    original_server = _server_summary(records)
    if replay_results is None:
        report.update(original_server)
    else:
        report["original_server"] = original_server
        report.update({"server_success": None, "server_total": len(records),
                       "server_pass_rate": None, "submitted": 0, "submission_coverage": 0.0,
                       "server_observed": 0, "server_result_coverage": 0.0,
                       "server_pass_rate_submitted": None,
                       "server_note": "replay only calls the vision model; it never submits /verify. "
                                      "Original server results are historical and cannot validate new predictions.",
                       "replay_results": replay_results})
    return report


def _image_path(record, filename):
    if not isinstance(filename, str) or not filename:
        raise ValueError("record image filename is missing")
    directory = record["path"].resolve()
    image = (directory / filename).resolve()
    if not image.is_relative_to(directory) or not image.is_file():
        raise ValueError("record image must be an existing file inside its group directory")
    return str(image)


def replay_records(records, model=None, verify=True, cross_check=True,
                   candidate_verify=True, main_scale=2.0, geometric_rescue=True):
    from _driver import locate_group

    outcomes = {}
    for record in records:
        try:
            prediction = record["prediction"]
            targets = record_targets(record)
            if record.get("prediction_error") or record.get("_prediction_validation_error"):
                raise ValueError("record target inventory is invalid")
            if not targets:
                raise ValueError("record has no target inventory")
            main = _image_path(record, prediction.get("main", "main.png"))
            paths = [_image_path(record, target.get("file")) for target in targets]
            config = prediction.get("config")
            config = config if isinstance(config, dict) else {}
            selected_model = model or config.get("model")
            results = locate_group(main, paths, model=selected_model, verify=verify,
                                   cross_check=cross_check, candidate_verify=candidate_verify,
                                   main_scale=main_scale, geometric_rescue=geometric_rescue)
            outcomes[record["name"]] = {
                "results": [{key: result[key] for key in RESULT_FIELDS if key in result}
                            if isinstance(result, dict) else None for result in results],
                "model": selected_model,
            }
        except Exception as exc:  # noqa: BLE001
            # 原始网络异常可能包含请求数据，只记录异常类型。
            outcomes[record["name"]] = {"results": [], "error": type(exc).__name__}
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-dir", required=True)
    parser.add_argument("--replay", action="store_true", help="显式允许调用视觉模型；不提交 /verify")
    parser.add_argument("--model", help="replay覆盖记录中的视觉模型")
    parser.add_argument("--no-verify", dest="verify", action="store_false")
    parser.add_argument("--no-cross-check", dest="cross_check", action="store_false")
    parser.add_argument("--no-candidate-verify", dest="candidate_verify", action="store_false")
    parser.add_argument("--no-geometric-rescue", dest="geometric_rescue", action="store_false",
                        help="关闭候选模式中针对碰撞/缺失的几何救援")
    parser.add_argument("--main-scale", type=float, default=2.0)
    parser.add_argument("--out", help="报告路径；不允许覆盖原prediction.json/truth.json")
    args = parser.parse_args()
    if args.main_scale <= 0 or not math.isfinite(args.main_scale):
        parser.error("--main-scale must be finite and positive")
    if args.replay and not args.out:
        parser.error("--replay requires --out so new predictions are saved separately")
    try:
        records = load_records(args.record_dir)
    except ValueError as exc:
        parser.error(str(exc))
    if not records:
        parser.error("no record groups found")
    output = Path(args.out) if args.out else None
    if output:
        protected = {str((record["path"] / name).resolve()).casefold()
                     for record in records for name in ("prediction.json", "truth.json")}
        if str(output.resolve()).casefold() in protected:
            parser.error("--out cannot overwrite original predictions or manual truth")
    replays = None
    if args.replay:
        replays = replay_records(records, model=args.model, verify=args.verify,
                                 cross_check=args.cross_check, candidate_verify=args.candidate_verify,
                                 main_scale=args.main_scale, geometric_rescue=args.geometric_rescue)
    report = evaluate_records(records, replays)
    if args.replay:
        report["replay_config"] = {"model": args.model, "verify": args.verify,
                                   "cross_check": args.cross_check,
                                   "candidate_verify": args.candidate_verify,
                                   "main_scale": args.main_scale,
                                   "geometric_rescue": args.geometric_rescue}
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        summary = {key: value for key, value in report.items()
                   if key not in ("rows", "group_rows", "replay_results")}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("Report:", output)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
