"""
CLI entrypoint for a discovery run.

    python -m src.agent.run --goal "log in and open a savings sub-account for member 12345" \\
        --start-url http://localhost:5001/login \\
        --member-id 12345 --acct-type Savings --deposit 100

    # harness test, no API key or real LLM call needed:
    python -m src.agent.run --dry-run --member-id 12345 --acct-type Savings --deposit 100
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.agent.llm_client import ClaudeDecider, ScriptedDecider, default_login_and_lookup_script
from src.agent.loop import DiscoveryAgent

EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "evidence"


def _serialize_log(log: list[dict]) -> list[dict]:
    # log entries are already plain dicts/primitives except PageElement objects
    # never leak into it, so this is a light pass mainly to be explicit/defensive.
    return json.loads(json.dumps(log, default=str))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal", default="Log in and open a sub-account for a member")
    parser.add_argument("--start-url", default="http://localhost:5001/login")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--dry-run", action="store_true", help="Use a scripted decider instead of calling Claude - for harness testing only.")
    parser.add_argument("--member-id", default="12345")
    parser.add_argument("--acct-type", default="Savings")
    parser.add_argument("--deposit", default="100")
    args = parser.parse_args()

    EVIDENCE_DIR.mkdir(exist_ok=True)
    run_id = time.strftime("discovery_%Y%m%d_%H%M%S")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()
        page.goto(args.start_url)

        if args.dry_run:
            decider = ScriptedDecider(
                default_login_and_lookup_script(args.member_id, args.acct_type, args.deposit)
            )
        else:
            decider = ClaudeDecider()

        agent = DiscoveryAgent(page, decider, max_steps=args.max_steps)
        final_state = agent.run(args.goal)

        screenshot_path = EVIDENCE_DIR / f"{run_id}_final.png"
        page.screenshot(path=str(screenshot_path))
        browser.close()

    log_path = EVIDENCE_DIR / f"{run_id}_log.json"
    with open(log_path, "w") as f:
        json.dump(
            {
                "goal": args.goal,
                "mode": "dry_run" if args.dry_run else "live_llm",
                "status": final_state["status"],
                "result": final_state["result"],
                "steps_taken": final_state["step"],
                "log": _serialize_log(final_state["log"]),
                "screenshot": screenshot_path.name,
            },
            f,
            indent=2,
        )

    print(f"Status: {final_state['status']}")
    print(f"Result: {final_state['result']}")
    print(f"Log written to: {log_path}")
    print(f"Screenshot written to: {screenshot_path}")

    if final_state["status"] != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
