"""
The agent's action space, expressed as Anthropic tool definitions, plus the
executor that turns a chosen tool call into a real Playwright interaction.

Kept deliberately small and closed: the agent can only click, type, select,
navigate, wait, finish, or give up. It cannot run arbitrary scripts - this
is part of the safety story (see src/escalation and REPORT.md, section 6).
"""

from __future__ import annotations

from src.agent.browser_state import PageElement

TOOLS = [
    {
        "name": "click",
        "description": "Click an interactive element identified by its index from the current element list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "element_index": {"type": "integer", "description": "The [index] shown next to the element."},
                "reasoning": {"type": "string", "description": "One short sentence on why this click moves toward the goal."},
            },
            "required": ["element_index", "reasoning"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text into a textbox identified by its index. Clears any existing value first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "element_index": {"type": "integer"},
                "text": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["element_index", "text", "reasoning"],
        },
    },
    {
        "name": "select_option",
        "description": "Choose an option in a <select> combobox identified by its index, by visible option text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "element_index": {"type": "integer"},
                "option_text": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["element_index", "option_text", "reasoning"],
        },
    },
    {
        "name": "navigate",
        "description": "Go directly to a URL. Only use this for the initial entry point - prefer clicking links/buttons afterward.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["url", "reasoning"],
        },
    },
    {
        "name": "finish_goal",
        "description": "Call this once the goal has been fully accomplished and the confirmation/checkpoint state is visible on the page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "outputs": {
                    "type": "object",
                    "description": "Key-value data extracted from the final page relevant to the goal (e.g. subaccount_id, balance).",
                },
                "checkpoint_evidence": {"type": "string", "description": "The exact text on the page that proves the goal was reached."},
            },
            "required": ["outputs", "checkpoint_evidence"],
        },
    },
    {
        "name": "give_up",
        "description": "Call this only if the goal cannot be completed - e.g. a hard failure, or the agent is stuck and needs a human.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
            },
            "required": ["reason"],
        },
    },
]


def _find_element(elements: list[PageElement], index: int) -> PageElement:
    for el in elements:
        if el.index == index:
            return el
    raise ValueError(f"No element with index {index} in current page state")


def execute_action(page, tool_name: str, tool_input: dict, elements: list[PageElement]) -> dict:
    """
    Executes one action against the live page. Returns a result dict used
    for structured logging: {ok: bool, detail: str}.
    Raises only on genuinely unexpected conditions - business-outcome-style
    situations (e.g. a locator no longer present) are returned as ok=False
    with a detail message, not raised, so the loop can decide what to do.
    """
    try:
        if tool_name == "click":
            el = _find_element(elements, tool_input["element_index"])
            page.locator(f"xpath={el.xpath}").first.click(timeout=5000)
            return {"ok": True, "detail": f"clicked [{el.index}] {el.role} \"{el.name}\""}

        if tool_name == "type_text":
            el = _find_element(elements, tool_input["element_index"])
            loc = page.locator(f"xpath={el.xpath}").first
            loc.fill("", timeout=5000)
            loc.fill(tool_input["text"], timeout=5000)
            return {"ok": True, "detail": f"typed into [{el.index}] {el.role} \"{el.name}\""}

        if tool_name == "select_option":
            el = _find_element(elements, tool_input["element_index"])
            page.locator(f"xpath={el.xpath}").first.select_option(label=tool_input["option_text"], timeout=5000)
            return {"ok": True, "detail": f"selected \"{tool_input['option_text']}\" in [{el.index}]"}

        if tool_name == "navigate":
            page.goto(tool_input["url"], timeout=10000)
            return {"ok": True, "detail": f"navigated to {tool_input['url']}"}

        return {"ok": False, "detail": f"unknown tool {tool_name}"}

    except Exception as exc:  # noqa: BLE001 - deliberately broad; surfaced to the loop as a failed step
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
