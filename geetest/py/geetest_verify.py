#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build and submit Geetest verification payloads; see README for verified boundaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geetest_records import AttemptRecord, DEFAULT_RECORD_DIR, safe_server, summarize_attempts
try:
    import geetest_load as gl
except ImportError:                       # 允许单独拷走这个文件用
    gl = None

VERIFY_URL = "https://gcaptcha4.geetest.com/verify"

# 硬编码在 JS 里的 RSA 公钥(取自 gcaptcha4.js 的 setPublic 调用)
RSA_MODULUS_HEX = (
    "C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977D02DC1D9451"
    "F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627715919221AEF91899CAE08C"
    "0D686D748B20A3603BE2318CA6BC2B59706592A9219D0BF05C9F65023A21D2330807252AE0066D5"
    "9CEEFA5F2748EA80BAB81"
)
RSA_E = 0x10001
RSA_N = int(RSA_MODULUS_HEX, 16)
AES_IV = b"0000000000000000"      # 就是这 16 个 ASCII 字符,不是 0x00
RSA_HEX_LEN = 256                 # pt=1 时 JS 要求 RSA 输出正好 256 个 hex 字符

HEADERS = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "referer": os.environ.get("GEETEST_REFERER", "https://www.geetest.com/"),
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
}


# --------------------------------------------------------------------------- #
# 1) pow
# --------------------------------------------------------------------------- #


def guid() -> str:
    """复刻 JS 的 guid():4 段 (65536*(1+random)|0).toString(16).substring(1),共 16 个 hex 字符。"""
    out = []
    for _ in range(4):
        v = int(65536 * (1 + random.random())) & 0xFFFFF      # 落在 [65536, 131071]
        out.append(format(v, "x")[1:5])
    return "".join(out)


def build_pow(lot_number: str, bits: int, hashfunc: str, datetime: str,
              captcha_id: str, version: str = "1") -> dict:
    """返回 {"pow_msg":..., "pow_sign":...}。

    bits=0 时整个 while 循环第一轮即命中:
    hashfunc 输出以 ``"0"*int(bits/4)`` 开头恒成立,所以直接用第一个 guid 的哈希。
    """
    hashers = {"md5": hashlib.md5, "sha1": hashlib.sha1, "sha256": hashlib.sha256}
    if hashfunc not in hashers:
        raise ValueError("不支持的 hashfunc: %r" % hashfunc)
    digest = hashers[hashfunc]
    max_bits = digest().digest_size * 8
    if isinstance(bits, bool) or not isinstance(bits, int) or not 0 <= bits <= max_bits:
        raise ValueError("bits must be an integer between 0 and %d for %s" % (max_bits, hashfunc))
    difficulty = "0" * (bits // 4)
    while True:
        g = guid()
        msg = "%s|%s|%s|%s|%s|%s||%s" % (version, bits, hashfunc, datetime,
                                         captcha_id, lot_number, g)
        sign = digest(msg.encode()).hexdigest()
        if not sign.startswith(difficulty):
            continue
        if bits % 4:                       # bits 不是 4 的倍数时还有额外的字符约束
            limit = {1: "7", 2: "3", 3: "1"}[bits % 4]
            if sign[bits // 4] > limit:
                continue
        return {"pow_msg": msg, "pow_sign": sign}


# --------------------------------------------------------------------------- #
# 2) AES(pt=1 的对称加密)
# --------------------------------------------------------------------------- #


def _aes_cbc_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128/192/256-CBC + PKCS#7。优先用现成库,没有就用内置的纯 Python 实现。"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding as sympad
        padder = sympad.PKCS7(128).padder()
        data = padder.update(plaintext) + padder.finalize()
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        return enc.update(data) + enc.finalize()
    except ImportError:
        pass
    try:
        from Crypto.Cipher import AES as _AES
        from Crypto.Util.Padding import pad as _pad
        return _AES.new(key, _AES.MODE_CBC, iv).encrypt(_pad(plaintext, 16))
    except ImportError:
        pass
    return _aes_cbc_encrypt_pure(plaintext, key, iv)


def _aes_cbc_encrypt_pure(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    """纯 Python AES(标准实现,无第三方依赖)。"""
    SBOX = [
        0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
        0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
        0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
        0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
        0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
        0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
        0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
        0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
        0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
        0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
        0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
        0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
        0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
        0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
        0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
        0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16]
    RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36, 0x6c, 0xd8, 0xab, 0x4d]

    def xtime(a):
        a <<= 1
        return (a ^ 0x1b) & 0xff if a & 0x100 else a

    def expand(k):
        nk, nr = len(k) // 4, len(k) // 4 + 6
        w = [list(k[4 * i:4 * i + 4]) for i in range(nk)]
        for i in range(nk, 4 * (nr + 1)):
            t = list(w[i - 1])
            if i % nk == 0:
                t = t[1:] + t[:1]
                t = [SBOX[b] for b in t]
                t[0] ^= RCON[i // nk - 1]
            elif nk > 6 and i % nk == 4:
                t = [SBOX[b] for b in t]
            w.append([w[i - nk][j] ^ t[j] for j in range(4)])
        return w, nr

    def encrypt_block(block, w, nr):
        s = [block[i] ^ w[i // 4][i % 4] for i in range(16)]

        def mix(a):
            t = a[0] ^ a[1] ^ a[2] ^ a[3]
            return [a[i] ^ t ^ xtime(a[i] ^ a[(i + 1) % 4]) for i in range(4)]
        for rnd in range(1, nr + 1):
            s = [SBOX[b] for b in s]
            s = [s[0], s[5], s[10], s[15], s[4], s[9], s[14], s[3],
                 s[8], s[13], s[2], s[7], s[12], s[1], s[6], s[11]]
            if rnd != nr:
                for c in range(4):
                    s[4 * c:4 * c + 4] = mix(s[4 * c:4 * c + 4])
            s = [s[i] ^ w[rnd * 4 + i // 4][i % 4] for i in range(16)]
        return bytes(s)

    w, nr = expand(key)
    data = plaintext + bytes([16 - len(plaintext) % 16] * (16 - len(plaintext) % 16))
    out, prev = bytearray(), iv
    for i in range(0, len(data), 16):
        blk = bytes(a ^ b for a, b in zip(data[i:i + 16], prev))
        prev = encrypt_block(blk, w, nr)
        out += prev
    return bytes(out)


def aes_key_hex_to_bytes(key: str) -> bytes:
    """JS 里 key 是 16 个字符的字符串,直接按 ASCII 当密钥用。"""
    return key.encode("utf-8")


# --------------------------------------------------------------------------- #
# 3) RSA(jsbn RSAKey + PKCS#1 v1.5)
# --------------------------------------------------------------------------- #


def rsa_encrypt_pkcs1(msg: bytes, n: int = RSA_N, e: int = RSA_E) -> str:
    """PKCS#1 v1.5 加密,返回大写无关的 hex(长度与 JS 一致:偶数位)。"""
    k = (n.bit_length() + 7) // 8
    if len(msg) > k - 11:
        raise ValueError("消息太长,超过 RSA 可加密长度")
    pad_len = k - 3 - len(msg)
    while True:
        ps = bytes(random.randrange(1, 256) for _ in range(pad_len))   # 非零随机填充
        if b"\x00" not in ps:
            break
    block = b"\x00\x02" + ps + b"\x00" + msg
    c = pow(int.from_bytes(block, "big"), e, n)
    h = format(c, "x")
    return h if len(h) % 2 == 0 else "0" + h


# --------------------------------------------------------------------------- #
# 4) 组 w
# --------------------------------------------------------------------------- #


def build_w(payload: dict, pt: str = "1", key: str | None = None,
            retries: int = 200) -> dict:
    """按 pt 组装 w。返回 {"w":..., "key":..., "cipher_hex":..., "rsa_hex":...}

    pt=1: w = hex(AES-CBC(明文)) + rsa_hex,且 JS 会换密钥直到 rsa_hex 正好 256 字符。
    pt=0: w = urlsafe_base64(明文),仅离线编码检查,尚未通过线上协议验证。
    """
    pt = str(pt)
    if pt not in {"0", "1"}:
        raise ValueError("Unsupported pt=%r; supported values are 0 (offline-only) and 1" % pt)
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 1:
        raise ValueError("retries must be a positive integer")
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    raw = plaintext.encode("utf-8")
    if pt == "0":                                        # 无加密分支
        import base64
        return {"w": base64.urlsafe_b64encode(raw).decode(), "key": None,
                "cipher_hex": None, "rsa_hex": None, "plaintext": plaintext}
    for _ in range(retries):
        k = key or guid()
        rsa_hex = rsa_encrypt_pkcs1(aes_key_hex_to_bytes(k))
        if len(rsa_hex) == RSA_HEX_LEN:
            break
    else:
        raise RuntimeError("%d 次 RSA 填充尝试都没得到 %d 位输出" % (retries, RSA_HEX_LEN))
    cipher = _aes_cbc_encrypt(raw, aes_key_hex_to_bytes(k), AES_IV)
    return {"w": cipher.hex() + rsa_hex, "key": k, "cipher_hex": cipher.hex(),
            "rsa_hex": rsa_hex, "plaintext": plaintext}


def build_payload(lot_number: str, points, main_size, passtime: int,
                  pow_msg: str, pow_sign: str, extra: dict | None = None) -> dict:
    """组装 w 的明文。points 是图内像素坐标 [[x,y],...],按 ques 顺序。"""
    w_px, h_px = main_size
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
           not math.isfinite(v) or v <= 0 for v in (w_px, h_px)):
        raise ValueError("main_size must contain two positive finite dimensions")
    # JS Math.round(x) rounds positive half-way values upward (Python uses ties-to-even).
    userresponse = [[math.floor(p[0] * 10000.0 / w_px + 0.5), math.floor(p[1] * 10000.0 / h_px + 0.5)]
                    for p in points] if points else []
    payload = {
        "passtime": int(passtime),
        "userresponse": userresponse,
        "device_id": "",
        "lot_number": lot_number,
        "pow_msg": pow_msg,
        "pow_sign": pow_sign,
        "geetest": "captcha",
        "lang": "zh",
        "ep": "123",
        "em": {"ph": 0, "cp": 0, "ek": "11", "wd": 1, "nt": 0, "si": 0, "sc": 0},
    }
    if extra:
        payload.update(extra)
    return payload


# --------------------------------------------------------------------------- #
# 5) 提交
# --------------------------------------------------------------------------- #


def http_get(url: str, headers: dict, timeout: float = 20.0):
    if gl is not None:
        return gl.http_get(url, headers, timeout)
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def submit_verify(captcha_id: str, challenge: str, lot_number: str, payload: str,
                  process_token: str, w: str, pt: str = "1", lang: str = "zho",
                  client_type: str = "web", payload_protocol: str = "1",
                  cookie: str | None = None, callback: str = "",
                  timeout: float = 20.0) -> dict:
    """/verify 是 JSONP(GET + query),返回解析后的 dict。"""
    params = {
        "callback": callback or ("geetest_%d" % int(time.time() * 1000)),
        "captcha_id": captcha_id,
        "client_type": client_type,
        "lot_number": lot_number,
        "payload": payload,
        "process_token": process_token,
        "payload_protocol": payload_protocol,
        "pt": pt,
        "w": w,
    }
    if challenge:
        params["challenge"] = challenge
    if lang:
        params["lang"] = lang
    headers = dict(HEADERS)
    if gl is not None and gl.DEFAULT_COOKIE and cookie is None:
        cookie = gl.DEFAULT_COOKIE
    if cookie:
        headers["cookie"] = cookie
    url = VERIFY_URL + "?" + urllib.parse.urlencode(params)
    try:
        status, _, body = http_get(url, headers, timeout)
    except urllib.error.HTTPError as e:
        return {"status": "error", "http": e.code}
    text = body.decode("utf-8", "replace")
    if gl is not None:
        try:
            return gl.unwrap_jsonp(text, params["callback"])
        except Exception:  # noqa: BLE001
            pass
    try:
        s = text[text.index("(") + 1:text.rindex(")")]
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return {"status": "error", "http": status}


# --------------------------------------------------------------------------- #
# 6) 全流程
# --------------------------------------------------------------------------- #


def attempt_once(args) -> tuple:
    """跑一组,返回 (退出码, 脱敏说明, 脱敏记录);异常也保存最后阶段。"""
    import geetest_solve as gs
    if getattr(args, "model", None):
        gs.MODEL = args.model
    config = {
        "model": gs.MODEL, "verify": getattr(args, "verify", True),
        "agree_px": getattr(args, "agree_px", 10.0),
        "strict_verify": getattr(args, "strict_verify", False),
        "locate_retries": getattr(args, "locate_retries", 2),
        "cross_check": getattr(args, "cross_check", True),
        "candidate_verify": getattr(args, "candidate_verify", True),
        "geometric_rescue": getattr(args, "geometric_rescue", True),
        "main_scale": getattr(args, "main_scale", 2.0),
        "workers": getattr(args, "workers", 2),
        "min_gap_px": getattr(args, "min_gap_px", 20.0),
        "timeout": getattr(args, "timeout", 20.0),
    }
    record = AttemptRecord(getattr(args, "record_dir", None), config)
    solver_file = getattr(gs, "__file__", None)
    if solver_file:
        implementation = {}
        for name in ("geetest_solve.py", "geetest_vision.py", "geetest_rescue.py"):
            try:
                with open(os.path.join(os.path.dirname(solver_file), name), "rb") as source:
                    implementation[name] = hashlib.sha256(source.read()).hexdigest()
            except OSError:
                pass
        record.data["implementation_sha256"] = implementation
    t0 = time.monotonic()

    def stop(code, outcome, explanation):
        record.data["outcome"] = outcome
        return code, explanation, record.data

    try:
        record.stage("load")
        headers = dict(gl.DEFAULT_HEADERS)
        cookie = getattr(args, "cookie", None)
        cookie = gl.DEFAULT_COOKIE if cookie is None else cookie
        if cookie:
            headers["cookie"] = cookie
        callback = "geetest_%d" % int(time.time() * 1000)
        load_params = dict(gl.DEFAULT_PARAMS)
        load_params["callback"] = callback
        if getattr(args, "captcha_id", None):
            load_params["captcha_id"] = args.captcha_id
        if getattr(args, "challenge", None):
            load_params["challenge"] = args.challenge
        load_params = gl.prepare_load_params(load_params)
        _, _, body = gl.http_get(gl.build_load_url(load_params), headers, config["timeout"])
        resp = gl.unwrap_jsonp(body.decode("utf-8", "replace"), callback)
        if not isinstance(resp, dict) or resp.get("status") != "success":
            return stop(1, "load_failed", "/load 未成功,未提交")
        data = resp["data"]
        if data.get("captcha_type") != "icon":
            return stop(1, "unsupported_challenge", "非图标点选题,未提交")

        record.stage("download")
        entries = gl.collect_image_paths(data)
        main_role = next((r for r, _ in entries
                          if r.split("[")[0].split(".")[0] in ("imgs", "img", "bg")), None)
        target_roles = [r for r, _ in entries if r.split("[")[0].split(".")[0] == "ques"]
        target_roles.sort(key=lambda r: int(re.search(r"\[(\d+)\]", r).group(1))
                          if re.search(r"\[(\d+)\]", r) else 0)
        record.expect_images(main_role, target_roles)
        main_role, main_bytes, targets, _, _ = gs.split_images(
            data, headers, config["timeout"], image_callback=record.save_image)
        # Keep a fallback for custom split_images adapters that do not invoke callbacks.
        if not record.data["main_available"]:
            record.save_image(main_role, main_bytes)
        for role, blob in targets:
            target = next((item for item in record.data["targets"] if item["role"] == role), None)
            if target is not None and not target["available"]:
                record.save_image(role, blob)
        if not targets or [r for r, _ in targets] != target_roles:
            return stop(1, "missing_images", "目标图不完整或顺序不一致,未提交")

        record.stage("prepare")
        main_orig = gs.load_rgb(main_bytes)
        sent = gs.prepare_main(main_bytes, scale=config["main_scale"])
        icons = [gs.prep_icon(blob) for _, blob in targets]
        record.stage("locate")
        results, _, warnings, cv_info = gs.locate_all(
            main_orig, gs.data_url(sent), sent.size, icons,
            verify=config["verify"], agree_px=config["agree_px"],
            strict=config["strict_verify"], locate_retries=config["locate_retries"],
            cross_check=config["cross_check"], min_gap_px=config["min_gap_px"],
            candidate_verify=config["candidate_verify"], workers=config["workers"],
            geometric_rescue=config["geometric_rescue"],
            log=lambda message: print("    " + message, file=sys.stderr, flush=True))
        record.predictions(results, cv_info)
        record.data["warning_count"] = len(warnings)
        points = []
        if len(results) != len(targets):
            return stop(1, "incomplete_predictions", "预测数与目标图数量不一致,未提交")
        for index, result in enumerate(results, 1):
            if result.get("index") != index or not result.get("found"):
                return stop(1, "not_found", "#%d 未定位到,未提交" % index)
            center = result.get("center")
            if (not isinstance(center, (list, tuple)) or len(center) != 2 or
                    any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in center) or
                    not (0 <= center[0] < main_orig.width and 0 <= center[1] < main_orig.height)):
                return stop(1, "invalid_prediction", "#%d 坐标无效,未提交" % index)
            points.append(center)
            print("    #%d center=%s verified=%s" %
                  (index, center, result.get("verified") is True), file=sys.stderr)
        for index, point in enumerate(points):
            if any(math.dist(point, other) < config["min_gap_px"] for other in points[index + 1:]):
                return stop(2, "coincident_points", "预测点过于接近,本组未提交")

        record.stage("build")
        pd = data.get("pow_detail") or {}
        captcha_id = data.get("captcha_id") or load_params["captcha_id"]
        lot_number = data.get("lot_number", "")
        dt = pd.get("datetime") or (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()) +
                                    ".000000+08:00")
        powr = build_pow(lot_number, int(pd.get("bits", 0)), pd.get("hashfunc", "md5"), dt,
                        captcha_id, str(pd.get("version", "1")))
        passtime = int((time.monotonic() - t0) * 1000)
        body_obj = build_payload(lot_number, points, main_orig.size, passtime,
                                 powr["pow_msg"], powr["pow_sign"])
        built = build_w(body_obj, str(data.get("pt", "1")))
        if getattr(args, "json_w", None):
            # Explicit debug export only; never included in automatic records.
            with open(args.json_w, "w", encoding="utf-8") as fh:
                fh.write(built["plaintext"])
        record.stage("submit")
        record.flush()
        record.data["server"]["submitted"] = True
        res = submit_verify(captcha_id, data.get("challenge") or load_params["challenge"], lot_number,
                            data.get("payload", ""), data.get("process_token", ""),
                            built["w"], str(data.get("pt", "1")), getattr(args, "lang", "zho"),
                            payload_protocol=str(data.get("payload_protocol", "1")),
                            cookie=cookie, timeout=config["timeout"])
        record.data["server"] = safe_server(res, submitted=True)
        code = verdict(res)
        record.stage("complete")
        outcome = "success" if code == 0 else "server_rejected" if code == 3 else "verify_error"
        return stop(code, outcome, "status=%s result=%s" %
                    (record.data["server"]["status"], record.data["server"]["result"]))
    except Exception as exc:  # noqa: BLE001 - keep the group in the denominator
        record.error(exc)
        return 1, "%s 阶段异常(%s),本组计入分母" % (
            record.data["stage"], type(exc).__name__), record.data
    finally:
        try:
            record.finish()
        except Exception as exc:
            # Preserve a known server outcome even if the final disk write fails.
            record.data["record_error"] = {"type": type(exc).__name__}
            print("[!] 记录写入失败(%s)" % type(exc).__name__, file=sys.stderr)


def full_flow(args) -> int:
    """普通尝试遇成功停止;benchmark 始终完成指定组数。"""
    try:
        import geetest_solve
    except ImportError as e:
        print("[!] --full 无法导入 geetest_solve.py (%s)" % type(e).__name__, file=sys.stderr)
        return 1
    if gl is None:
        print("[!] --full 需要同目录的 geetest_load.py", file=sys.stderr)
        return 1

    if getattr(args, "model", None):
        geetest_solve.MODEL = args.model
    try:
        geetest_solve.validate_vlm_config()
    except ValueError as exc:
        print("[!] %s" % exc, file=sys.stderr)
        return 1

    benchmark = getattr(args, "benchmark", 0)
    count = benchmark or getattr(args, "attempts", 3)
    records = []
    last = 1
    for i in range(1, count + 1):
        print("\n[*] 第 %d/%d 组" % (i, count), file=sys.stderr)
        try:
            code, why, record = attempt_once(args)
        except Exception as exc:  # includes record-directory I/O errors
            code, why = 1, "本组异常(%s),计入分母" % type(exc).__name__
            record = {"server": safe_server(None), "outcome": "error",
                      "error": {"type": type(exc).__name__}}
        records.append(record)
        print("[*] %s" % why, file=sys.stderr)
        last = code
        if code == 0 and not benchmark:
            break
        if i < count:
            time.sleep(1.0)
    summary = summarize_attempts(records, mode="benchmark" if benchmark else "attempts", requested=count)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if benchmark:
        return 0 if summary["success"] == count else (1 if summary["errors"] else 3)
    return last


def verdict(res: dict) -> int:
    safe = safe_server(res, submitted=True)
    if safe["status"] != "success":
        print("[!] /verify 未成功: status=%s result=%s" % (safe["status"], safe["result"]), file=sys.stderr)
        return 1
    r = safe["result"]
    print("[*] result=%s" % r, file=sys.stderr)
    if r == "success":
        print("[+] 通过", file=sys.stderr)
        return 0
    print("[!] server_rejected(result=%s),拒绝原因未知,不能仅据此归因于识别" % r,
          file=sys.stderr)
    return 3


# --------------------------------------------------------------------------- #
# 7) 自检
# --------------------------------------------------------------------------- #


def selftest() -> int:
    """Offline synthetic/NIST checks; these do not establish server acceptance."""
    import base64
    # NIST SP 800-38A F.2.1, first AES-128-CBC block. This implementation adds PKCS#7.
    aes_key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    plain = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
    expected = bytes.fromhex("7649abac8119b246cee98e9b12e9197d")
    assert _aes_cbc_encrypt_pure(plain, aes_key, iv)[:16] == expected
    assert _aes_cbc_encrypt(plain, aes_key, iv)[:16] == expected
    for hashfunc in ("md5", "sha1", "sha256"):
        result = build_pow("synthetic-lot", 0, hashfunc, "2000-01-01 00:00:00", "synthetic-captcha")
        assert result["pow_sign"] == getattr(hashlib, hashfunc)(result["pow_msg"].encode()).hexdigest()
    payload = build_payload("synthetic-lot", [[15, 25]], (300, 200), 1000, "synthetic-pow", "synthetic-sign")
    assert payload["userresponse"] == [[500, 1250]]
    built = build_w(payload, key="0123456789abcdef")
    assert len(built["rsa_hex"]) == RSA_HEX_LEN
    assert built["w"] == built["cipher_hex"] + built["rsa_hex"]
    assert len(bytes.fromhex(built["cipher_hex"])) % 16 == 0
    # pt=0 encoding is checked offline only; its live protocol remains unverified.
    unencrypted = build_w(payload, pt="0")
    assert json.loads(base64.urlsafe_b64decode(unencrypted["w"])) == payload
    print("Offline synthetic/NIST checks passed; pt=0 is not live-verified.")
    return 0


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="极验 gcaptcha4 的 pow/AES/RSA 与 /verify 提交",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--full", action="store_true", help="全流程:取题+大模型定位+提交")
    ap.add_argument("--selftest", action="store_true", help="离线自检(不需要网络)")
    ap.add_argument("--captcha-id", default=(gl.DEFAULT_PARAMS["captcha_id"] if gl else ""))
    ap.add_argument("--challenge", default=(gl.DEFAULT_PARAMS["challenge"] if gl else ""))
    ap.add_argument("--lot-number", default="")
    ap.add_argument("--payload", default="", help="/load 返回的 payload")
    ap.add_argument("--process-token", default="")
    ap.add_argument("--points", default="",
                    help='图内像素坐标,按 ques 顺序,如 "58,67;241,117;177,141"')
    ap.add_argument("--main-size", default="300x200", help="主图尺寸,默认 300x200")
    ap.add_argument("--passtime", type=int, default=0, help="耗时毫秒,默认自动")
    ap.add_argument("--pow-bits", type=int, default=0)
    ap.add_argument("--pow-hashfunc", default="md5")
    ap.add_argument("--pow-datetime", default="")
    ap.add_argument("--pow-version", default="1")
    ap.add_argument("--pt", default="1")
    ap.add_argument("--payload-protocol", default="1", help="手工提交时的 /load payload_protocol")
    ap.add_argument("--lang", default="zho")
    ap.add_argument("--cookie", default=None)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--no-verify", dest="verify", action="store_false",
                    help="--full 时跳过一致性复定位")
    ap.add_argument("--strict-verify", action="store_true", help="一致性校验未通过时不提交")
    ap.add_argument("--no-candidate-verify", dest="candidate_verify", action="store_false",
                    help="关闭候选区域选择,使用直接定位流程(用于消融对比)")
    ap.add_argument("--no-geometric-rescue", dest="geometric_rescue", action="store_false",
                    help="不对冲突或缺失坐标尝试几何救援")
    ap.add_argument("--main-scale", type=float, default=2.0,
                    help="送入模型的主图放大倍数,默认 2.0;记录保留原图")
    ap.add_argument("--model", help="覆盖 VLM_MODEL 模型名")
    ap.add_argument("--workers", type=int, choices=range(1, 5), default=2,
                    help="并行识别目标数,默认 2")
    ap.add_argument("--agree-px", type=float, default=10.0)
    ap.add_argument("--min-gap-px", type=float, default=20.0,
                    help="两个点击点的最小间距,小于它就换一套题重来,默认 20")
    ap.add_argument("--locate-retries", type=int, default=2,
                    help="模型没给出框时换问法重问同一张图的次数,默认 2")
    ap.add_argument("--no-cross-check", dest="cross_check", action="store_false",
                    help="关掉不含大模型的轮廓匹配交叉校验")
    ap.add_argument("--attempts", type=int, default=3, help="--full 最多试几套题,默认 3")
    ap.add_argument("--benchmark", type=int, default=0, metavar="N",
                    help="完成恰好 N 组全流程,成功后也继续,输出每组通过率")
    ap.add_argument("--record-dir", default=str(DEFAULT_RECORD_DIR), metavar="DIR",
                    help="每组图片/预测/空白真值模板的脱敏记录根目录")
    ap.add_argument("--json-w", metavar="FILE", help="显式导出 w 的请求明文(排查用,不属于脱敏记录)")
    args = ap.parse_args(argv)

    if args.benchmark < 0 or args.attempts < 1:
        ap.error("--benchmark 必须非负,--attempts 必须 >= 1")
    if not math.isfinite(args.main_scale) or args.main_scale <= 0:
        ap.error("--main-scale 必须为正有限数")

    if args.selftest:
        return selftest()
    if not args.captcha_id.strip():
        ap.error("Set GEETEST_CAPTCHA_ID or supply --captcha-id")
    if args.full or args.benchmark:
        return full_flow(args)

    if not args.lot_number or not args.points:
        ap.print_help()
        print("\n[!] 需要 --lot-number 与 --points(或加 --full)", file=sys.stderr)
        return 1

    points = []
    for pair in args.points.replace(",", " ").split(";"):
        xy = [float(v) for v in pair.replace(",", " ").split() if v]
        if len(xy) >= 2:
            points.append(xy[:2])
    try:
        w_px, h_px = [int(v) for v in re.split(r"[xX*,]", args.main_size)[:2]]
    except Exception:
        w_px, h_px = 300, 200

    if not args.pow_datetime:
        args.pow_datetime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()) + ".000000+08:00"
    powr = build_pow(args.lot_number, args.pow_bits, args.pow_hashfunc,
                     args.pow_datetime, args.captcha_id, args.pow_version)
    passtime = args.passtime or 3000
    body_obj = build_payload(args.lot_number, points, (w_px, h_px), passtime,
                             powr["pow_msg"], powr["pow_sign"])
    built = build_w(body_obj, args.pt)
    if args.json_w:
        with open(args.json_w, "w", encoding="utf-8") as fh:
            fh.write(built["plaintext"])
    print("[*] w 长度   = %d (密文 %d + RSA %d)" %
          (len(built["w"]), len(built["cipher_hex"] or ""), len(built["rsa_hex"] or "")),
          file=sys.stderr)
    try:
        res = submit_verify(args.captcha_id, args.challenge, args.lot_number, args.payload,
                            args.process_token, built["w"], args.pt, args.lang,
                            payload_protocol=args.payload_protocol,
                            cookie=args.cookie, timeout=args.timeout)
    except Exception as exc:
        print("[!] /verify 请求异常(%s)" % type(exc).__name__, file=sys.stderr)
        return 1
    print(json.dumps(safe_server(res, submitted=True), ensure_ascii=False, indent=2))
    return verdict(res)


if __name__ == "__main__":
    raise SystemExit(main())
