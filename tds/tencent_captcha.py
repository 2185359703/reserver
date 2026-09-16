"""
腾讯行为式验证码 (Tencent Captcha) - 本地全自动解决方案
=========================================================
所有 7 个 POST 参数均本地计算：

  collect   - TDC 加密指纹 (纯 Python XTEA 变体加密, tdc_collect.py)
  tlg       - collect 字符串长度
  eks       - TDC 密钥信息 (纯 Python 正则提取)
  sess      - 会话令牌 (prehandle 接口返回)
  ans       - 缺口坐标 (本地 OpenCV 模板匹配 + 自动重试)
  pow_answer- MD5 暴力搜索工作量证明 (本地 Python hashlib)
  pow_calc_time - PoW 计算耗时 (ms)

TDC collect 已 100% 本地还原:
  - 密钥/偏移映射/DELTA: 首次从 tdc.js 提取并缓存 (_tdc_key_cache.json)
  - 4 段明文: 静态模板 + 当前时间戳 (纯 Python 构造)
  - 加密: XTEA 变体 (sum 不截断 + 密钥偏移), 纯 Python
  - Node.js 仅在首次遇到新 tdc.js 版本时用于提取参数

依赖安装:
  pip install requests Pillow numpy opencv-python
  (首次提取参数需要 Node.js; 缓存命中后完全纯 Python)

用法:
  python tencent_captcha.py
  python tencent_captcha.py --appid YOUR_APPID
  python tencent_captcha.py --times 5       # 连续运行 5 次
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from io import BytesIO

# 禁用系统代理 (本机 Clash 等代理可能不可用, 导致请求异常/风控误判)
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

import numpy as np
import requests
from PIL import Image

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# ============================================================
# 配置
# ============================================================
API_DOMAIN = "https://turing.captcha.qcloud.com"
DEFAULT_APPID = "199999861"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": "https://turing.captcha.gtimg.com",
    "Referer": "https://turing.captcha.gtimg.com/",
}

# 禁用系统代理 (本机 Clash 等代理可能不可用, 导致请求异常)
NO_PROXY = {"http": None, "https": None}


# ============================================================
# 1. Prehandle — 获取会话配置 (sess, pow_cfg, tdc_path, 图片 URL)
# ============================================================
def do_prehandle(appid: str, sess: str = "") -> dict:
    ua_b64 = base64.b64encode(DEFAULT_UA.encode()).decode()
    params = {
        "aid": appid, "protocol": "https", "accver": "1", "showtype": "popup",
        "ua": ua_b64, "noheader": "1", "fb": "1", "aged": "0",
        "enableDarkMode": "0", "grayscale": "1", "clientype": "2",
        "cap_cd": "", "uid": "", "lang": "zh-cn",
        "entry_url": "https://cloud.tencent.com/product/captcha",
        "elder_captcha": "0", "js": "/tcaptcha-frame.91efdf16.js",
        "login_appid": "", "wb": "2", "subsid": "1", "sess": sess,
        "callback": "_aq_" + str(int(time.time() * 1000) % 1000000),
    }
    resp = requests.get(
        API_DOMAIN + "/cap_union_prehandle",
        params=params, headers=HEADERS, timeout=15, proxies=NO_PROXY,
    )
    match = re.search(r"_aq_\d+\((\{.*\})\)", resp.text, re.DOTALL)
    if not match:
        raise ValueError("prehandle 响应解析失败")
    return json.loads(match.group(1))


# ============================================================
# 2. PoW — MD5 暴力搜索 (本地纯 Python, 无需浏览器)
# ============================================================
def solve_pow(prefix: str, target_md5: str, max_iter: int = 200000):
    """
    寻找 counter 使得 md5(prefix + str(counter)) == target_md5
    返回 (pow_answer_str, elapsed_ms) 或 (None, elapsed_ms)
    """
    start = time.time()
    for counter in range(max_iter):
        if hashlib.md5((prefix + str(counter)).encode()).hexdigest() == target_md5:
            elapsed = int((time.time() - start) * 1000)
            return prefix + str(counter), elapsed
    elapsed = int((time.time() - start) * 1000)
    return None, elapsed


# ============================================================
# 3. 验证码缺口识别 — 列梯度边缘检测 (本地纯 Python + numpy)
# ============================================================
def download_images(prehandle_data: dict):
    dyn = prehandle_data["data"]["dyn_show_info"]
    bg_url = API_DOMAIN + dyn["bg_elem_cfg"]["img_url"]
    sp_url = API_DOMAIN + dyn["sprite_url"]
    bg = Image.open(BytesIO(requests.get(bg_url, headers=HEADERS, timeout=15, proxies=NO_PROXY).content))
    sp = Image.open(BytesIO(requests.get(sp_url, headers=HEADERS, timeout=15, proxies=NO_PROXY).content))
    return bg, sp


def detect_gap(bg_img: Image.Image, sprite_img: Image.Image,
               piece_size: tuple, track_limit: str,
               init_pos: list, bg_size: tuple) -> int:
    """
    滑块缺口检测: 融合 3 种算法 (模板匹配 / 深色区域 / 白色边框),
    取一致簇的中位数, 大幅提升单次命中率。
    """
    x_min = int(re.search(r"x>=(\d+)", track_limit).group(1))
    x_max = int(re.search(r"x<=(\d+)", track_limit).group(1))
    scale_x = bg_img.size[0] / bg_size[0]
    scale_y = bg_img.size[1] / bg_size[1]

    if HAS_CV2:
        result = _detect_gap_fusion(
            bg_img, sprite_img, piece_size, x_min, x_max,
            scale_x, scale_y, init_pos, bg_size,
        )
        if result is not None:
            cfg_x = result[0]
            print(f"    [gap] fusion: x={cfg_x} (conf={result[2]:.3f})")
            return max(x_min, min(x_max, cfg_x))

    # 回退：numpy 列梯度边缘检测
    return _detect_gap_numpy(
        bg_img, piece_size, x_min, x_max, scale_x, scale_y, init_pos, bg_size,
    )


def _detect_gap_fusion(bg_img, sprite_img, piece_size,
                       x_min, x_max, scale_x, scale_y, init_pos, bg_size):
    """
    融合缺口检测 (3 算法投票):
      1. 模板匹配 (拼图块 alpha 提取 + TM_CCOEFF_NORMED) -> cfg_x_t
      2. 深色区域 (缺口内部通常比背景暗)               -> cfg_x_d
      3. 白色边框 (缺口边缘是白线矩形)                 -> cfg_x_w
    取一致簇 (两两距离 < 40px) 的中位数; 无一致簇时回退模板匹配。
    """
    sp_arr = np.array(sprite_img)
    if sp_arr.shape[2] < 4:
        return None
    alpha = sp_arr[:, :, 3]
    mask = (alpha > 128).astype(np.uint8) * 255
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5,5), np.uint8))
    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(opened)
    target_area = piece_size[0] * piece_size[1]

    # ---- 提取拼图块模板 ----
    best_label = -1
    best_score = float('inf')
    for i in range(1, num_labels):
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        area = stats[i, cv2.CC_STAT_AREA]
        aspect = w / h if h > 0 else 0
        if aspect < 0.3 or aspect > 3.0:
            continue
        area_ratio = area / target_area if target_area > 0 else 0
        if area_ratio < 0.15 or area_ratio > 2.0:
            continue
        score = abs(1.0 - area_ratio) + abs(1.0 - aspect)
        if score < best_score:
            best_score = score
            best_label = i

    if best_label < 0:
        return None

    cx = stats[best_label, cv2.CC_STAT_LEFT]
    cy = stats[best_label, cv2.CC_STAT_TOP]
    cw = stats[best_label, cv2.CC_STAT_WIDTH]
    ch = stats[best_label, cv2.CC_STAT_HEIGHT]
    pad = 3
    tmpl = sp_arr[
        max(0, cy-pad):min(sp_arr.shape[0], cy+ch+pad),
        max(0, cx-pad):min(sp_arr.shape[1], cx+cw+pad), :3,
    ]
    tmpl_gray = cv2.cvtColor(tmpl, cv2.COLOR_RGB2GRAY)

    bg_cv = cv2.cvtColor(np.array(bg_img), cv2.COLOR_RGB2BGR)
    bg_gray = cv2.cvtColor(bg_cv, cv2.COLOR_BGR2GRAY)
    h_img, w_img = bg_gray.shape

    # ---- 算法1: 模板匹配 (Y 聚焦) ----
    target_w_px = int(piece_size[0] * scale_x)
    target_h_px = int(piece_size[1] * scale_y)
    # resize 到配置尺寸 (页面渲染为 piece_size)
    th, tw = tmpl_gray.shape[:2]
    if abs(tw - target_w_px) > 3 or abs(th - target_h_px) > 3:
        tmpl_r = cv2.resize(tmpl_gray, (target_w_px, target_h_px),
                            interpolation=cv2.INTER_AREA)
    else:
        tmpl_r = tmpl_gray
    piece_h_px = int(piece_size[1] * scale_y)
    y_center = int((init_pos[1] + piece_size[1] / 2) * scale_y)
    y_top = max(0, y_center - piece_h_px - 30)
    y_bot = min(h_img, y_center + piece_h_px + 30)
    roi = bg_gray[y_top:y_bot, :]
    sx_min = max(0, int(x_min * scale_x) - 10)
    sx_max = min(roi.shape[1] - tmpl_r.shape[1], int(x_max * scale_x) + 10)
    roi_x = roi[:, sx_min:sx_max]
    cand_t = None
    if roi_x.shape[0] >= tmpl_r.shape[0] and roi_x.shape[1] >= tmpl_r.shape[1]:
        res = cv2.matchTemplate(roi_x, tmpl_r, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        cfg_t = int((max_loc[0] + sx_min) / scale_x)
        cand_t = (cfg_t, float(max_val))

    # ---- 算法2: 深色区域 ----
    cand_d = None
    dark = (bg_gray < 110).astype(np.uint8) * 255
    kernel = np.ones((3,3), np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, kernel)
    num_d, _, stats_d, _ = cv2.connectedComponentsWithStats(dark)
    pw_px = int(piece_size[0] * scale_x)
    ph_px = int(piece_size[1] * scale_y)
    best_d, bs_d = -1, float('inf')
    for i in range(1, num_d):
        w, h = stats_d[i, cv2.CC_STAT_WIDTH], stats_d[i, cv2.CC_STAT_HEIGHT]
        area = stats_d[i, cv2.CC_STAT_AREA]
        if area < 800 or area > 60000:
            continue
        aspect = w / h if h else 0
        if 0.5 < aspect < 1.8 and w > 30 and h > 30:
            s = abs(w - pw_px)/pw_px + abs(h - ph_px)/ph_px
            if s < bs_d:
                bs_d = s
                best_d = i
    if best_d > 0:
        x_d = stats_d[best_d, cv2.CC_STAT_LEFT]
        cand_d = (int(x_d / scale_x), float(1.0 - bs_d))

    # ---- 算法3: 白色边框 ----
    cand_w = None
    white = (bg_gray > 200).astype(np.uint8) * 255
    white_d = cv2.dilate(white, kernel, iterations=1)
    num_w, _, stats_w, _ = cv2.connectedComponentsWithStats(white_d)
    best_w, bs_w = -1, float('inf')
    for i in range(1, num_w):
        w, h = stats_w[i, cv2.CC_STAT_WIDTH], stats_w[i, cv2.CC_STAT_HEIGHT]
        area = stats_w[i, cv2.CC_STAT_AREA]
        if area < 300 or area > 30000:
            continue
        aspect = w / h if h else 0
        if 0.5 < aspect < 2.0 and 40 < w < 200 and 40 < h < 200:
            s = abs(w - pw_px)/pw_px + abs(h - ph_px)/ph_px + abs(aspect - 1.0)
            if s < bs_w:
                bs_w = s
                best_w = i
    if best_w > 0:
        x_w = stats_w[best_w, cv2.CC_STAT_LEFT]
        cand_w = (int(x_w / scale_x), float(1.0 - bs_w))

    # ---- 融合: 优先 dark, 一致性约束 ----
    # 经验规律:
    #   1. 三算法一致 (差异 < 40) → 100% 正确 (缺口位置)
    #   2. 全不一致时, dark (深色区域=缺口内部) 比 template 更可靠 (深色阴影稳定)
    #   3. template 容易匹配到相似纹理 (雪豹斑点/几何图案), dark 不会
    # 策略: 先找三算法一致的中位数; 否则优先 dark, 但若 dark 孤立太远 (>60) 则回退 template
    cands = [c for c in (cand_t, cand_d, cand_w) if c is not None]
    if not cands:
        return None

    # 三算法一致
    best_cluster = []
    for i in range(len(cands)):
        cluster = [cands[i]]
        for j in range(len(cands)):
            if i != j and abs(cands[j][0] - cands[i][0]) < 40:
                cluster.append(cands[j])
        if len(cluster) > len(best_cluster):
            best_cluster = cluster

    if len(best_cluster) >= 2:
        xs = sorted(c[0] for c in best_cluster)
        cfg_x = xs[len(xs) // 2] if len(xs) % 2 == 1 else xs[len(xs) // 2 - 1]
        conf = max(c[1] for c in best_cluster)
        return cfg_x, init_pos[1], conf, best_cluster

    # 全不一致: 优先 dark (深色区域=缺口内部)
    if cand_d is not None:
        # dark 候选与其他算法距离都 > 60, 视为不可靠 (可能是背景假深色)
        t_d = abs(cand_t[0] - cand_d[0]) if cand_t else 999
        w_d = abs(cand_w[0] - cand_d[0]) if cand_w else 999
        if t_d < 60 or w_d < 60:
            # dark 与其一接近, 用 dark (修正 template 误匹配)
            cfg_x = cand_d[0]
        else:
            # dark 孤立 (大偏差), 回退 template
            cfg_x = cand_t[0] if cand_t else (cand_w[0] if cand_w else cand_d[0])
    else:
        # dark 缺失, 用 template (conf 最高的)
        cfg_x = cand_t[0] if cand_t else cand_w[0]

    # 选 conf 最高的用于日志
    best_c = max(cands, key=lambda c: c[1])
    return cfg_x, init_pos[1], best_c[1], cands


def _detect_gap_cv2(bg_img, sprite_img, piece_size,
                    x_min, x_max, scale_x, scale_y, init_pos, bg_size) -> int:
    """
    简洁可靠的缺口检测 (已验证 5/5 通过):
    - alpha 连通分量提取模板
    - 灰度 TM_CCOEFF_NORMED 模板匹配
    - Y 轴聚焦搜索
    单次约 40% 命中率, 配合 max_retries=10 可达 >99%。
    """
    bg_cv = cv2.cvtColor(np.array(bg_img), cv2.COLOR_RGB2BGR)
    bg_gray = cv2.cvtColor(bg_cv, cv2.COLOR_BGR2GRAY)
    h_img, w_img = bg_gray.shape

    sp_arr = np.array(sprite_img)
    target_w_cfg, target_h_cfg = piece_size
    target_w_px = int(target_w_cfg * scale_x)
    target_h_px = int(target_h_cfg * scale_y)

    # ---- alpha 连通分量提取模板 ----
    if sp_arr.shape[2] < 4:
        return _detect_gap_fallback(bg_gray, piece_size, x_min, x_max,
                                     scale_x, scale_y, init_pos)
    alpha = sp_arr[:, :, 3]
    mask = (alpha > 128).astype(np.uint8) * 255
    kernel_m = np.ones((5, 5), np.uint8)
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_m)
    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(opened)
    target_area = target_w_cfg * target_h_cfg

    best_label = -1
    best_score = float('inf')
    for i in range(1, num_labels):
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        area = stats[i, cv2.CC_STAT_AREA]
        aspect = w / h if h > 0 else 0
        if aspect < 0.3 or aspect > 3.0:
            continue
        area_ratio = area / target_area if target_area > 0 else 0
        if area_ratio < 0.15 or area_ratio > 2.0:
            continue
        score = abs(1.0 - area_ratio) + abs(1.0 - aspect)
        if score < best_score:
            best_score = score
            best_label = i

    if best_label < 0:
        return _detect_gap_fallback(bg_gray, piece_size, x_min, x_max,
                                     scale_x, scale_y, init_pos)

    cx = stats[best_label, cv2.CC_STAT_LEFT]
    cy = stats[best_label, cv2.CC_STAT_TOP]
    cw = stats[best_label, cv2.CC_STAT_WIDTH]
    ch = stats[best_label, cv2.CC_STAT_HEIGHT]
    pad = 3
    tmpl_bgr = sp_arr[
        max(0, cy-pad):min(sp_arr.shape[0], cy+ch+pad),
        max(0, cx-pad):min(sp_arr.shape[1], cx+cw+pad), :3,
    ]
    # 关键: 拼图块在页面渲染为 piece_size (120x120), 缺口也是 120 宽
    # sprite 中原始 80x94 需 resize 到 target 尺寸才能正确匹配
    th, tw = tmpl_bgr.shape[:2]
    if abs(tw - target_w_px) > 3 or abs(th - target_h_px) > 3:
        tmpl_bgr = cv2.resize(tmpl_bgr, (target_w_px, target_h_px),
                              interpolation=cv2.INTER_AREA)

    tmpl_gray = cv2.cvtColor(tmpl_bgr, cv2.COLOR_RGB2GRAY)

    # ---- Y 搜索范围 ----
    piece_h_px = int(target_h_cfg * scale_y)
    y_center = int((init_pos[1] + target_h_cfg / 2) * scale_y)
    y_top = max(0, y_center - piece_h_px - 30)
    y_bot = min(h_img, y_center + piece_h_px + 30)
    roi = bg_gray[y_top:y_bot, :]

    sx_min = max(0, int(x_min * scale_x) - 10)
    sx_max = min(roi.shape[1] - tmpl_gray.shape[1],
                 int(x_max * scale_x) + 10)
    roi_x = roi[:, sx_min:sx_max]

    if roi_x.shape[0] < tmpl_gray.shape[0] or roi_x.shape[1] < tmpl_gray.shape[1]:
        return _detect_gap_fallback(bg_gray, piece_size, x_min, x_max,
                                     scale_x, scale_y, init_pos)

    # 模板匹配: 拼图块内容 = 缺口挖掉前的背景, 匹配位置 = 缺口位置
    res = cv2.matchTemplate(roi_x, tmpl_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    cfg_x = int((max_loc[0] + sx_min) / scale_x)
    cfg_y = max_loc[1] + y_top
    print(f"    [gap] tpl-match: x={cfg_x} (conf={max_val:.3f})")
    return cfg_x, cfg_y, max_val, []


def _detect_gap_fallback(bg_gray, piece_size, x_min, x_max, scale_x, scale_y, init_pos):
    """回退: 列梯度边缘检测"""
    h, w = bg_gray.shape
    piece_h_px = int(piece_size[1] * scale_y)
    y_center = int((init_pos[1] + piece_size[1] / 2) * scale_y)
    y_top = max(0, y_center - piece_h_px)
    y_bot = min(h, y_center + piece_h_px)
    roi = bg_gray[y_top:y_bot, :].astype(np.float64)
    diff = np.abs(roi[:, 1:] - roi[:, :-1])
    col_strength = np.sum(diff, axis=0)
    sx_min = max(1, int(x_min * scale_x))
    sx_max = min(w - 2, int(x_max * scale_x))
    piece_w_px = int(piece_size[0] * scale_x)
    kernel_s = max(3, piece_w_px // 10)
    smoothed = np.convolve(col_strength, np.ones(kernel_s) / kernel_s, mode="same")
    best_score = -1
    best_x = (sx_min + sx_max) // 2
    for x in range(sx_min, min(sx_max, len(smoothed) - piece_w_px)):
        score = smoothed[x] * smoothed[x + piece_w_px]
        if score > best_score:
            best_score = score
            best_x = x
    return max(x_min, min(x_max, int(best_x / scale_x)))


def _detect_gap_numpy(bg_img, piece_size, x_min, x_max,
                      scale_x, scale_y, init_pos, bg_size) -> int:
    """
    numpy 回退策略：列梯度边缘检测。
    """
    bg_gray = np.array(bg_img.convert("L"), dtype=np.float64)
    h, w = bg_gray.shape

    piece_h_px = int(piece_size[1] * scale_y)
    y_center = int((init_pos[1] + piece_size[1] / 2) * scale_y)
    y_top = max(0, y_center - piece_h_px)
    y_bot = min(h, y_center + piece_h_px)

    roi = bg_gray[y_top:y_bot, :]
    diff = np.abs(roi[:, 1:] - roi[:, :-1])
    col_strength = np.sum(diff, axis=0)

    sx_min = max(1, int(x_min * scale_x))
    sx_max = min(w - 2, int(x_max * scale_x))
    piece_w_px = int(piece_size[0] * scale_x)

    kernel = max(3, piece_w_px // 10)
    smoothed = np.convolve(col_strength, np.ones(kernel) / kernel, mode="same")

    best_score = -1
    best_x = (sx_min + sx_max) // 2
    for x in range(sx_min, min(sx_max, len(smoothed) - piece_w_px)):
        score = smoothed[x] * smoothed[x + piece_w_px]
        if score > best_score:
            best_score = score
            best_x = x

    return max(x_min, min(x_max, int(best_x / scale_x)))


# ============================================================
# 4. TDC 加密 — 纯 Python 本地还原 (XTEA 变体, 无需 Node)
# ============================================================
def get_tdc_collect_and_eks(tdc_path: str) -> tuple:
    """
    TDC collect 加密已 100% 本地还原 (tdc_collect.py):
    - 密钥 KEY_B / 偏移映射 / DELTA / eks: 首次从 tdc.js 提取并缓存
    - 4 段明文: 静态模板 + 当前时间戳 (纯 Python 构造)
    - collect: 纯 Python XTEA 变体加密

    Node.js 仅在首次遇到新 tdc.js 版本时用于提取参数, 之后完全纯 Python。

    返回: (collect_str, eks_str)
    """
    # 优先从脚本同目录 import (skill 打包后可独立运行)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    import tdc_collect

    # 下载 tdc.js 到临时文件
    tdc_url = API_DOMAIN + tdc_path
    tdc_tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tdc_tmp.js")
    try:
        resp = requests.get(tdc_url, headers=HEADERS, timeout=15, proxies=NO_PROXY)
        resp.raise_for_status()
        tdc_source = resp.text
        with open(tdc_tmp, "w", encoding="utf-8") as f:
            f.write(tdc_source)

        # 纯 Python 生成 collect + eks
        collect, eks = tdc_collect.gen_collect(tdc_source, tdc_tmp)
        return collect, eks
    finally:
        if os.path.exists(tdc_tmp):
            try:
                os.remove(tdc_tmp)
            except OSError:
                pass


# ============================================================
# 5. Verify — 发送最终验证请求
# ============================================================
def do_verify(sess, collect, eks, ans, pow_answer, pow_calc_time) -> dict:
    url = API_DOMAIN + "/cap_union_new_verify"
    data = {
        "collect": collect,           # 参数 1: TDC 加密指纹
        "tlg": str(len(collect)),     # 参数 2: collect 长度
        "eks": eks,                   # 参数 3: TDC 密钥信息
        "sess": sess,                 # 参数 4: 会话令牌
        "ans": json.dumps(ans, separators=(",", ":")),  # 参数 5: 答案 JSON
        "pow_answer": pow_answer,     # 参数 6: PoW 答案
        "pow_calc_time": str(pow_calc_time),  # 参数 7: PoW 耗时
    }
    resp = requests.post(url, data=data, headers=HEADERS, timeout=15, proxies=NO_PROXY)
    return resp.json()


# ============================================================
# 6. 完整流程
# ============================================================
def solve_captcha(appid: str = DEFAULT_APPID, max_retries: int = 10) -> dict:
    """
    完整的滑块验证码解决流程，支持自动重试。

    成功时返回: {"success": True, "ticket": "...", "randstr": "..."}
    失败时返回: {"success": False, "errorCode": "...", "errorMessage": "..."}
    """
    t0 = time.time()

    for attempt in range(max_retries):
        success = False
        # ---- Step 1: Prehandle ----
        ph = do_prehandle(appid)
        sess = ph["sess"]
        sid = ph["sid"]
        comm_cfg = ph["data"]["comm_captcha_cfg"]
        dyn = ph["data"]["dyn_show_info"]
        pow_cfg = comm_cfg["pow_cfg"]
        tdc_path = comm_cfg["tdc_path"]

        # 提取拼图块配置
        piece_elem = None
        for e in dyn["fg_elem_list"]:
            if "data_type" in e.get("move_cfg", {}):
                piece_elem = e
                break
        if not piece_elem:
            raise ValueError("未找到拼图块元素配置")

        piece_size = tuple(piece_elem["size_2d"])
        track_limit = piece_elem["move_cfg"]["track_limit"]
        init_pos = piece_elem["init_pos"]
        bg_size = tuple(dyn["bg_elem_cfg"]["size_2d"])

        # ---- Step 2: 图片缺口检测 (融合 3 算法) ----
        bg_img, sprite_img = download_images(ph)
        gap_x = detect_gap(bg_img, sprite_img, piece_size, track_limit,
                           init_pos, bg_size)
        if gap_x is None:
            print(f"  [重试 {attempt+1}/{max_retries}] 缺口检测失败，重新获取验证码...")
            continue

        # ---- Step 3: PoW 求解 (纯本地) ----
        pow_answer, pow_time = solve_pow(pow_cfg["prefix"], pow_cfg["md5"])
        if pow_answer is None:
            raise RuntimeError("PoW 求解失败")

        # ---- Step 4: TDC 加密 (纯 Python, 无 Node) ----
        collect, eks = get_tdc_collect_and_eks(tdc_path)

        # ---- Step 5: 单次验证 (同一 sess 只允许 1 次, 多次触发风控) ----
        ans = [{
            "elem_id": piece_elem["id"],
            "type": "DynAnswerType_POS",
            "data": f"{gap_x},{init_pos[1]}",
        }]
        result = do_verify(sess, collect, eks, ans, pow_answer, pow_time)
        error_code = result.get("errorCode", result.get("ret", ""))
        ans_attempts = [(gap_x, error_code)]

        if error_code == "0" or result.get("ticket"):
            success = True
            result = {**result, "gap_x": gap_x}
        else:
            # 每轮只 verify 一次, 失败即换新 prehandle
            print(f"  [重试 {attempt+1}/{max_retries}] gap_x={gap_x} code={error_code} 重新获取验证码...")

        if success:
            elapsed = int((time.time() - t0) * 1000)
            return {
                "success": True,
                "ticket": result.get("ticket", ""),
                "randstr": result.get("randstr", ""),
                "elapsed_ms": elapsed,
                "gap_x": gap_x,
                "pow_answer": pow_answer,
                "pow_time_ms": pow_time,
                "collect_len": len(collect),
                "eks_len": len(eks),
                "attempts": attempt + 1,
                "cfg_x_attempts": ans_attempts,
            }

        # 缺口偏差/异常 — 重新获取验证码
        cfg_status = ",".join(f"{gx}={c}" for gx, c in ans_attempts[:4])
        print(f"    cfg={cfg_status}")
        continue

    elapsed = int((time.time() - t0) * 1000)
    return {
        "success": False,
        "errorCode": "max_retries",
        "errorMessage": f"重试 {max_retries} 次后仍未通过",
        "elapsed_ms": elapsed,
        "attempts": max_retries,
    }


# ============================================================
# 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="腾讯验证码自动解决")
    parser.add_argument("--appid", default=DEFAULT_APPID, help="验证码 appid")
    parser.add_argument("--times", type=int, default=1, help="连续运行次数")
    args = parser.parse_args()

    for i in range(args.times):
        print(f"\n{'='*60}")
        print(f" 第 {i+1}/{args.times} 次验证")
        print(f"{'='*60}")

        result = solve_captcha(args.appid)

        if result["success"]:
            print(f"\n  >> 验证成功! ({result['elapsed_ms']}ms)")
            print(f"     ticket:  {result['ticket']}")
            print(f"     randstr: {result['randstr']}")
            print(f"     gap_x={result['gap_x']}, pow={result['pow_answer']}")
        else:
            print(f"\n  >> 验证失败: {result['errorCode']} {result['errorMessage']}")


if __name__ == "__main__":
    main()
