"""
Cost-sensitive decision framework (spec section 15).

    Expected Value = P(recovery) x Recoverable Amount
                      - Action Cost
                      - Customer Annoyance Cost
                      - Risk Cost

Advisory only, not a gate — app/policies/engine.py stays the one hard
authority on what the agent can do. This just answers "is this worth it
in EV terms" as a signal alongside those rules, never a bypass.

Cost constants below are explicit and editable, not learned — keeps
the numbers behind any decision inspectable.
"""
from dataclasses import dataclass

# Action cost: the direct operational cost of attempting a recovery action
# (payment gateway fee for a retry attempt, message-sending cost, etc.).
# These are illustrative placeholder estimates — a production deployment
# would source them from actual provider billing.
ACTION_COST_BY_TYPE = {
    "payment_retry": 2.0,          # gateway retry fee (INR)
    "send_message": 0.5,           # email/SMS/WhatsApp send cost
    "send_payment_update_request": 0.5,
    "send_payment_link": 1.0,      # payment link generation + notification
    "escalate": 15.0,              # human reviewer time, amortized
}

# Customer annoyance cost: a soft, non-monetary cost representing the risk
# of damaging the customer relationship through repeated contact. Modeled
# as a cost that grows with how many times this customer has already been
# contacted for this case — repeated nagging is worse than a first attempt.
ANNOYANCE_COST_PER_PRIOR_ATTEMPT = 25.0

# Risk cost: a cost applied to strategies that carry compliance/reputational
# risk if used carelessly (e.g. a "stronger reminder" on a low-confidence
# diagnosis risks contacting the wrong customer aggressively).
RISK_COST_BY_STRATEGY = {
    "stronger_reminder": 20.0,
    "invoice_escalation": 30.0,
}


@dataclass
class ExpectedValueResult:
    expected_value: float
    probability_used: float
    recoverable_amount: float
    action_cost: float
    annoyance_cost: float
    risk_cost: float
    recommendation: str  # "act" | "reconsider" | "do_not_act"


def compute_expected_value(probability: float, amount: float, action_type: str,
                            strategy: str | None = None, prior_attempts: int = 0) -> ExpectedValueResult:
    """Computes the expected value of taking a given recovery action,
    exactly per the spec's formula. `probability` should be the best
    available recovery-probability estimate — the ML model's prediction
    when available (see app.agents.ai_service.predict_recovery_probability),
    falling back to the rule engine's diagnosis confidence otherwise."""
    action_cost = ACTION_COST_BY_TYPE.get(action_type, 1.0)
    annoyance_cost = ANNOYANCE_COST_PER_PRIOR_ATTEMPT * prior_attempts
    risk_cost = RISK_COST_BY_STRATEGY.get(strategy, 0.0)

    expected_value = (probability * amount) - action_cost - annoyance_cost - risk_cost

    if expected_value <= 0:
        recommendation = "do_not_act"
    elif expected_value < amount * 0.05:  # positive but thin margin
        recommendation = "reconsider"
    else:
        recommendation = "act"

    return ExpectedValueResult(
        expected_value=round(expected_value, 2),
        probability_used=probability,
        recoverable_amount=amount,
        action_cost=action_cost,
        annoyance_cost=annoyance_cost,
        risk_cost=risk_cost,
        recommendation=recommendation,
    )
