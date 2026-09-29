"""Scoring the router. TODO 5.

Two rules carried over from week 2 and one new one.

Carried over: report per route, as counts, never one overall percentage. At
four to seven gold records per route, one misroute moves that route by
fifteen to twenty five points, and a percentage at that sample size is a
lie told with a decimal point.

New this week: the confusion pairs matter more than the accuracy. Knowing
that the router is 20 out of 24 tells you to try harder. Knowing that it
sends requests to info, and only in that direction, tells you which
definition is wrong.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field


@dataclass
class RouteScore:
    per_route: dict[str, list[int]] = field(default_factory=dict)   # [hit, n]
    confusion: collections.Counter = field(
        default_factory=collections.Counter)
    policy_fired: collections.Counter = field(
        default_factory=collections.Counter)
    evidence_ok: int = 0
    total: int = 0
    ambiguous_total: int = 0
    ambiguous_hits: int = 0
    confidences: list[float] = field(default_factory=list)

    @property
    def hits(self) -> int:
        return sum(h for h, _ in self.per_route.values())

    @property
    def unambiguous_hits(self) -> int:
        return self.hits - self.ambiguous_hits

    @property
    def unambiguous_total(self) -> int:
        return self.total - self.ambiguous_total


# --------------------------------------------------------------------------
# TODO 5. Count per route, collect the confusion pairs, record the policy.
# --------------------------------------------------------------------------

def score_routes(results, queries) -> RouteScore:
    s = RouteScore()
    for routed, q in zip(results, queries):
        s.total += 1
        gold = q.route
        applied = routed.applied_route
        hit = applied == gold

        h, n = s.per_route.get(gold, [0, 0])
        s.per_route[gold] = [h + (1 if hit else 0), n + 1]

        if not hit:
            s.confusion[(gold, applied)] += 1

        if routed.evidence_ok:
            s.evidence_ok += 1

        if routed.policy_fired:
            s.policy_fired[routed.policy_fired] += 1

        s.confidences.append(routed.decision.confidence)

        if q.ambiguous:
            s.ambiguous_total += 1
            if hit:
                s.ambiguous_hits += 1

    return s

# --------------------------------------------------------------------------
# Given.
# --------------------------------------------------------------------------

def report(s: RouteScore, label: str) -> str:
    lines = [f"{label}: {s.hits}/{s.total} routed correctly"]
    lines.append(f"  excluding the ambiguous four: "
                 f"{s.unambiguous_hits}/{s.unambiguous_total}")
    lines.append("  per route: " + "  ".join(
        f"{r} {h}/{n}" for r, (h, n) in sorted(s.per_route.items())))
    lines.append(f"  evidence verbatim: {s.evidence_ok}/{s.total}")
    if s.confidences:
        lo, hi = min(s.confidences), max(s.confidences)
        lines.append(f"  confidence: min {lo:.2f} max {hi:.2f} "
                     f"distinct {len(set(s.confidences))}")
    if s.policy_fired:
        lines.append("  policy fired: " + "  ".join(
            f"{k} {v}" for k, v in s.policy_fired.most_common()))
    if s.confusion:
        lines.append("  confusion pairs, gold to applied:")
        for (g, p), n in s.confusion.most_common(6):
            lines.append(f"    {g:<10} -> {p:<10} {n}")
    return "\n".join(lines)
