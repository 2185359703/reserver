"""直接运行；修改同目录 config.json 或选择 --live。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _common.client import run_endpoint
from hotel_search import request

if __name__ == "__main__":
    raise SystemExit(run_endpoint(Path(__file__).resolve().parent, request.build_request, getattr(request, "fetch", None)))
