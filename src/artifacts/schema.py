"""
The artifact schema: a typed, versioned, reviewable contract for a
capability an AI agent can invoke - not just a recorded list of steps.

Locator strategy: role + accessible name is the PRIMARY locator (matches
Playwright's own get_by_role, which resolves through the accessibility
tree rather than brittle CSS/XPath). An XPath captured at discovery time
is stored as a fallback only - it's the kind of thing that breaks first
when a legacy app's markup shifts, which is exactly why it's not primary.

Field design deliberately separates:
- what the capability needs to run (inputs)
- what it hands back to the caller (outputs)
- how the caller knows it actually worked (success_checkpoint)
- what it's allowed to touch (allowlist) - ties into the safety guardrails
  built in src/escalation and described in REPORT.md section 6.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class LocatorStrategy(BaseModel):
    role: str = Field(..., description="Accessibility role, e.g. 'textbox', 'button', 'link', 'combobox'.")
    name: str = Field(..., description="Accessible name (label/placeholder/visible text) - primary locator.")
    xpath_fallback: Optional[str] = Field(
        None, description="XPath captured at discovery time. Used only if role+name resolution fails or is ambiguous."
    )
    reasoning: str = Field(
        ..., description="Why this locator should be robust (or its known limitations), for human review."
    )


class ArtifactStep(BaseModel):
    step_id: str
    action: Literal["click", "type_text", "select_option", "navigate"]
    locator: Optional[LocatorStrategy] = Field(None, description="Absent only for 'navigate' actions.")
    value: Optional[str] = Field(
        None,
        description=(
            "Literal value or a template reference to an input param, e.g. '{member_id}'. "
            "Templated values are substituted from `inputs` at replay time."
        ),
    )
    checkpoint: Optional[str] = Field(
        None, description="Assertion this step's result should satisfy before continuing (URL fragment or visible text)."
    )


class FieldSchema(BaseModel):
    type: Literal["string", "number", "boolean"]
    description: str


class TargetConfig(BaseModel):
    app: str
    entry_url: str


class Capability(BaseModel):
    """
    A single reusable, replayable capability - the artifact produced by a
    successful discovery run and consumed by the deterministic replay
    engine (src/replay) with no LLM in the loop.
    """

    name: str
    version: int = 1
    description: str
    target: TargetConfig

    inputs: dict[str, FieldSchema] = Field(default_factory=dict)
    outputs: dict[str, FieldSchema] = Field(default_factory=dict)

    steps: list[ArtifactStep]
    success_checkpoint: str = Field(
        ..., description="Text or URL fragment that must be present at the end of a successful replay."
    )

    allowlist: list[str] = Field(
        default_factory=list, description="Permitted domains/routes this capability may act within."
    )

    source_discovery_log: Optional[str] = Field(
        None, description="Filename of the /evidence/ log this artifact was built from, for traceability."
    )

    class Config:
        # Keep field order stable on serialization - makes diffs in git
        # readable when a capability is re-recorded after drift.
        json_encoders = {}
