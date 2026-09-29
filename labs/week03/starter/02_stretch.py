"""Block 4, completed. TODO 8. One variant per group.

    python 02_stretch.py --variant model --replay
    python 02_stretch.py --variant voting --replay

The written answers are at the bottom.
"""

from __future__ import annotations

import argparse
import collections
import time

from queries import QUERIES
from router import apply_policy, classify
from scoring import report, score_routes

from project.models import LARGE, SMALL
from project.trace import write_json

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "compare_mod", Path(__file__).with_name("01_compare.py"))
_cmp = importlib.util.module_from_spec(_spec)
sys.modules["compare_mod"] = _cmp
_spec.loader.exec_module(_cmp)


# --------------------------------------------------------------------------
# TODO 8. One variant. Your instructor assigns you one.
# --------------------------------------------------------------------------

def variant_model(client) -> None:
    """Variant A. Same prompt, same queries, same policy. Only the model.

    Run the classifier over the twenty four queries on SMALL and on LARGE,
    score both, and report per route as counts.

    Report four things per model, not one:
      * route accuracy, and route accuracy excluding the ambiguous four
      * how often the evidence span came back verbatim
      * the minimum and maximum confidence, and how many distinct values
      * the resident memory, which is in project/models.py

    Predict which model wins before you run it, and write the prediction
    down. Then read the confidence range carefully. One of the two models
    tells you something about your threshold from TODO 3a that you cannot
    unsee.
    """
    raise NotImplementedError("TODO 8: variant A, model routing")


def variant_voting(client, k: int = 3) -> None:
    routed_all = []
    disagreements = []
    for q in QUERIES:
        decisions = []
        for _ in range(k):
            decision, meta = classify(client, q.text, temperature=0.7)
            if decision is not None:
                decisions.append(decision)

        if not decisions:
            routed = apply_policy(None, q.text)
        else:
            votes = [d.route for d in decisions]
            majority_route, _ = collections.Counter(votes).most_common(1)[0]
            if len(set(votes)) > 1:
                disagreements.append((q.id, votes))
            rep = next(d for d in decisions if d.route == majority_route)
            routed = apply_policy(rep, q.text)

        routed_all.append(routed)

    s = score_routes(routed_all, QUERIES)
    print(report(s, f"voting (k={k})"))

    ambiguous_ids = {q.id for q in QUERIES if q.ambiguous}
    print(f"\ndisagreements: {len(disagreements)} of {len(QUERIES)} queries")
    for qid, votes in disagreements:
        flag = " (ambiguous)" if qid in ambiguous_ids else ""
        print(f"  {qid}{flag}: {votes}")

    write_json("artifacts/week03_voting.json", {
        "k": k,
        "hits": s.hits, "total": s.total,
        "disagreement_ids": [qid for qid, _ in disagreements],
        "ambiguous_ids": sorted(ambiguous_ids),
    })


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=("model", "voting"), required=True)
    ap.add_argument("--replay", action="store_true")
    args = ap.parse_args()

    client = _cmp.get_client(args.replay)
    if args.variant == "model":
        variant_model(client)
    else:
        variant_voting(client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
