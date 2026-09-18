from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from collect_all import ALL_FIELDS, INDEX_PATH, OUTPUT_PATH


def main() -> None:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    with OUTPUT_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        headers = reader.fieldnames or []

    index_records: dict[str, tuple[str, str]] = {}
    for issue in index["issues"]:
        for article in issue["articles"]:
            article_id = parse_qs(urlparse(article["href"]).query)["id"][0]
            index_records[article_id] = (str(issue["year"]), str(issue["issue"]))

    ids = [row["article_id"] for row in rows]
    mismatches = [
        row["article_id"]
        for row in rows
        if index_records.get(row["article_id"])
        != (row["index_year"], row["index_issue"])
        or row["year"] != row["index_year"]
    ]
    missing = sorted(set(index_records) - set(ids))
    extra = sorted(set(ids) - set(index_records))
    sha256 = hashlib.sha256(OUTPUT_PATH.read_bytes()).hexdigest()
    result = {
        "csv": OUTPUT_PATH.name,
        "bytes": OUTPUT_PATH.stat().st_size,
        "sha256": sha256,
        "columns": len(headers),
        "headers_match": headers == ALL_FIELDS,
        "rows": len(rows),
        "unique_ids": len(set(ids)),
        "index_articles": index["articleCount"],
        "issue_coverage": index["issueCount"],
        "index_year_range": index["yearRange"],
        "article_year_range": [min(row["year"] for row in rows), max(row["year"] for row in rows)],
        "journals": sorted({row["journal_cn"] for row in rows}),
        "missing_ids": missing,
        "extra_ids": extra,
        "year_or_issue_mismatches": mismatches,
        "nonempty": {
            "titles": sum(bool(row["title_cn"]) for row in rows),
            "authors": sum(bool(row["authors_cn"]) for row in rows),
            "abstracts": sum(bool(row["abstract_cn"]) for row in rows),
            "keywords": sum(bool(row["keywords_cn"]) for row in rows),
        },
    }
    ok = (
        result["headers_match"]
        and len(rows) == len(set(ids)) == index["articleCount"]
        and result["journals"] == ["中国农村经济"]
        and not missing
        and not extra
        and not mismatches
    )
    result["ok"] = ok
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
