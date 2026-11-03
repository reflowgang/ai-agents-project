"""The retry ladder. TODO 1 and 2.

Four responses to a failing step, and the lecture's instruction is to choose
one per step IN ADVANCE, by consequence, and to record which one fired:

    retry     the same call again, because the failure looks transient
    fallback  a different, worse call that is more likely to work
    degrade   answer partially, and say what is missing
    escalate  stop and ask a human

The policy is data, not control flow. That is what lets the same pipeline
run with one attempt in a fast test and three in the lab, and it is what
makes the policy reviewable. A try and except at every call site is the
thing this replaces, and finding fourteen of them later is the reason.
"""

from __future__ import annotations

import importlib
import random
import time
from dataclasses import dataclass, field

from .faults import ContractDrift, RateLimited, ServerError, ToolTimeout


class EmptyResult(Exception):
    """A call that SUCCEEDED and returned nothing.

    Raised by a caller's `validate`, never by the transport. Like
    ContractDrift it is not retryable: asking again for something that
    answered "nothing" gets the same nothing, only later.
    """


def _transport_errors() -> tuple[type[BaseException], ...]:
    """The real-world versions of the three harness faults.

    In the lab the faults are my own exception classes. In the application
    the failures come from whatever library made the call, so the same
    "the upstream is having a moment" line has to cover them too, or this
    module would retry nothing that actually happens.

    Deliberately NOT here: authentication errors, bad requests, and HTTP
    errors in general (urllib's HTTPError, requests' HTTPError). A 401 or a
    404 will not get better by asking again.
    """
    found: list[type[BaseException]] = [ConnectionError, TimeoutError]
    wanted = (
        ("openai", ("APIConnectionError",      # includes APITimeoutError
                    "RateLimitError", "InternalServerError")),
        ("requests.exceptions", ("ConnectionError", "Timeout")),
        ("httpx", ("TransportError",)),        # timeouts, refused connections
    )
    for module, names in wanted:
        try:
            mod = importlib.import_module(module)
        except ImportError:
            continue
        found += [getattr(mod, n) for n in names if hasattr(mod, n)]
    return tuple(dict.fromkeys(found))


# The failures the ladder knows how to handle. Anything else (a TypeError,
# a typo in my own code) is a bug, and a bug must crash loudly instead of
# being quietly turned into a "degraded" answer.
RETRYABLE = (ToolTimeout, RateLimited, ServerError) + _transport_errors()
HANDLED = RETRYABLE + (ContractDrift, EmptyResult)


@dataclass
class StepPolicy:
    attempts: int = 3
    base_delay: float = 0.05
    jitter: bool = True
    max_elapsed: float = 5.0
    on_exhausted: str = "degrade"       # or "escalate"


@dataclass
class Attempt:
    n: int
    error: str | None
    slept: float = 0.0                  # the wait AFTER this attempt


@dataclass
class StepResult:
    value: object = None
    ok: bool = False
    attempts: list[Attempt] = field(default_factory=list)
    used_fallback: bool = False
    outcome: str = "ok"                 # ok | fallback | degraded | escalated
    error: str | None = None            # why it ended up not ok, in one line
    elapsed: float = 0.0                # seconds, the whole ladder

    @property
    def tries(self) -> int:
        """Attempts on the PRIMARY call. The fallback is not counted here."""
        return len(self.attempts)


# --------------------------------------------------------------------------
# TODO 1. Which errors deserve a second call?
# --------------------------------------------------------------------------

def is_retryable(exc: BaseException) -> bool:
    """Retry only when the upstream is having "a moment".

    Retryable: ToolTimeout, RateLimited, ServerError. The call did not
    complete and a later call may.

    Not retryable: ContractDrift (and EmptyResult). The call SUCCEEDED; the
    response was valid and said something I cannot use. A retry returns the
    same answer as fast as the network allows. (A 401 would be on this side
    too, but it is not in this lab.)

    Everything I do not recognise is also False: an unknown error is not
    evidence that waiting will help.
    """
    return isinstance(exc, RETRYABLE)


# --------------------------------------------------------------------------
# TODO 2. Backoff with jitter, capped twice.
# --------------------------------------------------------------------------

def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def call_with_policy(fn, *args, policy: StepPolicy, fallback=None,
                     validate=None, **kwargs) -> StepResult:
    """Call `fn`, retrying by policy, then falling back, then degrading.

    1. Try `fn(*args)` up to `policy.attempts` times. `validate(value)`, if
       given, runs on every SUCCESS and raises ContractDrift / EmptyResult.
    2. Between tries wait base_delay * 2**(n-1), with jitter if asked, and
       never past `max_elapsed` in total.
    3. A non-retryable error stops the retrying at once but still goes on to
       the fallback: the primary is no use, a worse call may be.
    4. Nothing worked: return value None and the outcome "degraded" (or
       "escalated" if the policy says a human must decide). No exception
       leaves this function for a failure it knows about.
    """
    result = StepResult()
    started = time.monotonic()
    rng = random.Random()               # local, so I never touch global random
    total = max(1, policy.attempts)

    def attempt(call):
        value = call(*args, **kwargs)
        if validate is not None:
            validate(value)
        return value

    last: BaseException | None = None
    for n in range(1, total + 1):
        try:
            value = attempt(fn)
        except HANDLED as exc:
            last = exc
            record = Attempt(n, _describe(exc))
            result.attempts.append(record)
            if not is_retryable(exc) or n == total:
                break
            delay = policy.base_delay * (2 ** (n - 1))          # doubles
            if policy.jitter:
                delay = rng.uniform(0.0, delay)                 # full jitter
            if time.monotonic() - started + delay > policy.max_elapsed:
                break                                           # cap 2: time
            time.sleep(delay)
            record.slept = delay
            continue
        result.attempts.append(Attempt(n, None))
        result.value, result.ok, result.outcome = value, True, "ok"
        result.elapsed = time.monotonic() - started
        return result

    result.error = _describe(last) if last else "no attempt made"

    if fallback is not None:
        try:
            value = attempt(fallback)
        except HANDLED as exc:
            result.error += f"; fallback {_describe(exc)}"
        else:
            result.value, result.ok = value, True
            result.used_fallback, result.outcome = True, "fallback"
            result.elapsed = time.monotonic() - started
            return result

    result.value, result.ok = None, False
    result.outcome = ("escalated" if policy.on_exhausted == "escalate"
                      else "degraded")
    result.elapsed = time.monotonic() - started
    return result
