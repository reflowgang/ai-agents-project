"""Block 3. The few-shot variant, and the honest comparison.

    python 02_few_shot.py --replay
    python 02_few_shot.py

Same schema, same scorer, same ten documents. One variable changes, which is
the presence of examples. That is the whole experimental design, and it is
the part most student comparisons get wrong.

Two ways to break it, both easy and both fatal:

  * editing the schema or the scorer between the two runs, which makes the
    two numbers incomparable
  * taking examples from DOCS, which turns the second measurement into a
    memory test. Examples come from EXAMPLE_POOL, which is held out for
    exactly this reason.

Two TODO markers.
"""

from __future__ import annotations

import argparse
import json

from documents import DOCS, EXAMPLE_POOL, GOLD
from extractor import SYSTEM_ZERO_SHOT, get_client, run_variant
from scoring import compare

from project.trace import write_json


# --------------------------------------------------------------------------
# TODO 5. Choose your examples and build the block.
# --------------------------------------------------------------------------

FEW_SHOT_EXAMPLES = [
    (EXAMPLE_POOL[0][0], EXAMPLE_POOL[0][1],
     "I can still get in through the main door, so it is not blocking me."),
    (EXAMPLE_POOL[1][0], EXAMPLE_POOL[1][1],
     "Intervention immediate necessaire."),
    (EXAMPLE_POOL[2][0], EXAMPLE_POOL[2][1],
     "Ersatz waere bis zum 20/09/2026 gut."),
    (EXAMPLE_POOL[3][0], EXAMPLE_POOL[3][1],
     "For information only:"),
]


def few_shot_block(n: int = 4) -> str:
    lines = ["\nHere are examples of correctly extracted records:\n"]
    for doc, gold, quote in FEW_SHOT_EXAMPLES[:n]:
        answer = {
            "category": gold.category,
            "urgency": gold.urgency,
            "due_date": gold.due_date,
            "quote": quote,
        }
        lines.append(f"Message ({doc.lang}): {doc.text}")
        lines.append(f"Answer: {json.dumps(answer)}\n")
    return "\n".join(lines)


SYSTEM_FEW_SHOT = SYSTEM_ZERO_SHOT + "\n"   # + few_shot_block(), once written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true")
    args = ap.parse_args()

    client = get_client(args.replay)

    zero_board, _, zero_metas = run_variant(client, SYSTEM_ZERO_SHOT,
                                            "zero-shot", DOCS, GOLD)
    few_board, _, few_metas = run_variant(
        client, SYSTEM_ZERO_SHOT + "\n" + few_shot_block(), "few-shot",
        DOCS, GOLD)

    print(compare(zero_board, few_board, "zero-shot", "few-shot"))

    print("\nzero-shot category failures:")
    for doc_id, f, note in zero_board.failures:
        if f == "category":
            print(f"  {doc_id}  {note}")
    print("\nfew-shot category failures:")
    for doc_id, f, note in few_board.failures:
        if f == "category":
            print(f"  {doc_id}  {note}")

    zero_tok = sum(m["prompt_tokens"] for m in zero_metas)
    few_tok = sum(m["prompt_tokens"] for m in few_metas)
    per_call = (few_tok - zero_tok) / len(DOCS)
    print(f"\nexample block costs {per_call:.0f} input tokens per call, "
          f"{per_call * 1000:.0f} per thousand calls")

    write_json("artifacts/week02_comparison.json", {
        "zero_shot": {"hits": zero_board.hits, "invalid": zero_board.invalid},
        "few_shot": {"hits": few_board.hits, "invalid": few_board.invalid},
        "extra_input_tokens_per_call": round(per_call, 1),
    })

    # TODO 6. Read the comparison table and answer four questions in
    # DECISIONS.md. The table is the deliverable, not the two runs.
    #
    #   a. Which fields moved, and which did not move at all? Do not report
    #      one overall number. The interesting content is that they do not
    #      move together.
    #
    #   b. Did any field get WORSE? If one did, say which and diagnose it.
    #      This is the most valuable observation available today and it is
    #      the one students skip past looking for a win.
    #
    #   c. Did an error disappear, or did it change shape? A wrong label that
    #      became a different wrong label is not a fix. Look at the failure
    #      lines, not just the counts.
    #
    #   d. Would you ship the few-shot variant? Give the evidence, the token
    #      cost per thousand calls, and one sentence saying what would change
    #      your mind. "It scored higher" is not sufficient on ten records,
    #      and saying so is worth more marks than claiming a win.

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
