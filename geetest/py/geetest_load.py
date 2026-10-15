#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Load an authorized Geetest icon challenge using environment-based configuration."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid

try:  # requests 非必需,有则用
    import requests  # type: ignore
except Exception:  # pragma: no cover - 环境没装 requests 时走 urllib
    requests = None  # type: ignore


# --------------------------------------------------------------------------- #
# 请求参数(从环境变量读取,可用命令行覆盖)
# --------------------------------------------------------------------------- #

LOAD_URL = "https://gcaptcha4.geetest.com/load"

DEFAULT_PARAMS = {
    "callback": "",
    "captcha_id": os.environ.get("GEETEST_CAPTCHA_ID", ""),
    "challenge": os.environ.get("GEETEST_CHALLENGE", ""),
    "client_type": "web",
    "lang": "zho",
}

DEFAULT_HEADERS = {
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "referer": os.environ.get("GEETEST_REFERER", "https://www.geetest.com/"),
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
}

DEFAULT_COOKIE = os.environ.get("GEETEST_COOKIE", "")

# 图片 CDN 备选;具体题目优先采用 /load 返回的静态资源域名。
STATIC_HOSTS = ["https://static.geetest.com", "https://dn-staticdown.qbox.me"]

# 图片字段优先级:icon 类型 -> imgs + ques;slide 类型 -> bg/fullbg/slice/slice2
IMAGE_KEY_ORDER = ("imgs", "bg", "fullbg", "slice", "slice2", "puzzle", "img", "ques")
IMAGE_EXT_RE = re.compile(r"\.(?:png|jpe?g|webp|gif)(?:\?|#|$)", re.I)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def http_get(url: str, headers: dict, timeout: float = 15.0):
    """GET,返回 (status_code, content_type, body_bytes)。"""
    if requests is not None:
        resp = requests.get(url, headers=headers, timeout=timeout)
        return resp.status_code, resp.headers.get("Content-Type", ""), resp.content
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read()


def prepare_load_params(params: dict) -> dict:
    """Validate configuration and generate fresh optional request identifiers."""
    prepared = dict(params)
    captcha_id = prepared.get("captcha_id")
    if not isinstance(captcha_id, str) or not captcha_id.strip():
        raise ValueError("Set GEETEST_CAPTCHA_ID or supply --captcha-id before requesting /load")
    if not prepared.get("challenge"):
        prepared["challenge"] = uuid.uuid4().hex
    if not prepared.get("callback"):
        prepared["callback"] = "geetest_%d" % int(time.time() * 1000)
    return prepared


def build_load_url(params: dict) -> str:
    return LOAD_URL + "?" + urllib.parse.urlencode(prepare_load_params(params))


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #

def unwrap_jsonp(text: str, callback: str = "") -> dict:
    """把 ``callback({...})`` 剥成 dict;若本身就是纯 JSON 也能吃。"""
    raw = text.strip().lstrip("\ufeff").strip()
    if raw.endswith(";"):
        raw = raw[:-1].rstrip()
    if callback:
        prefix = callback + "("
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            if raw.endswith(")"):
                raw = raw[:-1]
    if not raw.startswith("{"):  # 兜底:用首尾括号切,兼容 callback 名变化
        left, right = raw.find("("), raw.rfind(")")
        if 0 <= left < right:
            raw = raw[left + 1:right]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("响应不是 JSON 对象")
    return data


def collect_image_paths(data: dict):
    """从 data 里按优先级收集 (role, 相对路径)。"""
    found = []
    seen = set()

    def add(role: str, value):
        if isinstance(value, str):
            path = value.strip()
            if path and path not in seen:
                seen.add(path)
                found.append((role, path))

    for key in IMAGE_KEY_ORDER:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    for k in ("img", "src", "path", "url", "value"):
                        add("%s[%d].%s" % (key, i, k), item.get(k))
                else:
                    add("%s[%d]" % (key, i), item)
        elif isinstance(value, dict):
            for k, item in value.items():
                add("%s.%s" % (key, k), item)
        else:
            add(key, value)

    # 兜底:data 里任何“看起来像图片路径”的字符串(排除 js/css/feedback 之类)
    for key, value in data.items():
        if isinstance(value, str) and "/" in value and IMAGE_EXT_RE.search(value):
            add(key, value)

    return found


def static_hosts(data: dict):
    """优先用响应里的 static_servers(当前接口没有该字段),否则用内置 CDN 列表。"""
    hosts = []
    servers = data.get("static_servers")
    if isinstance(servers, list):
        for host in servers:
            if isinstance(host, str) and host.strip():
                host = host.strip().rstrip("/")
                hosts.append(host if host.startswith("http") else "https://" + host)
    for host in STATIC_HOSTS:
        if host not in hosts:
            hosts.append(host)
    return hosts


def to_url(path: str, host: str) -> str:
    if path.startswith(("http://", "https://", "//")):
        return "https:" + path if path.startswith("//") else path
    return host.rstrip("/") + "/" + path.lstrip("/")


def pick_working_url(path: str, hosts, headers, timeout: float):
    """依次试各 CDN,返回第一个能取到图片的地址;全失败则退回首选地址。"""
    last = to_url(path, hosts[0])
    for host in hosts:
        url = to_url(path, host)
        last = url
        try:
            status, ctype, body = http_get(url, headers, timeout)
        except Exception:
            continue
        if status == 200 and body and (ctype.startswith("image/") or IMAGE_EXT_RE.search(url)):
            return url, status, ctype, len(body)
    return last, None, "", 0


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="请求极验 gcaptcha4 /load 并输出响应中的 4 个图片地址",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--captcha-id", default=DEFAULT_PARAMS["captcha_id"], help="captcha_id")
    parser.add_argument("--challenge", default=DEFAULT_PARAMS["challenge"], help="challenge(一次性)")
    parser.add_argument("--callback", default=DEFAULT_PARAMS["callback"], help="JSONP callback 名")
    parser.add_argument("--client-type", default=DEFAULT_PARAMS["client_type"], help="client_type")
    parser.add_argument("--lang", default=DEFAULT_PARAMS["lang"], help="lang,例如 zho / zh")
    parser.add_argument("--cookie", default=DEFAULT_COOKIE, help="Cookie 头(默认读取 GEETEST_COOKIE,可留空)")
    parser.add_argument("--timeout", type=float, default=15.0, help="超时秒数,默认 15")
    parser.add_argument("-d", "--download", metavar="DIR", help="把图片下载到该目录")
    parser.add_argument("--verify", action="store_true", help="逐个请求验证地址(状态码/类型/大小)")
    parser.add_argument("--json", action="store_true", help="额外打印完整原始响应")
    parser.add_argument("--plain", action="store_true", help="只打印 4 行图片 URL")
    args = parser.parse_args(argv)

    params = {
        "callback": args.callback,
        "captcha_id": args.captcha_id,
        "challenge": args.challenge,
        "client_type": args.client_type,
        "lang": args.lang,
    }
    headers = dict(DEFAULT_HEADERS)
    if args.cookie:
        headers["cookie"] = args.cookie

    try:
        params = prepare_load_params(params)
        load_url = build_load_url(params)
    except ValueError as exc:
        parser.error(str(exc))
    if not args.plain:
        print("[*] GET %s" % load_url)

    try:
        status, _, body = http_get(load_url, headers, args.timeout)
    except Exception as exc:
        print("[!] 请求 /load 失败: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    text = body.decode("utf-8", "replace")
    if not args.plain:
        print("[*] HTTP %s, %d bytes" % (status, len(body)))

    try:
        payload = unwrap_jsonp(text, params["callback"])
    except Exception as exc:
        print("[!] 解析响应失败: %s" % exc, file=sys.stderr)
        print(text[:1000], file=sys.stderr)
        return 2

    if args.json and not args.plain:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if payload.get("status") != "success":
        print("[!] 接口 status=%r(可能 challenge 已过期,需换新的)" % payload.get("status"),
              file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False)[:800], file=sys.stderr)
        return 3

    data = payload.get("data") or {}
    images = collect_image_paths(data)
    if not images:
        print("[!] 响应里没找到图片字段,data keys = %s" % list(data.keys()), file=sys.stderr)
        return 4

    hosts = static_hosts(data)
    if not args.plain:
        print("[*] captcha_type=%s, 找到 %d 张图片, CDN=%s"
              % (data.get("captcha_type"), len(images), ", ".join(hosts)))

    rows = []
    for role, path in images:
        if args.verify or args.download:
            url, code, ctype, size = pick_working_url(path, hosts, headers, args.timeout)
        else:
            url, code, ctype, size = to_url(path, hosts[0]), None, "", 0
        rows.append({"role": role, "path": path, "url": url,
                     "status": code, "content_type": ctype, "size": size})

    if args.download:
        os.makedirs(args.download, exist_ok=True)
        for i, row in enumerate(rows, 1):
            if row["status"] != 200:
                print("[!] 跳过 %s(取不到图)" % row["url"], file=sys.stderr)
                continue
            ext = os.path.splitext(urllib.parse.urlparse(row["url"]).path)[1] or ".jpg"
            name = "%d_%s_%s%s" % (i, data.get("captcha_type", "img"),
                                   re.sub(r"[^\w.-]", "_", row["role"]), ext)
            target = os.path.join(args.download, name)
            _, _, blob = http_get(row["url"], headers, args.timeout)
            with open(target, "wb") as fh:
                fh.write(blob)
            if not args.plain:
                print("[+] 已保存 %s (%d bytes)" % (target, len(blob)))

    if args.plain:
        for row in rows:
            print(row["url"])
        return 0

    total = len(rows)
    labels = {"imgs": "主图(待点选大图)", "ques": "待点选图标", "bg": "背景图",
              "fullbg": "完整背景图", "slice": "拼图滑块", "slice2": "拼图滑块2"}
    print("\n==== 图片请求地址(%d 个)====" % total)
    for i, row in enumerate(rows, 1):
        head = row["role"].split("[")[0].split(".")[0]
        label = labels.get(head, row["role"])
        line = "[%d/%d] %-16s %s" % (i, total, "%s(%s)" % (label, row["role"]), row["url"])
        if row["status"] is not None:
            line += "   <- %s %s %d bytes" % (row["status"], row["content_type"] or "-", row["size"])
        print(line)

    print("\n---- 纯 URL ----")
    for row in rows:
        print(row["url"])

    if total != 4:
        print("\n[!] 注意:本次拿到 %d 张图(题目预期 4 张)。图标题应为 imgs+ques 共 4 张;"
              "若 captcha_type 变成 slide,则为 bg/fullbg/slice/slice2。" % total, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
