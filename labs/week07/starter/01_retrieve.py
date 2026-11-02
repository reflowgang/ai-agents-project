"""Blocks 1 and 2. Index, retrieve, diagnose. TODO 4 and 5.

    python 01_retrieve.py --replay     # the recorded table, instant
    python 01_retrieve.py              # your own index, about 30 seconds

The written answers are at the bottom, and the second one is the reason this
session exists.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from chunking import load_chunks
from goldset import GOLD
from metrics import score
from retrieval import DenseIndex, keyword_search

from project.contracts import GoldCase, GoldSet
from project.fixtures import find_fixture, load_or_reference
from project.trace import write_json

LAB = "week07_rag_pipeline"
K = 5


def run_live(size: int = 600, overlap: int = 80) -> list[dict]:
    chunks = load_chunks(size=size, overlap=overlap)
    index = DenseIndex(chunks, name=f"s{size}")
    print(f"{len(chunks)} chunks, {index.dim} dimensions, "
          f"{index.index_seconds:.1f} seconds to build the index\n")

    rows = []
    for question in GOLD:
        dense = index.search(question.question, k=len(chunks))
        keyword = keyword_search(chunks, question.question, k=len(chunks))
        d = score(dense, question, k=K)
        kw = score(keyword, question, k=K)
        rows.append({
            "qid": question.qid, "tags": list(question.tags),
            "answerable": question.answerable,
            "dense_rank": d.rank, "dense_recall": d.recall_at_k,
            "keyword_rank": kw.rank, "keyword_recall": kw.recall_at_k,
            "top5": d.retrieved,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--size", type=int, default=600)
    ap.add_argument("--overlap", type=int, default=80)
    args = ap.parse_args()

    if args.replay:
        path = find_fixture("reference_runs.json", __file__, lab=LAB)
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        print(f"replay: {payload['meta']['chunks']} chunks, "
              f"{payload['meta']['embed_model']}, "
              f"recorded {payload['meta']['recorded_at']}\n")
        rows = payload["rows"]
    else:
        rows = run_live(args.size, args.overlap)

    print(f"{'qid':<5}{'dense rank':>12}{'kw rank':>9}"
          f"{'recall@' + str(K):>10}   tags")
    print("-" * 62)
    for row in rows:
        if not row["answerable"]:
            print(f"{row['qid']:<5}{'unanswerable':>12}{'-':>9}{'-':>10}   "
                  f"{','.join(row['tags'])}")
            continue
        print(f"{row['qid']:<5}{str(row['dense_rank']):>12}"
              f"{str(row['keyword_rank']):>9}"
              f"{row['dense_recall']:>10.2f}   {','.join(row['tags'])}")

    answerable = [r for r in rows if r["answerable"]]
    found = [r for r in answerable if r["dense_rank"]]
    print(f"\n  dense: gold chunk in the top {K} on "
          f"{sum(1 for r in answerable if r['dense_recall'] > 0)}"
          f"/{len(answerable)} questions")
    print(f"  dense beats keyword on "
          f"{sum(1 for r in found if r['keyword_rank'] and r['dense_rank'] < r['keyword_rank'])}"
          f", keyword beats dense on "
          f"{sum(1 for r in found if r['keyword_rank'] and r['keyword_rank'] < r['dense_rank'])}")

    write_json("artifacts/week07_retrieval.json", {"k": K, "rows": rows})

    # TODO 4. Read the table and answer three questions in DECISIONS.md.
    #   a. Ten of eleven questions rank the gold chunk first. Is that
    #      evidence that your pipeline is good, or that the task is easy?
    #      Answer honestly, in one sentence.
    #   b. One question fails on BOTH retrievers. Say why, and say whether
    #      any chunk size would fix it.
    #   c. Name the question where dense beats keyword by the most, and say
    #      what about that question makes meaning beat words.
    #
    # TODO 5. Correct the gold set from your own measurement.
    #
    # Q02's note in goldset.py claims that a rare exact token is where dense
    # retrieval loses and keyword search wins. Your table says otherwise.
    # Rewrite the note to match the evidence, in goldset.py, and record the
    # correction here.
    #
    # This is not busywork and it is not a trick. Course material is written
    # by people who reasoned about what should happen, and reasoning is what
    # measurement is for checking. You have just falsified a claim in your
    # own gold set, on your own data, and the right response is to fix the
    # claim rather than to quietly score around it.
    existing, source = load_or_reference("goldset.json", lab=LAB)
    print(f"\ngold set loaded from: {source}")
    goldset = GoldSet.model_validate(existing)
    already = {c.case_id for c in goldset.cases}
    for question in GOLD:
        if question.qid in already:
            continue
        goldset.cases.append(GoldCase(
            case_id=question.qid, week_added=7, question=question.question,
            expected={"gold_sections": list(question.gold_sections)},
            expected_behavior=(
                "answers from the named sections and cites them"
                if question.answerable
                else "refuses, because the corpus cannot answer it"),
            slice_tags=["task:retrieval", *question.tags],
            must_refuse=not question.answerable))
    write_json("artifacts/goldset.json", goldset)
    print(f"gold set now holds {len(goldset.cases)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())