"""Store allowlisted attempt metadata and distinguish server outcomes from predictions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
import uuid


DEFAULT_RECORD_DIR = Path(__file__).resolve().parents[1] / "samples" / "runs"
_STATES = {"success", "fail", "error", "forbidden", "deny", "denied", "retry",
           "invalid", "timeout", "not_submitted", "unknown", "other"}
_SOURCES = {"cv", "vlm", "model", "candidate", "candidate_match", "candidate_verify",
            "candidate_assignment", "contour", "contour_match", "one_to_one",
            "original", "fallback", "reconciled", "resampled", "kept", "cv_reconcile",
            "cv_fix", "cv_fill", "cv_force", "cv_agree", "none", "unknown",
            "vlm_proposal", "permuted", "cv-center", "cv_rescue"}


def _number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if math.isfinite(value) else None
    return None


def _vector(value, size):
    if isinstance(value, (list, tuple)) and len(value) == size:
        result = [_number(n) for n in value]
        if all(n is not None for n in result):
            return result
    return None


def safe_server(response, submitted=False):
    """Retain only known status/result values, never arbitrary server text."""
    response = response if isinstance(response, dict) else {}
    data = response.get("data")
    data = data if isinstance(data, dict) else {}

    def state(value):
        return value if isinstance(value, str) and value in _STATES else (
            None if value is None else "other")

    result = {"submitted": bool(submitted), "status": state(response.get("status")),
              "result": state(data.get("result", response.get("result")))}
    http = response.get("http")
    if isinstance(http, int) and not isinstance(http, bool) and 100 <= http <= 599:
        result["http"] = http
    return result


def safe_results(results):
    clean = []
    for pos, item in enumerate(results or [], 1):
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index < 1:
            index = pos
        rec = {"index": index, "found": item.get("found") is True,
               "center": _vector(item.get("center"), 2),
               "box": _vector(item.get("box", item.get("bbox")), 4)}
        for key, size in (("previous_center", 2), ("previous_box", 4)):
            if key in item:
                rec[key] = _vector(item[key], size)
        for key in ("verified", "candidate_verified", "consistency", "resampled"):
            if item.get(key) is None or isinstance(item.get(key), bool):
                rec[key] = item.get(key)
        for key in ("elapsed_ms", "agree_px", "cv_score", "candidate_score", "cv_margin"):
            number = _number(item.get(key))
            if number is not None:
                rec[key] = number
        cid = item.get("candidate_id")
        if cid is None or (isinstance(cid, int) and not isinstance(cid, bool) and cid >= 0):
            rec["candidate_id"] = cid
        for key in ("source", "cv_source", "candidate_source"):
            value = item.get(key)
            if value is not None:
                rec[key] = value if isinstance(value, str) and value in _SOURCES else "unknown"
        if item.get("verification_method") in {"candidate_choice", "unconfirmed", "geometry_rescue_unconfirmed"}:
            rec["verification_method"] = item["verification_method"]
        clean.append(rec)
    return clean


def safe_config(config):
    result = {}
    for key in ("verify", "strict_verify", "cross_check", "candidate_verify", "geometric_rescue"):
        if isinstance(config.get(key), bool):
            result[key] = config[key]
    for key in ("agree_px", "min_gap_px", "locate_retries", "main_scale", "timeout", "workers"):
        value = _number(config.get(key))
        if value is not None:
            result[key] = value
    model = config.get("model")
    if isinstance(model, str) and re.fullmatch(r"[\w./:-]{1,100}", model):
        result["model"] = model
    return result


def safe_cv_info(info):
    """Numeric candidate diagnostics only; no error/reason/raw model text."""
    if not isinstance(info, dict):
        return None
    result = {}
    for key in ("enabled", "applied"):
        if isinstance(info.get(key), bool):
            result[key] = info[key]
    for key in ("initial_candidates", "candidate_count"):
        count = info.get(key)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            result[key] = count
    if isinstance(info.get("candidates"), list):
        result["candidates"] = []
        for candidate in info["candidates"]:
            if not isinstance(candidate, dict):
                continue
            cid = candidate.get("id")
            box, center = _vector(candidate.get("box"), 4), _vector(candidate.get("center"), 2)
            if type(cid) is int and cid > 0 and box is not None and center is not None:
                source = candidate.get("source")
                result["candidates"].append({"id": cid, "box": box, "center": center,
                                             "source": source if source in {"cv", "vlm_proposal"} else "unknown"})
    shapes = info.get("shapes")
    if isinstance(shapes, list):
        result["shapes"] = []
        for shape in shapes:
            if isinstance(shape, dict):
                box = _vector(shape.get("box", shape.get("bbox")), 4)
                center = _vector(shape.get("center"), 2)
                result["shapes"].append({"box": box, "center": center})
            elif _vector(shape, 4) is not None:
                result["shapes"].append({"box": list(shape)})
    for key in ("scores", "score_matrix"):
        matrix = info.get(key)
        if isinstance(matrix, list):
            result[key] = [[_number(v) for v in row] for row in matrix
                           if isinstance(row, (list, tuple))]
    for key in ("assignment", "assign"):
        values = info.get(key)
        if isinstance(values, (list, tuple)):
            result[key] = [_number(v) for v in values]
    return result


class AttemptRecord:
    def __init__(self, directory=None, config=None):
        now = datetime.now(timezone(timedelta(hours=8)))
        self.path = Path(directory or DEFAULT_RECORD_DIR).resolve() / (
            now.strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex[:8])
        self.path.mkdir(parents=True, exist_ok=False)
        self._started = self._stage_started = time.monotonic()
        self._main_role = None
        self.data = {"schema": 1, "schema_version": 1, "started_at": now.isoformat(),
                     "main": "main.png", "main_available": False, "main_size": None,
                     "targets": [], "results": [], "config": safe_config(config or {}),
                     "server": safe_server(None), "stage": "created", "outcome": "pending",
                     "stage_durations_ms": {}, "elapsed_ms": 0.0, "error": None}
        self.flush()

    def stage(self, name):
        now = time.monotonic()
        durations = self.data["stage_durations_ms"]
        previous = self.data["stage"]
        durations[previous] = round(durations.get(previous, 0.0) +
                                    (now - self._stage_started) * 1000, 2)
        self._stage_started = now
        self.data["stage"] = name
        self.flush()

    def expect_images(self, main_role, target_roles):
        self._main_role = main_role
        self.data["targets"] = [
            {"index": i, "role": role if re.fullmatch(r"ques\[\d+\]", role) else "ques[%d]" % (i - 1),
             "file": "target_%d.png" % i, "available": False}
            for i, role in enumerate(target_roles, 1)]
        self._target_roles = dict(zip(target_roles, self.data["targets"]))
        self.flush()

    def save_image(self, role, blob):
        """Save original pixels (before model resizing), stripping image metadata."""
        from PIL import Image

        if role == self._main_role:
            filename, target = "main.png", None
        else:
            target = getattr(self, "_target_roles", {}).get(role)
            if target is None:
                return
            filename = target["file"]
        with Image.open(io.BytesIO(blob)) as image:
            mode = "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
            converted = image.convert(mode)
            pixels = Image.frombytes(mode, image.size, converted.tobytes())
            pixels.save(self.path / filename, format="PNG")
            digest = hashlib.sha256((mode + str(image.size)).encode("ascii") + pixels.tobytes()).hexdigest()
            if target is None:
                self.data["main_available"] = True
                self.data["main_size"] = list(image.size)
                self.data["main_sha256"] = digest
            else:
                target["available"] = True
                target["image_sha256"] = digest
        self.flush()

    def predictions(self, results, cv_info=None):
        self.data["results"] = safe_results(results)
        self.data["cv_info"] = safe_cv_info(cv_info)
        self.flush()

    def error(self, exception):
        # Exception messages may contain HTTP URLs, request bodies or credentials.
        name = type(exception).__name__
        self.data["error"] = {"type": name if re.fullmatch(r"\w{1,100}", name) else "Exception"}
        self.data["outcome"] = "error"

    def flush(self):
        self.data["elapsed_ms"] = round((time.monotonic() - self._started) * 1000, 2)
        hashes = [self.data.get("main_sha256")] + [t.get("image_sha256") for t in self.data["targets"]]
        if self.data["targets"] and all(hashes):
            self.data["image_set_sha256"] = hashlib.sha256("|".join(hashes).encode("ascii")).hexdigest()
        temporary = self.path / "prediction.json.tmp"
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(self.path / "prediction.json")
        truth_path = self.path / "truth.json"
        # This file is a blank human annotation template. Predictions never fill it.
        if not truth_path.exists() or self.data["stage"] in {"created", "load", "download"}:
            truth = {"schema": 1, "schema_version": 1, "needs_annotation": True,
                     "main": "main.png", "targets": [
                         {"index": target["index"], "role": target["role"],
                          "box": None, "center": None, "needs_annotation": True}
                         for target in self.data["targets"]]}
            truth_path.write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")

    def finish(self):
        # Preserve the last active stage on exceptions for failure attribution.
        self.stage(self.data["stage"])
        self.flush()


def summarize_attempts(records, *, mode="benchmark", requested=None):
    """A fresh group is the denominator even when no submission was possible."""
    servers = [r.get("server", {}) if isinstance(r, dict) else {} for r in records]
    total = len(servers)
    submitted = sum(s.get("submitted") is True for s in servers)
    success = sum(s.get("submitted") is True and s.get("status") == "success"
                  and s.get("result") == "success" for s in servers)
    errors = sum(bool(r.get("error")) or bool(r.get("record_error")) or
                 r.get("outcome") in {"error", "load_failed", "verify_error"}
                 for r in records if isinstance(r, dict))
    outcomes = {}
    allowed_outcomes = {"error", "load_failed", "verify_error", "success", "server_rejected",
                        "unsupported_challenge", "missing_images", "incomplete_predictions",
                        "not_found", "invalid_prediction", "coincident_points"}
    for record in records:
        outcome = record.get("outcome") if isinstance(record, dict) else None
        outcome = outcome if outcome in allowed_outcomes else "unknown"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return {"schema": 1, "mode": mode, "requested": requested,
            "total": total, "submitted": submitted, "not_submitted": total - submitted,
            "success": success, "errors": errors,
            "outcomes": outcomes,
            "server_rejected": sum(s.get("submitted") is True and
                                   s.get("status") == "success" and s.get("result") != "success"
                                   for s in servers),
            "success_per_total": success / total if total else None,
            "success_per_submitted": success / submitted if submitted else None}
