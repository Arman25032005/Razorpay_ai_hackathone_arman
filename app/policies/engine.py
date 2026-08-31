"""
Deterministic policy engine. This is plain code, not LLM reasoning — every
rule here is a hard constraint the agent cannot talk its way around.

`DEFAULT_POLICY` is the built-in fallback. `get_active_policy(db)` loads the
operator-editable policy from the `recovery_policies` table if one exists
(see PUT /api/policies), so changes made in the Policies tab actually take
effect on the next policy check — not just displayed.
"""
from dataclasses import dataclass

from app.models import utcnow


@dataclass
class PolicyCheckResult:
    allowed: bool
    reason: str
    requires_human_approval: bool = False


DEFAULT_POLICY = {
    "max_attempts": 3,
    "max_workflow_days": 7,
    "max_discount_percent": 10,
    "allowed_channels": ["email", "sms", "whatsapp"],
    "require_human_approval_for_discount": True,
    "require_human_approval_for_large_amount": True,
    "large_amount_threshold": 100000,
}


def get_active_policy(db=None) -> dict:
    """Returns the operator-configured policy if one has been saved via
    PUT /api/policies, else the built-in default. Accepts db=None so
    callers without a session (e.g. tests) still work."""
    if db is None:
        return DEFAULT_POLICY
    from app.models import RecoveryPolicy
    row = db.query(RecoveryPolicy).filter(RecoveryPolicy.name == "default").first()
    if not row:
        return DEFAULT_POLICY
    return {
        "max_attempts": row.max_attempts,
        "max_workflow_days": row.max_days,
        "max_discount_percent": row.max_discount_percent,
        "allowed_channels": row.allowed_channels or DEFAULT_POLICY["allowed_channels"],
        "require_human_approval_for_discount": row.require_human_approval_for_discount,
        "require_human_approval_for_large_amount": row.require_human_approval_for_large_amount,
        "large_amount_threshold": row.large_amount_threshold,
    }


def check_policy(case, action_type: str, channel: str | None, amount: float,
                  policy: dict = DEFAULT_POLICY) -> PolicyCheckResult:
    """Hard, code-enforced gate. Called before every action execution."""

    if case.attempt_count >= policy["max_attempts"]:
        return PolicyCheckResult(False, "Maximum attempts reached", requires_human_approval=True)

    age_days = (utcnow() - case.created_at).days
    if age_days > policy["max_workflow_days"]:
        return PolicyCheckResult(False, "Workflow expired (exceeded max_workflow_days)", requires_human_approval=True)

    if channel and channel not in policy["allowed_channels"]:
        return PolicyCheckResult(False, f"Channel '{channel}' is not in allowed_channels", requires_human_approval=True)

    already_approved = getattr(case, "human_approved", False)

    if action_type == "apply_discount" and policy["require_human_approval_for_discount"] and not already_approved:
        return PolicyCheckResult(False, "Discounts require human approval", requires_human_approval=True)

    if (amount and amount >= policy["large_amount_threshold"]
            and policy["require_human_approval_for_large_amount"] and not already_approved):
        return PolicyCheckResult(False, "Large-amount case requires human approval", requires_human_approval=True)

    return PolicyCheckResult(True, "Policy check passed")


def evaluate_stop_condition(case, policy: dict = DEFAULT_POLICY) -> str | None:
    """Returns a stop reason if the case should stop, else None."""
    if case.status == "RECOVERED":
        return "PAYMENT_RECOVERED"
    if case.attempt_count >= policy["max_attempts"]:
        return "MAXIMUM_ATTEMPTS_REACHED"
    age_days = (utcnow() - case.created_at).days
    if age_days > policy["max_workflow_days"]:
        return "WORKFLOW_EXPIRED"
    return None
