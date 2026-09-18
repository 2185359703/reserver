from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup, Tag


DETAIL_FIELDS = [
    "article_id",
    "title_cn",
    "title_en",
    "authors_cn",
    "authors_en_and_affiliations",
    "institutions_cn",
    "institutions_en",
    "journal_cn",
    "journal_en",
    "year",
    "issue",
    "index_issue",
    "pages",
    "page_count",
    "citation_count",
    "doi",
    "abstract_cn",
    "abstract_en",
    "funds",
    "keywords_cn",
    "keywords_en",
    "classifications",
    "source_url",
    "detail_html_bytes",
    "collected_at",
]


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def text_without_links(node: Tag | None) -> str:
    if node is None:
        return ""
    cloned = BeautifulSoup(str(node), "lxml")
    for element in cloned.select("a"):
        element.decompose()
    return clean_text(cloned.get_text(" ", strip=True))


def titled_links(node: Tag | None) -> list[str]:
    if node is None:
        return []
    values: list[str] = []
    for anchor in node.select("a[title]"):
        value = clean_text(anchor.get("title", ""))
        if value and value not in values:
            values.append(value)
    return values


def direct_spans(node: Tag | None) -> list[str]:
    if node is None:
        return []
    values: list[str] = []
    for span in node.find_all("span", recursive=False):
        if "label" in (span.get("class") or []):
            continue
        value = clean_text(span.get_text(" ", strip=True))
        if value and value not in values:
            values.append(value)
    return values


def full_variant(nodes: Iterable[Tag]) -> str:
    variants = list(nodes)
    return text_without_links(variants[-1]) if variants else ""


def parse_source_line(text: str) -> dict[str, str]:
    result = {"year": "", "issue": "", "pages": "", "page_count": ""}
    source = clean_text(text)
    match = re.search(
        r"(?P<year>\d{4})年第(?P<issue>[^期]+)期(?P<pages>.*?)(?:,|，)共(?P<count>\d+)页",
        source,
    )
    if match:
        result.update(
            {
                "year": match.group("year"),
                "issue": match.group("issue"),
                "pages": clean_text(match.group("pages")),
                "page_count": match.group("count"),
            }
        )
        return result
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", source)
    issue_match = re.search(r"第([^期]+)期", source)
    pages_match = re.search(r"期([^,，]+)", source)
    count_match = re.search(r"共(\d+)页", source)
    result.update(
        {
            "year": year_match.group(1) if year_match else "",
            "issue": issue_match.group(1) if issue_match else "",
            "pages": clean_text(pages_match.group(1)) if pages_match else "",
            "page_count": count_match.group(1) if count_match else "",
        }
    )
    return result


def parse_detail(
    html: str,
    article_id: str,
    index_issue: str,
    source_url: str,
    expected_year: str,
    expected_journal: str = "中国农村经济",
) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    title_node = soup.select_one(".article-title h1")
    if title_node is None:
        raise ValueError("详情页缺少 .article-title h1，可能命中挑战或空壳响应")
    title_clone = BeautifulSoup(str(title_node), "lxml")
    for element in title_clone.select(".pre-view, .cited"):
        element.decompose()
    title_cn = clean_text(title_clone.get_text(" ", strip=True))
    title_en_node = soup.select_one(".article-title > em")
    title_en = clean_text(title_en_node.get_text(" ", strip=True) if title_en_node else "")

    abstract = soup.select_one(".article-detail > .abstract")
    abstract_cn = full_variant(abstract.select(":scope > span.abstract") if abstract else [])
    abstract_en = full_variant(abstract.select(":scope > em") if abstract else [])

    author = soup.select_one(".article-detail > .author")
    authors_cn = titled_links(author)
    author_en_node = author.select_one(":scope > em") if author else None
    authors_en = clean_text(author_en_node.get_text(" ", strip=True) if author_en_node else "")

    organ = soup.select_one(".article-detail > .organ")
    institutions_cn = titled_links(organ)
    organ_en_node = organ.select_one(":scope > em") if organ else None
    institutions_en = clean_text(organ_en_node.get_text(" ", strip=True) if organ_en_node else "")

    journal = soup.select_one(".article-detail > .journal")
    journal_link = journal.select_one(".from > a[title]") if journal else None
    journal_cn = clean_text(journal_link.get("title", "")) if journal_link else ""
    journal_en_node = journal.select_one(":scope > em") if journal else None
    journal_en = clean_text(journal_en_node.get_text(" ", strip=True) if journal_en_node else "")
    volume = journal.select_one(".vol") if journal else None
    source_parts = parse_source_line(volume.get_text(" ", strip=True) if volume else "")
    if not source_parts["year"]:
        source_parts["year"] = expected_year
    if not source_parts["issue"]:
        source_parts["issue"] = index_issue

    cited = soup.select_one(".article-title .cited [data-zkbycount]")
    citation_count = clean_text(
        cited.get("data-zkbycount", "") or cited.get_text() if cited else ""
    )
    funds = direct_spans(soup.select_one(".article-detail > .fund"))
    subject = soup.select_one(".article-detail > .subject")
    keywords_cn = titled_links(subject)
    keywords_en = [
        clean_text(item.get_text(" ", strip=True))
        for item in subject.select(":scope > em > span")
        if clean_text(item.get_text(" ", strip=True))
    ] if subject and subject.select_one(":scope > em") else []
    classifications = titled_links(soup.select_one(".article-detail > .class"))

    article_main = soup.select_one(".article-main")
    searchable = article_main.get_text(" ", strip=True) if article_main else ""
    doi_match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", searchable, re.I)
    doi = doi_match.group(0).rstrip(".,;)") if doi_match else ""

    if journal_cn != expected_journal:
        raise ValueError(f"期刊校验失败：{journal_cn!r}")
    if source_parts["year"] != expected_year:
        raise ValueError(
            f"年份校验失败：页面={source_parts['year']!r}，索引={expected_year!r}"
        )

    return {
        "article_id": article_id,
        "title_cn": title_cn,
        "title_en": title_en,
        "authors_cn": " | ".join(authors_cn),
        "authors_en_and_affiliations": authors_en,
        "institutions_cn": " | ".join(institutions_cn),
        "institutions_en": institutions_en,
        "journal_cn": journal_cn,
        "journal_en": journal_en,
        **source_parts,
        "index_issue": index_issue,
        "citation_count": citation_count,
        "doi": doi,
        "abstract_cn": abstract_cn,
        "abstract_en": abstract_en,
        "funds": " | ".join(funds),
        "keywords_cn": " | ".join(keywords_cn),
        "keywords_en": " | ".join(keywords_en),
        "classifications": " | ".join(classifications),
        "source_url": source_url,
        "detail_html_bytes": str(len(html.encode("utf-8"))),
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)
