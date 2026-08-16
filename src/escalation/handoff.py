"""
Human escalation & handoff.

Control-transfer model: automation and the human share the exact same
Playwright `page` object / live browser session - there is no new browser,
no new login, no fresh context spun up for the handoff. "Pausing" means
the automation process stops acting; the page stays exactly as it was,
fully interactive. "Resuming" means the automation process starts acting
again from whatever state the page is now in, wherever the human left it.

In production this blocks on input() at the terminal - with headless=False
the browser window is already on-screen, so a human can literally click
into the live session directly. For automated testing, an
`operator_simulator` callback stands in for the human, performing a real
corrective Playwright action on the SAME page instead of a manual click -
this is what proves the handoff mechanism actually transfers control
rather than just compiling. See src/replay/engine.py for the integration
point (escalates on hard_failure, when enabled).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass
class InterventionRequest:
    capability_name: str
    step_id: str
    reason: str
    page_url_before: str
    screenshot_before: str
    timestamp: float


@dataclass
class HandoffRecord:
    request: InterventionRequest
    page_url_after: str
    screenshot_after: str
    resumed_at: float


def request_intervention(
    page,
    capability_name: str,
    step_id: str,
    reason: str,
    evidence_dir: Path,
    operator_simulator: Optional[Callable[[object], None]] = None,
) -> HandoffRecord:
    evidence_dir.mkdir(exist_ok=True)
    run_tag = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"

    screenshot_before = evidence_dir / f"escalation_{run_tag}_before.png"
    page.screenshot(path=str(screenshot_before))

    req = InterventionRequest(
        capability_name=capability_name,
        step_id=step_id,
        reason=reason,
        page_url_before=page.url,
        screenshot_before=screenshot_before.name,
        timestamp=time.time(),
    )
    (evidence_dir / f"escalation_{run_tag}_request.json").write_text(json.dumps(asdict(req), indent=2))

    print("\n" + "=" * 60)
    print("HUMAN INTERVENTION REQUESTED")
    print(f"  Capability: {capability_name}")
    print(f"  Step:       {step_id}")
    print(f"  Reason:     {reason}")
    print(f"  Live session is at: {page.url}")
    print(f"  Screenshot:         {screenshot_before}")
    print("Automation is paused. The browser session above is still live and")
    print("interactive - take whatever manual action is needed, then resume.")
    print("=" * 60)

    if operator_simulator is not None:
        operator_simulator(page)
    else:
        input("Press Enter once you have taken manual action, to resume automation...")

    screenshot_after = evidence_dir / f"escalation_{run_tag}_after.png"
    page.screenshot(path=str(screenshot_after))

    record = HandoffRecord(
        request=req,
        page_url_after=page.url,
        screenshot_after=screenshot_after.name,
        resumed_at=time.time(),
    )
    (evidence_dir / f"escalation_{run_tag}_handoff.json").write_text(json.dumps(asdict(record), indent=2))

    print(f"Automation resumed at {page.url}\n")
    return record
