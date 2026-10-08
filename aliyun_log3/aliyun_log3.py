"""使用 Node.js 计算阿里云 Log3 参数，并通过 curl_cffi 发送请求。

安装 Python 依赖：
    python -m pip install curl_cffi

只查看本地计算摘要，不发送网络请求：
    python aliyun_log3.py --dry-run

生成新的 Data、SignatureNonce、Signature，并发送一次请求：
    python aliyun_log3.py

明确需要重放原始取样时使用：
    python aliyun_log3.py --replay

新请求由 aliyun_log3_node.cjs 在本地计算，配置来自受保护的 .private 目录。
设备、事件与密钥按同一组 Camoufox 样本配套使用。样本池可用时随机选组，
每次重新生成上报时间、nonce 和签名。--sample baseline 选择初始配置；
--sample sample-01 选择指定样本。原始请求体仅保存在本地回放文件中。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode

from curl_cffi import requests


# 接口地址与通用请求头；正常模式使用所选样本内的实际浏览器请求头。
URL = 'https://cloudauth-device-dualstack.cn-shanghai.aliyuncs.com/'
HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://console.beeize.com",
    "Pragma": "no-cache",
    "Referer": "https://console.beeize.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "sec-ch-ua": "\"Chromium\";v=\"154\", \"Google Chrome\";v=\"154\", \"Not A(Brand\";v=\"99\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": "\"Windows\""
}
# 原始请求体保存在本地 .private/replay_request.json 中，避免提交设备数据和签名。


def load_replay_request() -> dict:
    """读取本地原始回放文件，校验接口地址及请求体格式。

    回放文件包含完整设备数据和签名，不应提交到版本控制或公共目录。
    正常参数计算不会读取该文件，因此缺少它仍可查看帮助或计算新请求。
    """
    path = Path(__file__).parent / ".private" / "replay_request.json"
    if not path.is_file():
        raise FileNotFoundError("缺少本地回放文件 .private/replay_request.json，请先保存自己的原始请求")
    request = json.loads(path.read_text(encoding="utf-8"))
    if request.get("url") != URL:
        raise ValueError("回放文件中的接口地址与 Log3 目标接口不一致")
    request["body"].encode("ascii")
    return request


def get_form() -> dict[str, str]:
    """返回原始表单字段的新字典，Data 等字段均已完成 URL 解码。

    不要再次 unquote，也不要在传入 post_log3(form=...) 前手动 URL 编码。
    """
    body = load_replay_request()["body"]
    return dict(parse_qsl(body, keep_blank_values=True, strict_parsing=True))


def calculate_request(
    *,
    profile: str | None = None,
    secrets: str | None = None,
    events: str | None = None,
    sample: str | None = None,
    node: str = "node",
) -> dict:
    """调用 Node.js 计算完整请求体，运行时不启动或访问浏览器。"""
    script = Path(__file__).with_name("aliyun_log3_node.cjs")
    command = [node, str(script)]
    for option, value in (("profile", profile), ("secrets", secrets), ("events", events), ("sample", sample)):
        if value is not None:
            command.extend([f"--{option}", value])
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Node.js 参数计算失败")
    request = json.loads(result.stdout)
    if request["url"] != URL:
        raise ValueError("样本配置中的接口地址与 Log3 目标接口不一致")
    return request


def send_calculated(
    request: Mapping,
    *,
    timeout: float = 30,
    proxy: str | None = None,
    impersonate: str | None = "firefox",
) -> requests.Response:
    """直接发送已计算的请求体，避免再次编码或重新生成参数。

    连接、长度和压缩协商请求头交给 curl_cffi 处理，其余头沿用配套样本。
    """
    excluded = {"host", "content-length", "connection", "accept-encoding"}
    headers = {
        key: value for key, value in request["headers"].items()
        if key.lower() not in excluded
    }
    return requests.post(
        request["url"], headers=headers, data=request["body"].encode("ascii"),
        impersonate=impersonate, default_headers=False, accept_encoding=None,
        timeout=timeout, proxy=proxy, allow_redirects=False, verify=True,
    )


def post_log3(
    form: Mapping[str, str] | None = None,
    *,
    timeout: float = 30,
    proxy: str | None = None,
    impersonate: str | None = "firefox",
    replay: bool = False,
    profile: str | None = None,
    secrets: str | None = None,
    events: str | None = None,
    sample: str | None = None,
    node: str = "node",
) -> requests.Response:
    """本地计算新参数，执行一次 POST，并返回完整响应对象。

    replay=True 表示重放原始取样。传入自定义 form 时，调用方应提供有效签名。
    浏览器模拟使用当前 curl_cffi 内置指纹，不保证与 Camoufox 的 TLS 完全一致。
    """
    if form is None and not replay:
        request = calculate_request(profile=profile, secrets=secrets, events=events, sample=sample, node=node)
    elif form is None:
        request = load_replay_request()
    else:
        request = {"url": URL, "headers": HEADERS.copy(), "body": urlencode(form)}
    return send_calculated(
        request, timeout=timeout, proxy=proxy, impersonate=impersonate,
    )


def describe_request() -> dict:
    """输出原始回放摘要，省略完整设备数据和签名。"""
    request = load_replay_request()
    form = dict(parse_qsl(request["body"], keep_blank_values=True, strict_parsing=True))
    return {
        "method": "POST",
        "url": URL,
        "content_type": request["headers"]["Content-Type"],
        "action": form["Action"],
        "version": form["Version"],
        "body_bytes": len(request["body"].encode("ascii")),
        "form_fields": list(form),
        "data_base64_characters": len(form["Data"]),
        "data_decoded_bytes": len(base64.b64decode(form["Data"], validate=True)),
        "signature_method": form["SignatureMethod"],
        "signature_decoded_bytes": len(
            base64.b64decode(form["Signature"], validate=True)
        ),
        "reuses_captured_signature_and_nonce": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="只查看本地计算摘要，不发送请求")
    parser.add_argument("--replay", action="store_true", help="重放本地保存的原始请求")
    parser.add_argument("--timeout", type=float, default=30, help="请求超时秒数，默认 30")
    parser.add_argument("--proxy", help="代理地址，例如 http://127.0.0.1:7897")
    parser.add_argument("--profile", help="设备与事件样本 JSON 路径，需与 --secrets 配套")
    parser.add_argument("--secrets", help="受保护的密钥 JSON 路径，需与 --profile 配套")
    parser.add_argument("--events", help="真实交互事件 JSON 路径，用于替换所选样本的事件")
    parser.add_argument("--sample", default="random", help="random 随机选组，baseline 初始配置，或指定样本编号")
    parser.add_argument("--node", default="node", help="Node.js 可执行文件路径")
    parser.add_argument(
        "--impersonate",
        default="firefox",
        help="curl_cffi 浏览器指纹；none 表示关闭模拟，默认 firefox",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout 必须大于零")
    try:
        if args.replay:
            request = load_replay_request()
        else:
            request = calculate_request(
                profile=args.profile, secrets=args.secrets, events=args.events, sample=args.sample, node=args.node,
            )
        if args.dry_run:
            if args.replay:
                summary = describe_request()
            else:
                form = request["form"]
                summary = {
                    "method": "POST", "url": request["url"], "action": form["Action"],
                    "timestamp": request["timestamp"], "nonce": form["SignatureNonce"],
                    "sample_id": request.get("sampleId"), "batch_id": request.get("batchId"),
                    "body_bytes": len(request["body"].encode("ascii")),
                    "data_characters": len(form["Data"]),
                    "data_sha256": hashlib.sha256(form["Data"].encode()).hexdigest(),
                    "local_calculation": True, "browser_required": False,
                    "device_and_events_source": "captured profile",
                }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        response = send_calculated(
            request,
            timeout=args.timeout,
            proxy=args.proxy,
            impersonate=None if args.impersonate.lower() == "none" else args.impersonate,
        )
    except (requests.RequestsError, OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"请求失败：{exc}", file=sys.stderr)
        return 1

    print(f"HTTP {response.status_code}")
    accepted = False
    try:
        payload = response.json()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        accepted = (
            isinstance(payload, dict)
            and str(payload.get("Code")) == "200"
            and payload.get("ResultObject") is True
        )
    except ValueError:
        print(response.text)
    # 同时检查 HTTP 状态和业务响应，确认 Log3 上报被接受。
    return 0 if 200 <= response.status_code < 300 and accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
