"""Local image candidates and structure-preserving shape matching."""

from __future__ import annotations

import math

import cv2
import numpy as np
from scipy import ndimage


MASK_SIZE = 128
MASK_PADDING = 12


def _rgb_array(image):
    """Accept RGB arrays or PIL images; composite transparency onto white."""
    if hasattr(image, "convert"):
        if "A" in image.getbands():
            rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
            alpha = rgba[:, :, 3:4] / 255.0
            return np.rint(rgba[:, :, :3] * alpha + 255 * (1 - alpha)).astype(np.uint8)
        return np.asarray(image.convert("RGB"), dtype=np.uint8)
    arr = np.asarray(image)
    if arr.ndim == 2:
        return np.repeat(arr[:, :, None], 3, axis=2).astype(np.uint8)
    if arr.ndim != 3 or arr.shape[2] not in (3, 4):
        raise ValueError("Expected an RGB/RGBA image")
    if arr.shape[2] == 4:
        rgba = arr.astype(np.float32)
        alpha = rgba[:, :, 3:4] / 255.0
        arr = rgba[:, :, :3] * alpha + 255 * (1 - alpha)
    return np.clip(arr, 0, 255).astype(np.uint8)


def normalize_mask(mask):
    """Crop and isotropically resize with padding; never close or fill holes.

    Returns a boolean 128x128 mask and the original (x, y, width, height).
    Empty input returns an empty mask and (0, 0, 0, 0).
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2:
        raise ValueError("Expected a 2D mask")
    out = np.zeros((MASK_SIZE, MASK_SIZE), dtype=bool)
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return out, (0, 0, 0, 0)
    x, y = int(xs.min()), int(ys.min())
    w, h = int(xs.max() - x + 1), int(ys.max() - y + 1)
    scale = (MASK_SIZE - 2 * MASK_PADDING) / max(w, h)
    rw, rh = max(1, round(w * scale)), max(1, round(h * scale))
    resized = cv2.resize(mask[y:y + h, x:x + w].astype(np.uint8),
                         (rw, rh), interpolation=cv2.INTER_NEAREST) > 0
    left, top = (MASK_SIZE - rw) // 2, (MASK_SIZE - rh) // 2
    out[top:top + rh, left:left + rw] = resized
    return out, (x, y, w, h)


def icon_mask(image):
    """Extract the dark foreground of a white-composited reference icon."""
    arr = _rgb_array(image)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    _, dark = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    # Uniform black/white images are not an identifiable reference icon.
    if gray.max() - int(gray.min()) < 24 or int(dark.sum()) < 8:
        return None
    return normalize_mask(dark)[0]


def _box_iou(a, b):
    x, y, w, h = a
    xx, yy, ww, hh = b
    inter = max(0, min(x + w, xx + ww) - max(x, xx)) * max(0, min(y + h, yy + hh) - max(y, yy))
    return inter / max(1, w * h + ww * hh - inter)


def _color_masks(arr):
    """Image-derived color seeds keep unrelated saturated surfaces separate."""
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB).astype(np.float32)
    # Quantization obtains deterministic seeds without random k-means or a
    # hard-coded palette. Both neutral and saturated objects are represented.
    quant = (lab.astype(np.uint16) // 16).astype(np.uint16)
    codes = quant[:, :, 0] * 256 + quant[:, :, 1] * 16 + quant[:, :, 2]
    counts = np.bincount(codes.ravel(), minlength=4096)
    seeds = []
    minimum = max(10, round(arr.shape[0] * arr.shape[1] * 0.00015))
    for code in np.argsort(-counts, kind="stable"):
        if counts[code] < minimum or len(seeds) >= 28:
            break
        pixels = lab[codes == code]
        seed = np.median(pixels, axis=0)
        if any(np.linalg.norm(seed - prior) < 18 for prior in seeds):
            continue
        seeds.append(seed)
    for index, seed in enumerate(seeds):
        distance = np.linalg.norm(lab - seed, axis=2)
        yield distance < 24, "color:%d" % index
    # Hue bands cover JPEG-shifted and antialiased strokes which may occupy too
    # few pixels for a dominant-color bin. Circular distance handles red at 0.
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    hue = hsv[:, :, 0].astype(np.int16)
    saturated = (hsv[:, :, 1] > 72) & (hsv[:, :, 2] > 65)
    for center in range(0, 180, 15):
        distance = np.abs(hue - center)
        mask = saturated & (np.minimum(distance, 180 - distance) <= 10)
        if int(mask.sum()) >= minimum:
            yield mask, "hue:%d" % center


def _proposals_from_mask(mask, source, image_shape):
    height, width = image_shape
    min_side = max(10, round(min(width, height) * 0.045))
    min_area = max(24, round(width * height * 0.00035))
    # Closing is only a grouping aid. The final foreground is sampled from the
    # original mask, so bridging dotted lines cannot erase interior structure.
    for radius in (1, 2):
        size = 2 * radius + 1
        grouped = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE,
                                  np.ones((size, size), np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(grouped, 8)
        for index in range(1, count):
            x, y, w, h, _ = (int(v) for v in stats[index])
            if (min(w, h) < min_side or max(w / max(1, h), h / max(1, w)) > 4.5
                    or w > width * 0.65 or h > height * 0.7
                    or w * h > width * height * 0.22):
                continue
            # Border-spanning artwork is overwhelmingly a background region.
            if (x == 0 or x + w == width) and (y == 0 or y + h == height):
                continue
            original = mask[y:y + h, x:x + w] & (labels[y:y + h, x:x + w] == index)
            area = int(original.sum())
            if area < min_area:
                continue
            normalized, (ox, oy, ow, oh) = normalize_mask(original)
            if min(ow, oh) < min_side:
                continue
            box = (x + ox, y + oy, ow, oh)
            occupancy = area / max(1, ow * oh)
            # Extreme sparse strands and almost rectangular blocks are weak
            # proposals, but retain them for callers to inspect if needed.
            quality = min(1.0, area / max(1, min_side * min_side))
            quality *= min(1.0, occupancy / 0.12)
            if occupancy > 0.92:
                quality *= 0.55
            yield {"box": box,
                   "center": [box[0] + box[2] / 2, box[1] + box[3] / 2],
                   "mask": normalized, "area": area,
                   "quality": float(quality), "source": source}


def extract_shapes(main_rgb):
    """Return local color/line proposals with xywh boxes in original pixels.

    Separate color masks prevent the union of neighboring saturated colors
    from becoming one giant background component. Large same-color surfaces
    also get a thin-stroke pass, without changing the stored foreground mask.
    """
    arr = _rgb_array(main_rgb)
    height, width = arr.shape[:2]
    if min(height, width) < 12:
        return []
    proposals = []
    for mask, source in _color_masks(arr):
        if mask.sum() < 24:
            continue
        proposals.extend(_proposals_from_mask(mask, source, (height, width)))
        # Background color can equal the icon color. Remove only the broad
        # interior, then group the surviving local strokes independently.
        kernel = max(7, round(min(height, width) * 0.04)) | 1
        broad = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN,
                                np.ones((kernel, kernel), np.uint8))
        thin = mask & ~broad.astype(bool)
        if thin.sum() >= 24 and not np.array_equal(thin, mask):
            proposals.extend(_proposals_from_mask(thin, source + ":thin", (height, width)))
    proposals.sort(key=lambda item: (-item["quality"], -item["area"], item["box"]))
    out = []
    for candidate in proposals:
        # Suppress only near-identical boxes. Nearby icons and meaningful
        # nested proposals must remain separate candidates.
        if any(_box_iou(candidate["box"], kept["box"]) > 0.78 for kept in out):
            continue
        out.append(candidate)
        if len(out) >= 32:
            break
    return out


def _boundary(mask):
    return mask & ~cv2.erode(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)


def _chamfer(a, b):
    if not a.any() or not b.any():
        return 0.0
    da = cv2.distanceTransform((~a).astype(np.uint8), cv2.DIST_L2, 3)
    db = cv2.distanceTransform((~b).astype(np.uint8), cv2.DIST_L2, 3)
    distance = (float(np.minimum(db[a], 18).mean()) + float(np.minimum(da[b], 18).mean())) / 2
    return math.exp(-distance / 5.0)


def _hole_signature(mask):
    """Area of genuine enclosed voids, excluding tiny JPEG pinholes."""
    holes = ndimage.binary_fill_holes(mask) & ~mask
    labels, count = ndimage.label(holes)
    areas = np.bincount(labels.ravel())[1:]
    meaningful = areas[areas >= 30]
    return meaningful, float(meaningful.sum())


def _aligned_score(a, b):
    union = np.logical_or(a, b).sum()
    iou = float(np.logical_and(a, b).sum()) / max(1, int(union))
    edge_a, edge_b = _boundary(a), _boundary(b)
    full_edge = _chamfer(edge_a, edge_b)
    # A colored outline may represent a solid reference. Compare the observed
    # strokes to the reference's full boundary as a separate rendering model.
    outline = _chamfer(a, edge_b)
    score = max(0.30 * iou + 0.70 * full_edge, 0.90 * outline + 0.10 * full_edge)
    # Do not rely on RETR_EXTERNAL: in outlined multipart symbols, one outer
    # stroke can enclose several distinct solid reference pieces. All edges
    # retain interior evidence. Still discount topology disagreement, including
    # the inherently ambiguous case of a thin ring versus an outlined disk.
    holes_a, area_a = _hole_signature(a)
    holes_b, area_b = _hole_signature(b)
    if (len(holes_a) > 0) != (len(holes_b) > 0):
        score *= 0.74
    elif area_a and area_b and a.sum() >= b.sum() * 0.52:
        score *= 0.85 + 0.15 * min(area_a, area_b) / max(area_a, area_b)
    return float(np.clip(score, 0, 1))


def match_score(a, b):
    """Compare candidate to reference over modest rotations, preserving holes.

    The first argument is an observed candidate; the second is a reference.
    Rendering-style tolerance is deliberately asymmetric.
    """
    if a is None or b is None:
        return 0.0
    a, b = np.asarray(a, dtype=bool), np.asarray(b, dtype=bool)
    if not a.any() or not b.any():
        return 0.0
    a, b = normalize_mask(a)[0], normalize_mask(b)[0]
    best = 0.0
    for angle in (0, -10, 10, -20, 20, -30, 30):
        if angle:
            matrix = cv2.getRotationMatrix2D(((MASK_SIZE - 1) / 2, (MASK_SIZE - 1) / 2), angle, 1)
            rotated = cv2.warpAffine(b.astype(np.uint8), matrix, (MASK_SIZE, MASK_SIZE),
                                     flags=cv2.INTER_NEAREST, borderValue=0) > 0
            rotated = normalize_mask(rotated)[0]
        else:
            rotated = b
        best = max(best, _aligned_score(a, rotated))
    return best


def score_matrix(shapes, icons):
    """Shape-by-target matrix; icons may be images or normalized bool masks."""
    masks = [icon if isinstance(icon, np.ndarray) and icon.ndim == 2 and icon.dtype == bool
             else icon_mask(icon) for icon in icons]
    return [[match_score(shape["mask"], target) for target in masks] for shape in shapes]
