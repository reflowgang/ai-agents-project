"""Stage: the human approval gate. Week 8.

The one irreversible step in this application is submitting a case, and it
reaches the gate through one function:

    request_approval(payload: dict, unattended: bool) -> dict

`payload` is what the pipeline sends: `action`, `case_id`, `arguments` (the
exact arguments, never a summary a model wrote), `evidence`, `degraded` and
`missing`. The return value has `verdict` ("approve", "edit", "reject" or
"timeout"), `by`, and for an edit the edited `arguments`.

What it does, and the decisions behind it (DECISIONS.md, week 8):

  * unattended (batch mode, week 10): nobody is there, so it returns the
    timeout verdict at once. Timeout means NOTHING is submitted.
  * attended, at a terminal: the graph runs to its `interrupt`, the
    checkpoint is written to disk, a person is asked on the terminal, and
    the answer resumes the graph. No answer within the timeout (default 120
    seconds, `APP_GATE_TIMEOUT` overrides it) is the same timeout verdict.
  * attended but no terminal to ask (stdin is not a tty, for example when
    piped or run from a script): treated as unanswered, for the same reason.

Week 8's rule holds: above the interrupt, read only. The node below writes
nothing at all. The submission itself happens in `pipeline.py`, after the
verdict, so a resume cannot submit twice.
"""

from __future__ import annotations

import getpass
import json
import os
import select
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from project.trace import repo_root

DEFAULT_TIMEOUT_S = 120.0


class GateState(TypedDict):
    payload: dict
    decision: dict


def _submit_node(state: GateState) -> dict:
    # Read only above this line: this node runs again from its first line
    # when the graph is resumed.
    decision = interrupt(state["payload"])
    return {"decision": decision}


def _build(saver):
    graph = StateGraph(GateState)
    graph.add_node("submit", _submit_node)
    graph.add_edge(START, "submit")
    graph.add_edge("submit", END)
    return graph.compile(checkpointer=saver)


# --------------------------------------------------------------------------
# The decision record: one line per request that reached the gate
# --------------------------------------------------------------------------

def _record(thread: str, payload: dict, decision: dict) -> None:
    """Appended AFTER the decision, never above the interrupt."""
    verdict = decision.get("verdict")
    submitted = verdict in ("approve", "edit")
    final = (decision.get("arguments") or payload.get("arguments")
             if submitted else None)
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "thread": thread, "case_id": payload.get("case_id"),
           "action": payload.get("action"), "verdict": verdict,
           "by": decision.get("by"), "submitted": submitted,
           "original_arguments": payload.get("arguments"),
           "final_arguments": final,
           "degraded": payload.get("degraded"),
           "missing": payload.get("missing")}
    path = repo_root() / "artifacts" / "app_approvals.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


# --------------------------------------------------------------------------
# Asking a person
# --------------------------------------------------------------------------

def _interactive() -> bool:
    return sys.stdin.isatty()


def _stream():
    return sys.stdin


def _timeout_s() -> float:
    try:
        return float(os.environ.get("APP_GATE_TIMEOUT", DEFAULT_TIMEOUT_S))
    except ValueError:
        return DEFAULT_TIMEOUT_S


def _unanswered(by: str) -> dict:
    return {"verdict": "timeout", "by": by, "arguments": {}}


def _ask_human(payload: dict, timeout: float, stream) -> dict:
    """Show the exact arguments and the evidence, then wait for an answer."""
    deadline = time.monotonic() + timeout
    who = getpass.getuser()
    arguments = payload.get("arguments") or {}

    print("\n  APPROVAL NEEDED")
    print(f"    action:    {payload.get('action')}")
    print(f"    case:      {payload.get('case_id')}")
    for key, value in arguments.items():
        print(f"    {key}: {value}")
    for item in payload.get("evidence") or []:
        print(f"    evidence:  {item}")
    if payload.get("degraded"):
        print(f"    DEGRADED, missing: {payload.get('missing')}")

    def ask(prompt: str) -> str | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        print(prompt, end="", flush=True)
        ready, _, _ = select.select([stream], [], [], remaining)
        if not ready:
            return None
        line = stream.readline()
        return None if line == "" else line.strip()

    answer = ask(f"\n  approve / edit / reject  ({timeout:.0f} s)? ")
    if answer is None:
        print("\n  no answer in time: nothing will be submitted")
        return _unanswered("no answer")
    answer = answer.lower()

    if answer in ("approve", "a"):
        return {"verdict": "approve", "by": who, "arguments": {}}
    if answer in ("edit", "e"):
        field = ask("  field to change: ")
        if field is None:
            print("\n  no answer in time: nothing will be submitted")
            return _unanswered("no answer")
        if field not in arguments:
            print(f"  there is no field {field!r}: treated as reject")
            return {"verdict": "reject", "by": who, "arguments": {},
                    "note": f"edit named an unknown field {field!r}"}
        value = ask(f"  new value for {field}: ")
        if value is None:
            print("\n  no answer in time: nothing will be submitted")
            return _unanswered("no answer")
        old = arguments[field]
        try:
            if isinstance(old, bool):
                new = value.lower() in ("true", "yes", "1")
            elif isinstance(old, int):
                new = int(value)
            elif isinstance(old, float):
                new = float(value)
            else:
                new = value
        except ValueError:
            print("  that value does not fit the field: treated as reject")
            return {"verdict": "reject", "by": who, "arguments": {},
                    "note": f"bad value for {field}"}
        return {"verdict": "edit", "by": who,
                "arguments": {**arguments, field: new}}
    if answer in ("reject", "r"):
        return {"verdict": "reject", "by": who, "arguments": {}}
    # Anything else is not an approval.
    print(f"  {answer!r} is not approve, edit or reject: treated as reject")
    return {"verdict": "reject", "by": who, "arguments": {},
            "note": f"unrecognised answer {answer!r}"}


# --------------------------------------------------------------------------
# The stage
# --------------------------------------------------------------------------

def request_approval(payload: dict, unattended: bool = False) -> dict:
    if unattended or not _interactive():
        decision = _unanswered("unattended" if unattended else "no terminal")
        _record("-", payload, decision)
        return decision

    thread = f"{payload.get('case_id', 'case')}-{uuid.uuid4().hex[:8]}"
    cfg = {"configurable": {"thread_id": thread}}
    db = repo_root() / "artifacts" / "app_gate.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)

    with SqliteSaver.from_conn_string(str(db)) as saver:
        graph = _build(saver)
        out = graph.invoke({"payload": payload, "decision": {}}, cfg)
        if "__interrupt__" not in out:
            # A bug, not a verdict: crash instead of guessing.
            raise RuntimeError("the approval gate did not pause")
        answer = _ask_human(payload, _timeout_s(), _stream())
        out = graph.invoke(Command(resume=answer), cfg)

    decision = dict(out["decision"])
    decision["thread"] = thread
    _record(thread, payload, decision)
    return decision
