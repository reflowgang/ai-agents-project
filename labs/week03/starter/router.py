"""The classifier and the policy layer. TODO 2 and 3.

Two calls per request in the routed system: one to decide, one to answer.
The first is cheap and its output is inspectable. The second is the work.

The policy layer between them is about three lines of code and three design
decisions, and the decisions are where the marks are.
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from routes import ROUTES, SPECIALISTS, SYSTEM_ROUTER

from project.models import BASE_URL, API_KEY, SMALL


class Decision(BaseModel):
    route: Literal["request", "info", "status", "complaint", "other"]
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(max_length=200)


class Routed(BaseModel):
    """What the policy layer decided, and why."""

    decision: Decision
    applied_route: str
    policy_fired: str | None = None      # None means the decision stood
    evidence_ok: bool = True


# --------------------------------------------------------------------------
# TODO 2. The classifying call.
# --------------------------------------------------------------------------

def classify(client, text: str, model: str = SMALL.name,
             temperature: float = 0.0) -> tuple[Decision | None, dict]:
    t0 = time.perf_counter()
    reply = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=200,
        messages=[
            {"role": "system", "content": SYSTEM_ROUTER},
            {"role": "user", "content": text},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "decision", "schema": Decision.model_json_schema()},
        },
    )
    raw = reply.choices[0].message.content
    meta = {
        "seconds": time.perf_counter() - t0,
        "prompt_tokens": reply.usage.prompt_tokens,
        "completion_tokens": reply.usage.completion_tokens,
        "raw": raw,
    }
    try:
        decision = Decision.model_validate_json(raw)
    except ValidationError:
        return None, meta
    return decision, meta


# --------------------------------------------------------------------------
# TODO 3. The policy layer.
# --------------------------------------------------------------------------

# TODO 3a. Pick a number, but look at the distribution before you pick it.
#
# Choosing a threshold because 0.7 sounds reasonable is the mistake this
# exercise exists to catch. Run the classifier over the twenty four queries
# first, print the confidences, and then decide. On one of the two course
# models the answer will surprise you.
CONFIDENCE_FLOOR = 0.5      # TODO 3a

# TODO 3b. Where does anything the policy rejects go?
#
# Not every route is equally safe to be wrong into. Ask what each specialist
# DOES on the sender's behalf, and pick the one whose actions are easiest to
# undo. One of the five logs a ticket, one escalates to a human, and one
# only answers. That should decide it.
SAFE_DEFAULT = "info"          # TODO 3b


def apply_policy(decision: Decision | None, text: str) -> Routed:
    if decision is None:
        fallback = Decision(route=SAFE_DEFAULT, confidence=0.0, evidence="")
        return Routed(decision=fallback, applied_route=SAFE_DEFAULT,
                      policy_fired="invalid_decision", evidence_ok=False)

    evidence_ok = bool(decision.evidence) and decision.evidence in text
    if not evidence_ok:
        return Routed(decision=decision, applied_route=SAFE_DEFAULT,
                      policy_fired="evidence_not_verbatim", evidence_ok=False)

    if decision.confidence < CONFIDENCE_FLOOR:
        return Routed(decision=decision, applied_route=SAFE_DEFAULT,
                      policy_fired="low_confidence", evidence_ok=True)

    return Routed(decision=decision, applied_route=decision.route,
                  policy_fired=None, evidence_ok=True)


# --------------------------------------------------------------------------
# Given. The answering call.
# --------------------------------------------------------------------------

def respond(client, system: str, text: str,
            model: str = SMALL.name) -> tuple[str, dict]:
    t0 = time.perf_counter()
    reply = client.chat.completions.create(
        model=model, temperature=0.0, max_tokens=250,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": text}])
    return reply.choices[0].message.content, {
        "seconds": time.perf_counter() - t0,
        "prompt_tokens": reply.usage.prompt_tokens,
        "completion_tokens": reply.usage.completion_tokens,
    }
