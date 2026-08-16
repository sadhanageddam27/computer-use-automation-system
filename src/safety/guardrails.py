"""
Safety primitives shared by the discovery agent and the replay engine:
allowlist enforcement and redaction. Kept as plain functions with no
framework dependency so both call sites use identical logic - a policy
that behaves differently in discovery vs. replay is a bug, not a feature.
"""

from __future__ import annotations

import fnmatch

SENSITIVE_INPUT_TYPES = {"password"}
REDACTED_PLACEHOLDER = "***REDACTED***"


def is_url_allowed(url: str, allowlist: list[str]) -> bool:
    """
    allowlist entries are glob patterns, e.g. 'http://localhost:5001/*'.
    Empty allowlist means nothing is permitted - fail closed, not open.
    """
    if not allowlist:
        return False
    return any(fnmatch.fnmatch(url, pattern) for pattern in allowlist)


def redact_if_sensitive(value: str | None, input_type: str | None) -> str | None:
    """
    Called at the point of logging/persisting, not at the point of use -
    the real value is still used to actually fill the field in the live
    browser; only what gets written to disk (evidence logs, artifacts) is
    redacted.
    """
    if value is not None and input_type in SENSITIVE_INPUT_TYPES:
        return REDACTED_PLACEHOLDER
    return value
