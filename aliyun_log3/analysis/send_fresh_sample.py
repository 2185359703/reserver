"""Send one locally rebuilt request for one paired, private sample profile."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import aliyun_log3


def main():
    sample_dir = Path(sys.argv[1]).resolve()
    if not sample_dir.is_relative_to(ROOT / ".private"):
        raise ValueError("Sample directory must be under the protected .private directory")
    request = aliyun_log3.calculate_request(
        profile=str(sample_dir / "profile.json"),
        secrets=str(sample_dir / "secrets.json"),
    )
    (sample_dir / "node_request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    response = aliyun_log3.send_calculated(request, timeout=30)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    (sample_dir / "node_response.json").write_text(
        json.dumps({"http_status": response.status_code, "payload": payload}, ensure_ascii=False), encoding="utf-8",
    )
    summary = {
        "httpStatus": response.status_code,
        "code": payload.get("Code"),
        "resultObject": payload.get("ResultObject") if isinstance(payload.get("ResultObject"), bool) else None,
        "requestId": payload.get("RequestId"),
        "timestamp": request["timestamp"],
        "nonce": request["form"]["SignatureNonce"],
        "dataSha256": hashlib.sha256(request["form"]["Data"].encode()).hexdigest(),
        "bodySha256": hashlib.sha256(request["body"].encode()).hexdigest(),
        "dataCharacters": len(request["form"]["Data"]),
    }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
