"""
The discovery loop: observe -> decide -> act, repeated until the goal is
reached, a hard stop condition is hit, or the agent gives up.

Implemented as a two-node LangGraph graph (reason, act) with a conditional
edge back to reason - the graph structure mirrors the loop directly, which
is the whole reason to reach for a graph here rather than a bare while
loop: the state machine IS the design.
"""

from __future__ import annotations

import time
from typing import TypedDict, Any

from langgraph.graph import StateGraph, END

from src.agent.browser_state import get_page_state, PageElement
from src.agent.tools import execute_action
from src.safety.guardrails import is_url_allowed, redact_if_sensitive


class DiscoveryState(TypedDict):
    goal: str
    step: int
    max_steps: int
    status: str  # "running" | "success" | "failed"
    result: dict | None
    log: list[dict]
    last_elements: list[PageElement]
    # scratch fields passed between the reason and act nodes within one tick -
    # must be declared here or LangGraph drops them when merging node output
    # back into state, since it only preserves schema-declared keys.
    _pending_decision: Any
    _page_state: Any


class DiscoveryAgent:
    def __init__(self, page, decider, max_steps: int = 20, step_timeout_s: float = 30.0, allowlist: list[str] | None = None):
        self.page = page
        self.decider = decider
        self.max_steps = max_steps
        self.step_timeout_s = step_timeout_s
        self.allowlist = allowlist or []
        self.graph = self._build_graph()

    def _build_graph(self):
        g = StateGraph(DiscoveryState)
        g.add_node("reason", self._reason_node)
        g.add_node("act", self._act_node)
        g.set_entry_point("reason")
        g.add_edge("reason", "act")
        g.add_conditional_edges(
            "act",
            lambda s: "reason" if s["status"] == "running" else END,
        )
        return g.compile()

    def _history_summary(self, log: list[dict]) -> str:
        lines = []
        for entry in log[-6:]:  # last few steps is enough context; keeps the prompt small
            lines.append(f"  step {entry['step']}: {entry['tool']} -> {entry['result_detail']}")
        return "\n".join(lines)

    def _reason_node(self, state: DiscoveryState) -> DiscoveryState:
        page_state = get_page_state(self.page)
        state["last_elements"] = page_state.elements

        decision = self.decider.decide(
            goal=state["goal"],
            state=page_state,
            history_summary=self._history_summary(state["log"]),
        )
        state["_pending_decision"] = decision  # type: ignore[typeddict-item]
        state["_page_state"] = page_state  # type: ignore[typeddict-item]
        return state

    def _act_node(self, state: DiscoveryState) -> DiscoveryState:
        decision = state["_pending_decision"]  # type: ignore[typeddict-item]
        page_state = state["_page_state"]  # type: ignore[typeddict-item]
        step_no = state["step"]

        target_element = None
        idx = decision.tool_input.get("element_index") if isinstance(decision.tool_input, dict) else None
        if idx is not None:
            for el in state["last_elements"]:
                if el.index == idx:
                    target_element = {"role": el.role, "name": el.name, "xpath": el.xpath, "input_type": el.input_type}
                    break

        if decision.tool_name == "finish_goal":
            state["status"] = "success"
            state["result"] = decision.tool_input
            self._log(state, step_no, decision, {"ok": True, "detail": "goal reported complete"}, page_state, target_element)
            return state

        if decision.tool_name == "give_up":
            state["status"] = "failed"
            state["result"] = {"reason": decision.tool_input.get("reason", "unspecified")}
            self._log(state, step_no, decision, {"ok": False, "detail": "agent gave up"}, page_state, target_element)
            return state

        if decision.tool_name == "navigate":
            target_url = decision.tool_input.get("url", "")
            if not is_url_allowed(target_url, self.allowlist):
                state["status"] = "failed"
                state["result"] = {"reason": f"blocked by allowlist policy: {target_url} is not permitted"}
                self._log(
                    state, step_no, decision,
                    {"ok": False, "detail": f"BLOCKED: navigate target {target_url} not in allowlist {self.allowlist}"},
                    page_state, target_element,
                )
                return state

        result = execute_action(self.page, decision.tool_name, decision.tool_input, state["last_elements"])
        self._log(state, step_no, decision, result, page_state, target_element)

        # Defense in depth: even actions that don't explicitly navigate
        # (a click on a link, an unexpected redirect) can move the page
        # outside the allowlist. Check where we actually ended up.
        if result["ok"] and not is_url_allowed(self.page.url, self.allowlist):
            state["status"] = "failed"
            state["result"] = {"reason": f"blocked by allowlist policy: ended up at {self.page.url}, which is not permitted"}
            return state

        state["step"] += 1
        if state["step"] >= state["max_steps"]:
            state["status"] = "failed"
            state["result"] = {"reason": "max_steps exceeded"}
        else:
            state["status"] = "running"
        return state

    def _log(self, state, step_no, decision, result, page_state, target_element=None):
        logged_input = dict(decision.tool_input) if isinstance(decision.tool_input, dict) else decision.tool_input
        if isinstance(logged_input, dict) and "text" in logged_input and target_element:
            logged_input["text"] = redact_if_sensitive(logged_input["text"], target_element.get("input_type"))

        state["log"].append(
            {
                "step": step_no,
                "timestamp": time.time(),
                "url_before": page_state.url,
                "tool": decision.tool_name,
                "input": logged_input,
                "target_element": target_element,
                "reasoning": decision.raw_text,
                "result_ok": result["ok"],
                "result_detail": result["detail"],
            }
        )

    def run(self, goal: str) -> DiscoveryState:
        initial: DiscoveryState = {
            "goal": goal,
            "step": 0,
            "max_steps": self.max_steps,
            "status": "running",
            "result": None,
            "log": [],
            "last_elements": [],
            "_pending_decision": None,
            "_page_state": None,
        }
        final_state = self.graph.invoke(initial, config={"recursion_limit": self.max_steps * 2 + 5})
        return final_state
