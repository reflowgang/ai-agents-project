"""The scorer. Two TODO markers, and it is the most important file today.

A prompt change is not an improvement until it has been measured, and this
is what measures it. Write it before you tune anything, because a scorer
written after you have seen the output tends to score what the output
already does.

One rule, and it decides most of the marks in this session: report per
field, as counts. Never one overall accuracy. Ten records means one error
moves a percentage by ten points, and an average across four fields hides
the only interesting thing in the data, which is that they do not move
together.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FIELDS = ("category", "urgency", "due_date", "quote")


@dataclass
class FieldResult:
    correct: bool
    got: object
    expected: object
    note: str = ""


@dataclass
class Scoreboard:
    """Counts per field, plus the failures worth reading."""

    hits: dict[str, int] = field(
        default_factory=lambda: {f: 0 for f in FIELDS})
    total: int = 0
    invalid: int = 0
    failures: list[tuple[str, str, str]] = field(default_factory=list)

    def as_counts(self) -> str:
        return "  ".join(f"{f} {self.hits[f]:>2}/{self.total}"
                         for f in FIELDS)


# --------------------------------------------------------------------------
# TODO 3. Score one record against its gold annotation.
# --------------------------------------------------------------------------

def score_one(record, gold, document_text: str) -> dict[str, FieldResult]:
    results = {}

    results["category"] = FieldResult(
        correct=record.category == gold.category,
        got=record.category, expected=gold.category,
    )
    results["urgency"] = FieldResult(
        correct=record.urgency == gold.urgency,
        got=record.urgency, expected=gold.urgency,
    )

    got_date = record.due_date
    date_correct = got_date == gold.due_date
    date_note = ""
    if not date_correct:
        if got_date in ("", "null", "None") and gold.due_date is None:
            date_note = "returned empty/string-null instead of None"
        elif got_date is not None and gold.due_date is None:
            date_note = f"invented a date ({got_date}) where none was stated"
        elif got_date is None and gold.due_date is not None:
            date_note = f"returned None where {gold.due_date} was expected"
        else:
            date_note = f"wrong date: got {got_date}, expected {gold.due_date}"
    results["due_date"] = FieldResult(
        correct=date_correct, got=got_date, expected=gold.due_date,
        note=date_note,
    )

    quote_correct = bool(record.quote) and record.quote in document_text
    results["quote"] = FieldResult(
        correct=quote_correct, got=record.quote,
        expected="(verbatim substring)",
        note="" if quote_correct else "quote not found verbatim in source",
    )

    return results
    raise NotImplementedError("TODO 3: score the four fields")


# --------------------------------------------------------------------------
# TODO 4. Aggregate.
# --------------------------------------------------------------------------

def score_all(records, golds, docs) -> Scoreboard:
    board = Scoreboard()
    for record, doc in zip(records, docs):
        board.total += 1
        gold = golds[doc.id]
        if record is None:
            board.invalid += 1
            for f in FIELDS:
                board.failures.append(
                    (doc.id, f, "invalid: failed schema validation"))
            continue
        results = score_one(record, gold, doc.text)
        for f in FIELDS:
            r = results[f]
            if r.correct:
                board.hits[f] += 1
            else:
                note = r.note or f"got {r.got!r}, expected {r.expected!r}"
                board.failures.append((doc.id, f, note))
    return board
    raise NotImplementedError("TODO 4: aggregate into a Scoreboard")


# --------------------------------------------------------------------------
# Given.
# --------------------------------------------------------------------------

def compare(a: Scoreboard, b: Scoreboard, label_a: str, label_b: str) -> str:
    """Two scoreboards side by side, per field, with the movement."""
    lines = [f"{'field':<10} {label_a:>12} {label_b:>12} {'move':>7}"]
    lines.append("-" * 44)
    for f in FIELDS:
        move = b.hits[f] - a.hits[f]
        lines.append(f"{f:<10} {a.hits[f]:>9}/{a.total} {b.hits[f]:>9}/{b.total} "
                     f"{move:>+7d}")
    lines.append(f"{'invalid':<10} {a.invalid:>12} {b.invalid:>12}")
    return "\n".join(lines)
