from __future__ import annotations

import re
from typing import Any


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def matches_topic(record: dict[str, Any], topic: dict[str, Any]) -> bool:
    haystack = f"{record.get('title', '')} {record.get('abstract', '')}".lower()
    for group in topic.get("required_term_groups", []):
        if not any(term.lower() in haystack for term in group):
            return False
    return True


def score_record(record: dict[str, Any], topic: dict[str, Any]) -> tuple[int, list[str]]:
    title = str(record.get("title", "")).lower()
    abstract = str(record.get("abstract", "")).lower()
    study_type = str(record.get("study_type", "")).lower()
    publication_types = " ".join(record.get("publication_types", []) or []).lower()
    status = str(record.get("status", "")).lower()
    haystack = " ".join([title, abstract, study_type, publication_types, status])
    score = 1
    reasons: list[str] = []

    structured_terms = {
        "guideline",
        "randomized controlled trial",
        "randomised controlled trial",
        "meta-analysis",
        "systematic review",
    }
    for term, points in topic.get("priority_terms", {}).items():
        normalized = term.lower()
        if normalized in structured_terms:
            matched = normalized in publication_types or normalized in study_type or normalized in title
        elif normalized in {"retraction", "expression of concern", "correction"}:
            matched = normalized in status or normalized in title
        else:
            matched = normalized in haystack
        if matched:
            score += int(points)
            reasons.append(f"{term} +{points}")

    outcome_hits = [
        term for term in topic.get("outcome_terms", []) if term.lower() in haystack
    ]
    if outcome_hits:
        bonus = min(4, len(outcome_hits))
        score += bonus
        reasons.append(f"outcomes({', '.join(outcome_hits[:4])}) +{bonus}")

    if record.get("record_type") == "preprint":
        score -= 2
        reasons.append("preprint -2")
    if record.get("record_type") == "trial":
        score += 2
        reasons.append("clinical trial registry +2")
    if record.get("status") in {"retracted", "expression_of_concern", "corrected"}:
        score += 10
        reasons.append(f"publication status alert({record['status']}) +10")

    return max(score, 0), reasons
