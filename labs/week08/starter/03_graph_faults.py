"""Checkpoint 1, on the real graph nodes. No gate, no model, about a second.

01_survive.py only calls the two tools through the policy. It never runs
`gather`, `price` or `draft`, so it cannot tell you whether the text the user
reads names what is missing. This does:

    python 03_graph_faults.py                          # every fault
    python 03_graph_faults.py --topic "library card"   # a topic nobody covers

For each fault it runs gather -> price -> draft directly and prints what the
policy did (tries, outcome) and the draft the user would read.
"""

from __future__ import annotations

import argparse
import time

from faults import FAULTS, FaultPlan
from graph import draft, gather, initial, price

LISTS = ("missing", "events", "side_effects")


def merge(state: dict, update: dict) -> None:
    for key, val in update.items():
        state[key] = state[key] + val if key in LISTS else val


def run(kind: str, topic: str) -> dict:
    spec = f"search:{kind}@1.0,fees:{kind}@1.0" if kind != "none" else ""
    plan = FaultPlan.parse(spec, seed=42)
    state = initial("RB-2026-0042", topic)
    started = time.monotonic()
    for node in (lambda s: gather(s, plan), lambda s: price(s, plan), draft):
        merge(state, node(state))
    state["_seconds"] = time.monotonic() - started
    return state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="parking vignette")
    args = ap.parse_args()

    print(f"topic: {args.topic!r}\n")
    for kind in ["none", *FAULTS]:
        s = run(kind, args.topic)
        print(f"[{kind}]  degraded={s['degraded']}  "
              f"({s['_seconds']:.2f} s)")
        for e in s["events"]:
            print(f"    {e}")
        print(f"    DRAFT: {s['draft']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
