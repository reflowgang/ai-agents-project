"""The loop, the caps, and the tool executor. TODO 1 to 5.

Twenty lines of control flow and four decisions. Week 5 replaces this file
with about four lines of Pydantic AI, and the point of today is to know
exactly what those four lines are hiding.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from handbook import INJECTION_MARKER
from tools import SCHEMAS as ANTHROPIC_SCHEMAS
from tools import compute, search_services

from project.models import LARGE
from project.trace import TraceRecorder, local_conditions

# --------------------------------------------------------------------------
# TODO 1. The schemas, in the shape this endpoint speaks.
# --------------------------------------------------------------------------
#
# The descriptions in tools.py are already good and they are the actual
# lesson: each one says what the tool returns, when NOT to use it, and what
# an empty result means. A tool description is an interface contract whose
# audience is a model, so it is prompt engineering rather than documentation.
#
# What changes here is only the envelope. The local endpoint speaks the
# OpenAI wire format, which nests the schema under "function" and calls the
# parameter block "parameters". Other providers nest it differently and call
# it "input_schema". Nothing about your tool changes, which is the point:
# the envelope is plumbing and the description is the design.

def to_openai_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema["description"],
            "parameters": schema["input_schema"],
        },
    }


SCHEMAS = [to_openai_schema(s) for s in ANTHROPIC_SCHEMAS]
DISPATCH: dict[str, Callable[..., Any]] = {
    "search_services": search_services,
    "compute": compute,
}

# The list of concrete triggers on the search_services line is not
# decoration, and removing it costs four tasks. Without it the model
# decides from its own prior whether a commune handbook would plausibly
# contain the answer, and it is wrong about that surprisingly often, in
# the direction of refusing to look. Measured: 7/10 with the list, 3/10
# without it, same model and same everything else.
SYSTEM = """\
You answer questions for the help desk of Remerbaach, a Luxembourg commune, \
using the tools provided.

search_services  searches the commune handbook. Use it for any fee, opening \
time, form number, phone number, address, deadline, or procedure.
compute          evaluates one arithmetic expression.

How to work:
  1. Decide whether the question needs a fact from the handbook. If it does, \
you MUST call search_services before answering. You do not know what this \
handbook contains and you cannot tell from the question alone.
  2. You may only say the handbook does not cover something AFTER a search \
has come back without it. Saying it without searching is always wrong.
  3. If the answer needs arithmetic, call compute. Do not calculate in your \
head, even when it looks easy.
  4. If the question needs no handbook fact and no arithmetic, answer \
directly and call nothing at all.

Rules for the answer:
  Never state a fee, opening time, form number, or deadline that did not \
appear in a search result. Quote figures exactly as the handbook gives them.
  Answer in the language of the question, in under eighty words.
"""


# --------------------------------------------------------------------------
# The record of one run
# --------------------------------------------------------------------------

@dataclass
class Run:
    task_id: str
    answer: str = ""
    steps: int = 0
    tool_calls: list[str] = field(default_factory=list)
    seconds: float = 0.0
    tokens: int = 0
    cap_fired: str | None = None
    saw_injection: bool = False       # did the hostile text reach the model
    tool_errors: int = 0

    @property
    def stopped_cleanly(self) -> bool:
        return self.cap_fired is None


# --------------------------------------------------------------------------
# TODO 5. Running the tools.
# --------------------------------------------------------------------------

MAX_RESULT_CHARS = 2000


def run_tool_call(name: str, raw_arguments: str) -> tuple[Any, bool]:
    fn = DISPATCH.get(name)
    if fn is None:
        return (f"error: unknown tool {name!r}. "
                f"Available tools: {', '.join(DISPATCH)}."), True

    try:
        args = json.loads(raw_arguments) if raw_arguments else {}
    except json.JSONDecodeError:
        return "error: the arguments were not valid JSON. Send a JSON object.", True
    if not isinstance(args, dict):
        return "error: the arguments must be a JSON object.", True

    try:
        result = fn(**args)
    except TypeError:
        return f"error: wrong arguments for {name}. Check the tool schema.", True
    except ValueError as exc:
        return f"error: {exc}", True
    except Exception as exc:
        return f"error: {name} failed ({type(exc).__name__}).", True

    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + " ...[truncated]"
    return text, False


# --------------------------------------------------------------------------
# TODO 2, 3, 4. The loop and its caps.
# --------------------------------------------------------------------------

def run_task(client, task, model: str = LARGE.name,
             max_steps: int = 6, stall_limit: int = 2,
             system: str = SYSTEM, week: int = 4,
             use_tools: bool = True) -> Run:
    """The whole agent: a while loop with three exits (TODO 2, 3, 4)."""
    run = Run(task_id=task.id)
    rec = TraceRecorder(
        week=week, case_id=task.id,
        conditions=local_conditions(model, temperature=0.0,
                                    system="react", language=task.lang),
        user_input=task.question)

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": task.question}]
    seen: set[str] = set()    
    stalls = 0
    t0 = time.perf_counter()

    for _ in range(max_steps):                      
        run.steps += 1
        with rec.step("model", f"{model}:step{run.steps}") as step:
            kwargs = {"tools": SCHEMAS} if use_tools else {}
            reply = client.chat.completions.create(
                model=model, messages=messages,
                temperature=0.0, max_tokens=400, **kwargs)
            step.tokens(reply.usage.prompt_tokens,
                        reply.usage.completion_tokens)
        run.tokens += reply.usage.prompt_tokens + reply.usage.completion_tokens

        msg = reply.choices[0].message
        calls = getattr(msg, "tool_calls", None) or []

        if not calls:                             
            run.answer = msg.content or ""
            break

        messages.append({
            "role": "assistant", "content": msg.content or "",
            "tool_calls": [{"id": c.id, "type": "function",
                            "function": {"name": c.function.name,
                                         "arguments": c.function.arguments}}
                           for c in calls]})

        signals: set[str] = set()
        for c in calls:
            name, raw = c.function.name, c.function.arguments
            with rec.step("tool", name) as tstep:
                result, errored = run_tool_call(name, raw)
                tstep.detail(errored=errored)
            run.tool_calls.append(name)
            if errored:
                run.tool_errors += 1
            if INJECTION_MARKER in str(result):
                run.saw_injection = True
            if name == "search_services":
                signals |= _doc_ids(result)
            elif not errored:
                signals.add(f"{name}:{raw}")       
            messages.append({"role": "tool", "tool_call_id": c.id,
                             "content": str(result)})

        if signals - seen:
            stalls = 0
            seen |= signals
        else:
            stalls += 1
            if stalls >= stall_limit:
                run.cap_fired = "no_progress"
                run.answer = _partial(run, "I stopped because my searches "
                                           "stopped finding anything new")
                break
    else:
        run.cap_fired = "step_cap"
        run.answer = _partial(run, f"I used all {max_steps} steps")

    run.seconds = time.perf_counter() - t0
    rec.finish(output=run.answer, outcome="ok", cap_fired=run.cap_fired,
               steps=run.steps, tool_calls=run.tool_calls)
    return run


def _partial(run: Run, reason: str) -> str:
    """What a capped run returns. Never an empty string, never a crash.

    A cap that returns nothing is indistinguishable from a system that had
    nothing to say, and week 8 spends a session on why that distinction
    matters. Say what happened and what was known so far.
    """
    return (f"I could not complete this. {reason}, after "
            f"{len(run.tool_calls)} tool call(s). Please telephone the help "
            f"desk on 4796-2222.")


def _doc_ids(tool_result: Any) -> set[str]:
    try:
        parsed = json.loads(tool_result) if isinstance(tool_result, str) \
            else tool_result
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(parsed, list):
        return set()
    return {h.get("doc_id") for h in parsed
            if isinstance(h, dict) and h.get("doc_id")}
