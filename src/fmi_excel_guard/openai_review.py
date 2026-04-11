from __future__ import annotations

import json
from collections.abc import Callable

from openai import OpenAI

from .models import Finding, MarketRecord, ReviewUsage

ALLOWED_CATEGORIES = {
    "numeric_inconsistency",
    "unit_scale_error",
    "company_name_error",
    "company_development_error",
}


def apply_openai_checks(
    *,
    record: MarketRecord,
    api_key: str,
    model: str,
    existing_findings: list[Finding],
    progress_callback: Callable[[int, int, ReviewUsage], None] | None = None,
) -> tuple[list[Finding], ReviewUsage]:
    client = OpenAI(api_key=api_key)
    findings: list[Finding] = []
    chunked_text = _chunk_text(_build_document_text(record))
    usage = ReviewUsage()

    for chunk_index, chunk_text in enumerate(chunked_text, start=1):
        response = client.responses.create(
            model=model,
            reasoning={"effort": "high"},
            input=_build_messages(
                record=record,
                chunk_text=chunk_text,
                chunk_index=chunk_index,
                total_chunks=len(chunked_text),
                existing_findings=findings or existing_findings,
            ),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "fmi_excel_guard_findings",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "findings": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "category": {"type": "string", "enum": sorted(ALLOWED_CATEGORIES)},
                                        "find_text": {"type": "string"},
                                        "replace_with": {"type": "string"},
                                        "why_flagged": {"type": "string"},
                                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                    },
                                    "required": ["category", "find_text", "replace_with", "why_flagged", "confidence"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["findings"],
                        "additionalProperties": False,
                    },
                }
            },
        )
        chunk_usage = _extract_usage(response)
        usage.input_tokens += chunk_usage.input_tokens
        usage.output_tokens += chunk_usage.output_tokens
        usage.total_tokens += chunk_usage.total_tokens

        payload = json.loads(response.output_text)
        for item in payload.get("findings", []):
            confidence = float(item.get("confidence", 0))
            if confidence < 0.9:
                continue
            category = str(item.get("category", ""))
            if category not in ALLOWED_CATEGORIES:
                continue
            findings.append(
                Finding(
                    market_name=record.market_name,
                    category=category,
                    source="openai",
                    confidence=confidence,
                    find_text=str(item.get("find_text", "")).strip(),
                    replace_with=str(item.get("replace_with", "")).strip(),
                    why_flagged=str(item.get("why_flagged", "")).strip(),
                )
            )
        findings = _dedupe_findings(findings)
        if progress_callback:
            progress_callback(chunk_index, len(chunked_text), usage)
    return findings, usage


def _build_messages(
    *,
    record: MarketRecord,
    chunk_text: str,
    chunk_index: int,
    total_chunks: int,
    existing_findings: list[Finding],
) -> list[dict[str, str]]:
    prompt = {
        "task": (
            "Review this uploaded market document for glaring errors only. "
            "Do not flag style issues, segmentation-only mismatches, taxonomy debates, or minor editorial edits. "
            "Only flag high-confidence company name errors, fabricated or wrong company developments, or obvious numeric contradictions missed by rules. "
            "You are reviewing one chunk of the uploaded document, so only return findings supported by the text in this chunk."
        ),
        "rules": [
            "Ignore segmentation mismatch unless it directly proves a factual company or numeric error.",
            "Prefer false negatives over false positives.",
            "Only use the supplied document content and widely known corporate facts.",
            "Return exact find_text copied from the workbook.",
            "Return replace_with as a single copy-paste sentence the upload team can use directly.",
            "If the exact correction is unknown, provide a safe neutral replacement sentence that removes the false claim.",
            "Do not repeat findings that are already listed in existing_findings.",
        ],
        "market": {
            "market_name": record.market_name,
            "meta_title": record.meta_title,
            "meta_desc": record.meta_desc,
            "rep_sub_title": record.rep_sub_title,
            "rep_title_excerpt": record.rep_title[:1200],
            "faq": [{"question": item.question, "answer": item.answer} for item in record.faq_items],
        },
        "chunk_context": {
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "chunk_text": chunk_text,
        },
        "existing_findings": [
            {
                "category": item.category,
                "find_text": item.find_text,
                "replace_with": item.replace_with,
            }
            for item in existing_findings
        ],
    }
    return [
        {
            "role": "system",
            "content": "You are a strict QA checker for uploaded FMI market content.",
        },
        {
            "role": "user",
            "content": json.dumps(prompt, ensure_ascii=True),
        },
    ]


def _build_document_text(record: MarketRecord) -> str:
    parts = [
        f"Market Name: {record.market_name}",
        f"Meta Title: {record.meta_title}",
        f"Meta Description: {record.meta_desc}",
        f"Report Subtitle: {record.rep_sub_title}",
        f"Report Title Excerpt: {record.rep_title}",
        f"Table of Contents: {record.toc_text}",
    ]
    for item in record.faq_items:
        parts.append(f"FAQ Q: {item.question}")
        parts.append(f"FAQ A: {item.answer}")
    for section in record.description_sections:
        if not section.text.strip():
            continue
        parts.append(f"Section: {section.title}")
        parts.append(section.text)
    return "\n\n".join(part for part in parts if part.strip())


def _chunk_text(text: str, *, max_chars: int = 9000, overlap: int = 700) -> list[str]:
    clean_text = text.strip()
    if not clean_text:
        return [""]

    if len(clean_text) <= max_chars:
        return [clean_text]

    chunks: list[str] = []
    start = 0
    while start < len(clean_text):
        end = min(start + max_chars, len(clean_text))
        if end < len(clean_text):
            boundary = clean_text.rfind("\n\n", start, end)
            if boundary <= start + 2000:
                boundary = clean_text.rfind(". ", start, end)
            if boundary > start + 2000:
                end = boundary + 1
        chunk = clean_text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(clean_text):
            break
        start = max(end - overlap, start + 1)
    return chunks or [clean_text]


def _extract_usage(response: object) -> ReviewUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return ReviewUsage()
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", input_tokens + output_tokens) or 0)
    return ReviewUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[Finding] = []
    for finding in findings:
        key = (finding.category, finding.find_text, finding.replace_with)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped
