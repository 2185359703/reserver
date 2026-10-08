"""对一组设备样本执行本地初始化和刷新，保存私有报文及脱敏摘要。"""
from pathlib import Path
import hashlib
import json
import sys
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from aliyun_initcaptcha import initialize_and_refresh

directory = Path(sys.argv[1]).resolve()
if not directory.is_relative_to(ROOT / ".private"):
    raise ValueError("样本必须位于 .private 目录")
result = initialize_and_refresh(profile=str(directory / "profile.json"), secrets=str(directory / "secrets.json"))
(directory / "local_flow.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
token = result["request"]["form"]["DeviceToken"]
summary = {
    "http": result["httpStatus"], "code": result["response"].get("Code"),
    "success": result["response"].get("Success"),
    "certifyIdSha256": hashlib.sha256(result["response"].get("CertifyId", "").encode()).hexdigest(),
    "nonceSha256": hashlib.sha256(result["request"]["form"]["SignatureNonce"].encode()).hexdigest(),
    "timestamp": result["request"]["form"]["Timestamp"],
    "tokenSha256": hashlib.sha256(token.encode()).hexdigest(),
    "bodySha256": hashlib.sha256(result["request"]["body"].encode()).hexdigest(),
    "tokenCharacters": len(token),
}
print(json.dumps(summary, ensure_ascii=False))
if not result["accepted"]:
    raise SystemExit(1)
