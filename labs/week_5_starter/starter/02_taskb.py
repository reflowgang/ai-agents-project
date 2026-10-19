from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time

from pydantic_ai import Agent, NativeOutput, RunContext

from agents import (MODEL, Fix, Verdict, list_files, planner, read_file,
                    replace_line, run_the_tests, validate_plan)
from repo import Work, apply_fix, diff_summary, run_tests

from project.trace import TraceRecorder, local_conditions, write_json

MAX_ROUNDS = 20
SOURCES = ("discounts.py", "pricing.py", "report.py")
RATE_NAMES = {"RESIDENT_RATE": 0.10, "OFF_PEAK_RATE": 0.20,
              "CLUB_RATE": 0.15, "MAX_TOTAL_DISCOUNT": 0.30}

TASK_B = (
    "Refactor, do not change behaviour. The four discount rates are written "
    "out by hand in several places: the numbers 0.10, 0.20, 0.15 and 0.30 "
    "inside the functions of discounts.py, and the texts '10%', '20%', "
    "'15%' and '30%' in pricing.py and report.py. The constants "
    "RESIDENT_RATE, OFF_PEAK_RATE, CLUB_RATE and MAX_TOTAL_DISCOUNT already "
    "exist at the top of discounts.py. Make them the only place where a rate "
    "is written, and use them everywhere else; build each text from the "
    "constant (for example f\"{RESIDENT_RATE:.0%}\" gives 10%). Every "
    "output must stay exactly the same. Do not change any test file.")


NUMBER = re.compile(r"(?<![\w.])0?\.(?:10?|20?|15|30?)(?!\w)")
PERCENT = re.compile(r"(?<!\d)(?:10|20|15|30)\s?%")
DEFINITION = re.compile(
    r"^(RESIDENT_RATE|OFF_PEAK_RATE|CLUB_RATE|MAX_TOTAL_DISCOUNT)"
    r"\s*=\s*0\.\d+\s*$")

GRID = '''
import json
from booking import Booking
from pricing import total_eur, discount_summary
from report import confirmation_line
out = []
for hall in ("main", "annex"):
    for hours in (0.5, 1, 2, 5):
        for start in range(24):
            for res in (False, True):
                for club in (False, True):
                    b = Booking(hours=hours, hall=hall, start_hour=start,
                                resident=res, club=club)
                    out.append([total_eur(b), discount_summary(b),
                                confirmation_line(b)])
print(json.dumps(out))
'''


def find_literals(work: Work) -> list[str]:
    """Every line that still writes a rate by hand, outside the one block of
    definitions. A heuristic: it will miss a rate written as 10 / 100."""
    hits = []
    for name in SOURCES:
        for no, line in enumerate(work.read(name).splitlines(), 1):
            if line.strip().startswith("#") or DEFINITION.match(line):
                continue
            if NUMBER.search(line) or PERCENT.search(line):
                hits.append(f"{name}:{no}: {line.strip()}")
    return hits


def constants_ok(work: Work) -> bool:
    d = work.read("discounts.py")
    for name, value in RATE_NAMES.items():
        defs = re.findall(rf"^{name}\s*=\s*(0\.\d+)\s*$", d, re.M)
        if len(defs) != 1 or abs(float(defs[0]) - value) > 1e-9:
            return False
        if d.count(name) < 2:
            return False
        if name not in work.read("pricing.py"):
            return False
        if name not in work.read("report.py"):
            return False
    return True


def behaviour(work: Work):
    try:
        p = subprocess.run([sys.executable, "-c", GRID], cwd=work.root,
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "timeout"
    if p.returncode != 0:
        return "error: " + p.stderr.strip()[-200:]
    return json.loads(p.stdout)


def check(work: Work, reference) -> dict:
    t = run_tests(work)
    left = find_literals(work)
    c_ok = constants_ok(work)
    same = behaviour(work) == reference
    return {"tests_green": t.passed, "tests_ran": t.ran,
            "literals_left": len(left), "literal_lines": left,
            "constants_ok": c_ok, "behaviour_same": same,
            "done": bool(t.passed and not left and c_ok and same)}


def start_repo() -> Work:
    """A scratch copy with the 20:00 bug already fixed: 16 of 16 green."""
    work = Work.fresh()
    res = apply_fix(work, "discounts.py",
                    "    return start_hour > OFF_PEAK_FROM_HOUR",
                    "    return start_hour >= OFF_PEAK_FROM_HOUR")
    if res != "applied" or not run_tests(work).passed:
        raise RuntimeError(f"could not prepare the start repository: {res}")
    return work


# --------------------------------------------------------------------------
# The agents. Same model, same output mode as week 5, Task B prompts.
# --------------------------------------------------------------------------

fixer_b = Agent(MODEL, output_type=NativeOutput(Fix), retries=3,
                system_prompt=(
    "You refactor a Python repository one line at a time, without changing "
    "what it outputs.\n"
    "You are given the goal, a plan, the three source files as they are now, "
    "and the lines that still write a discount rate by hand.\n"
    "Propose exactly ONE edit: old_line copied character for character from "
    "one of the files, and new_line. new_line may hold several lines "
    "separated by newlines, which is how you add an import.\n"
    "Replace a hand-written rate by the constant that holds it: "
    "RESIDENT_RATE is 10%, OFF_PEAK_RATE is 20%, CLUB_RATE is 15%, "
    "MAX_TOTAL_DISCOUNT is 30%. Never change a value, a threshold or a start "
    "hour, never edit a test, and add an import before a file uses a "
    "constant it does not import yet."))

reviewer_b = Agent(MODEL, output_type=NativeOutput(Verdict), retries=3,
                   system_prompt=(
    "You review ONE proposed edit in a refactoring that must not change what "
    "the program outputs. You see the edit and the lines that still write a "
    "discount rate by hand, nothing else.\n"
    "The constants are RESIDENT_RATE (10%), OFF_PEAK_RATE (20%), CLUB_RATE "
    "(15%) and MAX_TOTAL_DISCOUNT (30%).\n"
    "Reject the edit if it touches a test file, changes any value, threshold "
    "or start hour, uses a constant that does not match the rate it replaces, "
    "changes anything unrelated to the listed rates, or writes a new rate by "
    "hand.\n"
    "Approve it only if it replaces one hand-written rate by the matching "
    "constant, or adds the import such a replacement needs, and nothing else. "
    "Give a one-sentence reason."))

baseline_b = Agent(MODEL, system_prompt=(
    "You refactor a small Python repository using the tools provided.\n"
    "Goal: every discount rate is written once, as a constant at the top of "
    "discounts.py, and used everywhere else; outputs must not change.\n"
    "find_hardcoded_rates lists the rates still written by hand, read_file "
    "shows a file, replace_line changes one exact line (new_line may hold "
    "several lines separated by newlines, which is how you add an import), "
    "run_the_tests confirms nothing broke.\n"
    "Never edit anything under tests/. Stop when find_hardcoded_rates "
    "reports none and the tests pass."))

baseline_b.tool(list_files)
baseline_b.tool(read_file)
baseline_b.tool(replace_line)
baseline_b.tool(run_the_tests)


@baseline_b.tool
def find_hardcoded_rates(ctx: RunContext[Work]) -> str:
    """List the lines that still write a discount rate by hand."""
    return "\n".join(find_literals(ctx.deps)) or "none"


# --------------------------------------------------------------------------
# B and C: rounds of one structured edit each
# --------------------------------------------------------------------------

def round_prompt(work: Work, plan_text: str, report: str,
                 feedback: str) -> str:
    files = "\n\n".join(f"File {n}:\n{work.read(n)}" for n in SOURCES)
    return (f"{TASK_B}\n\nPlan:\n{plan_text}\n\n{files}\n\n"
            f"Rates still written by hand:\n{report}\n\n{feedback}"
            f"Propose the next single edit.")


def rate_report(work: Work) -> str:
    left = find_literals(work)
    if left:
        return "\n".join(left)
    return ("none written by hand. Each constant must still be defined once "
            "in discounts.py and used in discounts.py, pricing.py and "
            "report.py.")


async def run_rounds(work: Work, plan_text: str, propose_fix,
                     review_fix) -> dict:
    stats = {"rounds": 0, "applied": 0, "refused": 0, "rejected": 0,
             "errors": 0, "tokens": 0, "edits": []}
    feedback = ""
    for n in range(1, MAX_ROUNDS + 1):
        if not find_literals(work) and constants_ok(work):
            break
        stats["rounds"] = n
        report = rate_report(work)
        try:
            fix, tok = await propose_fix(
                round_prompt(work, plan_text, report, feedback))
            stats["tokens"] += tok
            verdict = None
            if review_fix is not None:
                diff = diff_summary(work, fix.path, fix.old_line,
                                    fix.new_line)
                verdict, tok = await review_fix(diff, report)
                stats["tokens"] += tok
        except Exception as exc:                             
            stats["errors"] += 1
            feedback = ""
            print(f"      round {n}: raised {type(exc).__name__}")
            if stats["errors"] >= 3:
                break
            continue
        label = f"{fix.path}: {fix.old_line.strip()[:48]}"
        if verdict is not None and verdict.decision == "reject":
            stats["rejected"] += 1
            feedback = (f"Your last proposal was rejected by a reviewer: "
                        f"{verdict.reason}\n\n")
            stats["edits"].append(f"rejected {label}")
            print(f"      round {n}: rejected  {label}")
            continue
        res = apply_fix(work, fix.path, fix.old_line, fix.new_line)
        if res == "applied":
            stats["applied"] += 1
            feedback = ""
            stats["edits"].append(f"applied {label}")
        else:
            stats["refused"] += 1
            feedback = f"Your last proposal was refused: {res}\n\n"
            stats["edits"].append(f"refused {label}")
        print(f"      round {n}: {res[:8]:<8} {label}")
    return stats


async def make_plan(task: str):
    r = await planner.run(task)
    tokens = r.usage.total_tokens or 0
    ok, problems = validate_plan(r.output)
    if not ok:
        print(f"      plan failed validation, repairing once: {problems}")
        r = await planner.run(
            f"{task}\n\nYour previous plan had these problems: "
            f"{'; '.join(problems)}. Make a corrected plan.")
        tokens += r.usage.total_tokens or 0
        ok, problems = validate_plan(r.output)
    return r.output, ok, problems, tokens


def record(arch: str, run_index: int, outcome: str, **notes) -> None:
    rec = TraceRecorder(
        week=5, case_id=f"TaskB-{arch}-run{run_index}",
        conditions=local_conditions("qwen2.5:7b", temperature=0.0,
                                    architecture=arch),
        user_input=TASK_B)
    with rec.step("check", "run_tests") as span:
        span.detail(**{k: str(v)[:200] for k, v in notes.items()})
    rec.finish(output=str(notes.get("edits", ""))[:300], outcome=outcome,
               **{k: str(v)[:300] for k, v in notes.items()})


def finish_row(arch: str, index: int, work: Work, reference, t0: float,
               **extra) -> dict:
    res = check(work, reference)
    row = {"architecture": arch, "run": index, **res, **extra,
           "seconds": round(time.perf_counter() - t0, 1)}
    record(arch.replace(" ", "-"), index, "ok" if res["done"] else "error",
           **row)
    work.cleanup()
    return row


async def run_baseline(index: int, reference) -> dict:
    work = start_repo()
    t0 = time.perf_counter()
    calls = tokens = 0
    raised = None
    try:
        r = await baseline_b.run(TASK_B, deps=work)
        calls = sum(1 for m in r.all_messages() for p in m.parts
                    if getattr(p, "tool_name", None))
        tokens = r.usage.total_tokens or 0
    except Exception as exc:                                  
        raised = type(exc).__name__
        print(f"      baseline run {index} raised {raised}")
    return finish_row("A baseline", index, work, reference, t0,
                      tool_calls=calls, tokens=tokens, raised=raised)


async def run_planned(index: int, with_reviewer: bool, reference) -> dict:
    arch = "C planner+reviewer" if with_reviewer else "B planner"
    work = start_repo()
    t0 = time.perf_counter()
    tokens, plan_ok, plan_problems = 0, None, []
    plan_text = "(no plan was produced)"
    try:
        plan, plan_ok, plan_problems, tokens = await make_plan(TASK_B)
        plan_text = f"Goal: {plan.goal}\n" + "\n".join(
            f"{i}. {s}" for i, s in enumerate(plan.steps, 1))
    except Exception as exc:                                  
        print(f"      {arch} run {index}: planner raised "
              f"{type(exc).__name__}")

    async def propose_fix(prompt):
        r = await fixer_b.run(prompt)
        return r.output, r.usage.total_tokens or 0

    async def review_fix(diff, report):
        r = await reviewer_b.run(
            f"Proposed change:\n{diff}\n\n"
            f"Rates still written by hand:\n{report}")
        return r.output, r.usage.total_tokens or 0

    stats = await run_rounds(work, plan_text, propose_fix,
                             review_fix if with_reviewer else None)
    stats["tokens"] += tokens
    return finish_row(arch, index, work, reference, t0,
                      plan_ok=plan_ok, plan_problems=plan_problems, **stats)


IMPORT_ALL = ("CLUB_RATE, MAX_TOTAL_DISCOUNT, OFF_PEAK_RATE, "
              "RESIDENT_RATE")
CORRECT = [
    ("discounts.py", "    return 0.10 if booking.resident else 0.0",
     "    return RESIDENT_RATE if booking.resident else 0.0"),
    ("discounts.py", "    return 0.20 if is_off_peak(booking.start_hour) else 0.0",
     "    return OFF_PEAK_RATE if is_off_peak(booking.start_hour) else 0.0"),
    ("discounts.py", "    return 0.15 if booking.club else 0.0",
     "    return CLUB_RATE if booking.club else 0.0"),
    ("discounts.py", "    return min(total, 0.30)",
     "    return min(total, MAX_TOTAL_DISCOUNT)"),
    ("pricing.py", "from discounts import total_discount",
     f"from discounts import {IMPORT_ALL}, total_discount"),
    ("pricing.py", '        parts.append("resident 10%")',
     '        parts.append(f"resident {RESIDENT_RATE:.0%}")'),
    ("pricing.py", '        parts.append("off peak 20%")',
     '        parts.append(f"off peak {OFF_PEAK_RATE:.0%}")'),
    ("pricing.py", '        parts.append("club 15%")',
     '        parts.append(f"club {CLUB_RATE:.0%}")'),
    ("pricing.py", '    return ", ".join(parts) + " (capped at 30%)"',
     '    return ", ".join(parts) + f" (capped at {MAX_TOTAL_DISCOUNT:.0%})"'),
    ("report.py", "from pricing import base_eur, total_eur",
     "from pricing import base_eur, total_eur\n"
     f"from discounts import {IMPORT_ALL}"),
    ("report.py", '    "resident": "10%",',
     '    "resident": f"{RESIDENT_RATE:.0%}",'),
    ("report.py", '    "off_peak": "20%",',
     '    "off_peak": f"{OFF_PEAK_RATE:.0%}",'),
    ("report.py", '    "club": "15%",', '    "club": f"{CLUB_RATE:.0%}",'),
    ("report.py", '    "cap": "30%",', '    "cap": f"{MAX_TOTAL_DISCOUNT:.0%}",'),
]


WRONG_LABEL = [
    (p, o, n.replace('f"resident {RESIDENT_RATE:.0%}"',
                     'f"resident {CLUB_RATE:.0%}"'))
    for p, o, n in CORRECT]


def selftest(reference) -> int:
    cases = [
        ("untouched start", [], dict(done=False, tests_green=True,
                                     literals_left=12, constants_ok=False,
                                     behaviour_same=True)),
        ("correct refactoring", CORRECT, dict(done=True, tests_green=True,
                                              literals_left=0,
                                              constants_ok=True,
                                              behaviour_same=True)),
        ("wrong constant in an untested function", WRONG_LABEL,
         dict(done=False, tests_green=True, literals_left=0,
              constants_ok=True, behaviour_same=False)),
    ]
    bad = 0
    for name, edits, expected in cases:
        work = start_repo()
        for e in edits:
            res = apply_fix(work, *e)
            if res != "applied":
                print(f"  edit refused in selftest: {e[0]}: {e[1]} -> {res}")
                bad += 1
        got = check(work, reference)
        work.cleanup()
        ok = all(got[k] == v for k, v in expected.items())
        bad += 0 if ok else 1
        print(f"[{'ok' if ok else 'MISMATCH':>8}] {name}")
        print("           " + ", ".join(f"{k}={got[k]}" for k in expected))
        if not ok:
            print("           expected " + ", ".join(
                f"{k}={v}" for k, v in expected.items()))
    return 1 if bad else 0


async def main_async(runs: int, only: set[str], reference) -> list[dict]:
    rows = []
    for i in range(1, runs + 1):
        jobs = []
        if "A" in only:
            jobs.append(("A", lambda i=i: run_baseline(i, reference)))
        if "B" in only:
            jobs.append(("B", lambda i=i: run_planned(i, False, reference)))
        if "C" in only:
            jobs.append(("C", lambda i=i: run_planned(i, True, reference)))
        for tag, job in jobs:
            print(f"  run {i}, {tag} ...", flush=True)
            row = await job()
            rows.append(row)
            print(f"    -> done={row['done']} tests={row['tests_green']} "
                  f"literals_left={row['literals_left']} "
                  f"behaviour_same={row['behaviour_same']} "
                  f"tokens={row['tokens']} seconds={row['seconds']}",
                  flush=True)
            write_json("artifacts/week05_taskb.json", {"rows": rows})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", default="A,B,C")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    start = start_repo()
    reference = behaviour(start)
    start.cleanup()
    print(f"reference behaviour recorded: {len(reference)} bookings")

    if args.selftest:
        return selftest(reference)

    only = {s.strip().upper() for s in args.only.split(",")}
    rows = asyncio.run(main_async(args.runs, only, reference))

    by_arch: dict[str, list[dict]] = {}
    for row in rows:
        by_arch.setdefault(row["architecture"], []).append(row)
    print(f"\n{'architecture':<22}{'done':>7}{'tests':>7}{'left':>7}"
          f"{'same':>7}{'tokens':>10}{'seconds':>9}")
    print("-" * 69)
    for arch, g in by_arch.items():
        n = len(g)
        print(f"{arch:<22}{sum(r['done'] for r in g):>4}/{n:<2}"
              f"{sum(r['tests_green'] for r in g):>4}/{n:<2}"
              f"{sum(r['literals_left'] for r in g) / n:>7.1f}"
              f"{sum(r['behaviour_same'] for r in g):>4}/{n:<2}"
              f"{sum(r['tokens'] for r in g) // n:>10}"
              f"{sum(r['seconds'] for r in g) / n:>9.1f}")
    print("\n(left = mean hand-written rates still in the code, start = 12)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
