"""
Turns a successful discovery run's evidence log into a typed, versioned
Capability artifact.

    python -m src.artifacts.build_from_log \\
        --log evidence/discovery_20260816_142313_log.json \\
        --name open_member_subaccount \\
        --param member_id=12345 --param acct_type=Savings --param deposit=100 \\
        --allow "http://localhost:5001/*"

Parameterization is a judgment call, not something inferred automatically:
you tell the builder which literal values used during discovery should
become named input parameters in the artifact (member_id, acct_type,
deposit), and it replaces the matching literal in each step's `value`
with a `{param_name}` template. Everything else in the recorded flow -
the login credentials, the navigation sequence, the button labels - stays
fixed, because it's part of the capability's mechanics, not its inputs.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from src.artifacts.schema import (
    ArtifactStep,
    Capability,
    FieldSchema,
    LocatorStrategy,
    TargetConfig,
)
from src.safety.guardrails import REDACTED_PLACEHOLDER

# Matches the older log format's result_detail strings for entries that
# predate structured target_element logging, e.g.:
#   clicked [2] button "Log In"
#   typed into [0] textbox "Username"
_DETAIL_RE = re.compile(r'\[(\d+)\]\s+(\w+)\s+"([^"]*)"')


def _infer_role_name(entry: dict) -> tuple[str, str]:
    """Fallback for log entries captured before target_element was recorded."""
    m = _DETAIL_RE.search(entry.get("result_detail", ""))
    if m:
        return m.group(2), m.group(3)
    return "unknown", f"element_{entry.get('input', {}).get('element_index', '?')}"


def _templatize(value: str, params: dict[str, str]) -> str:
    for name, literal in params.items():
        if literal is not None and str(literal) == str(value):
            return "{" + name + "}"
    return value


def build_capability(
    log: dict,
    name: str,
    description: str,
    target_url: str,
    app_name: str,
    params: dict[str, str],
    output_fields: dict[str, str],
    allowlist: list[str],
    source_log_name: str,
    risky_steps: set[str] | None = None,
) -> Capability:
    steps: list[ArtifactStep] = []
    auto_params_needed: set[str] = set()
    action_step_ids: list[str] = []

    for entry in log["log"]:
        tool = entry["tool"]
        if tool not in ("click", "type_text", "select_option", "navigate"):
            continue  # finish_goal / give_up are run outcomes, not replay steps

        step_id = f"step_{entry['step']:02d}"
        action_step_ids.append(step_id)

        if tool == "navigate":
            steps.append(
                ArtifactStep(
                    step_id=step_id,
                    action="navigate",
                    locator=None,
                    value=entry["input"]["url"],
                )
            )
            continue

        target = entry.get("target_element")
        if target:
            role, elem_name = target["role"], target["name"]
            xpath = target.get("xpath")
            input_type = target.get("input_type")
            reasoning = "Resolved from live accessibility scan at discovery time; role+name is primary, XPath is fallback only."
        else:
            role, elem_name = _infer_role_name(entry)
            xpath = None
            input_type = None
            reasoning = "Recovered from legacy log format (predates structured element capture) - role/name only, no XPath fallback available. Re-record to improve."

        locator = LocatorStrategy(role=role, name=elem_name, xpath_fallback=xpath, reasoning=reasoning)

        if tool == "type_text":
            raw_value = entry["input"]["text"]
            if raw_value == REDACTED_PLACEHOLDER or input_type == "password":
                # Never bake a secret into the artifact, even a redacted
                # placeholder - force it to be a required input supplied
                # fresh at replay time instead.
                value = "{password}"
                auto_params_needed.add("password")
            else:
                value = _templatize(raw_value, params)
        elif tool == "select_option":
            raw_value = entry["input"]["option_text"]
            value = _templatize(raw_value, params)
        else:  # click
            value = None

        steps.append(ArtifactStep(step_id=step_id, action=tool, locator=locator, value=value))

    # Default risky-step heuristic: the last state-changing action before
    # the goal's success checkpoint is treated as risky (irreversible)
    # unless the caller specifies otherwise.
    if risky_steps is None:
        last_click = next((s for s in reversed(steps) if s.action in ("click",)), None)
        risky_steps = {last_click.step_id} if last_click else set()
    for step in steps:
        if step.step_id in risky_steps:
            step.risky = True

    finish_entry = next((e for e in log["log"] if e["tool"] == "finish_goal"), None)
    checkpoint = (
        finish_entry["input"].get("checkpoint_evidence", "goal completed")
        if finish_entry
        else "goal completed"
    )

    all_input_names = set(params.keys()) | auto_params_needed
    inputs = {
        k: FieldSchema(
            type="string",
            description="Sensitive - never stored, must be supplied fresh at replay time." if k == "password" else f"Input parameter '{k}'",
        )
        for k in all_input_names
    }

    return Capability(
        name=name,
        description=description,
        target=TargetConfig(app=app_name, entry_url=target_url),
        inputs=inputs,
        outputs={k: FieldSchema(type="string", description=f"Output field '{k}'") for k in output_fields},
        steps=steps,
        success_checkpoint=checkpoint,
        allowlist=allowlist,
        source_discovery_log=source_log_name,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, help="Path to a discovery evidence log JSON.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--description", default="Auto-generated from a discovery run.")
    parser.add_argument("--target-url", default="http://localhost:5001/login")
    parser.add_argument("--app-name", default="legacy-bank-mock")
    parser.add_argument("--param", action="append", default=[], help="name=value, repeatable")
    parser.add_argument("--risky-step", action="append", default=None, help="step_id to mark risky, repeatable. Defaults to the last click step if omitted.")
    parser.add_argument("--allow", action="append", default=None)
    parser.add_argument("--out-dir", default="artifacts")
    args = parser.parse_args()

    params = dict(p.split("=", 1) for p in args.param)
    allowlist = args.allow if args.allow else ["http://localhost:5001/*"]

    with open(args.log) as f:
        log = json.load(f)

    finish_entry = next((e for e in log["log"] if e["tool"] == "finish_goal"), None)
    output_fields = finish_entry["input"].get("outputs", {}) if finish_entry else {}

    capability = build_capability(
        log=log,
        name=args.name,
        description=args.description,
        target_url=args.target_url,
        app_name=args.app_name,
        params=params,
        output_fields=output_fields,
        allowlist=allowlist,
        source_log_name=Path(args.log).name,
        risky_steps=set(args.risky_step) if args.risky_step else None,
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{args.name}_v{capability.version}.json"
    out_path.write_text(capability.model_dump_json(indent=2))

    print(f"Capability written to: {out_path}")
    print(f"Steps: {len(capability.steps)}")
    print(f"Inputs: {list(capability.inputs.keys())}")
    print(f"Outputs: {list(capability.outputs.keys())}")


if __name__ == "__main__":
    main()
