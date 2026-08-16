"""
Business-outcome patterns: legitimate end states the target app can return
that are NOT failures. Kept as data, not buried in control flow, so a
reviewer can see the whole taxonomy for this target app at a glance.
"""

from __future__ import annotations

import re

# (compiled pattern, outcome_kind) - checked against page text after every step.
BUSINESS_OUTCOME_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"No record found", re.IGNORECASE), "member_not_found"),
    (re.compile(r"restricted for this operator", re.IGNORECASE), "permission_denied"),
    (re.compile(r"exceeds available balance", re.IGNORECASE), "validation_error_insufficient_balance"),
    (re.compile(r"must be a non-negative number", re.IGNORECASE), "validation_error_invalid_deposit"),
    (re.compile(r"Invalid account type", re.IGNORECASE), "validation_error_invalid_account_type"),
]


def detect_business_outcome(page_text: str) -> tuple[str, str] | None:
    for pattern, kind in BUSINESS_OUTCOME_PATTERNS:
        m = pattern.search(page_text)
        if m:
            return kind, m.group(0)
    return None
