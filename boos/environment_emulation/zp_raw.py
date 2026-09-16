# -*- coding: utf-8 -*-
# 落盘两个接口的原始请求/响应报文(不做任何处理)
#   列表 GET /wapi/zpgeek/pc/recommend/job/list.json
#   详情 GET /wapi/zpgeek/job/detail.json
# 用法: python zp_raw.py
# 依赖同目录: security-js.js / cookies.json(自动回写新 token)
# 产出: raw_list.txt / raw_detail.txt
import json
import time
import urllib.parse
from pathlib import Path
import random
from curl_cffi import requests

HERE = Path(__file__).resolve().parent
JS = HERE / "security-js.js"
COOK = HERE / "cookies.json"
LIST = "https://www.zhipin.com/wapi/zpgeek/pc/recommend/job/list.json"
DETAIL = "https://www.zhipin.com/wapi/zpgeek/job/detail.json"
CITY = "101090500"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"
ENV = {
    "navigator": {
        "userAgent": UA,
        "platform": "Win32", "language": "zh-CN", "languages": ["zh-CN", "zh"],
        "hardwareConcurrency": 2, "maxTouchPoints": 0, "webdriver": False,
    },
    "screen": {"width": 3072, "height": 1728, "availWidth": 3072, "availHeight": 1680, "colorDepth": 24, "pixelDepth": 24},
    "window": {"innerWidth": 0, "innerHeight": 0, "outerWidth": 1652, "outerHeight": 1292,
               "screenX": 697, "screenY": 290, "devicePixelRatio": 1.5, "name": "zhipinFrame"},
    "location": {"href": "about:blank", "origin": "null"},
    "webgl": {"vendor": "Mozilla", "renderer": "ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0), or similar",
              "version": "WebGL 1.0", "unmaskedVendor": "Google Inc. (NVIDIA)",
              "unmaskedRenderer": "ANGLE (NVIDIA, NVIDIA GeForce GTX 980 Direct3D11 vs_5_0 ps_5_0), or similar"},
}

import iv8
ctx = iv8.JSContext(environment=ENV, time_mode="logical")
ctx.eval(JS.read_text(encoding="utf-8"), name="security.js")

state = json.loads(COOK.read_text(encoding="utf-8"))
s = requests.Session(impersonate="firefox", trust_env=False)
s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                  "Accept-Language": "zh-CN,zh;q=0.9", "X-Requested-With": "XMLHttpRequest",
                  "Referer": "https://www.zhipin.com/web/geek/jobs?ka=header-jobs"})
for c in state["cookies"]:
    s.cookies.set(c["name"], c["value"], domain=c.get("domain") or ".zhipin.com", path=c.get("path") or "/")


def keep(r):  # 服务端签发 seed -> 立刻重签 token 并回写(否则会话链断)
    seed, ts = r.cookies.get("__zp_sseed__"), r.cookies.get("__zp_sts__")
    if not seed:
        return
    quoted = urllib.parse.quote(ctx.eval("String(new ABC().z(%s, %s))" % (json.dumps(seed), ts)), safe="")
    s.cookies.set("__zp_stoken__", quoted, domain=".zhipin.com", path="/")
    for c in state["cookies"]:
        if c["name"] == "__zp_stoken__":
            c["value"] = quoted
    COOK.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def dump(r, path, note):
    jar = "; ".join("%s=%s" % (c.name, c.value) for c in s.cookies.jar)
    out = []
    out.append("### REQUEST (%s)\n" % note)
    out.append("%s %s HTTP/1.1\n" % (r.request.method, r.request.url))
    hdrs = dict(r.request.headers)
    if "cookie" not in {k.lower() for k in hdrs}:
        hdrs["Cookie"] = jar
    for k, v in hdrs.items():
        out.append("%s: %s\n" % (k, v))
    out.append("\n")
    out.append("### RESPONSE\n")
    out.append("HTTP/1.1 %s %s\n" % (r.status_code, getattr(r, "reason", "") or ""))
    items = r.headers.multi_items() if hasattr(r.headers, "multi_items") else r.headers.items()
    for k, v in items:
        out.append("%s: %s\n" % (k, v))
    out.append("\n")
    Path(path).write_bytes("".join(out).encode("utf-8") + r.content)
    return len(out), len(r.content)


t0 = time.time()
r = s.get(LIST, params={"page": 1, "pageSize": 15, "city": CITY, "_": int(time.time() * 1000)}, timeout=30)
keep(r)
jobs = (r.json().get("zpData") or {}).get("jobList") or []
n1, b1 = dump(r, HERE / "raw_list.txt", "list.json")
print("list.json  GET  code=%s  jobs=%s  headers=%s  body=%sB  -> raw_list.txt" % (r.json().get("code"), len(jobs), r.status_code, b1))

time.sleep(random.uniform(1.0, 2.0))
job = jobs[0]
r2 = s.get(DETAIL, params={"securityId": job.get("securityId"), "lid": job.get("lid"), "_": int(time.time() * 1000)}, timeout=30)
keep(r2)
n2, b2 = dump(r2, HERE / "raw_detail.txt", "detail.json")
print("detail.json GET code=%s  status=%s  body=%sB  -> raw_detail.txt" % (r2.json().get("code"), r2.status_code, b2))
print("elapsed=%.1fs" % (time.time() - t0))
