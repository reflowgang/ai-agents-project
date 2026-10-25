"""Week 6, entries 3 and 4: what k costs, and memory off against memory on.

    python labs/week06_memory_mcp_a2a/starter/04_memory_ab.py
    python labs/week06_memory_mcp_a2a/starter/04_memory_ab.py --runs 1
    python labs/week06_memory_mcp_a2a/starter/04_memory_ab.py --only cost

Two experiments, both on a TEMPORARY store, never on artifacts/memory_store.json.

  cost   (entry 3)  Part A is measured on the model: how many prompt tokens do
                    0, 1, 3 and 10 recalled facts add. Part B is SYNTHETIC: a
                    store of 400 facts that I generated, with the one fact we
                    want hidden among look-alikes, to see whether it survives
                    top-k. The 400 facts are mine, not real data.
  ab     (entry 4)  The same task with memory off and memory on. Counted: model
                    requests ("turns"), tokens, and a crude success check.

What is deliberately NOT tested here: the memory tools run IN-PROCESS, not over
MCP. This isolates the effect of memory from the effect of the network
boundary, which block 2 measures separately.

Memory API: revision-independent. Nothing in this file depends on the MCP
protocol revision (2026-07-28 applies to mcp_server.py, not here).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from memory import MemoryStore

from project.models import LARGE, pydantic_ai_model
from project.trace import TraceRecorder, local_conditions, write_json

MODEL = pydantic_ai_model(LARGE)
SUBJECT = "A. Muller"
MAX_REQUESTS = 10          


# --------------------------------------------------------------------------
# Experiment 1: what k costs
# --------------------------------------------------------------------------

TOPICS = ["waste collection", "parking permits", "school registration",
          "the sports hall rules", "bulky waste pickup", "a library card"]
TARGET = "prefers letters in French"
QUERY = "preferred language letters"


def synthetic_store(path: Path, n_total: int, target: str = "last",
                    seed: int = 7) -> MemoryStore:
    """n_total facts about ONE subject: one target and n_total - 1 look-alikes.

    Several look-alikes share words with the query ("letters", "language"),
    and one template shares BOTH, because a real store has many facts about
    the same kind of thing. `target` says whether the fact we want was
    written last (newest, which helps it on a tie) or first (oldest, which
    does not). Both are shown, because the first alone would flatter the
    search.
    """
    rng = random.Random(seed)
    store = MemoryStore(path=path)
    templates = [
        lambda: f"received letters about {rng.choice(TOPICS)}",
        lambda: f"asked for information in simple language about "
                f"{rng.choice(TOPICS)}",
        lambda: f"wants letters in simple language about "
                f"{rng.choice(TOPICS)}",
        lambda: f"booked the {rng.choice(['main', 'annex'])} hall for "
                f"{rng.choice([1, 2, 3])}h at {rng.randint(8, 22)}:00",
        lambda: f"asked about the opening hours of the "
                f"{rng.choice(['main', 'annex'])} hall",
    ]
    if n_total >= 1 and target == "first":
        store.write(SUBJECT, "preference", TARGET)
    for _ in range(max(0, n_total - 1)):
        store.write(SUBJECT, "note", rng.choice(templates)())
    if n_total >= 1 and target == "last":
        store.write(SUBJECT, "preference", TARGET)
    return store


def rank_of_target(store: MemoryStore) -> tuple[int | None, float]:
    """Rank of the target in the full ranking for QUERY, and search time."""
    t0 = time.perf_counter()
    everything = store.search(SUBJECT, QUERY, k=len(store.facts) + 1)
    ms = (time.perf_counter() - t0) * 1000
    for i, text in enumerate(everything, start=1):
        if text.startswith(TARGET):
            return i, ms
    return None, ms


async def run_cost() -> dict:
    out: dict = {"tokens": [], "synthetic": []}
    tmp = Path(tempfile.mkdtemp(prefix="week06_cost_"))

    store = synthetic_store(tmp / "a.json", 40)
    facts = store.search(SUBJECT, "letters language hall", k=10)
    agent = Agent(MODEL)
    base = "Reply with the single word OK."
    print("\ncost, part A (measured on the model)")
    print(f"  {'k':>3}{'prompt tokens':>16}{'added vs k=0':>16}"
          f"{'per fact':>11}")
    zero = None
    for k in (0, 1, 3, 10):
        prompt = base if k == 0 else (
            "Known facts about the resident:\n- "
            + "\n- ".join(facts[:k]) + "\n\n" + base)
        r = await agent.run(prompt)
        tokens = r.usage.input_tokens or 0
        zero = tokens if k == 0 else zero
        added = tokens - zero
        per = f"{added / k:.1f}" if k else ""
        print(f"  {k:>3}{tokens:>16}{added:>16}{per:>11}")
        out["tokens"].append({"k": k, "prompt_tokens": tokens,
                              "added": added})

    print("\ncost, part B (SYNTHETIC store, generated by this script)")
    print(f"  query: {QUERY!r}; the fact we want: {TARGET!r}")
    print(f"  {'facts':>6}{'target written':>16}{'rank':>6}{'in top 3':>10}"
          f"{'search ms':>11}")
    for n in (10, 50, 400):
        for where in ("last", "first"):
            s = synthetic_store(tmp / f"b{n}{where}.json", n, target=where)
            rank, ms = rank_of_target(s)
            row = {"facts": n, "target_written": where, "target_rank": rank,
                   "in_top_3": rank is not None and rank <= 3,
                   "search_ms": round(ms, 2)}
            out["synthetic"].append(row)
            print(f"  {n:>6}{where:>16}{str(rank):>6}"
                  f"{str(row['in_top_3']):>10}{row['search_ms']:>11.2f}")
    return out


# --------------------------------------------------------------------------
# Experiment 2: memory off against memory on
# --------------------------------------------------------------------------

TASK = ("Write a one-sentence confirmation for the booking of A. Muller: "
        "main hall, Saturday at 20:00, 2 hours. Write it in the language this "
        "resident prefers, and mention the club discount if they are entitled "
        "to one (club members get 15% off).")

BASE_PROMPT = (
    "You write short booking confirmations for the Remerbaach sports halls. "
    "If you need to know something about the resident, use ask_resident to "
    "ask them. Reply with the final confirmation text only.")
MEMORY_PROMPT = (
    BASE_PROMPT + " Before you ask the resident anything, call recall for "
    "this resident: it returns what is already known about them.")

FRENCH_MARKERS = ("réservation", "réservé", "votre", "salle", "samedi",
                  "remise", "heures", "réduction", "rabais", "vous ")


@dataclass
class Ctx:
    store: MemoryStore | None
    asked: list[str] = field(default_factory=list)
    recalled: int = 0
    queries: list[str] = field(default_factory=list)   
    empty_recalls: int = 0


def scripted_resident(question: str) -> str:
    """The resident's answers, fixed in advance so that runs are comparable."""
    q = question.lower()
    if any(w in q for w in ("language", "french", "prefer", "speak")):
        return "French, please."
    if any(w in q for w in ("club", "member", "discount", "entitled")):
        return "Yes, I belong to a registered sports club."
    return "I don't know."


agent_off = Agent(MODEL, deps_type=Ctx, system_prompt=BASE_PROMPT)
agent_on = Agent(MODEL, deps_type=Ctx, system_prompt=MEMORY_PROMPT)


@agent_off.tool
@agent_on.tool
def ask_resident(ctx: RunContext[Ctx], question: str) -> str:
    """Ask the resident one question and get their answer. Costs a turn."""
    ctx.deps.asked.append(question)
    return scripted_resident(question)


@agent_on.tool
def recall(ctx: RunContext[Ctx], subject: str, query: str) -> list[str]:
    """Look up what is already remembered about ONE resident.

    An empty list means nothing relevant is remembered, not that the
    resident has no history.
    """
    ctx.deps.recalled += 1
    ctx.deps.queries.append(f"{subject} | {query}")
    found = ctx.deps.store.search(subject, query, k=3)
    if not found:
        ctx.deps.empty_recalls += 1
    return found


def judge(reply: str) -> dict:
    """A crude check, and it is only that: read the replies yourself.
    Language: at least two French markers. Discount: the text mentions 15.
    A reply that is a tool call printed as text never counts."""
    low = reply.lower()
    as_tool_call = low.lstrip().startswith('{"name"')
    french = (not as_tool_call
              and sum(m in low for m in FRENCH_MARKERS) >= 2)
    discount = (not as_tool_call) and "15" in low
    return {"french": french, "discount": discount,
            "tool_call_as_text": as_tool_call,
            "success": french and discount}


def fresh_memory() -> MemoryStore:
    store = MemoryStore(path=Path(tempfile.mkdtemp(prefix="week06_ab_"))
                        / "store.json")
    store.write(SUBJECT, "preference", "prefers letters in French")
    store.write(SUBJECT, "status", "member of a registered sports club")
    return store


async def run_condition(name: str, index: int, tag: str) -> dict:
    agent = agent_on if name == "memory on" else agent_off
    ctx = Ctx(store=fresh_memory() if name == "memory on" else None)
    t0 = time.perf_counter()
    reply, tokens, turns, raised = "", 0, 0, None
    try:
        r = await agent.run(TASK, deps=ctx,
                            usage_limits=UsageLimits(
                                request_limit=MAX_REQUESTS))
        reply = str(r.output)
        tokens = r.usage.total_tokens or 0
        turns = r.usage.requests or 0
    except Exception as exc:                                  # noqa: BLE001
        raised = type(exc).__name__
        print(f"      {name} run {index} raised {raised}")
    row = {"condition": name, "run": index, "turns": turns, "tokens": tokens,
           "asked": len(ctx.asked), "recalled": ctx.recalled,
           "empty_recalls": ctx.empty_recalls, "queries": ctx.queries,
           "reply": reply[:200], "raised": raised,
           "seconds": round(time.perf_counter() - t0, 1), **judge(reply)}
    record(name, index, row, tag)
    return row


def record(name: str, index: int, row: dict, tag: str) -> None:
    suffix = f"-{tag}" if tag else ""
    rec = TraceRecorder(
        week=6,
        case_id=f"W6-memory-{name.replace(' ', '-')}-run{index}{suffix}",
        conditions=local_conditions("qwen2.5:7b", temperature=0.0,
                                    memory=name, max_requests=MAX_REQUESTS),
        user_input=TASK)
    with rec.step("model", "qwen2.5:7b") as span:
        span.detail(turns=row["turns"], asked=row["asked"],
                    recalled=row["recalled"])
    rec.finish(output=row["reply"][:300],
               outcome="ok" if row["success"] else "error",
               **{k: str(v)[:200] for k, v in row.items()
                  if k not in ("reply",)})


async def run_ab(runs: int, conditions: list[str], tag: str) -> list[dict]:
    rows = []
    for i in range(1, runs + 1):
        for name in conditions:
            print(f"  run {i}, {name} ...", flush=True)
            row = await run_condition(name, i, tag)
            rows.append(row)
            print(f"    -> success={row['success']} french={row['french']} "
                  f"discount={row['discount']} turns={row['turns']} "
                  f"tokens={row['tokens']} asked={row['asked']} "
                  f"recalled={row['recalled']} "
                  f"(empty {row['empty_recalls']}) "
                  f"seconds={row['seconds']}", flush=True)
            for q in row["queries"]:
                print(f"        recall query: {q}", flush=True)
    return rows


def summarise(rows: list[dict]) -> None:
    print(f"\n{'':<12}{'success':>9}{'turns':>8}{'tokens':>9}{'asked':>7}"
          f"{'recalled':>10}{'seconds':>9}")
    print("-" * 64)
    for name in ("memory off", "memory on"):
        g = [r for r in rows if r["condition"] == name]
        if not g:
            continue
        n = len(g)
        print(f"{name:<12}{sum(r['success'] for r in g):>5}/{n:<3}"
              f"{sum(r['turns'] for r in g) / n:>8.1f}"
              f"{sum(r['tokens'] for r in g) // n:>9}"
              f"{sum(r['asked'] for r in g) / n:>7.1f}"
              f"{sum(r['recalled'] for r in g) / n:>10.1f}"
              f"{sum(r['seconds'] for r in g) / n:>9.1f}")
    print("\n(turns, tokens, asked, recalled, seconds are means per run)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", choices=["cost", "ab", "both"], default="both")
    ap.add_argument("--conditions", default="memory off,memory on",
                    help='for example "memory on" to re-run only that side')
    ap.add_argument("--tag", default="",
                    help="v2 and so on: separates traces and the json file "
                         "so an earlier run is never overwritten")
    args = ap.parse_args()

    result: dict = {"model": "qwen2.5:7b", "runs_per_condition": args.runs,
                    "tag": args.tag}
    out_path = ("artifacts/week06_memory_ab"
                + (f"_{args.tag}" if args.tag else "") + ".json")
    conditions = [c.strip() for c in args.conditions.split(",")]

    async def go():
        if args.only in ("cost", "both"):
            result["cost"] = await run_cost()
            write_json(out_path, result)
        if args.only in ("ab", "both"):
            print("\nmemory off against memory on")
            result["rows"] = await run_ab(args.runs, conditions, args.tag)
            write_json(out_path, result)
            summarise(result["rows"])

    asyncio.run(go())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())