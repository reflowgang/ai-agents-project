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

import contextlib
import random
import time
from dataclasses import dataclass, field

from faults import ContractDrift, RateLimited, ServerError, ToolTimeout

try:
    from opentelemetry import trace as _otel
    _tracer = _otel.get_tracer("week08.retry")
except ImportError:                                       
    _otel = None
    _tracer = None


def _span(name: str, **attrs):
    if _tracer is None:
        return contextlib.nullcontext()
    return _tracer.start_as_current_span(name, attributes=attrs)


def _event(name: str, **attrs) -> None:
    if _otel is not None:
        _otel.get_current_span().add_event(name, attrs)


class EmptyResult(Exception):
    """A call that SUCCEEDED and returned nothing.

    Raised by a caller's `validate`, never by the transport. Like
    ContractDrift it is not retryable: asking again for something that
    answered "nothing" gets the same nothing, only later.
    """
RETRYABLE = (ToolTimeout, RateLimited, ServerError)
HANDLED = RETRYABLE + (ContractDrift, EmptyResult)


@dataclass
class StepPolicy:
    attempts: int = 3
    base_delay: float = 0.05
    jitter: bool = True
    max_elapsed: float = 5.0
    on_exhausted: str = "degrade"       


@dataclass
class Attempt:
    n: int
    error: str | None
    slept: float = 0.0                  


@dataclass
class StepResult:
    value: object = None
    ok: bool = False
    attempts: list[Attempt] = field(default_factory=list)
    used_fallback: bool = False
    outcome: str = "ok"                
    error: str | None = None            
    elapsed: float = 0.0               

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


def _ladder(fn, *args, policy: StepPolicy, fallback=None,
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
    rng = random.Random()               
    total = max(1, policy.attempts)

    def attempt(call, label, n=0):
        with _span(label, n=n) as sp:
            try:
                value = call(*args, **kwargs)
                if validate is not None:
                    validate(value)
            except HANDLED as exc:
                if sp is not None:
                    sp.set_attribute("error.type", type(exc).__name__)
                    sp.set_attribute("error.message", str(exc))
                raise
            return value

    last: BaseException | None = None
    for n in range(1, total + 1):
        try:
            value = attempt(fn, "attempt", n)
        except HANDLED as exc:
            last = exc
            record = Attempt(n, _describe(exc))
            result.attempts.append(record)
            if not is_retryable(exc) or n == total:
                break
            delay = policy.base_delay * (2 ** (n - 1))         
            if policy.jitter:
                delay = rng.uniform(0.0, delay)                 
            if time.monotonic() - started + delay > policy.max_elapsed:
                break                                          
            _event("backoff", seconds=round(delay, 4), after_attempt=n)
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
            value = attempt(fallback, "fallback")
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


def call_with_policy(fn, *args, policy: StepPolicy, fallback=None,
                     validate=None, **kwargs) -> StepResult:
    """The ladder above, wrapped in one span per step.

    The span carries the outcome, so a trace reader sees "step:search,
    3 tries, outcome fallback" without opening the children.
    """
    name = getattr(fn, "__name__", "call")
    with _span(f"step:{name}", attempts_allowed=policy.attempts) as sp:
        result = _ladder(fn, *args, policy=policy, fallback=fallback,
                         validate=validate, **kwargs)
        if sp is not None:
            sp.set_attribute("tries", result.tries)
            sp.set_attribute("outcome", result.outcome)
            sp.set_attribute("used_fallback", result.used_fallback)
            sp.set_attribute("elapsed_s", round(result.elapsed, 4))
            if result.error:
                sp.set_attribute("error", result.error)
        return result