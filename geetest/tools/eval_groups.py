# -*- coding: utf-8 -*-
"""固定图组定位评测；只有普通模式会调用视觉模型。

    python tools/eval_groups.py MODEL --groups 6 --verify
    python tools/eval_groups.py MODEL --no-cross-check --main-scale 1
    python tools/eval_groups.py --rescore samples/tmp/eval_groups_verify2.json

found_rate 是声称找到的比例；target_accuracy 是有真值的目标中，按输入
顺序返回且点击点落在对应真值框内的比例；group_accuracy 要求整组全对。
没有真值的目标单独统计为 unknown，不视为正确。框内命中是离线代理指标，
不等同于 /verify 通过率。历史重计分会利用 --dir 的 truth 补齐遗漏目标。
"""
import argparse
import json
import math
import os
import statistics
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GROUPS = os.path.join(ROOT, "samples", "groups")


def _vector(value, size):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) for v in value):
        return None
    return [float(v) for v in value]


def valid_box(value):
    box = _vector(value, 4)
    return box if box and box[2] > box[0] and box[3] > box[1] else None


def iou(a, b):
    a, b = valid_box(a), valid_box(b)
    if a is None or b is None:
        return None
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = ((a[2] - a[0]) * (a[3] - a[1])
             + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / union if union > 0 else 0.0


def _mid(box):
    return [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]


def _rate(numerator, denominator):
    return round(numerator / denominator, 6) if denominator else None


def score_group(targets, results, group, require_manual=False):
    """按原列表顺序比较，绝不重排预测来掩盖顺序错误或用 zip 丢目标。"""
    results = results if isinstance(results, list) else []
    rows = []
    for position, target in enumerate(targets):
        expected_index = target.get("index", position + 1)
        raw = results[position] if position < len(results) else None
        prediction = raw if isinstance(raw, dict) else {}
        missing = raw is None
        found = prediction.get("found") is True
        predicted_index = prediction.get("index", expected_index)
        order_ok = predicted_index == expected_index
        box = valid_box(prediction.get("box", prediction.get("bbox")))
        center = _vector(prediction.get("center"), 2)
        # 历史报告只存框时可推导中心；显式空/坏中心表示不可点击，不能修饰成成功。
        if "center" not in prediction and box is not None:
            center = _mid(box)
        truth = valid_box(target.get("box"))
        explicitly_marked = target.get("needs_annotation") is False
        annotated = (truth is not None and target.get("needs_annotation") is not True
                     and (not require_manual or explicitly_marked))
        correct = None
        err = overlap = center_err = None
        if annotated:
            correct = bool(found and order_ok and center is not None
                           and truth[0] <= center[0] <= truth[2]
                           and truth[1] <= center[1] <= truth[3])
            if found and box is not None:
                err = round(max(abs(box[k] - truth[k]) for k in range(4)), 3)
                overlap = round(iou(box, truth), 6)
            if found and center is not None:
                gt_center = _vector(target.get("center"), 2) or _mid(truth)
                center_err = round(math.dist(center, gt_center), 3)
        rows.append({
            "group": group, "index": expected_index,
            "role": target.get("role"), "glyph": target.get("glyph"),
            "style": target.get("style"), "found": found,
            "missing": missing, "invalid": not missing and (not isinstance(raw, dict)
                                                               or (found and center is None)),
            "predicted_index": predicted_index if not missing else None,
            "order_ok": order_ok and not missing,
            "annotated": annotated, "correct": correct,
            "box": box, "center": center, "truth": truth,
            "err": err, "iou": overlap, "center_err": center_err,
        })
    extras = max(0, len(results) - len(targets))
    annotated_group = bool(rows) and all(row["annotated"] for row in rows)
    return {"group": group, "rows": rows, "extra_results": extras,
            "annotated": annotated_group,
            "correct": (all(row["correct"] for row in rows) and not extras)
            if annotated_group else None}


def summarize_groups(group_scores):
    rows = [row for group in group_scores for row in group["rows"]]
    known = [row for row in rows if row["annotated"]]
    known_groups = [group for group in group_scores if group["annotated"]]
    errs = [row["err"] for row in rows if row["err"] is not None]
    overlaps = [row["iou"] for row in rows if row["iou"] is not None]
    center_errors = [row["center_err"] for row in rows if row["center_err"] is not None]
    found = sum(row["found"] for row in rows)
    correct = sum(row["correct"] is True for row in rows)
    group_correct = sum(group["correct"] is True for group in group_scores)
    missing = sum(row["missing"] for row in rows)
    # fails 含错误定位/缺失；未知真值另计，不能伪装成功。
    fails = sum(row["correct"] is False or not row["found"] for row in rows)
    return {
        "groups": len(group_scores), "n": len(rows), "found": found,
        "found_rate": _rate(found, len(rows)), "correct": correct,
        "target_accuracy": _rate(correct, len(known)),
        "target_accuracy_denominator": len(known),
        "target_accuracy_lower_bound": _rate(correct, len(rows)),
        "group_correct": group_correct,
        "group_accuracy": _rate(group_correct, len(known_groups)),
        "group_accuracy_denominator": len(known_groups),
        "group_accuracy_lower_bound": _rate(group_correct, len(group_scores)),
        "annotated_targets": len(known), "unknown_targets": len(rows) - len(known),
        "annotation_coverage": _rate(len(known), len(rows)),
        "annotated_groups": len(known_groups),
        "unknown_groups": len(group_scores) - len(known_groups),
        "group_annotation_coverage": _rate(len(known_groups), len(group_scores)),
        "wrong": sum(row["correct"] is False and row["found"] for row in rows),
        "missing": missing,
        "not_found": sum(not row["found"] and not row["missing"] for row in rows),
        "invalid": sum(row["invalid"] for row in rows),
        "order_errors": sum(not row["order_ok"] and not row["missing"] for row in rows),
        "extra_results": sum(group["extra_results"] for group in group_scores),
        "fails": fails,
        "median": round(statistics.median(errs), 3) if errs else None,
        "p90": round(sorted(errs)[int(0.9 * (len(errs) - 1))], 3) if errs else None,
        "max": round(max(errs), 3) if errs else None,
        "iou": round(statistics.mean(overlaps), 6) if overlaps else None,
        "center_error_median": round(statistics.median(center_errors), 3) if center_errors else None,
        "metric_note": "accuracy uses annotated targets/groups only; lower_bound uses all; "
                       "click must be inside the corresponding truth box in input order; "
                       "offline box hits do not establish /verify success",
        "rows": rows, "group_rows": group_scores,
    }


def group_dirs(directory, limit=None):
    dirs = sorted(path for path in Path(directory).iterdir()
                  if path.is_dir() and (path / "truth.json").exists())
    return dirs if limit is None else dirs[:limit]


def read_truth(gdir):
    with open(Path(gdir) / "truth.json", encoding="utf-8") as stream:
        truth = json.load(stream)
    return sorted(truth["targets"], key=lambda item: item["index"])


def load_group(gdir):
    targets = read_truth(gdir)
    gdir = Path(gdir)
    main = next((gdir / name for name in ("main.png", "main.jpg")
                 if (gdir / name).exists()), gdir / "main.png")
    packed = []
    for item in targets:
        candidates = sorted(path for path in gdir.iterdir()
                            if path.name.startswith("target_%d_" % item["index"])
                            and path.suffix.lower() in (".png", ".jpg", ".jpeg"))
        if not candidates:
            raise FileNotFoundError("target image missing for index %s" % item["index"])
        packed.append((str(candidates[0]), item.get("box"), item))
    return str(main), packed


def eval_model(model, groups, verify, rounds=1, cross_check=True,
               candidate_verify=True, main_scale=2.0, geometric_rescue=True):
    from _driver import locate_group

    t0 = time.time()
    scores = []
    for gdir in groups:
        targets = read_truth(gdir)
        error = None
        try:
            main, packed = load_group(gdir)
            results = locate_group(main, [p for p, _, _ in packed], model=model,
                                   verify=verify, cross_check=cross_check,
                                   candidate_verify=candidate_verify, main_scale=main_scale,
                                   geometric_rescue=geometric_rescue)
        except Exception as exc:  # noqa: BLE001
            results = []
            # 不把带请求正文/凭据的原始异常写进评测文件。
            error = type(exc).__name__
        score = score_group(targets, results, Path(gdir).name)
        if error:
            score["error"] = error
        scores.append(score)
    report = summarize_groups(scores)
    report.update({"model": model, "seconds": round(time.time() - t0, 1),
                   "config": {"verify": verify, "cross_check": cross_check,
                              "candidate_verify": candidate_verify, "main_scale": main_scale,
                              "geometric_rescue": geometric_rescue},
                   "group_errors": sum("error" in score for score in scores)})
    return report


def rescore_report(report, truth_dir=DEFAULT_GROUPS):
    """旧 rows 仍可计分；有原样本目录时恢复被旧 zip/异常吞掉的完整目标数。"""
    if report.get("group_rows"):
        scores = []
        for old_group in report["group_rows"]:
            targets, predictions = [], []
            for row in old_group.get("rows", []):
                targets.append({"index": row["index"], "box": row.get("truth"),
                                "needs_annotation": not row.get("annotated", False),
                                "role": row.get("role"), "glyph": row.get("glyph")})
                if not row.get("missing"):
                    predictions.append({"index": row.get("predicted_index", row["index"]),
                                        "found": row.get("found"), "box": row.get("box"),
                                        "center": row.get("center")})
                else:
                    predictions.append(None)
            score = score_group(targets, predictions, old_group["group"])
            score["extra_results"] = old_group.get("extra_results", 0)
            if score["extra_results"] and score["annotated"]:
                score["correct"] = False
            scores.append(score)
        denominator_complete = True
    else:
        buckets = {}
        for row in report.get("rows", []):
            label = str(row.get("group", "unknown"))
            group_name, _, glyph = label.partition(".")
            buckets.setdefault(group_name, []).append((glyph, row))
        available = {path.name: path for path in group_dirs(truth_dir)} if Path(truth_dir).is_dir() else {}
        declared_groups = report.get("groups", len(buckets))
        # 旧CLI固定取排序后的前N组；恢复完全没输出的组。
        if isinstance(declared_groups, int) and available and set(buckets) <= set(available):
            for name in list(available)[:declared_groups]:
                buckets.setdefault(name, [])
        scores = []
        denominator_complete = len(buckets) == declared_groups
        for name, entries in buckets.items():
            if name in available:
                targets = read_truth(available[name])
                predictions, used = [], set()
                for target in targets:
                    match = next(((i, row) for i, (glyph, row) in enumerate(entries)
                                  if i not in used and "error" not in row
                                  and (row.get("index") == target["index"]
                                       or (glyph and glyph == target.get("glyph")))), None)
                    if match is None:
                        predictions.append(None)
                    else:
                        i, row = match
                        used.add(i)
                        predictions.append({**row, "index": target["index"]})
                scores.append(score_group(targets, predictions, name))
            else:
                denominator_complete = False
                usable = [row for _, row in entries if "error" not in row]
                targets = [{"index": row.get("index", i + 1), "box": row.get("truth")}
                           for i, row in enumerate(usable)]
                scores.append(score_group(targets, usable, name))
    rescored = summarize_groups(scores)
    rescored.update({"model": report.get("model"), "seconds": report.get("seconds"),
                     "rescored": True, "denominator_complete": denominator_complete,
                     "source_reported_n": report.get("n"),
                     "source_reported_fails": report.get("fails"),
                     "truth_source": "existing synthetic/group truth; not server verification"})
    return rescored


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="*")
    parser.add_argument("--groups", type=int, default=6, help="用前 N 组")
    parser.add_argument("--dir", default=DEFAULT_GROUPS)
    parser.add_argument("--verify", action="store_true", help="开启二次校验")
    parser.add_argument("--no-cross-check", dest="cross_check", action="store_false")
    parser.add_argument("--no-candidate-verify", dest="candidate_verify", action="store_false")
    parser.add_argument("--no-geometric-rescue", dest="geometric_rescue", action="store_false",
                        help="关闭候选模式中针对碰撞/缺失的几何救援")
    parser.add_argument("--main-scale", type=float, default=2.0)
    parser.add_argument("--rescore", metavar="FILE", help="仅离线重新计算已有 JSON 指标")
    parser.add_argument("--out", help="JSON报告；rescore默认另存 *_rescored.json")
    args = parser.parse_args()
    if args.main_scale <= 0 or not math.isfinite(args.main_scale):
        parser.error("--main-scale must be finite and positive")
    if args.rescore:
        with open(args.rescore, encoding="utf-8") as stream:
            previous = json.load(stream)
        previous = previous if isinstance(previous, list) else [previous]
        reports = [rescore_report(report, args.dir) for report in previous]
        source = Path(args.rescore)
        output = Path(args.out) if args.out else source.with_name(source.stem + "_rescored.json")
        if output.resolve() == source.resolve():
            parser.error("--out cannot overwrite the original report")
    else:
        if not args.models:
            parser.error("provide a model, or use --rescore FILE for offline scoring")
        if args.groups < 1:
            parser.error("--groups must be positive")
        dirs = group_dirs(args.dir, args.groups)
        if not dirs:
            parser.error("no groups with truth.json found")
        reports = [eval_model(model, dirs, args.verify, cross_check=args.cross_check,
                              candidate_verify=args.candidate_verify, main_scale=args.main_scale,
                              geometric_rescue=args.geometric_rescue)
                   for model in args.models]
        output = Path(args.out or os.path.join(ROOT, "samples", "tmp", "eval_groups.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as stream:
        json.dump(reports, stream, ensure_ascii=False, indent=2)
    for report in reports:
        print("%s found=%s/%s target_accuracy=%s group_accuracy=%s wrong=%s missing=%s unknown=%s"
              % (report["model"], report["found"], report["n"], report["target_accuracy"],
                 report["group_accuracy"], report["wrong"], report["missing"], report["unknown_targets"]))
    print("Report:", output)


if __name__ == "__main__":
    main()
