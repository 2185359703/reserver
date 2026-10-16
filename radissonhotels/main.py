"""直接运行八个公开数据接口；--build 仅在本地计算请求。"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from _common.client import execute_endpoint

ROOT = Path(__file__).resolve().parent
FIELDS = {
    "hotel_search": {"hotels": list},
    "hotel_cards": {"hotels": list},
    "multiple_hotels_rates": {"batches": list},
    "hotel_details": {"hotel": dict},
    "hotel_rooms": {"rooms": list},
    "stays_by_rooms": {"rooms": dict},
    "calendar_from": {"dates": list},
    "autocomplete": {"places": list, "hotels": list},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="仅生成八个请求，不发送 HTTP")
    args = parser.parse_args()
    mode = "build" if args.build else "live"
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "mode": mode,
              "browser_used": False, "browser_state_imported": False,
              "cookie_values_saved": False, "independent_session_per_api": True,
              "dependency_snapshot_used": mode == "build", "records": []}
    for name, fields in FIELDS.items():
        module = importlib.import_module(name + ".request")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = execute_endpoint(ROOT / name, module.build_request,
                                    getattr(module, "fetch", None), mode=mode)
        try:
            evidence = json.loads(buffer.getvalue())
        except ValueError:
            evidence = {}
        item = {"folder": name, "exit_code": code, "accepted": code == 0,
                "requests": evidence.get("requests", []) if mode == "live" else [],
                "business_fields": {}}
        if mode == "live" and code == 0:
            data = json.loads((ROOT / name / "response.json").read_text(encoding="utf-8"))
            item["accepted"] = isinstance(data, dict) and all(type(data.get(k)) is v for k, v in fields.items())
            item["business_fields"] = {key: {"type": type(data[key]).__name__, "count": len(data[key])}
                                       for key in fields if key in data and isinstance(data[key], (dict, list))}
            if name == "multiple_hotels_rates":
                batches = data.get("batches", [])
                item["accepted"] = item["accepted"] and bool(batches) and all(
                    isinstance(batch, dict) and isinstance(batch.get("hotels"), list) for batch in batches)
                if item["accepted"]:
                    item["batch_hotel_counts"] = [len(batch["hotels"]) for batch in batches]
        if code:
            item["error"] = evidence.get("error", "Endpoint execution failed")
        result["records"].append(item)
        print(json.dumps({"folder": name, "mode": mode, "accepted": item["accepted"],
                          "statuses": [record.get("status") for record in item["requests"]]}, ensure_ascii=False), flush=True)
        time.sleep(0.2)
    result["accepted_count"] = sum(item["accepted"] for item in result["records"])
    result["attempted_count"] = len(result["records"])
    result["all_eight_accepted"] = result["accepted_count"] == 8
    output = ROOT / "data" / ("latest-build-run.json" if args.build else "latest-live-run.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"mode": mode, "accepted": result["accepted_count"], "total": len(FIELDS),
                      "evidence": str(output)}, ensure_ascii=False), flush=True)
    return 0 if result["all_eight_accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
