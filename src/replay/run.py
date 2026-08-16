"""
CLI entrypoint for deterministic replay - no LLM involved.

    # happy path
    python -m src.replay.run --artifact artifacts/open_member_subaccount_v1.json \\
        --input member_id=12345 --input acct_type=Checking --input deposit=50

    # business outcome (member doesn't exist - not a crash)
    python -m src.replay.run --artifact artifacts/open_member_subaccount_v1.json \\
        --input member_id=00000 --input acct_type=Savings --input deposit=50

    # recoverable: unexpected confirmation dialog injected by the target app
    python -m src.replay.run --artifact artifacts/open_member_subaccount_v1.json \\
        --input member_id=12345 --input acct_type=Savings --input deposit=25 --simulate-dialog

    # recoverable: session expiry injected mid-flow
    python -m src.replay.run --artifact artifacts/open_member_subaccount_v1.json \\
        --input member_id=12345 --input acct_type=Savings --input deposit=25 --simulate-session-expiry
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.artifacts.schema import Capability
from src.replay.engine import ReplayEngine

EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "evidence"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--input", action="append", default=[], help="name=value, repeatable")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--simulate-dialog", action="store_true", help="Testing hook: inject the target app's unexpected-confirmation-dialog condition.")
    parser.add_argument("--simulate-session-expiry", action="store_true", help="Testing hook: inject the target app's session-expiry condition mid-flow.")
    args = parser.parse_args()

    inputs = dict(p.split("=", 1) for p in args.input)
    capability = Capability.model_validate_json(Path(args.artifact).read_text())

    EVIDENCE_DIR.mkdir(exist_ok=True)
    run_id = time.strftime("replay_%Y%m%d_%H%M%S")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()

        if args.simulate_dialog:
            # Force the subaccount-creation form to submit with the mock
            # app's dialog-injection hook, once that page is reached.
            page.on(
                "load",
                lambda pg: pg.evaluate(
                    """
                    () => {
                      const f = document.querySelector('form[action=""], form:not([action])');
                      if (f && window.location.pathname.includes('/subaccount/new')) {
                        f.action = window.location.pathname + '?inject_dialog=1';
                      }
                    }
                    """
                ) if "/subaccount/new" in pg.url else None,
            )

        if args.simulate_session_expiry:
            # After the 4th real page load (post member-search), force a
            # session-expiry redirect exactly once, mid-flow.
            state = {"loads": 0}

            def _maybe_expire(pg):
                state["loads"] += 1
                if state["loads"] == 4:
                    pg.goto(pg.url + ("&" if "?" in pg.url else "?") + "inject_expired=1")

            page.on("load", _maybe_expire)

        engine = ReplayEngine(page)
        result = engine.run(capability, inputs)

        screenshot_path = EVIDENCE_DIR / f"{run_id}_final.png"
        page.screenshot(path=str(screenshot_path))
        browser.close()

    result_path = EVIDENCE_DIR / f"{run_id}_result.json"
    result_path.write_text(
        json.dumps(
            {
                "artifact": Path(args.artifact).name,
                "inputs": inputs,
                "result": result.model_dump(),
                "screenshot": screenshot_path.name,
            },
            indent=2,
        )
    )

    print(f"Status: {result.status}")
    print(f"Result: {result.model_dump()}")
    print(f"Result written to: {result_path}")

    if result.status == "hard_failure":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
