"""Geometric fallback proposals; proposals alone do not prove recognition correctness."""

from __future__ import annotations

from copy import deepcopy
import math


def _numbers(value, length):
    try:
        if len(value) != length:
            return None
        result = [float(item) for item in value]
    except (TypeError, ValueError, OverflowError):
        return None
    return result if all(math.isfinite(item) for item in result) else None


def _near(a, b, gap):
    return math.hypot(a[0] - b[0], a[1] - b[1]) < gap


def geometry_rescue(results, candidates, scores, min_gap_px=20,
                    min_score=.48, min_margin=.04):
    """Repair eligible points in place and return a list of applied changes.

    ``candidates`` have ``id``, ``box`` (xyxy), ``center``, and optional ``source``.
    ``scores`` is candidate-by-target, with columns in ``results`` input order.
    A target is eligible only when its current point is missing, explicitly
    ``found=False``, or within ``min_gap_px`` of another present point.

    Only the target's top-scoring candidate may be proposed. Its margin is
    measured against the next candidate at a spatially independent center, so
    duplicate boxes around one object do not masquerade as a competitor. With
    no independent competitor, there is insufficient margin evidence.

    Proposals that collide with retained points or each other are revoked in
    batches, including cascades caused by a revoked point becoming retained.
    No second-best substitution is made to force a one-to-one assignment.
    Every applied change remains explicitly unverified.
    """
    for name, value in (("min_gap_px", min_gap_px), ("min_score", min_score),
                        ("min_margin", min_margin)):
        try:
            valid = math.isfinite(value)
        except TypeError:
            valid = False
        if not valid or (name == "min_gap_px" and value <= 0) or (name != "min_gap_px" and not 0 <= value <= 1):
            raise ValueError("Invalid %s" % name)
    target_count = len(results)
    if len(scores) != len(candidates) or any(len(row) != target_count for row in scores):
        raise ValueError("scores must have one row per candidate and one column per result")
    if not target_count or not candidates:
        return []

    current = [_numbers(item.get("center"), 2) for item in results]
    missing = [point is None or not item.get("found", True)
               for point, item in zip(current, results)]
    colliding = set()
    for first in range(target_count):
        if missing[first]:
            continue
        for second in range(first + 1, target_count):
            if not missing[second] and _near(current[first], current[second], min_gap_px):
                colliding.update((first, second))
    eligible = colliding | {index for index, absent in enumerate(missing) if absent}
    if not eligible:
        return []

    usable = {}
    for index, candidate in enumerate(candidates):
        point = _numbers(candidate.get("center"), 2)
        box = _numbers(candidate.get("box"), 4)
        if point is None or box is None or box[2] <= box[0] or box[3] <= box[1]:
            continue
        if not (box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]):
            continue
        usable[index] = {"center": point, "box": box, "id": candidate.get("id", index + 1),
                         "source": candidate.get("source", "geometry")}

    proposals = {}
    for target in sorted(eligible):
        ranked = []
        for candidate_index in usable:
            try:
                score = float(scores[candidate_index][target])
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(score) and 0 <= score <= 1:
                ranked.append((score, candidate_index))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        if not ranked:
            continue
        best_score, best_index = ranked[0]
        best = usable[best_index]
        runner_up = next((score for score, index in ranked[1:]
                          if not _near(best["center"], usable[index]["center"], min_gap_px)), None)
        if runner_up is None or best_score < min_score:
            continue
        margin = best_score - runner_up
        if margin + 1e-12 < min_margin:
            continue
        proposals[target] = {**best, "score": best_score, "margin": margin,
                             "runner_up_score": runner_up,
                             "reason": "missing" if missing[target] else "coincident_points"}

    # Removing a proposal restores its original point. Repeat the checks until
    # no remaining proposal conflicts with that enlarged retained-point set.
    while proposals:
        rejected = set()
        retained = [current[index] for index in range(target_count)
                    if index not in proposals and not missing[index]]
        for target, proposed in proposals.items():
            if any(_near(proposed["center"], point, min_gap_px) for point in retained):
                rejected.add(target)
        indices = sorted(proposals)
        for offset, first in enumerate(indices):
            for second in indices[offset + 1:]:
                if _near(proposals[first]["center"], proposals[second]["center"], min_gap_px):
                    rejected.update((first, second))
        if not rejected:
            break
        proposals = {index: value for index, value in proposals.items() if index not in rejected}

    changes = []
    for target in sorted(proposals):
        proposal = proposals[target]
        result = results[target]
        previous_center, previous_box = deepcopy(result.get("center")), deepcopy(result.get("box"))
        result.update({"previous_center": previous_center, "previous_box": previous_box,
                       "center": list(proposal["center"]), "box": list(proposal["box"]),
                       "found": True, "verified": False, "candidate_verified": False,
                       "consistency": None, "cv_source": "cv_rescue",
                       "cv_score": proposal["score"], "cv_margin": proposal["margin"],
                       "candidate_id": proposal["id"], "candidate_source": proposal["source"],
                       "verification_method": "geometry_rescue_unconfirmed"})
        changes.append({"index": result.get("index", target + 1), "target_index": target,
                        "reason": proposal["reason"], "previous_center": deepcopy(previous_center),
                        "previous_box": deepcopy(previous_box), "center": list(proposal["center"]),
                        "box": list(proposal["box"]), "candidate_id": proposal["id"],
                        "score": proposal["score"], "margin": proposal["margin"],
                        "runner_up_score": proposal["runner_up_score"], "verified": False})
    return changes
