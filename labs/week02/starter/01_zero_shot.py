"""Block 2. The zero-shot baseline, scored per field.

    python 01_zero_shot.py --replay     # the shipped recording, instant
    python 01_zero_shot.py              # your own model, about 45 seconds

Develop your scorer against `--replay`. The recording holds every model
answer for both variants, so your scorer runs in well under a second and you
can iterate on it properly instead of waiting forty-five seconds to find out
you compared the wrong field.

The recording contains real failures, because the model really does make
them. If your scorer reports forty out of forty, your scorer does nothing.

One TODO marker here. TODO 1 to 4 live in extractor.py and scoring.py, and
this file will not run until they are done.
"""

from __future__ import annotations

import argparse

from documents import DOCS, GOLD
from extractor import (PROMPT_VERSION, SYSTEM_ZERO_SHOT, get_client,
                       run_variant)

from project.trace import write_json
from dataclasses import asdict
from project.contracts import GoldCase, GoldSet


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true")
    args = ap.parse_args()

    client = get_client(args.replay)
    board, records, metas = run_variant(client, SYSTEM_ZERO_SHOT,
                                        "zero-shot", DOCS, GOLD)

    if board.failures:
        print("failures worth reading:")
        for doc_id, fieldname, note in board.failures[:10]:
            print(f"  {doc_id}  {fieldname:<9} {note}")

    write_json("artifacts/week02_zero_shot.json", {
        "variant": "zero-shot",
        "prompt_version": PROMPT_VERSION,
        "hits": board.hits, "total": board.total, "invalid": board.invalid,
    })

    behaviors = {
        "REQ-01": "extracts category access and urgency urgent, with no "
                  "due date because the message states none.",
        "REQ-02": "extracts category hardware and urgency standard, with "
                  "due date 2026-09-15 from the French DD/MM/YYYY date.",
        "REQ-03": "extracts category billing and urgency standard, with no "
                  "due date because the message only says it is not urgent.",
        "REQ-04": "extracts category facilities and urgency urgent, with "
                  "no due date because none is stated.",
        "REQ-05": "extracts category access and urgency standard, with no "
                  "due date because 'before the end of the month' is a "
                  "relative expression, not a date.",
        "REQ-06": "extracts category billing and urgency info, with no "
                  "due date, because the message is a notice requiring no "
                  "action.",
        "REQ-07": "extracts category facilities and urgency standard, "
                  "with due date 2026-10-01 from the stated meeting date.",
        "REQ-08": "extracts category hardware and urgency urgent, with no "
                  "due date because none is stated.",
        "REQ-09": "extracts category other and urgency info, with no due "
                  "date, because it is a suggestion requiring no action.",
        "REQ-10": "extracts category access and urgency standard, with no "
                  "due date because 'before the end of the month' is "
                  "relative, not a stated date.",
    }

    goldset = GoldSet(cases=[
        GoldCase(
            case_id=doc.id,
            week_added=2,
            question=doc.text,
            expected=asdict(GOLD[doc.id]),
            expected_behavior=behaviors[doc.id],
            slice_tags=[doc.lang],
        )
        for doc in DOCS
    ])
    write_json("artifacts/goldset.json", goldset.model_dump())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
