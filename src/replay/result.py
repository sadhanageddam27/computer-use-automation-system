"""
The replay result contract, deliberately three-way per the brief's own
language:

- BUSINESS_OUTCOME: a legitimate answer the caller needs to know about
  (e.g. "no such member"). Not a crash.
- RECOVERABLE conditions are handled inline during replay and do NOT
  appear as a terminal status - they show up in `recovered_conditions`
  on a result that otherwise succeeded or failed for an unrelated reason.
- HARD_FAILURE: stop, and surface enough to debug (step, expected, observed).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class RecoveredCondition(BaseModel):
    step_id: str
    kind: str  # "unexpected_dialog" | "session_expired"
    detail: str


class ReplayResult(BaseModel):
    status: Literal["success", "business_outcome", "hard_failure"]

    # success
    outputs: dict = Field(default_factory=dict)

    # business_outcome
    business_outcome_kind: Optional[str] = None
    business_outcome_detail: Optional[str] = None

    # hard_failure
    failed_step: Optional[str] = None
    expected: Optional[str] = None
    observed: Optional[str] = None

    recovered_conditions: list[RecoveredCondition] = Field(default_factory=list)
    steps_completed: int = 0
