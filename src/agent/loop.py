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
    def __init__(self, page, decider, max_steps: int = 20, step_timeout_s: float = 30.0):
        self.page = page
        self.decider = decider
        self.max_steps = max_steps
        self.step_timeout_s = step_timeout_s
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

        if decision.tool_name == "finish_goal":
            state["status"] = "success"
            state["result"] = decision.tool_input
            self._log(state, step_no, decision, {"ok": True, "detail": "goal reported complete"}, page_state)
            return state

        if decision.tool_name == "give_up":
            state["status"] = "failed"
            state["result"] = {"reason": decision.tool_input.get("reason", "unspecified")}
            self._log(state, step_no, decision, {"ok": False, "detail": "agent gave up"}, page_state)
            return state

        result = execute_action(self.page, decision.tool_name, decision.tool_input, state["last_elements"])
        self._log(state, step_no, decision, result, page_state)

        state["step"] += 1
        if state["step"] >= state["max_steps"]:
            state["status"] = "failed"
            state["result"] = {"reason": "max_steps exceeded"}
        else:
            state["status"] = "running"
        return state

    def _log(self, state, step_no, decision, result, page_state):
        state["log"].append(
            {
                "step": step_no,
                "timestamp": time.time(),
                "url_before": page_state.url,
                "tool": decision.tool_name,
                "input": decision.tool_input,
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
