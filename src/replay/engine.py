"""
Deterministic replay: given a saved Capability artifact and a set of input
parameters, re-run the recorded flow with NO model in the decision loop.

Locator resolution: role+name first (matches how the artifact was
recorded and is robust to markup/CSS churn), falling back to the
discovery-time XPath only if role+name resolution fails or is ambiguous.
If both fail, that's a hard failure - not a retry-forever situation.

Recoverable conditions are detected and handled BEFORE they can turn into
hard failures: an unexpected confirmation dialog is dismissed by
proceeding, and a session-expiry redirect is recovered by re-running the
recorded login sub-sequence, then resuming at the original step. Both are
logged in the result so a reviewer can see what happened, not just that
it eventually succeeded.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

from src.artifacts.schema import ArtifactStep, Capability
from src.escalation.handoff import request_intervention
from src.replay.outcomes import detect_business_outcome
from src.replay.result import RecoveredCondition, ReplayResult
from src.safety.guardrails import is_url_allowed

DEFAULT_TIMEOUT_MS = 5000


class HardFailureError(Exception):
    def __init__(self, step_id: str, expected: str, observed: str):
        self.step_id = step_id
        self.expected = expected
        self.observed = observed
        super().__init__(f"{step_id}: expected {expected!r}, observed {observed!r}")


class BusinessOutcomeSignal(Exception):
    def __init__(self, kind: str, detail: str):
        self.kind = kind
        self.detail = detail
        super().__init__(detail)


def _substitute(value: str | None, inputs: dict) -> str | None:
    if value is None:
        return None
    for name, val in inputs.items():
        value = value.replace("{" + name + "}", str(val))
    return value


def _resolve_locator(page, step: ArtifactStep):
    loc = step.locator
    role_locator = page.get_by_role(loc.role, name=loc.name, exact=True)
    try:
        if role_locator.count() == 1:
            return role_locator.first
    except Exception:  # noqa: BLE001 - fall through to xpath fallback
        pass

    if loc.xpath_fallback:
        xpath_locator = page.locator(f"xpath={loc.xpath_fallback}")
        try:
            if xpath_locator.count() >= 1:
                return xpath_locator.first
        except Exception:  # noqa: BLE001
            pass

    raise HardFailureError(
        step_id=step.step_id,
        expected=f"role={loc.role!r} name={loc.name!r} (or xpath fallback)",
        observed="no matching element found via role+name or xpath fallback",
    )


class ReplayEngine:
    def __init__(
        self,
        page,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        escalate_on_hard_failure: bool = False,
        evidence_dir: Optional[Path] = None,
        operator_simulator: Optional[Callable[[object], None]] = None,
        auto_confirm_risky: bool = False,
        risky_action_confirmer: Optional[Callable[[Capability, ArtifactStep], bool]] = None,
    ):
        self.page = page
        self.timeout_ms = timeout_ms
        self.escalate_on_hard_failure = escalate_on_hard_failure
        self.evidence_dir = evidence_dir or Path("evidence")
        # Testing hooks only - see src/escalation/handoff.py docstring.
        self.operator_simulator = operator_simulator
        self.auto_confirm_risky = auto_confirm_risky
        self.risky_action_confirmer = risky_action_confirmer

    def run(self, capability: Capability, inputs: dict) -> ReplayResult:
        recovered: list[RecoveredCondition] = []
        completed = 0

        try:
            if not is_url_allowed(capability.target.entry_url, capability.allowlist):
                raise HardFailureError(
                    step_id="entry",
                    expected=f"entry_url within allowlist {capability.allowlist}",
                    observed=capability.target.entry_url,
                )

            for i, step in enumerate(capability.steps):
                self._handle_recoverable_conditions(capability, step, recovered)

                if step.action == "navigate":
                    target_url = _substitute(step.value, inputs)
                    if not is_url_allowed(target_url, capability.allowlist):
                        raise HardFailureError(
                            step_id=step.step_id,
                            expected=f"navigate target within allowlist {capability.allowlist}",
                            observed=target_url,
                        )

                if step.risky and not self._confirm_risky_step(capability, step):
                    raise HardFailureError(
                        step_id=step.step_id,
                        expected="operator confirmation to proceed with a state-changing action",
                        observed="confirmation declined or not given",
                    )

                try:
                    self._execute_step(step, inputs)
                except HardFailureError as exc:
                    if not self.escalate_on_hard_failure:
                        raise

                    handoff = request_intervention(
                        page=self.page,
                        capability_name=capability.name,
                        step_id=step.step_id,
                        reason=f"expected {exc.expected}, observed {exc.observed}",
                        evidence_dir=self.evidence_dir,
                        operator_simulator=self.operator_simulator,
                    )
                    text = self.page.inner_text("body")

                    if capability.success_checkpoint in text:
                        # Human completed the remainder of the goal manually.
                        recovered.append(
                            RecoveredCondition(
                                step_id=step.step_id,
                                kind="human_intervention",
                                detail=f"Operator completed the goal manually; resumed at {handoff.page_url_after}.",
                            )
                        )
                        return self._finalize_success(capability, inputs, recovered, i + 1)

                    outcome = detect_business_outcome(text)
                    if outcome:
                        kind, detail = outcome
                        recovered.append(
                            RecoveredCondition(
                                step_id=step.step_id,
                                kind="human_intervention",
                                detail=f"Operator action surfaced a business outcome: {detail}",
                            )
                        )
                        raise BusinessOutcomeSignal(kind, detail)

                    # Retry the same step once now that a human has acted on
                    # the live session - this is the "hand control back and
                    # resume" path, not a fresh attempt from scratch.
                    self._execute_step(step, inputs)
                    recovered.append(
                        RecoveredCondition(
                            step_id=step.step_id,
                            kind="human_intervention",
                            detail=f"Operator resolved the blocking condition; automation resumed at {handoff.page_url_after}.",
                        )
                    )

                if not is_url_allowed(self.page.url, capability.allowlist):
                    raise HardFailureError(
                        step_id=step.step_id,
                        expected=f"resulting page within allowlist {capability.allowlist}",
                        observed=self.page.url,
                    )

                completed = i + 1
                self._check_business_outcome(step.step_id)

            # One more recoverable-condition check after the last step, since
            # some conditions (like this app's confirmation interstitial)
            # only appear as a RESULT of the final action, not before it.
            self._handle_recoverable_conditions(capability, capability.steps[-1], recovered)

            return self._finalize_success(capability, inputs, recovered, completed)

        except BusinessOutcomeSignal as exc:
            return ReplayResult(
                status="business_outcome",
                business_outcome_kind=exc.kind,
                business_outcome_detail=exc.detail,
                recovered_conditions=recovered,
                steps_completed=completed,
            )

        except HardFailureError as exc:
            return ReplayResult(
                status="hard_failure",
                failed_step=exc.step_id,
                expected=exc.expected,
                observed=exc.observed,
                recovered_conditions=recovered,
                steps_completed=completed,
            )

    def _confirm_risky_step(self, capability: Capability, step: ArtifactStep) -> bool:
        if self.auto_confirm_risky:
            return True
        if self.risky_action_confirmer is not None:
            return self.risky_action_confirmer(capability, step)

        print("\n" + "!" * 60)
        print(f"RISKY ACTION: {capability.name} / {step.step_id} ({step.action})")
        print("This step is state-changing/irreversible. Confirm to proceed.")
        print("!" * 60)
        answer = input("Type 'yes' to proceed, anything else to decline: ").strip().lower()
        return answer == "yes"

    def _finalize_success(self, capability: Capability, inputs: dict, recovered: list, completed: int) -> ReplayResult:
        final_text = self.page.inner_text("body")
        if capability.success_checkpoint not in final_text:
            raise HardFailureError(
                step_id=capability.steps[-1].step_id,
                expected=f"page to contain checkpoint text {capability.success_checkpoint!r}",
                observed=final_text[:200],
            )

        outputs = {k: _substitute("{" + k + "}", inputs) for k in capability.outputs}
        extracted = self._extract_bonus_outputs(final_text)
        outputs.update(extracted)

        return ReplayResult(
            status="success",
            outputs=outputs,
            recovered_conditions=recovered,
            steps_completed=completed,
        )

    def _execute_step(self, step: ArtifactStep, inputs: dict):
        if step.action == "navigate":
            self.page.goto(_substitute(step.value, inputs), timeout=self.timeout_ms * 2)
            return

        locator = _resolve_locator(self.page, step)

        if step.action == "click":
            locator.click(timeout=self.timeout_ms)
        elif step.action == "type_text":
            locator.fill("", timeout=self.timeout_ms)
            locator.fill(_substitute(step.value, inputs), timeout=self.timeout_ms)
        elif step.action == "select_option":
            locator.select_option(label=_substitute(step.value, inputs), timeout=self.timeout_ms)
        else:
            raise HardFailureError(step.step_id, "a known action type", step.action)

    def _check_business_outcome(self, step_id: str):
        text = self.page.inner_text("body")
        match = detect_business_outcome(text)
        if match:
            kind, detail = match
            raise BusinessOutcomeSignal(kind, detail)

    def _handle_recoverable_conditions(self, capability: Capability, upcoming_step: ArtifactStep, recovered: list):
        """
        Called before every step. Detects conditions that legitimately occur
        at runtime and are not part of the recorded happy-path sequence.
        """
        text = self.page.inner_text("body")

        # Recoverable: unexpected confirmation interstitial
        if "Are you sure?" in text:
            confirm_btn = self.page.get_by_role("button", name="Yes, proceed")
            confirm_btn.click(timeout=self.timeout_ms)
            recovered.append(
                RecoveredCondition(
                    step_id=upcoming_step.step_id,
                    kind="unexpected_dialog",
                    detail="Confirmation interstitial dismissed by proceeding.",
                )
            )
            return self._handle_recoverable_conditions(capability, upcoming_step, recovered)

        # Recoverable: session/timeout expiry (redirected to login mid-flow)
        is_login_step = upcoming_step.step_id in ("step_01", "step_02", "step_03")
        if "Operator Login" in text and not is_login_step:
            self._replay_login_subsequence(capability, inputs={})
            recovered.append(
                RecoveredCondition(
                    step_id=upcoming_step.step_id,
                    kind="session_expired",
                    detail="Detected redirect to login mid-flow; re-authenticated and resumed.",
                )
            )

    def _replay_login_subsequence(self, capability: Capability, inputs: dict):
        login_steps = [s for s in capability.steps if s.step_id in ("step_01", "step_02", "step_03")]
        for step in login_steps:
            self._execute_step(step, inputs)

    def _extract_bonus_outputs(self, page_text: str) -> dict:
        """Best-effort extraction of well-known fields beyond the declared schema outputs."""
        extra = {}
        m = re.search(r"Sub-Account ID\s*\n?\s*(\d+)", page_text)
        if m:
            extra["subaccount_id"] = m.group(1)
        return extra
