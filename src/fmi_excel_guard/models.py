from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class FAQItem:
    question: str
    answer: str


@dataclass(slots=True)
class Section:
    title: str
    text: str


@dataclass(slots=True)
class MarketRecord:
    rep_id: int
    market_name: str
    meta_desc: str
    meta_title: str
    rep_title: str
    rep_sub_title: str
    toc_text: str
    faq_items: list[FAQItem] = field(default_factory=list)
    description_sections: list[Section] = field(default_factory=list)


@dataclass(slots=True)
class Finding:
    market_name: str
    category: str
    source: str
    confidence: float
    find_text: str
    replace_with: str
    why_flagged: str


@dataclass(slots=True)
class ReviewUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
