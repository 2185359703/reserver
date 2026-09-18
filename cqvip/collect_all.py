from __future__ import annotations

import argparse
import csv
import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from article_parser import DETAIL_FIELDS, clean_text, parse_detail, write_csv
from cqvip_client import BASE_URL, CqvipClient, SUMMARY_URL


ROOT = Path(__file__).parent
INDEX_PATH = ROOT / "data" / "article_index_all.json"
OUTPUT_PATH = ROOT / "data" / "中国农村经济_全年份_文章详情.csv"
FAILURE_PATH = ROOT / "中国农村经济_全年份_采集失败.csv"

ALL_FIELDS = [
    *DETAIL_FIELDS[:12],
    "index_year",
    "index_order",
    "index_title",
    *DETAIL_FIELDS[12:],
]
FAILURE_FIELDS = [
    "article_id",
    "index_year",
    "index_issue",
    "index_title",
    "source_url",
    "error",
    "failed_at",
]

_thread_state = threading.local()


def load_index() -> tuple[dict, list[dict[str, str]]]:
    payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    records: list[dict[str, str]] = []
    for issue in payload["issues"]:
        for article in issue["articles"]:
            href = article["href"]
            article_id = parse_qs(urlparse(href).query).get("id", [""])[0]
            if not article_id:
                raise ValueError(f"文章链接缺少 id：{href}")
            records.append(
                {
                    "article_id": article_id,
                    "index_year": str(issue["year"]),
                    "index_issue": str(issue["issue"]),
                    "index_title": clean_text(article.get("title", "")),
                    "source_url": urljoin(BASE_URL, href),
                    "index_order": str(len(records) + 1),
                }
            )
    ids = [record["article_id"] for record in records]
    if len(records) != payload["articleCount"] or len(ids) != len(set(ids)):
        raise ValueError(
            f"索引校验失败：records={len(records)}, expected={payload['articleCount']}, "
            f"unique={len(set(ids))}"
        )
    return payload, records


def load_existing(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ALL_FIELDS:
            raise ValueError("已有全量 CSV 的表头与当前脚本不一致，请换输出文件或使用 --no-resume")
        return list(reader)


def new_client() -> CqvipClient:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            client = CqvipClient()
            client.bootstrap()
            return client
        except Exception as exc:  # noqa: BLE001 - bootstrap retry boundary
            last_error = exc
            time.sleep(attempt)
    raise RuntimeError(f"Cookie 会话初始化失败：{last_error}") from last_error


def thread_client() -> CqvipClient:
    client = getattr(_thread_state, "client", None)
    if client is None:
        client = new_client()
        _thread_state.client = client
    return client


def fetch_one(item: dict[str, str], retries: int, delay: float) -> dict[str, str]:
    client = thread_client()
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = client.get(item["source_url"], referer=SUMMARY_URL)
            if response.status_code == 412 or len(response.content) < 20_000:
                raise RuntimeError(
                    f"响应疑似挑战/空壳：status={response.status_code}, bytes={len(response.content)}"
                )
            row = parse_detail(
                response.text,
                item["article_id"],
                item["index_issue"],
                item["source_url"],
                item["index_year"],
            )
            row.update(
                {
                    "index_year": item["index_year"],
                    "index_order": item["index_order"],
                    "index_title": item["index_title"],
                }
            )
            time.sleep(max(delay, 0))
            return row
        except Exception as exc:  # noqa: BLE001 - per-record retry boundary
            last_error = exc
            if attempt < retries:
                time.sleep(attempt)
                client = new_client()
                _thread_state.client = client
    raise RuntimeError(str(last_error)) from last_error


def append_writer(path: Path, fields: list[str]) -> tuple[object, csv.DictWriter]:
    exists = path.exists() and path.stat().st_size > 0
    handle = path.open("a", encoding="utf-8-sig", newline="")
    writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
    if not exists:
        writer.writeheader()
        handle.flush()
    return handle, writer


def main() -> int:
    parser = argparse.ArgumentParser(description="采集《中国农村经济》维普全部年份公开文章元数据")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--workers", type=int, default=4, help="并发会话数，建议 1–6")
    parser.add_argument("--delay", type=float, default=0.15, help="每个工作线程请求后的等待秒数")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="仅采集前 N 篇（0 表示全部）")
    parser.add_argument("--no-resume", action="store_true", help="忽略已有 CSV，重新采集")
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("--workers 必须在 1–8 之间")

    payload, index = load_index()
    if args.no_resume and args.output.exists():
        args.output.unlink()
    existing = [] if args.no_resume else load_existing(args.output)
    completed = {row["article_id"] for row in existing}
    pending = [item for item in index if item["article_id"] not in completed]
    if args.limit > 0:
        pending = pending[: args.limit]
    print(
        f"覆盖 {payload['yearRange'][0]}–{payload['yearRange'][1]}，"
        f"刊期 {payload['issueCount']} 个，文章索引 {len(index)} 篇。",
        flush=True,
    )
    print(
        f"已完成 {len(completed)} 篇，待采集 {len(pending)} 篇，并发 {args.workers}。",
        flush=True,
    )
    if not pending:
        return 0

    output_handle, output_writer = append_writer(args.output, ALL_FIELDS)
    failure_handle = None
    failure_writer = None
    failures: list[dict[str, str]] = []
    done = 0
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_map: dict[Future, dict[str, str]] = {
                executor.submit(fetch_one, item, args.retries, args.delay): item
                for item in pending
            }
            for future in as_completed(future_map):
                item = future_map[future]
                done += 1
                try:
                    row = future.result()
                    output_writer.writerow(row)
                    output_handle.flush()
                except Exception as exc:  # noqa: BLE001 - continue remaining records
                    failure = {
                        **item,
                        "error": clean_text(str(exc)),
                        "failed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    }
                    failures.append(failure)
                    if failure_handle is None:
                        failure_handle, failure_writer = append_writer(FAILURE_PATH, FAILURE_FIELDS)
                    failure_writer.writerow(failure)
                    failure_handle.flush()
                if done % 25 == 0 or done == len(pending):
                    elapsed = max(time.monotonic() - started, 0.001)
                    rate = done / elapsed
                    remaining = (len(pending) - done) / rate if rate else 0
                    print(
                        f"[{done}/{len(pending)}] {rate:.1f} 篇/秒，"
                        f"预计剩余 {remaining / 60:.1f} 分钟，失败 {len(failures)}。",
                        flush=True,
                    )
    finally:
        output_handle.close()
        if failure_handle is not None:
            failure_handle.close()

    rows = load_existing(args.output)
    rows.sort(key=lambda row: int(row["index_order"]))
    write_csv(args.output, rows, ALL_FIELDS)
    if not failures and FAILURE_PATH.exists():
        FAILURE_PATH.unlink()
    unique = {row["article_id"] for row in rows}
    print(
        f"完成：CSV {len(rows)} 行，唯一文章 {len(unique)} 篇，本轮失败 {len(failures)} 篇。",
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
