from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urljoin

from curl_cffi import requests


BASE_URL = "https://qikan.cqvip.com"
SUMMARY_URL = f"{BASE_URL}/Qikan/Journal/Summary?kind=1&gch=94178X"
ROOT = Path(__file__).parent
COOKIE_HELPER = ROOT / "js" / "cqvip_cookie.bundle.js"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) "
    "Gecko/20100101 Firefox/152.0"
)


def document_headers(*, same_origin: bool = False) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if same_origin else "none",
    }
    if not same_origin:
        headers["Sec-Fetch-User"] = "?1"
    return headers


def _node_executable() -> str:
    configured = os.environ.get("CQVIP_NODE")
    candidate = configured or shutil.which("node.exe") or shutil.which("node")
    if not candidate:
        raise RuntimeError("未找到 Node.js，请安装 Node.js 18+ 或设置 CQVIP_NODE")
    return candidate


def _generate_cookie_pair() -> tuple[str, str]:
    if not COOKIE_HELPER.is_file():
        raise RuntimeError(f"Cookie 算法文件不存在：{COOKIE_HELPER}")
    proc = subprocess.run(
        [_node_executable(), str(COOKIE_HELPER), "--json-cookie"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=60,
    )
    if proc.returncode:
        raise RuntimeError(f"Cookie 算法执行失败（{proc.returncode}）：{proc.stderr[-2000:]}")
    try:
        result = json.loads(proc.stdout)
        s_cookie = result["s"]
        t_cookie = result["t"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Cookie 算法输出格式异常：{proc.stdout[-1000:]}") from exc
    if len(s_cookie) != 88 or len(t_cookie) != 300:
        raise RuntimeError(f"Cookie 长度异常：S={len(s_cookie)}, T={len(t_cookie)}")
    return s_cookie, t_cookie


class CqvipClient:
    def __init__(self) -> None:
        self.session = requests.Session(impersonate="firefox")
        self.summary_html = ""

    def bootstrap(self) -> str:
        s_cookie, generated_t = _generate_cookie_pair()

        self.session = requests.Session(impersonate="firefox")
        self.session.cookies.set(
            "6HZbKHDjIEcgS", s_cookie, domain="qikan.cqvip.com", path="/"
        )
        self.session.cookies.set(
            "6HZbKHDjIEcgT", generated_t, domain="qikan.cqvip.com", path="/"
        )
        response = self.session.get(
            SUMMARY_URL,
            headers={**document_headers(same_origin=True), "Referer": SUMMARY_URL},
            timeout=30,
        )
        if (
            response.status_code != 200
            or len(response.content) < 100_000
            or "中国农村经济" not in response.text
        ):
            raise RuntimeError(
                "Cookie 回放未取得真实正文："
                f"status={response.status_code}, bytes={len(response.content)}"
            )
        self.summary_html = response.text
        return self.summary_html

    def get(self, url: str, *, referer: str = SUMMARY_URL) -> requests.Response:
        response = self.session.get(
            urljoin(BASE_URL, url),
            headers={**document_headers(same_origin=True), "Referer": referer},
            timeout=30,
        )
        return response

    def post_form(
        self, url: str, data: dict[str, str], *, referer: str = SUMMARY_URL
    ) -> requests.Response:
        response = self.session.post(
            urljoin(BASE_URL, url),
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": BASE_URL,
                "Referer": referer,
                "X-Requested-With": "XMLHttpRequest",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            },
            timeout=30,
        )
        return response
