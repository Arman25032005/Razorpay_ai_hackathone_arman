"""
Payment State Machine.

Formalizes payment status transitions as an explicit, validated state
machine rather than leaving them as implicit string assignments scattered
across the codebase. This is the single source of truth for "is this
transition legal, and which status wins when events arrive out of order or
twice."

States are based on Razorpay's actual documented payment lifecycle (see
docs/RAZORPAY_INTEGRATION.md) — not invented. Razorpay's real payment
entity statuses are: created -> authorized -> captured, with failed,
refunded, and partially_refunded as additional terminal/near-terminal
states. We map our internal "succeeded"/"failed"/"pending" vocabulary onto
that, since the recovery agent only needs to know "did money move or not,"
but the underlying transition legality follows Razorpay's real lifecycle.

Critical responsibilities this module exists to satisfy (spec section 8 and
section 35's "graceful failure" requirement):
- Never let a stale/out-of-order webhook downgrade a payment that has
  already reached a later, more-authoritative state.
- Never let a duplicate event trigger a second transition.
- Always require checking CURRENT state before executing a recovery
  action — if a payment has already succeeded by the time an agent goes to
  act on it, the action must be cancelled, not executed anyway.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class PaymentState(str, Enum):
    """Mirrors Razorpay's real payment entity lifecycle
    (https://razorpay.com/docs/payments/payments/ — 'created', 'authorized',
    'captured', 'refunded', 'failed' are Razorpay's actual documented
    statuses). PENDING is our internal placeholder for a payment we've
    created a case for but haven't yet received a terminal webhook on.
    """
    PENDING = "pending"
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"        # = our "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


# Rank determines authority: a higher rank can overwrite a lower rank, but
# never the reverse. This is how we handle out-of-order webhook delivery —
# e.g. a late "authorized" event arriving after we already recorded
# "captured" must NOT downgrade the payment back to authorized.
_STATE_RANK = {
    PaymentState.PENDING: 0,
    PaymentState.CREATED: 1,
    PaymentState.AUTHORIZED: 2,
    PaymentState.FAILED: 3,       # terminal for this attempt, but see note below
    PaymentState.CAPTURED: 4,     # money has moved — highest ordinary authority
    PaymentState.REFUNDED: 5,
    PaymentState.PARTIALLY_REFUNDED: 5,
}

# Legal forward transitions. FAILED -> CAPTURED is intentionally allowed:
# this is the exact real-world sequence Razorpay documents (a customer's
# UPI retry can succeed after an initial failure webhook was already
# delivered) — see docs/RAZORPAY_INTEGRATION.md. It is the reason recovery
# actions must always re-verify current status before executing.
_LEGAL_TRANSITIONS: dict[PaymentState, set[PaymentState]] = {
    PaymentState.PENDING: {PaymentState.CREATED, PaymentState.AUTHORIZED, PaymentState.CAPTURED, PaymentState.FAILED},
    PaymentState.CREATED: {PaymentState.AUTHORIZED, PaymentState.CAPTURED, PaymentState.FAILED},
    PaymentState.AUTHORIZED: {PaymentState.CAPTURED, PaymentState.FAILED},
    PaymentState.FAILED: {PaymentState.CAPTURED},  # late-authorization / customer retry
    PaymentState.CAPTURED: {PaymentState.REFUNDED, PaymentState.PARTIALLY_REFUNDED},
    PaymentState.REFUNDED: set(),
    PaymentState.PARTIALLY_REFUNDED: {PaymentState.REFUNDED},
}

TERMINAL_STATES = {PaymentState.CAPTURED, PaymentState.REFUNDED}


@dataclass
class TransitionResult:
    accepted: bool
    new_state: PaymentState
    reason: str


def apply_transition(current: PaymentState | str, incoming: PaymentState | str) -> TransitionResult:
    """Decides whether an incoming payment-status event should be applied,
    given the current recorded state. This is the reconciliation logic
    spec section 7 asks for: 'the system must recognize the latest
    authoritative state,' not just the latest-arriving event."""
    current = PaymentState(current) if not isinstance(current, PaymentState) else current
    incoming = PaymentState(incoming) if not isinstance(incoming, PaymentState) else incoming

    if current == incoming:
        return TransitionResult(False, current, "Duplicate event — state unchanged, no-op (idempotent)")

    if incoming not in _LEGAL_TRANSITIONS.get(current, set()):
        # Not a legal forward transition. Check if it's just a stale/
        # out-of-order lower-rank event rather than a real error.
        if _STATE_RANK[incoming] <= _STATE_RANK[current]:
            return TransitionResult(
                False, current,
                f"Out-of-order event ignored: '{incoming.value}' has lower/equal authority than current '{current.value}'",
            )
        return TransitionResult(False, current, f"Illegal transition: {current.value} -> {incoming.value}")

    return TransitionResult(True, incoming, f"Transition applied: {current.value} -> {incoming.value}")


def is_terminal(state: PaymentState | str) -> bool:
    state = PaymentState(state) if not isinstance(state, PaymentState) else state
    return state in TERMINAL_STATES


def verify_before_action(current_state: PaymentState | str) -> tuple[bool, str]:
    """Gate that MUST be called immediately before executing any recovery
    action on a payment. Spec section 35's graceful-failure demo case is
    exactly this: an agent decides to retry, but a fresh status check shows
    the payment already CAPTURED — so the action must be cancelled, not
    executed regardless."""
    state = PaymentState(current_state) if not isinstance(current_state, PaymentState) else current_state
    if state == PaymentState.CAPTURED:
        return False, "Payment already captured — recovery action cancelled, nothing to recover"
    if state == PaymentState.REFUNDED:
        return False, "Payment was refunded — recovery action cancelled, this is not a recoverable case"
    if state not in (PaymentState.FAILED, PaymentState.PENDING):
        return False, f"Payment is in state '{state.value}', not a recoverable failure state — action cancelled"
    return True, "Payment confirmed still in a recoverable failed state"
