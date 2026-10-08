"""使用本地 Node.js 构建阿里云 InitCaptcha 初始化与刷新请求。

默认流程：随机选取配套设备样本，初始化取得服务端 CertifyId，
本地计算新的 DeviceToken、UTC 时间与签名，再发送刷新请求。
仅处理初始化参数，不提供验证码答案或登录/短信业务操作。

只查看本地计算摘要：python aliyun_initcaptcha.py --dry-run
执行一次初始化与刷新：python aliyun_initcaptcha.py
指定样本：python aliyun_initcaptcha.py --sample sample-01
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from curl_cffi import requests

ENDPOINT = "https://10jtz0.captcha-open.aliyuncs.com/"
ROOT = Path(__file__).resolve().parent


def calculate(*, phase: str, sample: str = "random", certify_id: str | None = None,
              profile: str | None = None, secrets: str | None = None, node: str = "node",
              server_config: dict | None = None) -> dict:
    """调用 Node.js 选组并计算请求体，不启动浏览器。"""
    command = [node, str(ROOT / "aliyun_initcaptcha_node.cjs"), "--phase", phase, "--sample", sample]
    for name, value in (("certify-id", certify_id), ("profile", profile), ("secrets", secrets)):
        if value is not None:
            command.extend([f"--{name}", value])
    # DeviceConfig 是初始化响应中的会话参数，仅通过本地临时文件传给 Node.js。
    private_dir = ROOT / ".private" / "initcaptcha"
    private_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="config-", dir=private_dir) as temporary:
        if server_config is not None:
            file = Path(temporary) / "server.json"
            file.write_text(json.dumps(server_config, ensure_ascii=False), encoding="utf-8")
            command.extend(["--server-config", str(file)])
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "本地计算失败")
    request = json.loads(result.stdout)
    if request["url"] != ENDPOINT:
        raise ValueError("样本接口与 InitCaptcha 目标接口不一致")
    return request


def send(request: dict, *, timeout: float = 30, proxy: str | None = None,
         impersonate: str | None = "firefox") -> requests.Response:
    """直接发送 Node.js 序列化后的表单，不再次编码，不自动重试。"""
    excluded = {"host", "content-length", "connection", "accept-encoding"}
    headers = {k: v for k, v in request["headers"].items() if k.lower() not in excluded}
    return requests.post(request["url"], headers=headers, data=request["body"].encode("ascii"),
                         timeout=timeout, proxy=proxy, impersonate=impersonate,
                         default_headers=False, accept_encoding=None, allow_redirects=False, verify=True)


def accepted(response: requests.Response) -> tuple[bool, dict]:
    """同时检查 HTTP 与业务响应，避免将普通 200 当作初始化成功。"""
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return (response.status_code == 200 and payload.get("Code") == "Success"
            and payload.get("Success") is True and bool(payload.get("CertifyId"))), payload


def initialize_and_refresh(*, sample: str = "random", profile: str | None = None,
                           secrets: str | None = None, timeout: float = 30,
                           proxy: str | None = None, impersonate: str | None = "firefox",
                           node: str = "node") -> dict:
    """先获取新的服务端 CertifyId，再用同一组设备样本刷新。

    初次随机选择后固定 sampleId，确保两次请求不会混用不同样本。
    """
    bootstrap = calculate(phase="bootstrap", sample=sample, profile=profile, secrets=secrets, node=node)
    first_response = send(bootstrap, timeout=timeout, proxy=proxy, impersonate=impersonate)
    ok, first_payload = accepted(first_response)
    if not ok:
        raise RuntimeError(f"初始化未成功：HTTP {first_response.status_code}，Code={first_payload.get('Code')}")
    refresh = calculate(phase="refresh", sample=bootstrap["sampleId"],
                        certify_id=first_payload["CertifyId"], profile=profile, secrets=secrets, node=node,
                        server_config={"DeviceConfig": first_payload["DeviceConfig"]})
    response = send(refresh, timeout=timeout, proxy=proxy, impersonate=impersonate)
    ok, payload = accepted(response)
    return {"accepted": ok, "sampleId": bootstrap["sampleId"], "bootstrap": bootstrap,
            "bootstrapResponse": first_payload, "request": refresh,
            "response": payload, "httpStatus": response.status_code}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="只计算初始化摘要，不发网络请求")
    parser.add_argument("--sample", default="random", help="随机选组 random、初始 baseline 或样本编号")
    parser.add_argument("--profile", help="配套设备样本 JSON")
    parser.add_argument("--secrets", help="与设备样本配套的密钥 JSON")
    parser.add_argument("--timeout", type=float, default=30, help="每次请求的超时秒数")
    parser.add_argument("--proxy", help="代理地址")
    parser.add_argument("--impersonate", default="firefox", help="curl_cffi 指纹；none 表示关闭")
    parser.add_argument("--node", default="node", help="Node.js 可执行文件")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout 必须大于零")
    try:
        if args.dry_run:
            request = calculate(phase="bootstrap", sample=args.sample, profile=args.profile,
                                secrets=args.secrets, node=args.node)
            print(json.dumps({"sampleId": request["sampleId"], "phase": request["phase"],
                              "url": request["url"], "timestamp": request["form"]["Timestamp"],
                              "nonce": request["form"]["SignatureNonce"],
                              "bodyBytes": len(request["body"].encode("ascii"))}, ensure_ascii=False, indent=2))
            return 0
        result = initialize_and_refresh(sample=args.sample, profile=args.profile, secrets=args.secrets,
                                        timeout=args.timeout, proxy=args.proxy,
                                        impersonate=None if args.impersonate == "none" else args.impersonate,
                                        node=args.node)
        token = result["request"]["form"]["DeviceToken"]
        print(json.dumps({"sampleId": result["sampleId"], "httpStatus": result["httpStatus"],
                          "code": result["response"].get("Code"), "success": result["response"].get("Success"),
                          "certifyIdSha256": hashlib.sha256(result["response"].get("CertifyId", "").encode()).hexdigest(),
                          "deviceTokenCharacters": len(token),
                          "deviceTokenSha256": hashlib.sha256(token.encode()).hexdigest()}, ensure_ascii=False, indent=2))
        return 0 if result["accepted"] else 1
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, requests.RequestsError) as exc:
        print(f"请求失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
