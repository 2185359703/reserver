#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Locate challenge icons with a configured vision model and local shape candidates."""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import geetest_load as gl          # 同目录的取图模块
except ImportError as exc:             # pragma: no cover
    print("[!] 需要同目录的 geetest_load.py: %s" % exc, file=sys.stderr)
    raise SystemExit(1)

from PIL import Image, ImageDraw, ImageOps

# --------------------------------------------------------------------------- #
# 模型配置
# --------------------------------------------------------------------------- #

BASE_URL = os.environ.get("VLM_BASE_URL", "")
API_KEY = os.environ.get("VLM_API_KEY", "")
MODEL = os.environ.get("VLM_MODEL", "")
ICON_MIN_SIDE = 256           # 目标图短边不足该值时整数倍放大
MAX_SIDE = 1600               # 送模型前主图最长边上限

LOCATE_PROMPT = """图1是一张主图,像素尺寸 {w}x{h}。
图2是**一个**「目标图形」的黑色剪影(为了看清细节已放大)。
请在图1中找出与图2形状相同的那一个图形,并给出它的位置。

注意:
- 它在图1中可能被改成别的颜色、被描边化或手绘化、被轻微旋转或缩放,但整体形状一致;
- 图1中可能存在与它相似但并不相同的图形,请仔细比较细节(轮廓凹凸、是否空心、
  内部有没有笔画),不要认错。

严格只输出一个 JSON 对象,不要解释、不要 markdown 代码块:
{{"main_size":[{w},{h}],"box":[x1,y1,x2,y2]}}
box 是目标图形在图1中的绝对像素坐标 [左, 上, 右, 下];图1中确实没有就填 null。"""

# 模型这一次没给出框(答 null 或 JSON 解析失败)时,换这个问法再问一遍同一张图。
# 重试只处理本轮没有有效坐标的情况,不把重复回答当作正确性证明。
LOCATE_PROMPT_RETRY = """图1是一张主图,像素尺寸 {w}x{h}。
图2是**一个**「目标图形」的黑色剪影(已放大)。
这个图形**一定存在**于图1中,只是可能被改成了别的颜色、被画成了虚线或点阵描边、
被缩小、或者颜色和背景很接近。上一轮没找到它,请再仔细看一遍。

严格只输出一个 JSON 对象,不要解释、不要 markdown 代码块:
{{"main_size":[{w},{h}],"box":[x1,y1,x2,y2]}}
不要回答 null。box 是绝对像素坐标 [左, 上, 右, 下]。"""

# 独立复定位:换个问法、只问中心点,用于跟首次定位做一致性校验(置信度)。
RELOCATE_PROMPT = """图1是一张主图,像素尺寸 {w}x{h}。
图2是**一个**「目标图形」的黑色剪影(已放大)。
图1里画着这个图形,但它被改成了别的颜色、被画成了虚线/点阵描边,可能还被旋转或缩放。
请找出它,并只回答它的**外接矩形中心点**。

严格只输出 JSON:{{"center":[x,y]}}。不要解释。"""


# --------------------------------------------------------------------------- #
# 图片工具
# --------------------------------------------------------------------------- #


def load_rgb(src, max_side: int = 0) -> Image.Image:
    """读图(src 可以是路径、bytes 或 BytesIO);透明底合成到白底,过大则等比缩小。"""
    if isinstance(src, (bytes, bytearray)):     # PIL 会把 bytes 当文件名,必须包成 BytesIO
        src = io.BytesIO(src)
    im = Image.open(src)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])       # 黑剪影 + 透明底,直接送会看不清
        im = bg
    else:
        im = im.convert("RGB")
    if max_side and max(im.size) > max_side:
        k = max_side / max(im.size)
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    return im


def upscale_to(im: Image.Image, min_side: int) -> Image.Image:
    """按短边等比放大；插值只改变展示尺寸，不恢复原图中不存在的细节。"""
    if min(im.size) >= min_side:
        return im
    k = min(8, -(-min_side // min(im.size)))
    return im.resize((im.width * k, im.height * k), Image.LANCZOS)


def prep_icon(src) -> Image.Image:
    """目标图往往只有 48x48,整数倍放大后再送模型。"""
    return upscale_to(load_rgb(src, MAX_SIDE), ICON_MIN_SIDE)


def prepare_main(src, scale: float = 2.0) -> Image.Image:
    """主图保持比例放大，小图可与原图尺寸通过 to_pixels 精确换算。"""
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("main scale must be positive and finite")
    im = load_rgb(src)
    factor = min(scale, MAX_SIDE / max(im.size))
    size = (max(1, round(im.width * factor)), max(1, round(im.height * factor)))
    return im.resize(size, Image.Resampling.LANCZOS) if size != im.size else im


def data_url(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def img_part(im: Image.Image) -> dict:
    return {"type": "image_url", "image_url": {"url": data_url(im), "detail": "high"}}


# --------------------------------------------------------------------------- #
# 模型调用
# --------------------------------------------------------------------------- #


def validate_vlm_config() -> None:
    """Require provider settings only when model inference is requested."""
    values = {"VLM_BASE_URL": BASE_URL, "VLM_API_KEY": API_KEY, "VLM_MODEL": MODEL}
    missing = [name for name, value in values.items() if not isinstance(value, str) or not value.strip()]
    if missing:
        raise ValueError("Missing model configuration: " + ", ".join(missing))
    parsed = urllib.parse.urlparse(BASE_URL)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("VLM_BASE_URL must be an HTTP(S) URL without embedded credentials")


def chat(content: list, retries: int = 3, max_tokens: int = 2048,
         temperature: float = 0.0) -> str:
    """发一次请求,返回正文。网关有并发/限流,429 与 5xx 退避重试。

    temperature 默认 0。不同调用仍可能有差异；重复答案不代表正确。
    """
    validate_vlm_config()
    payload = {"model": MODEL, "messages": [{"role": "user", "content": content}],
               "temperature": temperature, "max_tokens": max_tokens}
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY},
    )
    last = "请求失败"
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                msg = json.loads(r.read().decode("utf-8", "replace"))["choices"][0]["message"]
            text = (msg.get("content") or "").strip()
            if text:
                return text
            last = "模型正文为空(max_tokens=%d)" % max_tokens
        except urllib.error.HTTPError as e:
            e.close()
            last = "模型请求 HTTP %s" % e.code
            if e.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(last)
        except Exception as e:  # noqa: BLE001
            last = "模型请求失败: %s" % type(e).__name__
        if attempt < retries:
            time.sleep(3.0 * (attempt + 1))
    raise RuntimeError(last)


def first_json(text: str) -> dict:
    """从模型输出里抠出第一个 JSON 对象(容忍 markdown 代码块和前后废话)。"""
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text[i:])
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                continue
    raise ValueError("模型输出里没有有效 JSON 对象")


# --------------------------------------------------------------------------- #
# 坐标换算
# --------------------------------------------------------------------------- #


def to_pixels(box: list, sent_wh: tuple, true_wh: tuple, reported: list | None) -> list:
    """把模型给的框换算到**原图**像素坐标。

    模型可能按 0~1、按它自己以为的尺寸、或按我们送过去的(可能缩小过的)尺寸给坐标,
    这里统一换算回来并做越界裁剪,避免越界框污染下游。
    """
    sw, sh = sent_wh
    tw, th = true_wh
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError("box must contain four coordinates")
    if any(isinstance(v, bool) for v in box):
        raise ValueError("boolean coordinate")
    x1, y1, x2, y2 = (float(v) for v in box)
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
        raise ValueError("non-finite coordinate")
    if all(0 <= v <= 1 for v in (x1, y1, x2, y2)):       # 0~1 坐标仅转换一次
        x1, y1, x2, y2 = x1 * sw, y1 * sh, x2 * sw, y2 * sh
    elif (reported and len(reported) >= 2
          and all(math.isfinite(float(v)) and float(v) > 1 for v in reported[:2])):
        x1, y1, x2, y2 = x1 * sw / reported[0], y1 * sh / reported[1], \
                         x2 * sw / reported[0], y2 * sh / reported[1]
    if (sw, sh) != (tw, th):                              # 送图前缩小/放大过
        x1, y1, x2, y2 = x1 * tw / sw, y1 * th / sh, x2 * tw / sw, y2 * th / sh
    x1, x2 = sorted((min(max(x1, 0.0), tw), min(max(x2, 0.0), tw)))
    y1, y2 = sorted((min(max(y1, 0.0), th), min(max(y2, 0.0), th)))
    return [round(v, 2) for v in (x1, y1, x2, y2)]


# --------------------------------------------------------------------------- #
# 定位 + 校验
# --------------------------------------------------------------------------- #


def to_center_pixels(center, sent_wh: tuple, true_wh: tuple, reported: list | None = None) -> list:
    """把模型给的中心点换算到**原图**像素坐标(规则同 to_pixels)。"""
    x, y = center[:2]
    return to_pixels([x, y, x, y], sent_wh, true_wh, reported)[:2]


def mid(box: list) -> list:
    return [round((box[0] + box[2]) / 2, 2), round((box[1] + box[3]) / 2, 2)]


def relocate_center(main_url: str, sent_wh: tuple, true_wh: tuple,
                    target_im: Image.Image, temperature: float = 0.0) -> list | None:
    """换个问法**独立再定位一次**,只要中心点;问不成(网络/解析失败)返回 None。

    这一步是置信度校验:模型在整图上定位很准,但判断"裁剪块是哪个图形"很不可靠,
    所以用「两次独立定位是否指向同一处」代替原来的裁剪比对。
    """
    content = [{"type": "text", "text": RELOCATE_PROMPT.format(w=sent_wh[0], h=sent_wh[1])},
               {"type": "image_url", "image_url": {"url": main_url, "detail": "high"}},
               img_part(target_im)]
    for tokens in (2048, 4096):                  # 空正文时加大配额再试一次
        try:
            obj = first_json(chat(content, retries=1, max_tokens=tokens,
                                  temperature=temperature))
        except Exception:  # noqa: BLE001
            continue
        c = obj.get("center") or obj.get("point")
        if isinstance(c, dict):
            c = [c.get("x"), c.get("y")]
        if isinstance(c, (list, tuple)) and len(c) >= 2 and all(
                isinstance(v, (int, float)) for v in c[:2]):
            size = obj.get("main_size") or obj.get("image_size")
            size = [float(v) for v in size] if isinstance(size, (list, tuple)) and len(size) >= 2 else None
            return to_center_pixels(c, sent_wh, true_wh, size)
        b = obj.get("box", obj.get("bbox"))      # 模型改答外接框时退回用框中心
        if isinstance(b, dict):
            b = [b.get(k) for k in ("x1", "y1", "x2", "y2")]
        if isinstance(b, (list, tuple)) and len(b) >= 4 and all(v is not None for v in b[:4]):
            return mid(to_pixels(b, sent_wh, true_wh, None))
    return None


def locate_one(main_im: Image.Image, main_url: str, sent_wh: tuple, target_im: Image.Image,
               index: int, verify: bool, agree_px: float = 10.0,
               strict: bool = False, retries: int = 2,
               temperature: float = 0.0) -> dict:
    """定位单个目标,并记录多次定位的一致性;它不是正确性证明。

    main_im 是原图;main_url/sent_wh 是送给模型的那张图及其尺寸。
    两次中心偏差 <= agree_px 记 verified=true;偏差过大再定位一轮;
    仍不一致时**默认保留坐标**(取各轮里偏差最小的那轮)并记 verified=false
    只有 strict=True 才把不一致的结果当成未找到。

    retries: 模型**没给出框**(答 null / 解析失败)时,换 LOCATE_PROMPT_RETRY 重问几次。
    重问可能补出有效框,但是否正确仍需人工标注或服务端结果验证。
    """
    def make_content(retry: bool):
        tpl = LOCATE_PROMPT_RETRY if retry else LOCATE_PROMPT
        return [{"type": "text", "text": tpl.format(w=sent_wh[0], h=sent_wh[1])},
                {"type": "image_url", "image_url": {"url": main_url, "detail": "high"}},
                img_part(target_im)]

    content = make_content(False)
    t0 = time.time()
    item = {"index": index, "found": False, "box": None, "center": None,
            "verified": None, "note": "", "elapsed_ms": 0.0}
    cands = []            # 每轮定位:{"box","center","agree_px"},fallback 时取偏差最小的那个
    disputes = []
    nodata = 0            # 模型没给框的次数
    max_locate = 2 if verify else 1

    for attempt in range(max_locate):
        box, size = None, None
        while True:                                   # 没给框就换问法重问,不计入定位轮次
            try:
                obj = first_json(chat(content, temperature=temperature))
            except Exception as e:  # noqa: BLE001
                if nodata < retries:
                    nodata += 1
                    content = make_content(True)
                    continue
                item["note"] = str(e)[:200]
                break
            box = obj.get("box", obj.get("bbox"))
            size = obj.get("main_size") or obj.get("image_size")
            if isinstance(box, dict):
                box = [box.get(k) for k in ("x1", "y1", "x2", "y2")]
            if box:
                break
            if nodata < retries:
                nodata += 1
                content = make_content(True)
                continue
            item["note"] = "模型 %d 次都判定主图里没有这个图形" % (nodata + 1)
            break
        if item["note"]:
            break
        if not box:
            item["note"] = "模型没给出框"
            break

        size = size if isinstance(size, (list, tuple)) and len(size) >= 2 else None
        px = to_pixels(box, sent_wh, main_im.size, [float(v) for v in size] if size else None)
        if px[2] - px[0] < 1 or px[3] - px[1] < 1:
            item["note"] = "模型给的框退化(宽或高 < 1px)"
            break
        center = mid(px)
        rec = {"box": px, "center": center, "agree_px": None}
        cands.append(rec)

        if not verify:                                   # --no-verify:直接采信
            item.update(found=True, box=px, center=center, verified=None,
                        note="未做一致性校验(--no-verify)" +
                             ("(第 %d 次才给出框)" % (nodata + 1) if nodata else ""))
            break

        other = relocate_center(main_url, sent_wh, main_im.size, target_im, temperature)
        if other is None:                                # 校验本身没问成,不等于否
            item.update(found=not strict, box=px, center=center, verified=None,
                        note="独立复定位未问成(空正文/网络),结果未经一致性确认")
            break

        dist = ((center[0] - other[0]) ** 2 + (center[1] - other[1]) ** 2) ** 0.5
        rec["agree_px"] = round(dist, 2)
        if dist <= agree_px:
            item.update(found=True, box=px, center=center, verified=True, agree_px=round(dist, 2))
            if attempt:
                item["note"] = "首轮不一致,第二轮定位与复定位一致"
            elif nodata:
                item["note"] = "第 %d 次才给出框" % (nodata + 1)
            break

        disputes.append({"attempt": attempt + 1, "box": px, "center": center,
                         "relocate_center": other, "agree_px": round(dist, 2)})

    if not item["found"]:
        if cands:            # 默认保留坐标,只标低置信;取偏差最小的那轮
            best = min(cands, key=lambda c: (c["agree_px"] is None, c["agree_px"]))
            gap = best["agree_px"]
            item.update(found=not strict, box=best["box"], center=best["center"],
                        verified=False, agree_px=gap,
                        note="独立复定位未达一致标准(最好的那轮偏差 %s px > 阈值 %.1f px),坐标仅供参考"
                             % ("%.1f" % gap if gap is not None else "未知", agree_px))
            if strict:
                item["box_disputed"] = best["box"]
        if disputes:
            item["disputes"] = disputes
    item["elapsed_ms"] = round((time.time() - t0) * 1000, 1)
    return item


# --------------------------------------------------------------------------- #
# 客观交叉校验:不用大模型,直接从主图里抽出彩色描边图形,与目标剪影做轮廓匹配。
#
# 为什么需要它:模型最强的能力是"在整图里精确定位",最弱的是"判断自己认对了没有"
# —— 多次独立定位可能完全一致、却把两个目标指到同一个点或指错形状,
# 一致性校验对这类"稳定地认错"是盲的。轮廓匹配是纯几何、可复现的,正好补这个盲区。
#
# 只在 numpy + opencv + scipy 都装了、且抽出的候选图形不少于目标数时才启用;
# 而且必须明显优于模型自己的配对才会动手,证据不足就原样保留。
# --------------------------------------------------------------------------- #

_CV = None


def cv_engine():
    """惰性加载 cv2/numpy/scipy;缺任何一个就返回 None(整套校验自动跳过)。"""
    global _CV
    if _CV is None:
        try:
            import numpy as np
            import cv2
            from scipy import ndimage
            from scipy.optimize import linear_sum_assignment
            _CV = {"np": np, "cv2": cv2, "ndimage": ndimage, "lsa": linear_sum_assignment}
        except Exception:  # noqa: BLE001
            _CV = False
    return _CV or None


def _cv_norm(mask):
    """保留比例、孔洞和内部笔画，空白填充到统一画布。"""
    from geetest_vision import normalize_mask
    return normalize_mask(mask)


def cv_extract_shapes(main_rgb: Image.Image) -> list:
    """分颜色提取候选，避免先合并所有彩色像素而丢掉图标边界。"""
    if cv_engine() is None:
        return []
    from geetest_vision import extract_shapes
    return extract_shapes(main_rgb)


def _cv_icon_mask(icon_rgb: Image.Image):
    from geetest_vision import icon_mask
    return icon_mask(icon_rgb)


def _cv_iou(a, b) -> float:
    np = cv_engine()["np"]
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter) / float(union) if union else 0.0


def cv_reconcile(main_rgb: Image.Image, icon_imgs: list, results: list,
                 min_iou: float = 0.72, margin: float = 0.12,
                 apply: bool = False) -> dict | None:
    """轮廓匹配给出纠偏建议；只有显式 apply=True 才修改结果。

    两种会动手的情况:
      · 多个目标落到同一个图形、另有图形没人认领 -> 用图形自身中心补上缺口;
      · 轮廓匹配给出的配对在 IoU 总和上明显更好 -> 按它重排这些点。
    证据不足(差异没超过 margin,或最好配对连 min_iou 都不到)时一律不动。
    """
    eng = cv_engine()
    if eng is None:
        return None
    np, lsa = eng["np"], eng["lsa"]
    n = len(results)
    shapes = cv_extract_shapes(main_rgb)
    if len(shapes) < n:
        return {"enabled": False,
                "reason": "只抽到 %d 个候选图形(< %d 个目标),跳过" % (len(shapes), n)}
    tmasks = [_cv_icon_mask(i) for i in icon_imgs]
    if any(m is None for m in tmasks):
        return {"enabled": False, "reason": "目标剪影提取失败,跳过"}

    from geetest_vision import score_matrix
    iou = score_matrix(shapes, icon_imgs)
    row, col = lsa(-np.array(iou))
    cv_assign = {int(c): int(r) for r, c in zip(row, col) if int(c) < n}

    # 模型每个点最贴近哪个图形(超过容差就算没对上)
    model_shape = {}
    for i, r in enumerate(results):
        if not r.get("found") or not r.get("center"):
            continue
        cx, cy = r["center"]
        best, bd = None, 1e18
        for si, s in enumerate(shapes):
            d = ((cx - s["center"][0]) ** 2 + (cy - s["center"][1]) ** 2) ** 0.5
            if d < bd:
                best, bd = si, d
        tol = max(25.0, 0.75 * max(shapes[best]["box"][2], shapes[best]["box"][3]))
        model_shape[i] = best if bd <= tol else None

    info = {"enabled": True,
            "shapes": [[round(c, 1) for c in s["center"]] for s in shapes],
            "scores": [[round(float(v), 3) for v in r_] for r_ in iou],
            "cv_assign": {str(k): v for k, v in sorted(cv_assign.items())},
            "model_assign": {str(k): v for k, v in sorted(model_shape.items())}}

    if all(model_shape.get(t) == cv_assign.get(t) for t in range(n)):
        info["action"] = "轮廓匹配与模型一致,无需改动"
        return info

    score_model = sum(iou[s][i] for i, s in model_shape.items() if s is not None)
    score_cv = sum(iou[cv_assign[t]][t] for t in range(n) if t in cv_assign)
    info["score_model"] = round(score_model, 3)
    info["score_cv"] = round(score_cv, 3)
    if len(cv_assign) < n:
        info["action"] = "轮廓匹配没能给全部 %d 个目标配上图形,保留模型结果" % n
        return info
    gaps = [iou[cv_assign[t]][t] - max(
        (iou[s][t] for s in range(len(shapes)) if s != cv_assign[t]), default=0)
        for t in range(n)]
    info["score_gaps"] = [round(float(v), 3) for v in gaps]
    if not (score_cv > score_model + margin
            and all(iou[cv_assign[t]][t] >= min_iou and gaps[t] >= margin for t in range(n))):
        info["action"] = ("轮廓匹配证据不足(%.3f vs %.3f),保留模型结果"
                          % (score_cv, score_model))
        return info

    owner = {}                                   # 图形 -> 认领它的模型结果(取最近的)
    for i, s in model_shape.items():
        if s is not None and s not in owner:
            owner[s] = i
    snap_c = [list(r["center"]) if r.get("center") else None for r in results]
    snap_b = [list(r["box"]) if r.get("box") else None for r in results]

    plan, applied = {}, []
    for t in range(n):
        s = cv_assign[t]
        if owner.get(s) == t:
            center, box, src = snap_c[t], snap_b[t], "model"
        elif owner.get(s) is not None:           # 借用"占了这块图形"的那个目标的精确点
            o = owner[s]
            center, box, src = snap_c[o], snap_b[o], "permuted(<-#%d)" % (o + 1)
        else:                                    # 这块图形没人认领 -> 用图形自身中心补上
            b = shapes[s]["box"]
            center = [round(shapes[s]["center"][0], 2), round(shapes[s]["center"][1], 2)]
            box = [b[0], b[1], b[0] + b[2], b[1] + b[3]]
            src = "cv-center"
        plan[t] = (center, box, src)
        if center and (not snap_c[t] or not results[t].get("found")
                       or abs(center[0] - snap_c[t][0]) > 0.5
                       or abs(center[1] - snap_c[t][1]) > 0.5):
            applied.append("#%d %s -> %s [%s]" % (t + 1, snap_c[t], center, src))

    if not applied:
        info["action"] = "轮廓匹配判定应重排,但坐标一致,未改动"
        return info
    info["proposed"] = applied
    if not apply:
        info["action"] = "轮廓给出候选建议，交给视觉匹配确认；不自动覆盖坐标"
        return info
    for t, (center, box, src) in plan.items():
        results[t]["center"] = center
        results[t]["box"] = box
        results[t]["found"] = center is not None and box is not None
        results[t]["cv_source"] = src
        if src != "model":                       # 换过点的,原来的"已验证"不再成立
            results[t]["verified"] = None
            results[t]["note"] = ((results[t]["note"] + "; ") if results[t]["note"] else "") \
                + "轮廓匹配校正:%s" % src
    info["action"] = "已按轮廓匹配校正"
    info["applied"] = applied
    return info


def _find_coincident(results: list, min_gap_px: float) -> list:
    """找出点击点过近的目标对(纯查询,不改结果)。"""
    pairs, live = [], [r for r in results if r.get("found") and r.get("center")]
    for a in range(len(live)):
        for b in range(a + 1, len(live)):
            ra, rb = live[a], live[b]
            d = ((ra["center"][0] - rb["center"][0]) ** 2
                 + (ra["center"][1] - rb["center"][1]) ** 2) ** 0.5
            if d < min_gap_px:
                pairs.append((ra, rb, d))
    return pairs


CANDIDATE_PROMPT = """图1是唯一需要寻找的目标剪影。图2是主图候选区域的编号图册，
每格左上角的数字是该候选的固定ID，图3是完整原图，供理解背景。
请从图2选出与图1属于同一个图形的候选。只匹配当前这一个目标。
目标可能改变颜色、轻微旋转、从实心改为描边或点阵；请比较整体轮廓、局部凸起、
内部符号和笔画，忽略背景。候选可能是背景碎片、不完整图形或近似形状。
本题要点选的是叠加在背景上的一组彩色描边/点阵图标。背景插画里的真实物品、
大字或装饰即使语义相似也不能选，必须比对与目标相同的轮廓和内部布局。
如果多个候选含有同一图形，选择图形完整、居中、边界最贴近图形的那个，
不能选择只是大块背景里顺带包含目标的区域。
不要仅因大轮廓相似就选中。没有合适候选、目标被截断或无法区分时允许不选。
严格返回 JSON：{{"candidate_id":整数或null,"match":"clear或uncertain或none"}}。
只有能明确匹配时填clear，拿不准填uncertain；不要输出像素坐标。
可用的candidate_id只有：{ids}。"""


def _box_iou(a, b):
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _add_candidate(candidates, box, size, source):
    """候选ID只追加；后续补点不会使先前的编号发生变化。"""
    if not box or len(box) != 4 or not all(math.isfinite(float(v)) for v in box):
        return None
    w, h = size
    x1, y1, x2, y2 = [float(v) for v in box]
    box = [max(0, x1), max(0, y1), min(w, x2), min(h, y2)]
    if box[2] - box[0] < 4 or box[3] - box[1] < 4:
        return None
    for c in candidates:
        if _box_iou(c["box"], box) >= 0.65:
            return c["id"]
    if len(candidates) >= 20:
        return None
    cid = len(candidates) + 1
    candidates.append({"id": cid, "box": [round(v, 2) for v in box],
                       "center": mid(box), "source": source})
    return cid


def _candidate_panel(main, candidates):
    """带留白的等比候选图册；编号在裁剪图之外，不遮挡图标笔画。"""
    columns, cw, ch = min(4, len(candidates)), 208, 224
    panel = Image.new("RGB", (columns * cw, math.ceil(len(candidates) / columns) * ch), "#eeeeee")
    draw = ImageDraw.Draw(panel)
    for k, c in enumerate(candidates):
        x, y = (k % columns) * cw, (k // columns) * ch
        b = c["box"]
        pad = max(4, round(max(b[2] - b[0], b[3] - b[1]) * .12))
        crop = main.crop((max(0, math.floor(b[0] - pad)), max(0, math.floor(b[1] - pad)),
                          min(main.width, math.ceil(b[2] + pad)), min(main.height, math.ceil(b[3] + pad))))
        crop = ImageOps.contain(crop, (cw - 16, ch - 40), Image.Resampling.LANCZOS)
        draw.rectangle((x + 3, y + 3, x + cw - 4, y + ch - 4), fill="white", outline="#999999")
        draw.text((x + 10, y + 8), "ID %d" % c["id"], fill="black", font_size=18)
        panel.paste(crop, (x + (cw - crop.width) // 2, y + 32 + (ch - 40 - crop.height) // 2))
    return panel


def _choose_candidate(icon, candidates, panel_url, main_url):
    t0 = time.monotonic()
    ids = [c["id"] for c in candidates]
    content = [{"type": "text", "text": CANDIDATE_PROMPT.format(ids=ids)}, img_part(icon),
               {"type": "image_url", "image_url": {"url": panel_url, "detail": "high"}},
               {"type": "image_url", "image_url": {"url": main_url, "detail": "high"}}]
    try:
        obj = first_json(chat(content, retries=1, max_tokens=2048))
        cid = obj.get("candidate_id")
        clear = type(cid) is int and cid in ids and obj.get("match") == "clear"
        return {"candidate_id": cid if clear else None,
                "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
                "candidate_verified": clear}
    except Exception as e:
        return {"candidate_id": None, "candidate_verified": False,
                "error_type": type(e).__name__,
                "elapsed_ms": round((time.monotonic() - t0) * 1000, 1)}


def _ordered_map(fn, items, workers):
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), 4, len(items)))) as pool:
        return list(pool.map(fn, items))


def _candidate_result(choice, candidates, index):
    candidate = next((c for c in candidates if c["id"] == choice["candidate_id"]), None)
    if candidate is None:
        return None
    return {"index": index, "found": True, "box": list(candidate["box"]),
            "center": list(candidate["center"]), "verified": True,
            "candidate_verified": True, "candidate_id": candidate["id"],
            "candidate_source": candidate["source"], "consistency": None,
            "verification_method": "candidate_choice", "note": "候选形状匹配确认；服务端结果另行记录",
            "elapsed_ms": choice["elapsed_ms"]}


def _scores_for_candidates(candidates, shapes, scores, image_size):
    """Map geometric evidence to displayed boxes; reject clipped edge fragments."""
    width, height = image_size
    columns = len(scores[0]) if scores else 0
    rows = []
    for candidate in candidates:
        box = candidate["box"]
        if box[0] <= 0 or box[1] <= 0 or box[2] >= width or box[3] >= height or not shapes:
            rows.append([None] * columns)
            continue
        overlaps = []
        for shape in shapes:
            x, y, w, h = shape["box"]
            overlaps.append(_box_iou(box, [x, y, x + w, y + h]))
        best = max(range(len(overlaps)), key=lambda i: overlaps[i])
        rows.append(scores[best] if overlaps[best] >= .65 else [None] * columns)
    return rows


def _locate_with_candidates(main_orig, main_url, sent_wh, icons, strict,
                            locate_retries, cross_check, min_gap_px, log, workers,
                            geometric_rescue=True):
    """先选候选ID，候选不足时整图定位补充，再对不确定目标比较一次。"""
    candidates, cv_info, results = [], {"enabled": bool(cross_check)}, [None] * len(icons)
    rescue_shapes, rescue_scores = [], []
    if cross_check:
        try:
            shapes = cv_extract_shapes(main_orig)
            # Geometry ranks the proposal shortlist only. It never decides
            # the final target identity or overrides a model choice.
            if shapes:
                from geetest_vision import score_matrix
                scores = score_matrix(shapes, icons)
                rescue_shapes, rescue_scores = shapes, scores
                selected = set()
                for t in range(len(icons)):
                    selected.update(sorted(range(len(shapes)), key=lambda k: -scores[k][t])[:4])
                for k in sorted(range(len(shapes)), key=lambda k: -max(scores[k])):
                    if len(selected) >= 14:
                        break
                    selected.add(k)
                shapes = [shapes[k] for k in sorted(selected, key=lambda k: (shapes[k]["box"][1], shapes[k]["box"][0]))]
            for s in shapes:
                x, y, w, h = s["box"]
                _add_candidate(candidates, [x, y, x + w, y + h], main_orig.size, "cv")
        except Exception as e:
            cv_info.update(enabled=False, reason=type(e).__name__)
    cv_info["initial_candidates"] = len(candidates)
    log("提取 %d 个候选区域，逐目标进行形状匹配" % len(candidates))
    if candidates:
        panel_url = data_url(_candidate_panel(main_orig, candidates))
        choices = _ordered_map(lambda icon: _choose_candidate(icon, candidates, panel_url, main_url), icons, workers)
        counts = {}
        for c in choices:
            cid = c["candidate_id"]
            if cid is not None:
                counts[cid] = counts.get(cid, 0) + 1
        for i, choice in enumerate(choices):
            if counts.get(choice["candidate_id"]) == 1:
                results[i] = _candidate_result(choice, candidates, i + 1)

    pending = [i for i, result in enumerate(results) if result is None]
    if pending:
        log("%d 个目标未获明确匹配，整图定位补充候选" % len(pending))
        def locate(i):
            try:
                r = locate_one(main_orig, main_url, sent_wh, icons[i], i + 1, False,
                               retries=locate_retries)
                r.update(candidate_verified=False, verified=None, verification_method="unconfirmed")
                return r
            except Exception as e:
                return {"index": i + 1, "found": False, "box": None, "center": None,
                        "verified": None, "candidate_verified": False,
                        "note": "定位失败: " + type(e).__name__, "elapsed_ms": 0.0}
        fallback = _ordered_map(locate, pending, workers)
        for i, r in zip(pending, fallback):
            results[i] = r
            if r.get("found"):
                _add_candidate(candidates, r["box"], main_orig.size, "vlm_proposal")
        if candidates:
            panel_url = data_url(_candidate_panel(main_orig, candidates))
            choices = _ordered_map(lambda i: _choose_candidate(icons[i], candidates, panel_url, main_url), pending, workers)
            for i, choice in zip(pending, choices):
                confirmed = _candidate_result(choice, candidates, i + 1)
                if confirmed is not None:
                    confirmed["elapsed_ms"] += results[i]["elapsed_ms"]
                    results[i] = confirmed
                else:
                    results[i]["elapsed_ms"] += choice["elapsed_ms"]
                    results[i]["note"] = "未获候选形状匹配确认，保留定位供复查"

    warnings = []
    if geometric_rescue and rescue_shapes and min_gap_px > 0:
        from geetest_rescue import geometry_rescue
        rows = _scores_for_candidates(candidates, rescue_shapes, rescue_scores, main_orig.size)
        changes = geometry_rescue(results, candidates, rows, min_gap_px=min_gap_px)
        cv_info["rescues"] = changes
        for change in changes:
            log("#%d 坐标冲突/缺失，经几何匹配提供候选；仍记为未确认" % change["index"])
            warnings.append("#%d 使用几何救援候选，未经视觉确认" % change["index"])
    pairs = _find_coincident(results, min_gap_px) if min_gap_px > 0 else []
    # 一对一约束是冲突检查，绝不强迫模型改选第二名来凑齐答案。
    for a in range(len(results)):
        for b in range(a + 1, len(results)):
            ca, cb = results[a].get("candidate_id"), results[b].get("candidate_id")
            if ca is not None and ca == cb and not any(p[0] is results[a] and p[1] is results[b] for p in pairs):
                pairs.append((results[a], results[b], 0.0))
    for ra, rb, _ in pairs:
        for r in (ra, rb):
            r.update(verified=False, candidate_verified=False, note="候选分配冲突，不能作为匹配确认")
        warnings.append("#%d 与 #%d 的坐标几乎重合或选择同一候选，至少有一个认错" % (ra["index"], rb["index"]))
    for r in results:
        if r.get("verified") is not True:
            if strict:
                r["found"] = False
            warnings.append("#%d 未获形状匹配确认" % r["index"])
    cv_info.update(candidates=candidates, candidate_count=len(candidates),
                   action="候选由模型逐目标匹配，一对一冲突单独标记")
    return results, [r.get("center") for r in results], warnings, cv_info


def locate_all(main_orig: Image.Image, main_url: str, sent_wh: tuple, icons: list,
               verify: bool = True, agree_px: float = 10.0, strict: bool = False,
               locate_retries: int = 2, cross_check: bool = True,
               min_gap_px: float = 20.0, resample_temp: float = 0.7, log=None,
               candidate_verify: bool = True, workers: int = 2,
               geometric_rescue: bool = True):
    """所有入口共用：默认逐目标选候选，或显式选择旧式重复定位路径。

    verified 是本地视觉确认，不代表服务端通过；warnings 保留冲突和不确定性。
    """
    log = log or (lambda _s: None)
    if not icons:
        raise ValueError("no target icons")
    if verify and candidate_verify:
        return _locate_with_candidates(main_orig, main_url, sent_wh, icons, strict,
                                       locate_retries, cross_check, min_gap_px, log, workers,
                                       geometric_rescue=geometric_rescue)
    results = _ordered_map(
        lambda i: locate_one(main_orig, main_url, sent_wh, icons[i], i + 1, verify,
                             agree_px, strict, retries=locate_retries),
        list(range(len(icons))), workers)

    warnings = []

    # 1) 轮廓交叉校验提供候选建议;抽不到图形时跳过。
    cv_info = None
    if cross_check and len(results) > 1:
        try:
            cv_info = cv_reconcile(main_orig, icons, results)
        except Exception as e:  # noqa: BLE001
            cv_info = {"enabled": False, "reason": "%s: %s" % (type(e).__name__, e)}
        if cv_info:
            if cv_info.get("applied"):
                warnings.append("轮廓匹配校正: " + "; ".join(cv_info["applied"]))
            elif not cv_info.get("enabled"):
                log("轮廓匹配跳过: %s" % cv_info.get("reason"))
            else:
                log("轮廓匹配: %s" % cv_info.get("action"))

    # 2) 两个目标撞到同一点:后者带温度重新采样。
    #    更换采样温度可能产生其他候选,仍不保证定位正确。
    pairs = _find_coincident(results, min_gap_px) if min_gap_px > 0 else []
    if pairs:
        targets_to_retry = sorted({results.index(ra) for ra, _, _ in pairs})
        for t in targets_to_retry:
            ra = results[t]
            others = [r["center"] for j, r in enumerate(results)
                      if j != t and r.get("found") and r.get("center")]
            d = min((math.dist(ra["center"], other) for other in others), default=float("inf"))
            if d >= min_gap_px:
                continue
            log("#%d 与他人相距 %.1fpx,带温度重新采样 ..." % (ra["index"], d))
            try:
                nr = locate_one(main_orig, main_url, sent_wh, icons[t], t + 1, verify,
                                agree_px, strict, retries=locate_retries,
                                temperature=resample_temp)
            except Exception as e:  # noqa: BLE001
                log("重采样失败: %s" % e)
                continue
            if nr.get("found") and nr.get("center") and others:
                nd = min(((nr["center"][0] - o[0]) ** 2 + (nr["center"][1] - o[1]) ** 2) ** 0.5
                         for o in others)
                if nd >= min_gap_px:
                    nr["role"] = results[t].get("role")
                    nr["resampled"] = True
                    nr["note"] = ((nr["note"] + "; ") if nr["note"] else "") + \
                        "与其他目标重合后重新采样得到(新点距离 %.1fpx)" % nd
                    results[t] = nr
                    warnings.append("#%d 原与其他目标重合(%.1fpx),重采样后移到 %s"
                                    % (ra["index"], d, nr["center"]))
                    log("#%d 重采样得到新点 %s(距离 %.1fpx)" % (ra["index"], nr["center"], nd))
                else:
                    log("#%d 重采样仍与别人过近(%s),保留原值" % (ra["index"], nr["center"]))

    # 3) 最终几何自检(唯一的判定点,避免留下已失效的"重合"批注)
    final_pairs = _find_coincident(results, min_gap_px) if min_gap_px > 0 else []
    seen = []
    for ra, rb, d in final_pairs:
        for r, other in ((ra, rb), (rb, ra)):
            r["verified"] = False
            r["note"] = ((r["note"] + "; ") if r["note"] else "") + \
                "与 #%d 的点击点几乎重合(相距 %.1fpx),两者至少有一个认错" % (other["index"], d)
        key = tuple(sorted((ra["index"], rb["index"])))
        if key not in seen:
            seen.append(key)
    for key in seen:
        warnings.append("#%d 与 #%d 的坐标几乎重合,至少有一个认错" % key)
    for r in results:
        r["consistency"] = r.get("verified")
        if strict and verify and r.get("verified") is not True:
            r["found"] = False
    return results, [r.get("center") for r in results], warnings, cv_info


# --------------------------------------------------------------------------- #
# 取图
# --------------------------------------------------------------------------- #


def download(path: str, hosts: list, headers: dict, timeout: float):
    """依次试各 CDN,返回 (url, bytes);全失败抛异常。"""
    last = ""
    for host in hosts:
        url = gl.to_url(path, host)
        try:
            status, ctype, body = gl.http_get(url, headers, timeout)
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (url, e)
            continue
        if status == 200 and body:
            return url, body
        last = "%s -> HTTP %s" % (url, status)
    raise RuntimeError("图片下载失败: %s" % last)


def fetch_captcha(args):
    """请求 /load,返回 (data, params);出问题直接抛 RuntimeError。"""
    params = dict(gl.DEFAULT_PARAMS)
    params.update({"captcha_id": args.captcha_id, "challenge": args.challenge,
                   "callback": args.callback, "client_type": args.client_type,
                   "lang": args.lang})
    params = gl.prepare_load_params(params)
    headers = dict(gl.DEFAULT_HEADERS)
    if args.cookie:
        headers["cookie"] = args.cookie

    status, _, body = gl.http_get(gl.build_load_url(params), headers, args.timeout)
    payload = gl.unwrap_jsonp(body.decode("utf-8", "replace"), params["callback"])
    if payload.get("status") != "success":
        raise RuntimeError("接口 status=%r,data=%s" % (payload.get("status"),
                          json.dumps(payload.get("data"), ensure_ascii=False)[:300]))
    return payload["data"], params, headers


def split_images(data: dict, headers: dict, timeout: float, image_callback=None):
    """返回 (main_bytes, targets[(role, bytes)], urls, captcha_type)。"""
    entries = gl.collect_image_paths(data)          # [(role, path)] 已按优先级排好
    hosts = gl.static_hosts(data)
    main_role = next((r for r, _ in entries if r.split("[")[0].split(".")[0] in ("imgs", "img", "bg")), None)
    picks = [e for e in entries if e[0].split("[")[0].split(".")[0].startswith("ques")]
    if main_role is None or not picks:
        raise RuntimeError(
            "这套题不是图标点选题(captcha_type=%s,字段=%s);本文件只解 icon 点选。"
            % (data.get("captcha_type"), [r for r, _ in entries]))
    if len(picks) < 2:
        raise RuntimeError("只拿到 %d 个待点选图标,无法定位" % len(picks))
    def target_index(entry):
        match = re.search(r"\[(\d+)\]", entry[0])
        return int(match.group(1)) if match else entries.index(entry)
    picks.sort(key=target_index)
    if isinstance(data.get("ques"), list):
        if [target_index(p) for p in picks] != list(range(len(data["ques"]))):
            raise RuntimeError("目标图片清单不完整，不能改变原始点选顺序")

    urls = {}
    main_bytes = None
    targets = []
    requested = [next(e for e in entries if e[0] == main_role)] + picks
    for role, path in requested:
        try:
            url, blob = download(path, hosts, headers, timeout)
        except Exception as e:  # noqa: BLE001
            print("[!] 取图失败 %s: %s" % (role, type(e).__name__), file=sys.stderr)
            continue
        urls[role] = url
        if image_callback is not None:
            image_callback(role, blob)
        if role == main_role:
            main_bytes = blob
        elif role.split("[")[0].split(".")[0].startswith("ques"):
            targets.append((role, blob))
    if main_bytes is None:
        raise RuntimeError("主图没取到")
    if len(targets) != len(picks):
        raise RuntimeError("目标图片下载不完整(%d/%d)，不能提交缺少目标的坐标" % (len(targets), len(picks)))
    return main_role, main_bytes, targets, urls, data.get("captcha_type")


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #


def annotate(main_im: Image.Image, results: list, out_path: str) -> None:
    im = main_im.copy()
    d = ImageDraw.Draw(im)
    colors = [(230, 30, 30), (30, 90, 230), (20, 160, 60), (200, 120, 0), (150, 30, 200)]
    for r in results:
        if not r["found"]:
            continue
        c = colors[(r["index"] - 1) % len(colors)]
        d.rectangle(r["box"], outline=c, width=2)
        d.text((r["box"][0] + 2, max(0, r["box"][1] - 11)), "#%d" % r["index"], fill=c)
    parent = os.path.dirname(os.path.abspath(out_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    im.save(out_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="极验 gcaptcha4 图标点选题:取图 + 大模型定位,输出点击坐标。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--captcha-id", default=gl.DEFAULT_PARAMS["captcha_id"], help="captcha_id")
    ap.add_argument("--challenge", default=gl.DEFAULT_PARAMS["challenge"], help="challenge(一次性)")
    ap.add_argument("--callback", default=gl.DEFAULT_PARAMS["callback"], help="JSONP callback 名")
    ap.add_argument("--client-type", default=gl.DEFAULT_PARAMS["client_type"], help="client_type")
    ap.add_argument("--lang", default=gl.DEFAULT_PARAMS["lang"], help="lang,例如 zho / zh")
    ap.add_argument("--cookie", default=gl.DEFAULT_COOKIE, help="Cookie 头(默认 GEETEST_COOKIE,可留空)")
    ap.add_argument("--model", help="模型名(默认 %s)" % MODEL)
    ap.add_argument("--timeout", type=float, default=20.0, help="HTTP 超时秒数,默认 20")
    ap.add_argument("--no-verify", dest="verify", action="store_false",
                    help="跳过形状匹配校验，仅输出整图定位结果")
    ap.add_argument("--no-candidate-verify", dest="candidate_verify", action="store_false",
                    help="使用旧的重复定位一致性方式，供同图对比")
    ap.add_argument("--no-geometric-rescue", dest="geometric_rescue", action="store_false",
                    help="不对冲突或缺失坐标尝试几何救援")
    ap.add_argument("--main-scale", type=float, default=2.0, help="主图等比展示倍率，默认2")
    ap.add_argument("--workers", type=int, choices=range(1, 5), default=2,
                    help="独立目标最多并行模型请求数，默认2")
    ap.add_argument("--agree-px", type=float, default=10.0,
                    help="旧式重复定位的中心偏差容忍值(px)，默认10；不是正确率")
    ap.add_argument("--strict-verify", action="store_true",
                    help="两次定位不一致时当作未找到(默认保留坐标,只标 verified=false)")
    ap.add_argument("--min-gap-px", type=float, default=20.0,
                    help="两个目标点击点的最小间距(px),小于此值判为可疑;0=关闭,默认 20")
    ap.add_argument("--locate-retries", type=int, default=2,
                    help="模型没给出框时,换问法重问同一张图的次数,默认 2(0=不重试)")
    ap.add_argument("--no-cross-check", dest="cross_check", action="store_false",
                    help="关闭CV候选与轮廓建议(需要numpy/opencv/scipy)")
    ap.add_argument("--debug", metavar="PNG", help="输出标注了框与序号的调试图")
    ap.add_argument("--save-dir", metavar="DIR", help="把 4 张图存到该目录(默认不落盘)")
    ap.add_argument("--out", metavar="JSON", help="结果 JSON 写到文件(默认只打印)")
    ap.add_argument("--pretty", action="store_true", help="JSON 美化输出")
    args = ap.parse_args(argv)
    if args.model:
        globals()["MODEL"] = args.model

    try:
        validate_vlm_config()
    except ValueError as exc:
        ap.error(str(exc))

    # 1) 取题 -------------------------------------------------------------- #
    print("[*] 请求 /load ...", file=sys.stderr)
    try:
        data, params, headers = fetch_captcha(args)
    except Exception as e:  # noqa: BLE001
        print("[!] 取题失败: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1

    try:
        main_role, main_bytes, targets, urls, ctype = split_images(data, headers, args.timeout)
    except Exception as e:  # noqa: BLE001
        print("[!] 取图失败: %s" % e, file=sys.stderr)
        return 1
    print("[*] captcha_type=%s, 主图 1 张 + 目标 %d 张" % (ctype, len(targets)), file=sys.stderr)

    # 2) 定位 -------------------------------------------------------------- #
    main_orig = load_rgb(main_bytes)
    sent = prepare_main(main_bytes, args.main_scale)
    main_url = data_url(sent)
    icons = [prep_icon(blob) for _, blob in targets]

    results, _, warnings, cv_info = locate_all(
        main_orig, main_url, sent.size, icons,
        verify=args.verify, agree_px=args.agree_px, strict=args.strict_verify,
        locate_retries=args.locate_retries, cross_check=args.cross_check,
        min_gap_px=args.min_gap_px,
        log=lambda s: print("    " + s, file=sys.stderr),
        candidate_verify=args.candidate_verify, workers=args.workers,
        geometric_rescue=args.geometric_rescue)
    for r, (role, _) in zip(results, targets):
        r["role"] = role
        detail = ("box=%s center=%s verified=%s" % (r["box"], r["center"], r["verified"])
                  if r["found"] else "未找到 (%s)" % r["note"])
        print("  #%d %-10s %s" % (r["index"], role, detail), file=sys.stderr, flush=True)
    for wmsg in warnings:
        print("  [!] %s" % wmsg, file=sys.stderr)

    # 4) 落盘 -------------------------------------------------------------- #
    if args.save_dir:
        os.makedirs(args.save_dir, exist_ok=True)
        main_ext = os.path.splitext(urllib.parse.urlparse(urls.get(main_role, "")).path)[1] or ".jpg"
        main_name = os.path.join(args.save_dir, "1_main" + main_ext)
        with open(main_name, "wb") as fh:
            fh.write(main_bytes)
        print("[+] %s" % main_name, file=sys.stderr)
        for i, (role, blob) in enumerate(targets, 2):
            name = os.path.join(args.save_dir, "%d_%s.png" % (i, re.sub(r"[^\w]", "_", role)))
            with open(name, "wb") as fh:
                fh.write(blob)
            print("[+] %s" % name, file=sys.stderr)

    w, h = main_orig.size
    clicks = [r["center"] for r in results]
    out = {
        "captcha_id": params["captcha_id"], "challenge": params["challenge"],
        "lot_number": data.get("lot_number"), "captcha_type": ctype,
        "payload": data.get("payload"), "process_token": data.get("process_token"),
        "main_size": [w, h], "model": MODEL, "verify": bool(args.verify),
        "warnings": warnings,
        "cross_check": cv_info,
        "results": results,
        "clicks": clicks,
        "clicks_norm": [[round(c[0] / w, 4), round(c[1] / h, 4)] if c else None for c in clicks],
        "clicks_percent": [[round(c[0] * 100.0 / w, 2), round(c[1] * 100.0 / h, 2)] if c else None
                           for c in clicks],
    }
    text = json.dumps(out, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.out:
        parent = os.path.dirname(os.path.abspath(args.out))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)

    if args.debug:
        annotate(main_orig, results, args.debug)
        print("调试图: %s" % args.debug, file=sys.stderr)

    ok = all(r["found"] for r in results)
    print("\n按顺序点击(像素,原图 %dx%d):" % (w, h), file=sys.stderr)
    for r in results:
        if r["found"]:
            extra = ("两次定位偏差 %.1fpx" % r["agree_px"]) if r.get("agree_px") is not None else (
                "候选形状匹配" if r.get("candidate_verified") else
                "几何救援候选，未经视觉确认" if r.get("cv_source") == "cv_rescue" else "未经匹配确认")
            print("  #%d %-10s center=%-16s verified=%-5s (%s)"
                  % (r["index"], r["role"], r["center"], r["verified"], extra), file=sys.stderr)
        else:
            print("  #%d %-10s 未找到 (%s)" % (r["index"], r["role"], r["note"] or ""),
                  file=sys.stderr)
    low = [r["index"] for r in results if r["found"] and r["verified"] is not True]
    if low:
        print("  [!] #%s 没拿到一致性确认,建议加 --debug 看一眼调试图再点" % low, file=sys.stderr)
    print("  (本脚本仅输出坐标；/verify 实测请运行 geetest_verify.py)", file=sys.stderr)
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
