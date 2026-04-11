from __future__ import annotations

import math
import re
from collections import Counter

from .models import Finding, MarketRecord

MONEY_UNIT_MULTIPLIERS = {
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}


def run_rule_checks(record: MarketRecord) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_forecast_years(record))
    findings.extend(check_market_math(record))
    return findings


def check_forecast_years(record: MarketRecord) -> list[Finding]:
    sources: list[tuple[str, str, int, str]] = []

    meta_title_year = _extract_trailing_year(record.meta_title)
    if meta_title_year:
        sources.append(
            (
                "meta_title",
                record.meta_title,
                meta_title_year,
                _replace_trailing_year(record.meta_title, meta_title_year),
            )
        )

    for label, text in [
        ("meta_desc", record.meta_desc),
        ("rep_sub_title", record.rep_sub_title),
        ("rep_title", record.rep_title),
    ]:
        year = _extract_year_after_by(text)
        if year:
            sources.append((label, text, year, text))

    for item in record.faq_items:
        if not _is_forecast_faq(item.question, item.answer):
            continue
        year = _extract_year_after_by(f"{item.question} {item.answer}")
        if year:
            sources.append(("faq", item.answer, year, item.answer))

    if len(sources) < 2:
        return []

    counts = Counter(year for _, _, year, _ in sources)
    if len(counts) <= 1:
        return []

    consensus_year, consensus_count = counts.most_common(1)[0]
    if consensus_count < 2:
        return []

    findings: list[Finding] = []
    for label, text, year, _ in sources:
        if year == consensus_year:
            continue
        replacement = _replace_year_phrase(text, year, consensus_year)
        findings.append(
            Finding(
                market_name=record.market_name,
                category="numeric_inconsistency",
                source="rule",
                confidence=0.97,
                find_text=text,
                replace_with=replacement,
                why_flagged=(
                    f"This text points to {year}, but other forecast references in the same market point to {consensus_year}. "
                    "The upload team should align the forecast end year."
                ),
            )
        )
    return _dedupe_findings(findings)


def check_market_math(record: MarketRecord) -> list[Finding]:
    sentences = _candidate_sentences(record)
    if not sentences:
        return []

    forecast_years = _extract_forecast_period(record)
    findings: list[Finding] = []

    for sentence in sentences:
        metrics = _extract_sentence_metrics(sentence)
        if not metrics:
            continue

        start_amount, start_unit = metrics["start_amount"], metrics["start_unit"]
        end_amount, end_unit = metrics["end_amount"], metrics["end_unit"]
        cagr = metrics["cagr"]
        periods = metrics["periods"] or forecast_years or 10
        if not periods or periods <= 0:
            continue

        normalized_start = _normalize_money_value(start_amount, start_unit)
        normalized_end = _normalize_money_value(end_amount, end_unit)
        implied_cagr = ((normalized_end / normalized_start) ** (1 / periods) - 1) * 100
        if not math.isfinite(implied_cagr):
            continue
        if abs(implied_cagr - cagr) <= 1.0:
            continue

        replacement = _build_numeric_replacement(record, metrics, implied_cagr)
        category = "unit_scale_error" if start_unit != end_unit or _looks_like_scale_problem(metrics, implied_cagr) else "numeric_inconsistency"
        findings.append(
            Finding(
                market_name=record.market_name,
                category=category,
                source="rule",
                confidence=0.95,
                find_text=sentence,
                replace_with=replacement,
                why_flagged=(
                    f"The published figures imply roughly {implied_cagr:.1f}% CAGR over {periods} years, "
                    f"which does not match the stated {cagr:.1f}%."
                ),
            )
        )

    if findings:
        return _dedupe_findings(findings)

    consensus = _derive_consensus_metrics(record)
    if not consensus:
        return []

    meta_desc_metrics = _extract_meta_desc_metrics(record.meta_desc)
    if not meta_desc_metrics:
        return []

    end_gap = abs(meta_desc_metrics["end_normalized"] - consensus["end_normalized"]) / max(consensus["end_normalized"], 1)
    cagr_gap = abs(meta_desc_metrics["cagr"] - consensus["cagr"])
    if end_gap <= 0.03 and cagr_gap <= 1.0:
        return []

    replacement = (
        f"{record.market_name} is projected to grow from USD {consensus['start_amount']:.1f} {consensus['start_unit']} "
        f"in 2026 to USD {consensus['end_amount']:.1f} {consensus['end_unit']} by {consensus['end_year']}, "
        f"registering a CAGR of {consensus['cagr']:.1f}%."
    )
    return [
        Finding(
            market_name=record.market_name,
            category="unit_scale_error" if end_gap > 0.5 else "numeric_inconsistency",
            source="rule",
            confidence=0.94,
            find_text=record.meta_desc,
            replace_with=replacement,
            why_flagged=(
                "The metadata sentence does not match the stronger numeric consensus drawn from the detailed title and FAQ values."
            ),
        )
    ]


def _candidate_sentences(record: MarketRecord) -> list[str]:
    sentences: list[str] = []
    for text in [record.meta_desc, record.rep_title, record.rep_sub_title]:
        sentences.extend(_split_sentences(text))
    for item in record.faq_items:
        sentences.extend(_split_sentences(item.answer))
    return [sentence for sentence in sentences if "usd" in sentence.lower() and "%" in sentence]


def _split_sentences(text: str) -> list[str]:
    raw_parts = re.split(r"(?<=[.!?])\s+", text)
    return [part.strip() for part in raw_parts if len(part.strip()) >= 25]


def _extract_forecast_period(record: MarketRecord) -> int | None:
    years = []
    for item in record.faq_items:
        question = item.question.lower()
        if "2026" in question and "2036" in question:
            return 10
    for text in [record.rep_title, record.rep_sub_title, record.meta_desc]:
        if "2026" in text and "2036" in text:
            return 10
    return None


def _extract_sentence_metrics(sentence: str) -> dict[str, float | str | int] | None:
    lowered = sentence.lower()
    grow_match = re.search(
        r"from\s+usd\s+([\d.]+)\s+(billion|million|thousand)\s+to\s+usd\s+([\d.]+)\s+(billion|million|thousand)\s+by\s+(20\d{2}).*?(?:cagr of|registering a cagr of|exhibiting a cagr of|at a cagr of)\s+([\d.]+)%",
        lowered,
        flags=re.I,
    )
    if grow_match:
        return {
            "start_amount": float(grow_match.group(1)),
            "start_unit": grow_match.group(2),
            "end_amount": float(grow_match.group(3)),
            "end_unit": grow_match.group(4),
            "end_year": int(grow_match.group(5)),
            "cagr": float(grow_match.group(6)),
            "periods": 10,
        }

    return None


def _derive_consensus_metrics(record: MarketRecord) -> dict[str, float | int | str] | None:
    start_match = re.search(
        r"valued at usd\s+([\d.]+)\s+(billion|million|thousand)\s+in\s+2025",
        record.rep_title.lower(),
        flags=re.I,
    )
    sales_match = re.search(
        r"reach usd\s+([\d.]+)\s+(billion|million|thousand)\s+in\s+2026\s+and\s+usd\s+([\d.]+)\s+(billion|million|thousand)\s+by\s+(20\d{2})",
        record.rep_title.lower(),
        flags=re.I,
    )
    cagr_match = re.search(
        r"(?:cagr of|registering a cagr of|exhibiting a cagr of|at a cagr of)\s+([\d.]+)%",
        f"{record.meta_desc} {record.rep_title}",
        flags=re.I,
    )
    if not (sales_match and cagr_match):
        return None

    start_amount = float(sales_match.group(1))
    start_unit = sales_match.group(2)
    end_amount = float(sales_match.group(3))
    end_unit = sales_match.group(4)
    end_year = int(sales_match.group(5))
    return {
        "start_amount": start_amount,
        "start_unit": start_unit,
        "end_amount": end_amount,
        "end_unit": end_unit,
        "end_year": end_year,
        "cagr": float(cagr_match.group(1)),
        "end_normalized": _normalize_money_value(end_amount, end_unit),
    }


def _extract_meta_desc_metrics(text: str) -> dict[str, float] | None:
    lowered = text.lower()
    match = re.search(
        r"usd\s+([\d.]+)\s+(billion|million|thousand)\s+by\s+(20\d{2}).*?(?:cagr of|registering a cagr of|exhibiting a cagr of|at a cagr of)\s+([\d.]+)%",
        lowered,
        flags=re.I,
    )
    if not match:
        return None
    return {
        "end_normalized": _normalize_money_value(float(match.group(1)), match.group(2)),
        "cagr": float(match.group(4)),
    }


def _replace_trailing_year(text: str, year: int) -> str:
    return re.sub(r"(20\d{2})\s*$", str(year), text)


def _replace_year_phrase(text: str, old_year: int, new_year: int) -> str:
    updated = text.replace(f"by {old_year}", f"by {new_year}")
    updated = updated.replace(f"- {old_year}", f"- {new_year}")
    updated = updated.replace(f" {old_year})", f" {new_year})")
    if updated == text:
        updated = text.replace(str(old_year), str(new_year), 1)
    return updated


def _extract_trailing_year(text: str) -> int | None:
    match = re.search(r"(20\d{2})\s*$", text)
    return int(match.group(1)) if match else None


def _extract_year_after_by(text: str) -> int | None:
    match = re.search(r"\bby\s+(20\d{2})\b", text, flags=re.I)
    return int(match.group(1)) if match else None


def _is_forecast_faq(question: str, answer: str) -> bool:
    text = f"{question} {answer}".lower()
    has_forecast_signal = any(
        keyword in text
        for keyword in ("market size", "demand growth", "cagr", "projected to reach", "forecast", "global market")
    )
    has_numeric_signal = ("usd" in text) or ("%" in text)
    return has_forecast_signal and has_numeric_signal


def _normalize_money_value(amount: float, unit: str) -> float:
    return amount * MONEY_UNIT_MULTIPLIERS[unit.lower()]


def _build_numeric_replacement(record: MarketRecord, metrics: dict[str, float | str | int], implied_cagr: float) -> str:
    return (
        f"{record.market_name} is projected to grow from USD {metrics['start_amount']:.1f} {metrics['start_unit']} "
        f"to USD {metrics['end_amount']:.1f} {metrics['end_unit']} by {metrics['end_year']}, "
        f"registering a CAGR of {implied_cagr:.1f}%."
    )


def _looks_like_scale_problem(metrics: dict[str, float | str | int], implied_cagr: float) -> bool:
    stated = float(metrics["cagr"])
    return abs(implied_cagr - stated) > 3.0


def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[Finding] = []
    for finding in findings:
        key = (finding.market_name, finding.find_text, finding.replace_with)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped
