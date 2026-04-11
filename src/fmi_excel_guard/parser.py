from __future__ import annotations

import re
from collections import defaultdict
from io import BytesIO
from typing import BinaryIO

import pandas as pd
from bs4 import BeautifulSoup

from .models import FAQItem, MarketRecord, Section


def load_market_records(file_obj: str | BytesIO | BinaryIO) -> list[MarketRecord]:
    workbook = pd.ExcelFile(file_obj)
    meta_df = workbook.parse("Meta Data")
    toc_df = workbook.parse("ToC")
    faq_df = workbook.parse("FAQ")
    description_df = workbook.parse("Description")

    toc_by_id = {
        int(row["repid"]): html_to_text(row.get("ToC", ""))
        for _, row in toc_df.iterrows()
    }

    faq_by_id: dict[int, list[FAQItem]] = defaultdict(list)
    for _, row in faq_df.iterrows():
        rep_id = int(row["repid"])
        faq_by_id[rep_id].append(
            FAQItem(
                question=normalize_text(row.get("question", "")),
                answer=normalize_text(row.get("answer", "")),
            )
        )

    sections_by_id: dict[int, list[Section]] = defaultdict(list)
    for _, row in description_df.iterrows():
        rep_id = int(row["rep_id"])
        sections_by_id[rep_id].append(
            Section(
                title=normalize_text(row.get("rep_title", "")),
                text=html_to_text(row.get("rep_description", "")),
            )
        )

    records: list[MarketRecord] = []
    for _, row in meta_df.iterrows():
        rep_id = int(row["rep_id"])
        records.append(
            MarketRecord(
                rep_id=rep_id,
                market_name=normalize_text(row.get("rep_keyword", "")),
                meta_desc=normalize_text(row.get("meta_desc", "")),
                meta_title=normalize_text(row.get("meta_title", "")),
                rep_title=html_to_text(row.get("rep_title", "")),
                rep_sub_title=html_to_text(row.get("rep_sub_title", "")),
                toc_text=toc_by_id.get(rep_id, ""),
                faq_items=faq_by_id.get(rep_id, []),
                description_sections=sections_by_id.get(rep_id, []),
            )
        )

    return records


def normalize_text(value: object) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text).strip()


def html_to_text(value: object) -> str:
    text = "" if value is None else str(value)
    if "<" not in text and ">" not in text:
        return normalize_text(text)
    soup = BeautifulSoup(text, "html.parser")
    return normalize_text(soup.get_text(" ", strip=True))
