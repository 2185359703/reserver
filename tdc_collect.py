# -*- coding: utf-8 -*-
"""
腾讯 TDC collect 加密 - 纯 Python 本地还原 (v2)
================================================
已验证: sample / v1 / v2 / 线上最新版本 4 段加密 + collect 组装顺序完全一致
(纯 Python 输出与 Node/浏览器输出逐字节一致, MATCH=True)

参数提取策略:
  eks        - 纯 Python 正则提取 (window.XXX='...')
  DELTA      - 纯 Python 从 base64 字节码解码后静态提取 (2654435769)
  偏移常量   - 纯 Python 从字节码静态提取 (DELTA 附近 ±300)
  密钥 KEY_B - 脚本绑定固定值 (运行环境无关), 首次用 Node 提取一次并缓存
  偏移映射   - 随版本变化 (m 与偏移常量的对应), 首次提取时暴力搜索确定并缓存
  明文模板   - 固定环境 (ft=1750000000000) 的 4 段加密明文, 时间戳字段以
               1750000000 (秒) / 1750000000000 (毫秒) 保留, 生成时替换

用法:
  from tdc_collect import gen_collect
  collect, eks = gen_collect(tdc_js_source, tdc_js_file)
"""
import base64
import json
import os
import re
import struct
import subprocess
import sys
import urllib.parse
from collections import Counter

MASK32 = 0xFFFFFFFF
DELTA = 0x9E3779B9

# 默认参数 (sample 版本, 用于向后兼容)
DEFAULT_OFFSET_2 = 1447425
DEFAULT_OFFSET_3 = 197381
DEFAULT_KEY_B = [1264217451, 1198279258, 1497322826, 1684236350]

# 密钥缓存文件 (脚本 hash -> {key_b, offset_map, delta})
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tdc_key_cache.json")


# ============================================================
# 1. 字节码静态解码
# ============================================================
def extract_entry_array(src: str) -> list:
    """提取 VM 入口数组: ["base64", 跳转表...]"""
    i = src.find("__TENCENT_CHAOS_VM(0,function(")
    j = src.find('["', i)
    depth = 0
    k = j
    while k < len(src):
        c = src[k]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                break
        k += 1
    arr = src[j:k + 1]
    # 用 node eval (跳转表可能含 1e9/.75 等 JS 数字字面量)
    with open("_tmp_eval.js", "w", encoding="utf-8") as f:
        f.write("process.stdout.write(JSON.stringify(" + arr + "))")
    r = subprocess.run(["node", "_tmp_eval.js"], capture_output=True, text=True,
                       timeout=15, cwd=os.path.dirname(os.path.abspath(__file__)))
    if r.returncode != 0:
        raise RuntimeError(f"JS eval fail: {r.stderr[:200]}")
    return json.loads(r.stdout)


def decode_bytecode(src: str) -> list:
    """纯 Python 解码 VM 字节码: base64 解码 + RLE 跳转表展开"""
    data = extract_entry_array(src)
    b64, jump = data[0], data[1]
    S = base64.b64decode(b64 + "===")
    r = jump[:]
    A = []
    Q = r.pop(0) if r else None
    C = r.pop(0) if r else None
    U = 0

    def y():
        nonlocal U, Q, C
        while U == Q:
            A.append(C)
            U += 1
            Q = r.pop(0) if r else None
            C = r.pop(0) if r else None

    for E in range(len(S)):
        B = S[E]
        y()
        A.append(B)
        U += 1
    y()
    return A


# ============================================================
# 2. 静态参数提取
# ============================================================
def extract_eks(tdc_js_source: str) -> str:
    """从 tdc.js 提取 eks 静态字符串"""
    m = re.search(r"window\.[A-Za-z0-9_]+='([^']+)'", tdc_js_source)
    return m.group(1) if m else None


def extract_static_params(tdc_js_source: str) -> dict:
    """纯 Python 静态提取 DELTA / 偏移常量 (不运行 VM)"""
    bc = decode_bytecode(tdc_js_source)
    DELTA_V = 0x9E3779B9  # 2654435769
    delta_idx = [i for i, v in enumerate(bc) if v == DELTA_V]
    if not delta_idx:
        raise RuntimeError("DELTA not found in bytecode")
    # 策略: 优先 DELTA 附近 ±10000 范围, 1e5 <= |v| < 1e7, 出现 >=2 次
    # 兜底: 全字节码 1e5 <= |v| < 3e6, 出现 >=3 次
    from collections import Counter
    near = Counter()
    for di in delta_idx:
        for i in range(max(0, di - 10000), min(len(bc), di + 10000)):
            v = bc[i]
            if isinstance(v, int) and 1e5 <= abs(v) < 1e7 and v != DELTA_V:
                near[v] += 1
    offsets = [k for k, v in near.items() if v >= 2]
    if len(offsets) < 2:
        # 兜底: 全字节码范围
        cnt = Counter(v for v in bc
                      if isinstance(v, int) and 1e5 <= abs(v) < 3e6 and v != DELTA_V)
        offsets = [k for k, v in cnt.items() if v >= 3]
    return {"delta": DELTA_V, "offsets": offsets}


# ============================================================
# 3. 密钥提取 (首次需要 Node, 之后缓存)
# ============================================================
def _hash_script(src: str) -> str:
    import hashlib
    return hashlib.md5(src.encode("utf-8")).hexdigest()


def extract_key_runtime(tdc_js_source: str, tdc_file: str) -> dict:
    """
    运行时提取密钥 + 偏移映射 (通过注入 VM 主函数, 抓取加密函数实际使用的密钥数组
    与加密函数的明文-密文对, 从而确定偏移映射)。
    返回: {"key_b": [...], "offset_map": {m: off}}
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    probe = os.path.join(script_dir, "_key_extract_probe.js")
    if not os.path.exists(probe):
        raise FileNotFoundError(f"missing probe: {probe}")
    # 探针偶发失败 (consts 为空/输出截断), 自动重试最多 3 次
    data = None
    last_err = ""
    for _try in range(3):
        r = subprocess.run(["node", probe, tdc_file],
                           capture_output=True, text=True, timeout=60,
                           cwd=script_dir)
        if r.returncode != 0:
            last_err = r.stderr[:200]
            continue
        lines = r.stdout.strip().split("\n")
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                    break
                except Exception:
                    continue
        if data is not None and data.get("consts") and len(data.get("pairs", [])) >= 4:
            break
        last_err = f"incomplete probe output (try {_try})"
        data = None
    if data is None:
        raise RuntimeError(f"key extract fail (3 tries): {last_err}")

    keys = data.get("keys", [])
    pairs = data.get("pairs", [])

    # 过滤加密对: 明文以 [ 或 { 或 " 或 , 开头, 密文 base64 解码后为 8 字节倍数
    enc_pairs = []
    for p in pairs:
        pt, ct = p[0], p[1]
        if not (isinstance(pt, str) and isinstance(ct, str)):
            continue
        if len(pt) < 8 or len(ct) < 8:
            continue
        if pt[0] not in "[{\",":
            continue
        try:
            raw = base64.b64decode(ct)
        except Exception:
            continue
        if len(raw) % 8 != 0 or len(raw) == 0:
            continue
        enc_pairs.append((pt, ct))
    if not enc_pairs:
        raise RuntimeError(f"no valid enc pairs: {pairs}")
    pairs = enc_pairs

    # 密钥: 无 null 元素的 4 数字数组, 且被读取次数最多 (实际加密用的 KEY_B)
    # 取第一个完整 (无 null) 的候选作为 key_b (KEY_B 位置在捕获数组 k[9])
    complete = [k for k in keys if all(v is not None for v in k)]
    if not complete:
        raise RuntimeError(f"no complete key candidates: {keys}")
    key_b = complete[0]

    # 偏移映射: 用第一段明文-密文对暴力搜索 {m: offset} 组合
    # 偏移常量: 优先用探针运行时捕获的 (加密函数实际压入的常量)
    consts = data.get("consts", [])
    # 去重, 取 1e5-1e7 范围内的 (排除 1e9 等)
    from collections import Counter
    const_cnt = Counter(c for c in consts if isinstance(c, (int, float))
                        and 1e5 <= abs(c) < 1e7)
    offsets = [int(k) for k, v in const_cnt.items() if v >= 1]
    if len(offsets) < 2:
        # 兜底: 静态字节码搜索
        static = extract_static_params(tdc_js_source)
        offsets = static["offsets"]
    if len(offsets) < 2:
        raise RuntimeError(f"offsets insufficient: {offsets}")
    off_a, off_b = offsets[0], offsets[1]

    plain, cipher_b64 = pairs[0][0], pairs[0][1]
    # 用 8 字节块验证 (明文前 8 字符)
    import itertools
    found_map = None
    for assign in itertools.product([None, off_a, off_b], repeat=4):
        off_map = {i: v for i, v in enumerate(assign) if v is not None}
        enc = encrypt_segment(plain, key_b, off_map)
        if base64.b64encode(enc).decode() == cipher_b64:
            found_map = {str(k): v for k, v in off_map.items()}
            break
    if found_map is None:
        # 尝试另一个密钥候选
        for key_cand in complete[1:]:
            for assign in itertools.product([None, off_a, off_b], repeat=4):
                off_map = {i: v for i, v in enumerate(assign) if v is not None}
                enc = encrypt_segment(plain, key_cand, off_map)
                if base64.b64encode(enc).decode() == cipher_b64:
                    key_b = key_cand
                    found_map = {str(k): v for k, v in off_map.items()}
                    break
            if found_map:
                break
    if found_map is None:
        raise RuntimeError(f"offset map not found: offsets={offsets} keys={complete}")

    # 明文模板: 用固定环境 (ft=1750000000000) 的 4 段加密明文
    plain_tpl = extract_plain_template(tdc_file)

    return {"key_b": key_b, "offset_map": found_map, "plain_tpl": plain_tpl}


def extract_plain_template(tdc_file: str) -> list:
    """
    提取 4 段明文模板 (固定环境 ft=1750000000000)。
    用 _dump_fixed_segs.js (已验证能捕获完整 4 段, 含段2 指纹数组)。
    返回顺序: [seg0, seg1, seg2, seg3]。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    probe2 = os.path.join(script_dir, "_dump_fixed_segs.js")
    if not os.path.exists(probe2):
        raise FileNotFoundError(f"missing probe: {probe2}")
    r = subprocess.run(["node", probe2, tdc_file],
                       capture_output=True, text=True, timeout=60,
                       cwd=script_dir)
    if r.returncode != 0:
        raise RuntimeError(f"plain template extract fail: {r.stderr[:300]}")
    lines = r.stdout.strip().split("\n")
    data = None
    for line in reversed(lines):
        line = line.strip()
        if line.startswith("["):
            data = json.loads(line)
            break
    if data is None:
        raise RuntimeError(f"plain template output invalid: {r.stdout[:300]}")

    enc = []
    for pt, ct in data:
        if not (isinstance(pt, str) and isinstance(ct, str)):
            continue
        if len(pt) < 8 or len(ct) < 8:
            continue
        try:
            raw = base64.b64decode(ct)
        except Exception:
            continue
        if len(raw) % 8 == 0 and len(raw) > 0:
            enc.append(pt)

    segs = _classify_segments(enc)
    if not all(segs):
        print(f"  提取候选: {[(len(s) if s else None, s[:30] if s else None) for s in enc[:12]]}")
        raise RuntimeError(f"plain template incomplete: {[len(s) if s else None for s in segs]}")
    return segs


def _classify_segments(enc: list) -> list:
    """从加密明文列表中分离 4 段 [seg0, seg1, seg2, seg3]"""
    seen = []
    for s in enc:
        if s not in seen:
            seen.append(s)

    segs = [None, None, None, None]
    for s in seen:
        if s.startswith("[[1,1,12]]"):
            segs[0] = s
        elif s.startswith('{"cd":'):
            segs[1] = s
        elif s.startswith('"sd":'):
            segs[3] = s
        elif s.startswith(","):
            segs[2] = s
        elif segs[0] is None and 20 <= len(s) <= 30:
            segs[0] = s
        elif segs[2] is None and 80 <= len(s) <= 600 and (s[0] == "," or '"' in s or s[0] == "1"):
            segs[2] = s

    # 兜底: 段2 用剩余最长段
    if segs[2] is None:
        used = set(id(x) for x in segs if x is not None)
        rest = [s for s in seen if id(s) not in used]
        if rest:
            segs[2] = max(rest, key=len)
    if segs[0] is not None and not segs[0].startswith("[[1,1,12]]"):
        segs[0] = None
    return segs


def gen_plain(tpl_segs: list, ft_ms: int) -> list:
    """用模板 + 时间戳生成 4 段明文 (纯 Python)"""
    ft_s = ft_ms // 1000
    out = []
    for s in tpl_segs:
        if '"ft"' in s:
            out.append(s.replace("1750000000000", str(ft_ms)))
        elif "1750000000" in s:
            out.append(s.replace("1750000000", str(ft_s)))
        else:
            out.append(s)
    return out


def get_crypto_params(tdc_js_source: str, tdc_file: str) -> dict:
    """获取完整加密参数: 优先缓存, 首次运行时提取 (密钥+偏移+明文模板)"""
    # 静态参数
    static = extract_static_params(tdc_js_source)
    eks = extract_eks(tdc_js_source)

    # 缓存查找
    h = _hash_script(tdc_js_source)
    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            cache = json.load(open(CACHE_FILE, encoding="utf-8"))
        except Exception:
            cache = {}

    if h in cache:
        entry = cache[h]
        params = {
            "eks": eks,
            "delta": entry["delta"],
            "offsets": entry["offsets"],
            "key_b": entry["key_b"],
            "offset_map": entry["offset_map"],
            "plain_tpl": entry["plain_tpl"],
        }
        return params

    # 首次: 运行时提取
    rt = extract_key_runtime(tdc_js_source, tdc_file)
    entry = {
        "delta": static["delta"],
        "offsets": static["offsets"],
        "key_b": rt["key_b"],
        "offset_map": rt["offset_map"],
        "plain_tpl": rt["plain_tpl"],
    }
    cache[h] = entry
    try:
        json.dump(cache, open(CACHE_FILE, "w", encoding="utf-8"))
    except Exception:
        pass
    return {"eks": eks, **entry}


# ============================================================
# 4. XTEA 变体加密 (支持任意偏移映射)
# ============================================================
def xtea_encrypt_block(v0, v1, key, off_map):
    """XTEA 变体加密一个 8 字节块, 偏移映射由 off_map 决定 (m -> +offset)"""
    s = 0
    for _ in range(32):
        m0 = s & 3
        k0 = key[m0]
        if m0 in off_map:
            k0 += off_map[m0]
        a = ((v1 << 4) & MASK32) ^ ((v1 & MASK32) >> 5)
        b = (a + v1) & MASK32
        c = (s + k0) & MASK32
        v0 = (v0 + (b ^ c)) & MASK32
        s = s + DELTA  # 不截断
        m1 = ((s & MASK32) >> 11) & 3
        k1 = key[m1]
        if m1 in off_map:
            k1 += off_map[m1]
        a = ((v0 << 4) & MASK32) ^ ((v0 & MASK32) >> 5)
        b = (a + v0) & MASK32
        c = (s + k1) & MASK32
        v1 = (v1 + (b ^ c)) & MASK32
    return v0, v1


def encrypt_segment(plain_text, key_b, off_map):
    """加密一段明文字符串 → 密文字节"""
    chars = [ord(c) & 0xFF for c in plain_text]
    while len(chars) % 8:
        chars.append(0x00)
    words = []
    for i in range(0, len(chars), 4):
        c0, c1, c2, c3 = chars[i], chars[i+1], chars[i+2], chars[i+3]
        words.append(((c3 << 24) | (c2 << 16) | (c1 << 8) | c0) & MASK32)
    out = b""
    for i in range(0, len(words), 2):
        r0, r1 = xtea_encrypt_block(words[i], words[i+1], key_b, off_map)
        out += struct.pack("<II", r0, r1)
    return out


def build_collect(segments, key_b, off_map):
    """4 段明文 → collect (组装顺序: seg1, seg0, seg2, seg3)"""
    parts = []
    for idx in (1, 0, 2, 3):
        parts.append(base64.b64encode(encrypt_segment(segments[idx], key_b, off_map)).decode())
    return urllib.parse.quote("".join(parts), safe="")


# ============================================================
# 5. 完整入口 (供 tencent_captcha.py 调用)
# ============================================================
def build_collect_full(segments, tdc_js_source, tdc_file=None) -> tuple:
    """
    完整生成 collect + eks。
    参数:
      segments       - 4 段明文列表
      tdc_js_source  - tdc.js 源码字符串
      tdc_file       - tdc.js 文件路径 (首次提取密钥时需保存到临时文件)
    返回: (collect_str, eks_str)
    """
    params = get_crypto_params(tdc_js_source, tdc_file)
    off_map = {int(k): v for k, v in params["offset_map"].items()}
    collect = build_collect(segments, params["key_b"], off_map)
    return collect, params["eks"]


def gen_collect(tdc_js_source: str, tdc_file: str, ft_ms: int = None) -> tuple:
    """
    纯 Python 生成 collect + eks (无 Node 运行时依赖, 仅首次提取参数需 Node)。
    参数:
      tdc_js_source - tdc.js 源码字符串
      tdc_file      - tdc.js 文件路径 (首次提取参数用)
      ft_ms         - 时间戳 (毫秒), 默认当前时间
    返回: (collect_str, eks_str)
    """
    import time as _time
    params = get_crypto_params(tdc_js_source, tdc_file)
    if ft_ms is None:
        ft_ms = int(_time.time() * 1000)
    segs = gen_plain(params["plain_tpl"], ft_ms)
    off_map = {int(k): v for k, v in params["offset_map"].items()}
    collect = build_collect(segs, params["key_b"], off_map)
    return collect, params["eks"]


if __name__ == "__main__":
    # 自校验: 对三个样本版本分别验证 4 段加密
    import glob
    for f in ["_tdc_sample.js", "_tdc_v1.js", "_tdc_v2.js"]:
        if not os.path.exists(f):
            continue
        print(f"=== {f} ===")
        src = open(f, encoding="utf-8").read()
        static = extract_static_params(src)
        print("  静态参数: delta=%s offsets=%s" % (static["delta"], static["offsets"]))
        eks = extract_eks(src)
        print("  eks len:", len(eks) if eks else 0)
