"""Homework: one run, one trace, with a retry, a fallback and the gate visible.

    pip install opentelemetry-api opentelemetry-sdk
    python 04_otel_trace.py

No model is called. The faults are the deterministic ones from faults.py:

    search:timeout@1.0   every search call times out -> 3 attempts, then the
                         fallback answers from the cache
    fees:flaky@1.0       the first fees call fails, the second succeeds
                         -> one retry, no fallback

Then the run reaches the gate, pauses, and is resumed. THE APPROVER IS
SIMULATED by this script (`simulated=True` on its span): it answers
"approve" itself so the run can be traced start to finish in one process.
The two-process resume is what 02_gate.py shows.

The trace is printed as a tree and saved to artifacts/week08_otel_trace.json.
"""

from __future__ import annotations

import json
import time

# The provider must exist before retry.py is imported, so that the tracer
# retry.py asks for belongs to it.
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter)

EXPORTER = InMemorySpanExporter()
PROVIDER = TracerProvider()
PROVIDER.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(PROVIDER)
TRACER = trace.get_tracer("week08.run")

import graph as G                                     # noqa: E402
from faults import FaultPlan                          # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver   # noqa: E402
from langgraph.errors import GraphInterrupt           # noqa: E402
from langgraph.types import Command                   # noqa: E402

from project.trace import repo_root, write_json       # noqa: E402

CASE = "RB-2026-0042"
SPEC = "search:timeout@1.0,fees:flaky@1.0"


def traced(name, fn):
    """A span around one graph node. A pause at the gate is not an error."""
    def inner(*args, **kwargs):
        with TRACER.start_as_current_span(
                f"node:{name}", record_exception=False,
                set_status_on_exception=False) as span:
            try:
                return fn(*args, **kwargs)
            except GraphInterrupt:
                span.set_attribute("paused_at_gate", True)
                raise
    return inner


for _name in ("gather", "price", "draft", "submit"):
    setattr(G, _name, traced(_name, getattr(G, _name)))


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _ms(span) -> float:
    return (span.end_time - span.start_time) / 1e6


def _label(span) -> str:
    a = dict(span.attributes or {})
    bits = []
    if span.name.startswith("step:"):
        bits.append(f"tries={a.get('tries')} outcome={a.get('outcome')}")
    if span.name == "attempt":
        bits.append(f"n={a.get('n')}")
        bits.append(f"ERROR {a['error.type']}: {a['error.message']}"
                    if "error.type" in a else "ok")
    if span.name == "fallback":
        bits.append(f"ERROR {a['error.type']}" if "error.type" in a
                    else "answered")
    if a.get("paused_at_gate"):
        bits.append("PAUSED at the gate")
    if span.name in ("gate.decision", "pipeline.resume"):
        bits += [f"{k}={v}" for k, v in a.items()]
    for ev in span.events:
        if ev.name == "backoff":
            bits.append(f"[backoff {ev.attributes['seconds']} s]")
    return "  ".join(bits)


def render(spans) -> None:
    kids: dict = {}
    for s in spans:
        parent = s.parent.span_id if s.parent else None
        kids.setdefault(parent, []).append(s)
    for group in kids.values():
        group.sort(key=lambda s: s.start_time)

    def walk(parent, depth):
        for s in kids.get(parent, []):
            print(f"{'  ' * depth}{s.name:<{26 - 2 * depth}}"
                  f"{_ms(s):8.1f} ms  {_label(s)}")
            walk(s.context.span_id, depth + 1)

    walk(None, 0)


def main() -> int:
    plan = FaultPlan.parse(SPEC, seed=42)
    db = repo_root() / "artifacts" / "week08_otel_runs.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.unlink(missing_ok=True)
    cfg = {"configurable": {"thread_id": CASE}}

    with TRACER.start_as_current_span(
            "case_run", attributes={"case_id": CASE, "faults": SPEC}):
        with SqliteSaver.from_conn_string(str(db)) as saver:
            graph = G.build(plan, saver)

            with TRACER.start_as_current_span("pipeline.start"):
                out = graph.invoke(G.initial(CASE, "parking vignette"), cfg)
            payload = out["__interrupt__"][0].value

            with TRACER.start_as_current_span(
                    "gate.decision",
                    attributes={"verdict": "approve", "by": "a.muller",
                                "simulated": True,
                                "degraded": payload["degraded"],
                                "missing": ", ".join(payload["missing"])}):
                answer = {"verdict": "approve", "by": "a.muller"}

            with TRACER.start_as_current_span(
                    "pipeline.resume",
                    attributes={"verdict": answer["verdict"]}):
                final = graph.invoke(Command(resume=answer), cfg)

    spans = EXPORTER.get_finished_spans()
    print(f"faults: {SPEC}   (seed 42, approver simulated)\n")
    render(spans)

    steps = [s for s in spans if s.name.startswith("step:")]
    attempts = [s for s in spans if s.name == "attempt"]
    checks = {
        "retry visible": any(
            (s.attributes or {}).get("tries", 0) > 1 for s in steps)
        and any("error.type" in (a.attributes or {}) for a in attempts),
        "fallback visible": any(s.name == "fallback" for s in spans),
        "gate visible": any(s.name == "gate.decision" for s in spans)
        and any((s.attributes or {}).get("paused_at_gate") for s in spans),
    }
    print()
    for name, ok in checks.items():
        print(f"  {name}: {'yes' if ok else 'NO'}")
    print(f"  submitted: {final.get('submitted')}   "
          f"degraded: {final.get('degraded')}   "
          f"missing: {sorted(set(final.get('missing', [])))}")

    write_json("artifacts/week08_otel_trace.json", [
        {"name": s.name, "span_id": f"{s.context.span_id:016x}",
         "parent": f"{s.parent.span_id:016x}" if s.parent else None,
         "ms": round(_ms(s), 2), "attributes": dict(s.attributes or {}),
         "events": [{"name": e.name, "attributes": dict(e.attributes or {})}
                    for e in s.events]} for s in spans])
    print("\n  full trace saved to artifacts/week08_otel_trace.json")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
