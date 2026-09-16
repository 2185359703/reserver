# -*- coding: utf-8 -*-
# __zp_stoken__ 一键生成(独立目录, 无外部依赖文件)  用法: python zp_stoken.py
# 流程: 用 cookies.json 请求职位列表 -> 拿 __zp_sseed__/__zp_sts__
#       -> iv8 本地生成 -> 回写新 token 到 cookies.json(保持会话链) -> 原始输出
# 输出: 仅 token 原始值(不截断、不修饰)
import json
import os
import time
import urllib.parse
from pathlib import Path

from curl_cffi import requests

HERE = Path(__file__).resolve().parent
JS = HERE / "security-js.js"
COOK = HERE / "cookies.json"

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

# 1) 取 seed / ts
state = json.loads(COOK.read_text(encoding="utf-8"))
s = requests.Session(impersonate="firefox", trust_env=False)
s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                  "Accept-Language": "zh-CN,zh;q=0.9", "X-Requested-With": "XMLHttpRequest",
                  "Referer": "https://www.zhipin.com/web/geek/jobs?ka=header-jobs"})
for c in state["cookies"]:
    s.cookies.set(c["name"], c["value"], domain=c.get("domain") or ".zhipin.com", path=c.get("path") or "/")
r = s.get("https://www.zhipin.com/wapi/zpgeek/pc/recommend/job/list.json",
          params={"page": 1, "pageSize": 15, "city": "101090500", "_": int(time.time() * 1000)}, timeout=30)
seed, ts = r.cookies.get("__zp_sseed__"), r.cookies.get("__zp_sts__")

# 2) iv8 本地生成
fd, null = os.dup(1), os.open(os.devnull, os.O_WRONLY)  # 静音 iv8 原生横幅(直写 fd1)
os.dup2(null, 1)
import iv8
ctx = iv8.JSContext(environment=ENV, time_mode="logical")
ctx.eval(JS.read_text(encoding="utf-8"), name="security.js")
token = ctx.eval("String(new ABC().z(%s, %s))" % (json.dumps(seed), ts))
os.dup2(fd, 1)

# 3) 回写(种子已消费, 必须换成新 token, 否则会话链断)
quoted = urllib.parse.quote(token, safe="")
for f in [COOK]:
    st = json.loads(f.read_text(encoding="utf-8"))
    for c in st["cookies"]:
        if c["name"] == "__zp_stoken__":
            c["value"] = quoted
    f.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
print(token)
