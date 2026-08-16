"""
Two implementations of "decide the next action":

- ClaudeDecider: the real thing - calls the Anthropic API with the current
  page state and asks for exactly one tool call.
- ScriptedDecider: a fixed, deterministic sequence of actions used only to
  test the harness (browser control, element scanning, logging, artifact
  export) without spending API calls or requiring a key. Selected via
  --dry-run on the CLI. The actual discovery run submitted as evidence
  MUST use ClaudeDecider - the brief is explicit that this cannot be
  faked or described.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from src.agent.browser_state import PageState
from src.agent.tools import TOOLS

SYSTEM_PROMPT = """You are a computer-use agent operating an internal member-servicing \
console for a bank. The console is a legacy, server-rendered application: there are no \
test IDs and no clean DOM, so you are shown each interactive element's accessible role \
and name rather than raw HTML.

You will be given a goal and the current page state after every action. Call exactly \
one tool per turn to make progress toward the goal. Use the element indices exactly as \
given - they are only valid for the current page state, not previous ones.

Call finish_goal only when the page clearly shows the completed/confirmation state, and \
quote the exact confirming text in checkpoint_evidence. Call give_up if you are stuck \
after several attempts at the same step, or if the page shows a permission-denied or \
similar condition that a human should decide on."""


@dataclass
class Decision:
    tool_name: str
    tool_input: dict
    raw_text: str | None  # any plain reasoning text alongside the tool call


class ClaudeDecider:
    def __init__(self, model: str = "claude-sonnet-4-5"):
        import anthropic  # imported lazily so dry-run mode doesn't require the package configured with a key

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it before running a real discovery run."
            )
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def decide(self, goal: str, state: PageState, history_summary: str) -> Decision:
        user_content = (
            f"Goal: {goal}\n\n"
            f"Progress so far:\n{history_summary or '(nothing yet)'}\n\n"
            f"Current page state:\n{state.to_prompt_text()}"
        )
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": user_content}],
        )
        tool_block = next(b for b in resp.content if b.type == "tool_use")
        text_block = next((b.text for b in resp.content if b.type == "text"), None)
        return Decision(tool_name=tool_block.name, tool_input=tool_block.input, raw_text=text_block)


class ScriptedDecider:
    """Deterministic action sequence for harness testing. See module docstring."""

    def __init__(self, script: list[dict]):
        self._script = script
        self._i = 0

    def decide(self, goal: str, state: PageState, history_summary: str) -> Decision:
        if self._i >= len(self._script):
            return Decision("give_up", {"reason": "scripted sequence exhausted"}, None)
        step = self._script[self._i]
        self._i += 1
        return Decision(step["tool"], step["input"], step.get("reasoning"))


def default_login_and_lookup_script(member_id: str, acct_type: str, deposit: str) -> list[dict]:
    """
    Scripted sequence matching the goal 'log in, look up a member, and open
    a sub-account' against target-app/app.py, purely for harness testing.
    Element indices below match the current app.py markup as of this
    commit; the real ClaudeDecider re-derives indices from live state
    each turn instead of hardcoding them.
    """
    return [
        {"tool": "navigate", "input": {"url": "http://localhost:5001/login", "reasoning": "start at login"}},
        {"tool": "type_text", "input": {"element_index": 0, "text": "agent", "reasoning": "fill username"}},
        {"tool": "type_text", "input": {"element_index": 1, "text": "x", "reasoning": "fill password"}},
        {"tool": "click", "input": {"element_index": 2, "reasoning": "submit login"}},
        {"tool": "type_text", "input": {"element_index": 0, "text": member_id, "reasoning": "enter member id"}},
        {"tool": "click", "input": {"element_index": 1, "reasoning": "submit search"}},
        {"tool": "click", "input": {"element_index": 0, "reasoning": "open new sub-account"}},
        {"tool": "select_option", "input": {"element_index": 0, "option_text": acct_type, "reasoning": "choose account type"}},
        {"tool": "type_text", "input": {"element_index": 1, "text": deposit, "reasoning": "enter initial deposit"}},
        {"tool": "click", "input": {"element_index": 2, "reasoning": "submit new sub-account"}},
        {
            "tool": "finish_goal",
            "input": {
                "outputs": {"acct_type": acct_type, "deposit": deposit},
                "checkpoint_evidence": "Sub-account opened successfully.",
            },
            "reasoning": "confirmation screen reached",
        },
    ]
