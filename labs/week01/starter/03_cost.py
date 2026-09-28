"""Block 4. What one call costs, in the three currencies that matter here.

    python 03_cost.py

You are running locally, so nothing you do today costs money. That is
convenient and it is also a distortion, because in any job you take,
somebody watches the bill. So this block measures the two costs that are
real on your machine, and estimates the one that is not.

  seconds   measured, and it is dominated by how much the model writes
  memory    measured, and it decides which model you can run at all
  euros     estimated from a dated price list, and labeled as an estimate

Two TODO markers.
"""

from __future__ import annotations

import subprocess
import time

from openai import OpenAI

from project.models import BASE_URL, API_KEY, LARGE, SMALL
from project.prices import PRICE_DATE, estimate, local_cost_note
from project.trace import write_json

SHORT = "What is the capital of Luxembourg? Answer in one word."
LONG = ("A resident asks whether they need a parking vignette if they park "
        "in a visitor bay. Explain what information you would need before "
        "answering, and why.")


def timed(client, prompt: str, model: str, max_tokens: int = 200):
    t0 = time.perf_counter()
    reply = client.chat.completions.create(
        model=model, temperature=0.0, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}])
    return reply, time.perf_counter() - t0


def main() -> int:
    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    rows = []

    # Cost one, seconds. Two prompts, same model, same settings, and the
    # only thing that changes is how much the model has to write.
    for label, prompt in (("short", SHORT), ("long", LONG)):
        reply, secs = timed(client, prompt, SMALL.name)
        rows.append({
            "case": label, "model": SMALL.name, "seconds": round(secs, 3),
            "prompt_tokens": reply.usage.prompt_tokens,
            "completion_tokens": reply.usage.completion_tokens,
        })
        print(f"{label:<6} {secs:>6.2f}s  "
              f"in {reply.usage.prompt_tokens:>4} "
              f"out {reply.usage.completion_tokens:>4}")

    ratio = rows[1]["seconds"] / max(rows[0]["seconds"], 1e-9)
    print(f"\nThe long answer took {ratio:.0f} times as long as the short "
          f"one.\nCompare that with the ratio of their output tokens.\n")

    subprocess.run(["ollama", "stop", SMALL.name])
    reply_cold, cold_secs = timed(client, SHORT, SMALL.name)
    reply_warm, warm_secs = timed(client, SHORT, SMALL.name)
    print(f"\ncold start: {cold_secs:.2f}s")
    print(f"warm call:  {warm_secs:.2f}s")
    rows.append({
        "case": "cold_start", "model": SMALL.name, "seconds": round(cold_secs, 3),
        "prompt_tokens": reply_cold.usage.prompt_tokens,
        "completion_tokens": reply_cold.usage.completion_tokens,
    })
    rows.append({
        "case": "warm", "model": SMALL.name, "seconds": round(warm_secs, 3),
        "prompt_tokens": reply_warm.usage.prompt_tokens,
        "completion_tokens": reply_warm.usage.completion_tokens,
    })

    long_row = rows[1]  
    cases_per_night = 200
    nights = 14 * 7  

    print(f"\nEstimated cost of a {cases_per_night}-case golden set, "
          f"run nightly for {nights} nights ({PRICE_DATE} price list):\n")

    for tier in ("small", "large"):
        est = estimate(long_row["prompt_tokens"],
                       long_row["completion_tokens"], tier=tier)
        run_cost = est.total * cases_per_night
        semester_cost = run_cost * nights
        print(f"  {tier:<6} tier: {run_cost:.4f} EUR per night, "
              f"~{semester_cost:.2f} EUR for the semester. "
              f"Estimate, not a measurement.")

    write_json("artifacts/week01_cost.json",
               {"rows": rows, "price_list_date": PRICE_DATE})
    print(local_cost_note())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
