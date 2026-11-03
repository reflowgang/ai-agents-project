"""The pipeline as a graph, with one gated step. TODO 3 to 5.

Four nodes. Three of them execute automatically and one stops and asks a
human, and which one is a decision about consequence rather than about
confidence.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from faults import ContractDrift, FaultPlan
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from retry import EmptyResult, StepPolicy, call_with_policy

import tools


def append(a: list, b: list) -> list:
    return a + b


class CaseState(TypedDict):
    case_id: str
    topic: str
    passages: list[str]
    fee: dict
    draft: str
    submitted: bool
    decision: str
    degraded: bool
    missing: Annotated[list[str], append]
    events: Annotated[list[str], append]
    side_effects: Annotated[list[str], append]


SEARCH_POLICY = StepPolicy(attempts=3, base_delay=0.05, jitter=True,
                           max_elapsed=3.0, on_exhausted="degrade")
FEES_POLICY = StepPolicy(attempts=3, base_delay=0.05, jitter=True,
                         max_elapsed=3.0, on_exhausted="degrade")


def validate_fee(value) -> None:
    """TODO 4. Where contract drift is caught.

    Checks the SHAPE of what came back, on the success path: a dict, with
    `fee_eur` a non-negative number and `period` a non-empty string. A
    renamed field (`amount_eur`) or an HTML error page fails here, and
    ContractDrift is not retryable, so the ladder does not retry it.

    An empty dict is the one case with its own exception: the call worked
    and knows nothing about this topic. That is EmptyResult, not drift.
    """
    if not isinstance(value, dict):
        raise ContractDrift(f"fee is a {type(value).__name__}, expected a dict")
    if not value:
        raise EmptyResult("fee lookup returned nothing")
    missing = [k for k in ("fee_eur", "period") if k not in value]
    if missing:
        raise ContractDrift(
            f"fee lacks {missing}; keys received: {sorted(value)}")
    amount = value["fee_eur"]
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise ContractDrift(f"fee_eur is a {type(amount).__name__}")
    if amount < 0:
        raise ContractDrift(f"fee_eur is negative: {amount}")
    if not isinstance(value["period"], str) or not value["period"].strip():
        raise ContractDrift("period is not a non-empty string")


def validate_passages(value) -> None:
    """The same idea for search: a list of non-empty strings, and not empty."""
    if not isinstance(value, list):
        raise ContractDrift(
            f"search returned a {type(value).__name__}, expected a list")
    if not value:
        raise EmptyResult("search returned no passages")
    if not all(isinstance(p, str) and p.strip() for p in value):
        raise ContractDrift("search returned an item that is not text")


# --------------------------------------------------------------------------
# TODO 3. The nodes, each with its own policy.
# --------------------------------------------------------------------------

def gather(state: CaseState, plan: FaultPlan) -> dict:
    """TODO 3a. Search, with retries and a fallback.

    The empty-result decision (DECISIONS.md, entry 3): an empty list from the
    primary search is treated as "the primary does not cover this", which is
    NOT retried (same question, same nothing) but DOES go to the fallback,
    one cheap extra call. If the fallback is empty too, the step degrades and
    the draft says no handbook text was found. It never answers from nothing.
    """
    res = call_with_policy(
        tools.search, plan, state["topic"], policy=SEARCH_POLICY,
        fallback=tools.keyword_fallback, validate=validate_passages)
    event = f"gather:{res.outcome}:tries={res.tries}"
    if res.error:
        event += f":{res.error}"
    if res.ok:
        out = {"passages": res.value, "events": [event]}
        if res.used_fallback:
            out["degraded"] = True
            out["missing"] = ["live handbook search (cached text used)"]
        return out
    return {"passages": [], "degraded": True,
            "missing": ["handbook text"], "events": [event]}


def price(state: CaseState, plan: FaultPlan) -> dict:
    """TODO 3b. The fee lookup, passing `validate_fee` to the policy."""
    res = call_with_policy(
        tools.fees, plan, state["topic"], policy=FEES_POLICY,
        validate=validate_fee)
    event = f"price:{res.outcome}:tries={res.tries}"
    if res.error:
        event += f":{res.error}"
    if res.ok:
        return {"fee": res.value, "events": [event]}
    return {"fee": {}, "degraded": True, "missing": ["the fee"],
            "events": [event]}


def draft(state: CaseState) -> dict:
    """TODO 5. The goal predicate, and what a partial answer says.

    Goal: the answer carries handbook text AND a fee. If either is missing,
    or a worse source was used, the run is degraded and the TEXT says so.
    """
    passages = state.get("passages") or []
    fee = state.get("fee") or {}
    missing = sorted(set(state.get("missing", [])))
    complete = bool(passages) and bool(fee)
    degraded = state.get("degraded", False) or not complete

    lines = []
    if passages:
        lines.append(passages[0])
    if fee:
        lines.append(f"Fee: {fee['fee_eur']:.2f} EUR per {fee['period']}.")
    if not passages and not fee:
        lines.append(f"I could not retrieve anything about "
                     f"{state['topic']}.")
    if missing:
        lines.append("Incomplete answer. Missing: " + ", ".join(missing) +
                     ". Please check with the commune before relying on "
                     "this.")
    text = " ".join(lines)
    return {"draft": text, "degraded": degraded,
            "events": [f"draft:{'degraded' if degraded else 'complete'}"]}


def submit(state: CaseState) -> dict:
    """The gated step. Exactly one, chosen by consequence.

    Everything above the interrupt runs AGAIN on resume, because the node
    re-executes from its first line. Put nothing here that writes.

    And the part the state will not show you: the first execution never
    returns, so its return value is discarded while whatever it did to the
    outside world is not. A write above this line happens twice and appears
    once. `02_gate.py` demonstrates it.
    """
    decision = interrupt({
        "action": "submit_case",
        "case_id": state["case_id"],
        "arguments": {"topic": state["topic"], "fee": state.get("fee", {})},
        "evidence": state.get("passages", []),
        "degraded": state.get("degraded", False),
        "missing": sorted(set(state.get("missing", []))),
    })

    verdict = decision.get("verdict")
    if verdict == "approve":
        return {"submitted": True, "decision": "approve",
                "side_effects": [f"SUBMITTED {state['case_id']}"],
                "events": [f"gate:approve:{decision.get('by', '?')}"]}
    if verdict == "edit":
        edited = decision.get("arguments", {})
        return {"submitted": True, "decision": "edit",
                "fee": edited.get("fee", state.get("fee", {})),
                "side_effects": [f"SUBMITTED {state['case_id']} (edited)"],
                "events": [f"gate:edit:{decision.get('by', '?')}"]}
    return {"submitted": False, "decision": verdict or "reject",
            "events": [f"gate:{verdict or 'reject'}:"
                       f"{decision.get('by', '?')}"]}


def build(plan: FaultPlan, saver):
    graph = StateGraph(CaseState)
    graph.add_node("gather", lambda s: gather(s, plan))
    graph.add_node("price", lambda s: price(s, plan))
    graph.add_node("draft", draft)
    graph.add_node("submit", submit)
    graph.add_edge(START, "gather")
    graph.add_edge("gather", "price")
    graph.add_edge("price", "draft")
    graph.add_edge("draft", "submit")
    graph.add_edge("submit", END)
    return graph.compile(checkpointer=saver)


def initial(case_id: str, topic: str) -> CaseState:
    return {"case_id": case_id, "topic": topic, "passages": [], "fee": {},
            "draft": "", "submitted": False, "decision": "",
            "degraded": False, "missing": [], "events": [],
            "side_effects": []}