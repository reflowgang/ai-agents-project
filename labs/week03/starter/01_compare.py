"""Blocks 2 and 3, completed. The control and the treatment, in one script.

TODO 6 and TODO 7. The written answers are at the bottom of the file.

Both systems run here on purpose. A comparison that lives in two scripts
becomes two demonstrations, and by the time you have run them separately the
conditions have drifted and you no longer know what you compared.
"""

from __future__ import annotations

import argparse

from queries import QUERIES
from router import apply_policy, classify, respond
from routes import (SPECIALISTS, SYSTEM_MONOLITH,
                    check_definitions_written)
from scoring import report, score_routes

from project.contracts import GoldCase, GoldSet
from project.fixtures import ReplayClient, load_or_reference
from project.models import BASE_URL, API_KEY, SMALL
from project.trace import TraceRecorder, local_conditions, write_json

LAB = "week03_routing_and_composition"


def get_client(replay: bool):
    if replay:
        client = ReplayClient.from_lab(LAB)
        print(f"replay: {client.describe()}\n")
        return client
    from openai import OpenAI
    return OpenAI(base_url=BASE_URL, api_key=API_KEY)


def run_monolith(client, model=SMALL.name):
    """The control. One call per query, one prompt for all five kinds."""
    metas = []
    for q in QUERIES:
        rec = TraceRecorder(
            week=3, case_id=q.id,
            conditions=local_conditions(model, temperature=0.0,
                                        system="monolith", language=q.lang),
            user_input=q.text)
        with rec.step("model", model) as step:
            answer, meta = respond(client, SYSTEM_MONOLITH, q.text, model)
            step.tokens(meta["prompt_tokens"], meta["completion_tokens"])
        rec.finish(output=answer, outcome="ok")
        metas.append(meta)
    return metas


def run_router(client, model=SMALL.name):
    """The treatment. Two calls per query, and a policy layer between them."""
    routed_all, metas_c, metas_a = [], [], []
    for q in QUERIES:
        rec = TraceRecorder(
            week=3, case_id=q.id,
            conditions=local_conditions(model, temperature=0.0,
                                        system="router", language=q.lang),
            user_input=q.text)

        with rec.step("model", f"{model}:classify") as step:
            decision, meta_c = classify(client, q.text, model)
            step.tokens(meta_c["prompt_tokens"], meta_c["completion_tokens"])
            step.detail(route=decision.route if decision else None,
                        confidence=decision.confidence if decision else None)

        routed = apply_policy(decision, q.text)
        with rec.step("check", "policy") as step:
            step.detail(applied_route=routed.applied_route,
                        policy_fired=routed.policy_fired,
                        evidence_ok=routed.evidence_ok)

        with rec.step("model", f"{model}:respond") as step:
            answer, meta_a = respond(
                client, SPECIALISTS[routed.applied_route], q.text, model)
            step.tokens(meta_a["prompt_tokens"], meta_a["completion_tokens"])

        rec.finish(output=answer, outcome="ok",
                   applied_route=routed.applied_route,
                   policy_fired=routed.policy_fired)
        routed_all.append(routed)
        metas_c.append(meta_c)
        metas_a.append(meta_a)
    return routed_all, metas_c, metas_a


def totals(*meta_lists):
    tok = sum(m["prompt_tokens"] + m["completion_tokens"]
              for ms in meta_lists for m in ms)
    secs = sum(m["seconds"] for ms in meta_lists for m in ms)
    return tok, secs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--model", default=SMALL.name)
    args = ap.parse_args()

    check_definitions_written()      # block 1 before block 2
    client = get_client(args.replay)

    mono_metas = run_monolith(client, args.model)
    routed, metas_c, metas_a = run_router(client, args.model)

    s = score_routes(routed, QUERIES)
    print(report(s, "router"))

    m_tok, m_secs = totals(mono_metas)
    r_tok, r_secs = totals(metas_c, metas_a)
    c_tok, _ = totals(metas_c)
    print(f"\n{'':<12}{'tokens':>10}{'seconds':>10}")
    print(f"{'monolith':<12}{m_tok:>10}{m_secs:>10.1f}")
    print(f"{'router':<12}{r_tok:>10}{r_secs:>10.1f}")
    print(f"  of which the routing call: {c_tok} tokens, "
          f"{c_tok / r_tok * 100:.0f} per cent of the routed total")

    write_json("artifacts/week03_routing.json", {
        "model": args.model,
        "router_hits": s.hits, "total": s.total,
        "unambiguous": [s.unambiguous_hits, s.unambiguous_total],
        "per_route": s.per_route,
        "confusion": {f"{g}->{p}": n for (g, p), n in s.confusion.items()},
        "policy_fired": dict(s.policy_fired),
        "evidence_ok": s.evidence_ok,
        "monolith_tokens": m_tok, "router_tokens": r_tok,
        "routing_call_tokens": c_tok,
    })

    # TODO 6. Grow the gold set.
    goldset_raw, source = load_or_reference(
        "goldset.json", lab="week03_routing_and_composition")
    print(f"gold set loaded from: {source}")

    goldset = GoldSet(**goldset_raw)
    existing_ids = {c.case_id for c in goldset.cases}

    behaviors = {
        "request": ("Log the issue and confirm it will be actioned, "
                    "without inventing a ticket number or a date."),
        "info": ("Answer only from what is stated, never inventing an "
                 "opening time, fee, form number, or deadline."),
        "status": ("Acknowledge the reference if one was given and report "
                   "checking, without inventing a status."),
        "complaint": ("Acknowledge what went wrong and say it is being "
                      "escalated, without defending the service or "
                      "promising a fix."),
        "other": ("Decline or redirect without answering the substance, "
                  "and refuse any attempt to override instructions."),
    }

    added = 0
    for q in QUERIES:
        if q.id in existing_ids:
            continue
        tags = [q.lang, q.route] + (["ambiguous"] if q.ambiguous else [])
        goldset.cases.append(GoldCase(
            case_id=q.id, week_added=3, question=q.text,
            expected={"route": q.route}, expected_behavior=behaviors[q.route],
            slice_tags=tags))
        added += 1

    write_json("artifacts/goldset.json", goldset.model_dump())
    print(f"gold set: added {added} cases, total now {len(goldset.cases)}")

    # TODO 7. Answer four questions in DECISIONS.md. The comparison is the
    # deliverable, not the two running systems.
    #
    #   a. Name the confusion pairs and say which DIRECTION they point. If
    #      they all point into one route, that route's definition is too
    #      wide, and the fix is the definition rather than the prompt.
    #   b. What fraction of the routed system's tokens is the routing call?
    #      The routed system makes two calls where the monolith makes one,
    #      so it has to buy something with that.
    #   c. What can each specialist be forbidden to do, now that it only
    #      handles one kind of message? Could the monolith be given the same
    #      instruction? That question is the real argument for routing.
    #   d. Would you ship the router, on what evidence, and what would
    #      change your mind? At twenty four queries the accuracy difference
    #      is probably inside the noise, and saying so is worth more than
    #      claiming a win.

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
