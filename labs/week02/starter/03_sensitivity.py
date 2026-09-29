"""Block 4. Change one thing nobody would flag in review, and measure it.

    python 03_sensitivity.py --variant role --replay
    python 03_sensitivity.py --variant english_only

Fifteen minutes, one variant per group, so that the plenary has four results
instead of one. Your instructor will assign you one.

The point of this block is not which variant wins. It is that a change no
reviewer would comment on moves a measured number, which is why a prompt is
a versioned artifact and why "I improved the prompt" is not a claim anybody
should accept without a table.

One TODO marker.
"""

from __future__ import annotations

import argparse

from documents import DOCS, GOLD
from extractor import SYSTEM_ZERO_SHOT, get_client, run_variant
from scoring import compare

from project.trace import write_json

VARIANTS = ("baseline", "role", "reordered", "no_delimiter", "english_only")


# --------------------------------------------------------------------------
# TODO 8. Build one variant and measure it against your few-shot baseline.
# --------------------------------------------------------------------------

def build_system(variant: str) -> str:
    import importlib
    few_shot_mod = importlib.import_module("02_few_shot")
    baseline = SYSTEM_ZERO_SHOT + "\n" + few_shot_mod.few_shot_block()

    if variant == "baseline":
        return baseline
    if variant == "role":
        return "You are a senior service desk analyst.\n" + baseline
    raise NotImplementedError(f"TODO 8: variant {variant!r} not implemented "
                              f"(only baseline and role are built)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=VARIANTS, required=True)
    ap.add_argument("--replay", action="store_true")
    args = ap.parse_args()

    client = get_client(args.replay)

    base = run_variant(client, build_system("baseline"), "baseline",
                       DOCS, GOLD)[0]
    if args.variant == "baseline":
        return 0
    other = run_variant(client, build_system(args.variant), args.variant,
                        DOCS, GOLD)[0]

    print(compare(base, other, "baseline", args.variant))

    # Per language, which is where the english_only variant shows its hand
    # and where an overall average would have hidden it entirely.
    for lang in ("en", "fr", "de"):
        ids = {d.id for d in DOCS if d.lang == lang}
        n = len(ids)
        print(f"  {lang}: {n} documents"
              f"   baseline field errors "
              f"{sum(1 for f in base.failures if f[0] in ids)}"
              f"   {args.variant} field errors "
              f"{sum(1 for f in other.failures if f[0] in ids)}")

    write_json(f"artifacts/week02_sensitivity_{args.variant}.json", {
        "variant": args.variant,
        "baseline_hits": base.hits, "variant_hits": other.hits,
    })

    # Write in DECISIONS.md: what you changed, what moved, and by how much.
    # If nothing moved, say so. A variant that changes nothing measurable is
    # a real result and it is worth reporting, because it tells the room
    # which knobs are worth arguing about and which are superstition.

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
