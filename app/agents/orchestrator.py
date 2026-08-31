"""
Core agent orchestrator.

EVENT -> NORMALIZATION -> RISK DETECTION -> CONTEXT RETRIEVAL -> DIAGNOSIS ->
STRATEGY SELECTION -> POLICY CHECK -> ACTION EXECUTION -> OBSERVE RESULT ->
NEXT ACTION OR STOP -> MEASUREMENT -> AUDIT LOG

Every step writes an AuditEvent. Policy is enforced in code (app.policies.engine),
never left to the LLM/rule-engine's discretion.

Performance note: each top-level function (create_case, analyze_case,
execute_next_action) commits exactly once, at the end. Audit events and
intermediate state changes are flushed (not committed) as they happen, so
they're visible to in-transaction queries but don't pay a fsync round trip
each — this matters at 1,000+ simulated customers with 500+ cases.
"""
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import RecoveryCase, RecoveryAction, AuditEvent, Payment, Invoice, Customer, utcnow
from app.agents import ai_service
from app.policies.engine import check_policy, evaluate_stop_condition, get_active_policy, DEFAULT_POLICY
from app.providers.payment import payment_provider
from app.providers.communication import communication_provider, render_message
from app.payment_state_machine import PaymentState, verify_before_action

ACTION_TYPE_BY_STRATEGY = {
    "immediate_payment_retry": "payment_retry",
    "delayed_retry": "payment_retry",
    "payment_method_update": "send_payment_update_request",
    "friendly_reminder": "send_message",
    "stronger_reminder": "send_message",
    "checkout_recovery_message": "send_message",
    "payment_link": "send_payment_link",
    "promise_to_pay_request": "send_message",
    "invoice_escalation": "send_message",
    "human_escalation": "escalate",
}

# Granular live-agent step labels, driving the "ANALYZING -> UNDERSTANDING
# CUSTOMER -> DIAGNOSING -> ..." experience in the UI via case.current_step.
STEP_DETECTED = "detected"
STEP_UNDERSTANDING_CUSTOMER = "understanding_customer"
STEP_DIAGNOSING = "diagnosing"
STEP_CHOOSING_STRATEGY = "choosing_strategy"
STEP_POLICY_CHECK = "policy_check"
STEP_EXECUTING = "executing"
STEP_WAITING_FOR_RESULT = "waiting_for_result"
STEP_RECOVERED = "recovered"
STEP_STOPPED = "stopped"


def _select_channel(case: RecoveryCase, action_type: str) -> str:
    """Picks a communication channel per case context so the demo actually
    exercises email, SMS, and WhatsApp (spec section 12) instead of only
    ever using one. Deterministic and policy-bounded — the channel still
    passes through check_policy()'s allowed_channels gate."""
    if action_type == "send_payment_link":
        return "sms"  # short, tappable — best fit for a payment link
    if case.source_type == "checkout_abandoned":
        return "whatsapp"  # high-intent, casual re-engagement
    if case.source_type == "invoice_overdue":
        return "email"  # formal B2B correspondence
    if case.attempt_count and case.attempt_count > 1:
        return "whatsapp"  # escalate channel on repeat attempts
    return "email"


def _log(db: Session, case: RecoveryCase, actor_type: str, action: str, description: str, metadata: dict | None = None):
    ev = AuditEvent(case_id=case.id, actor_type=actor_type, action=action,
                     description=description, event_metadata=metadata or {})
    db.add(ev)
    db.flush()
    return ev


def _apply_strategy_optimizer(db: Session, decision) -> str:
    """Recovery Policy Optimizer hook (spec section 47): among the strategies
    the diagnosis step judged eligible (decision.strategy_scores), prefer
    whichever has the best real track record — but only once there's enough
    history (>=5 attempts) to trust it, and only ever choosing among
    strategies the diagnosis step already approved. This never lets
    historical performance override the diagnosis into an unvetted
    strategy; it only re-ranks within the AI's own candidate set."""
    candidates = [s["strategy"] for s in (decision.strategy_scores or [])
                  if s["strategy"] != "human_escalation"]
    if len(candidates) < 2:
        return decision.recommended_strategy
    try:
        from app.policies.optimizer import recommend_strategy_order
        ranked = recommend_strategy_order(db, candidates)
    except Exception:
        return decision.recommended_strategy  # optimizer is advisory only; never block on failure
    return ranked[0] if ranked else decision.recommended_strategy


def build_customer_context(db: Session, customer: Customer) -> dict:
    payments = customer.payments
    successful = [p for p in payments if p.status == "succeeded"]
    failed = [p for p in payments if p.status == "failed"]
    invoices = customer.invoices
    on_time = [i for i in invoices if i.status == "paid" and i.days_overdue == 0]

    total_payments = len(payments)
    success_rate = (len(successful) / total_payments) if total_payments else 0.5
    most_recent = max((p.created_at for p in payments), default=None)
    days_since_last_payment = (utcnow() - most_recent).days if most_recent else 90

    return {
        "customer_id": customer.id,
        "name": customer.name,
        "lifetime_value": customer.lifetime_value,
        "successful_payments": len(successful),
        "previous_failures": len(failed),
        "on_time_invoice_rate": (len(on_time) / len(invoices)) if invoices else 1.0,
        "previous_recovery_messages": 0,  # populated by caller for checkout cases
        "payment_count": total_payments,
        "success_rate": round(success_rate, 4),
        "days_since_last_payment": days_since_last_payment,
    }


def create_case(db: Session, customer: Customer, source_type: str, source_id: str,
                 amount: float, currency: str = "INR", risk_level: str = "medium") -> RecoveryCase:
    case = RecoveryCase(
        customer_id=customer.id, source_type=source_type, source_id=source_id,
        amount_at_risk=amount, currency=currency, risk_level=risk_level,
        status="OPEN", current_step=STEP_DETECTED,
    )
    db.add(case)
    db.flush()
    _log(db, case, "SYSTEM", "case_created",
         f"Revenue-at-risk case opened for {currency} {amount:,.0f} ({source_type})")
    db.commit()
    db.refresh(case)
    return case


def analyze_case(db: Session, case: RecoveryCase, **event_kwargs) -> RecoveryCase:
    """DIAGNOSE + DECIDE step."""
    case.status = "ANALYZING"
    case.current_step = STEP_UNDERSTANDING_CUSTOMER
    _log(db, case, "AI_AGENT", "context_retrieval", "Retrieving customer context and history")

    ctx = build_customer_context(db, case.customer)
    if case.source_type == "checkout_abandoned":
        prior = db.query(RecoveryAction).filter(RecoveryAction.case_id == case.id).count()
        ctx["previous_recovery_messages"] = prior

    case.current_step = STEP_DIAGNOSING
    try:
        decision = ai_service.diagnose(
            case.source_type, ctx,
            failure_reason=event_kwargs.get("failure_reason"),
            amount=case.amount_at_risk,
            days_overdue=event_kwargs.get("days_overdue", 0),
        )
    except Exception as exc:
        # Spec section 38: if diagnosis fails for any reason, never guess or
        # execute an action — mark for human review instead. This is the
        # same fail-closed contract ai_service.diagnose already honors
        # internally for the real-LLM path; this is the outer safety net.
        decision = ai_service.Decision(
            root_cause="unknown", root_cause_confidence=0.0,
            customer_context_summary="Diagnosis engine unavailable.",
            recommended_strategy="human_escalation",
            reasoning_summary="Automated diagnosis failed; routed to human review rather than guessing.",
            human_escalation_required=True,
            escalation_reason=f"Diagnosis engine error: {type(exc).__name__}",
        )
        _log(db, case, "SYSTEM", "diagnosis_failed",
             f"AI diagnosis unavailable ({type(exc).__name__}) — case marked for human review", {"error": str(exc)})

    case.current_step = STEP_CHOOSING_STRATEGY
    decision.recommended_strategy = _apply_strategy_optimizer(db, decision)

    case.root_cause = decision.root_cause
    case.root_cause_confidence = decision.root_cause_confidence
    case.reasoning_summary = decision.reasoning_summary
    case.recommended_strategy = decision.recommended_strategy
    case.human_escalation_required = decision.human_escalation_required
    case.escalation_reason = decision.escalation_reason
    case.status = "ACTION_READY" if not decision.human_escalation_required else "ESCALATED"

    _log(db, case, "AI_AGENT", "diagnosis_completed",
         f"Root cause: {decision.root_cause} (confidence {decision.root_cause_confidence:.2f}). {ctx['name']} — {decision.customer_context_summary}",
         {"strategy_scores": decision.strategy_scores})

    if decision.human_escalation_required:
        _log(db, case, "AI_AGENT", "escalated", decision.escalation_reason or "Escalated to human review")
        case.resolved_at = None

    db.commit()
    db.refresh(case)
    return case


def execute_next_action(db: Session, case: RecoveryCase) -> RecoveryCase:
    """POLICY CHECK -> ACT -> OBSERVE -> STOP/CONTINUE step. One attempt per call."""
    if case.status in ("RECOVERED", "STOPPED", "EXPIRED", "ESCALATED"):
        return case

    # --- Pre-action payment-status verification (spec section 35) ---
    # Before executing ANY action, re-check the payment's current
    # authoritative status. Diagnosis may have happened seconds or hours
    # earlier — the payment can have already been resolved independently
    # in the meantime (customer paid via another channel, a late webhook
    # arrived, etc.). Never execute an action against a stale assumption.
    if case.source_type in ("payment_failed", "subscription_failed"):
        payment = db.query(Payment).filter(Payment.id == case.source_id).first()
        if payment:
            current_state = (
                PaymentState.CAPTURED if payment.status == "succeeded"
                else PaymentState.REFUNDED if payment.status == "refunded"
                else PaymentState.FAILED
            )
            can_act, verify_reason = verify_before_action(current_state)
            _log(db, case, "PAYMENT_PROVIDER", "status_verified",
                 f"Pre-action status check: {verify_reason}")
            if not can_act:
                already_recovered = current_state == PaymentState.CAPTURED
                case.status = "RECOVERED" if already_recovered else "STOPPED"
                case.current_step = STEP_RECOVERED if already_recovered else STEP_STOPPED
                case.stop_reason = "PAYMENT_ALREADY_RESOLVED" if already_recovered else "NOT_RECOVERABLE"
                if already_recovered:
                    case.amount_recovered = case.amount_at_risk
                    case.resolved_at = utcnow()
                _log(db, case, "SYSTEM", "action_cancelled",
                     f"Recovery action cancelled before execution: {verify_reason}")
                _log(db, case, "SYSTEM", "workflow_stopped",
                     f"Workflow stopped. Reason: {case.stop_reason}")
                db.commit()
                db.refresh(case)
                return case

    strategy = case.recommended_strategy or "human_escalation"
    action_type = ACTION_TYPE_BY_STRATEGY.get(strategy, "escalate")
    channel = _select_channel(case, action_type) if action_type in (
        "send_message", "send_payment_update_request", "send_payment_link") else None

    case.current_step = STEP_POLICY_CHECK
    policy = get_active_policy(db)
    policy_result = check_policy(case, action_type, channel, case.amount_at_risk, policy=policy)
    _log(db, case, "SYSTEM", "policy_check",
         f"Policy check: {'PASSED' if policy_result.allowed else 'BLOCKED'} — {policy_result.reason}")

    if not policy_result.allowed:
        case.status = "ESCALATED"
        case.human_escalation_required = True
        case.escalation_reason = policy_result.reason
        _log(db, case, "SYSTEM", "escalated", f"Escalated to human: {policy_result.reason}")
        db.commit()
        db.refresh(case)
        return case

    case.status = "EXECUTING"
    case.current_step = STEP_EXECUTING
    case.attempt_count += 1

    action = RecoveryAction(case_id=case.id, action_type=action_type, channel=channel, status="pending")
    db.add(action)
    db.flush()

    case.current_step = STEP_WAITING_FOR_RESULT

    if action_type == "payment_retry":
        result = payment_provider.retry_payment(case.source_id, case.amount_at_risk, case.root_cause,
                                                  intervention=None if case.attempt_count == 1 else case.recommended_strategy)
        action.status = "succeeded" if result["status"] == "succeeded" else "failed"
        action.result = result["status"]
        action.provider_response = result
        _log(db, case, "PAYMENT_PROVIDER", "payment_retry_result",
             f"Payment retry {result['status']} for {case.currency} {case.amount_at_risk:,.0f}", result)

        if result["status"] == "succeeded":
            action.amount_recovered = case.amount_at_risk
            case.amount_recovered = case.amount_at_risk
            case.status = "RECOVERED"
            case.current_step = STEP_RECOVERED
            case.stop_reason = "PAYMENT_RECOVERED"
            case.resolved_at = utcnow()
            _log(db, case, "SYSTEM", "recovered",
                 f"{case.currency} {case.amount_at_risk:,.0f} recovered")
            _log(db, case, "SYSTEM", "workflow_stopped", "Workflow stopped. Reason: PAYMENT_RECOVERED")
    else:
        body = render_message(strategy, case.customer.name, case.amount_at_risk, case.currency)
        send_channel = channel or "email"
        send_result = communication_provider.send(send_channel, case.customer.email or case.customer.phone or "unknown", body)
        action.status = "sent"
        action.message_body = body
        action.result = send_result["status"]
        action.provider_response = send_result
        _log(db, case, "AI_AGENT", "message_sent",
             f"{send_channel.capitalize()} sent: \"{body[:80]}{'...' if len(body) > 80 else ''}\"", send_result)

        # simulate customer response to the intervention on the *next* retry
        result = payment_provider.retry_payment(case.source_id, case.amount_at_risk, case.root_cause,
                                                  intervention=strategy)
        if result["status"] == "succeeded":
            action.amount_recovered = case.amount_at_risk
            case.amount_recovered = case.amount_at_risk
            case.status = "RECOVERED"
            case.current_step = STEP_RECOVERED
            case.stop_reason = "PAYMENT_RECOVERED"
            case.resolved_at = utcnow()
            _log(db, case, "CUSTOMER", "customer_action", "Customer responded and completed payment")
            _log(db, case, "SYSTEM", "recovered", f"{case.currency} {case.amount_at_risk:,.0f} recovered")
            _log(db, case, "SYSTEM", "workflow_stopped", "Workflow stopped. Reason: PAYMENT_RECOVERED")

    if case.status not in ("RECOVERED",):
        stop_reason = evaluate_stop_condition(case, policy=policy)
        if stop_reason:
            case.status = "ESCALATED" if stop_reason == "MAXIMUM_ATTEMPTS_REACHED" else "STOPPED"
            case.current_step = STEP_STOPPED
            case.stop_reason = stop_reason
            case.human_escalation_required = case.status == "ESCALATED"
            case.escalation_reason = case.escalation_reason or ("Maximum recovery attempts reached without success" if stop_reason == "MAXIMUM_ATTEMPTS_REACHED" else None)
            _log(db, case, "SYSTEM", "workflow_stopped", f"Workflow stopped. Reason: {stop_reason}")
        else:
            case.status = "ACTION_READY"

    db.commit()
    db.refresh(case)
    return case


def run_case_to_completion(db: Session, case: RecoveryCase, **event_kwargs) -> RecoveryCase:
    """Runs the full bounded loop for one case: analyze once, then execute
    actions until RECOVERED / STOPPED / ESCALATED."""
    analyze_case(db, case, **event_kwargs)
    policy = get_active_policy(db)
    guard = 0
    while case.status in ("ACTION_READY",) and guard < policy["max_attempts"] + 1:
        execute_next_action(db, case)
        guard += 1
    return case
