# -*- coding: utf-8 -*-
# 职位列表 + 岗位详情: 分页循环, 每页输出薪资与每条岗位详情(原始 JSON)
# 用法: python zp_list.py [页数] [城市]
# 依赖同目录: security-js.js / cookies.json(自动回写新 token)
import json
import os
import sys
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
PAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 1
CITY = sys.argv[2] if len(sys.argv) > 2 else "101090500"

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

# fd, null = os.dup(1), os.open(os.devnull, os.O_WRONLY)  # 静音 iv8 原生横幅(直写 fd1)
# os.dup2(null, 1)
import iv8
ctx = iv8.JSContext(environment=ENV, time_mode="logical")
ctx.eval(JS.read_text(encoding="utf-8"), name="security.js")
# os.dup2(fd, 1)

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


for page in range(1, PAGES + 1):
    r = s.get(LIST, params={"page": page, "pageSize": 15, "city": CITY, "_": int(time.time() * 1000)}, timeout=30)
    keep(r)
    for job in (r.json().get("zpData") or {}).get("jobList") or []:
        print(job["salaryDesc"])
        print(job['jobName'])
        print(job['jobDegree'])
        print(job['brandName'])
        time.sleep(random.uniform(0.5,2))
        d = s.get(DETAIL, params={"securityId": job.get("securityId"), "lid": job.get("lid"),
                                  "_": int(time.time() * 1000)}, timeout=30)
        keep(d)
        print(json.dumps(d.json(), ensure_ascii=False))
        time.sleep(random.uniform(0.5,3))
