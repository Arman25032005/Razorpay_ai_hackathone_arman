"""
AI decision service.

Architecture: LLM -> structured decision -> policy engine -> action executor.
The LLM (or, in demo mode, a deterministic rule-based stand-in with the same
JSON contract) NEVER calls providers directly — it only returns a structured
decision object that the orchestrator validates and the policy engine gates.

Set LLM_API_KEY in the environment to route diagnosis through a real
model — Groq (OpenAI-compatible chat/completions) or Anthropic Claude
(/v1/messages), auto-detected from the key format, see LLM_PROVIDER below;
otherwise ai_service falls back to the deterministic RULES engine below,
which is fully offline and demo-safe.
"""
import json
import os
from dataclasses import dataclass, field

USE_LLM = bool(os.getenv("LLM_API_KEY"))
# Auto-detect provider from key format: Groq keys are prefixed "gsk_",
# Anthropic keys "sk-ant-". Override with LLM_PROVIDER if ever ambiguous.
LLM_PROVIDER = os.getenv("LLM_PROVIDER") or (
    "groq" if os.getenv("LLM_API_KEY", "").startswith("gsk_") else "anthropic"
)
LLM_MODEL = os.getenv("LLM_MODEL") or (
    "openai/gpt-oss-120b" if LLM_PROVIDER == "groq" else "claude-sonnet-4-6"
)


@dataclass
class Decision:
    root_cause: str
    root_cause_confidence: float
    customer_context_summary: str
    recommended_strategy: str
    reasoning_summary: str
    human_escalation_required: bool = False
    escalation_reason: str | None = None
    strategy_scores: list = field(default_factory=list)


# ---- deterministic rule engine (demo-mode "AI") -----------------------------
# Mirrors the JSON schema an LLM call would return, so swapping in a real
# model later requires no changes downstream of this function.

def _diagnose_payment_failure(customer_ctx: dict, failure_reason: str) -> Decision:
    successes = customer_ctx["successful_payments"]
    failures = customer_ctx["previous_failures"]
    reliable = successes >= 5 and failures <= 1

    # failure_reason buckets come from app.decline_codes.classify_decline —
    # Razorpay's real documented error.reason taxonomy collapsed into a
    # vocabulary the strategy layer can act on (see that module's docstring
    # for the full reason -> bucket mapping and why each grouping exists).
    cause_map = {
        "card_expired": "expired_card",
        "insufficient_funds": "insufficient_funds",
        "auth_failed": "authentication_failure",
        "otp_failed": "authentication_failure",
        "bank_declined": "bank_decline",
        "card_declined_by_issuer": "bank_decline",
        "invalid_method": "invalid_payment_method",
        "network_timeout": "temporary_failure",
        "temporary_failure": "temporary_failure",
        "payment_cancelled": "customer_hesitation",
        "limit_exceeded": "transaction_limit_exceeded",
        "risk_declined": "risk_or_compliance_decline",
    }
    root_cause = cause_map.get(failure_reason, "unknown")
    confidence = 0.94 if root_cause != "unknown" else 0.4

    strategy_by_cause = {
        "expired_card": "payment_method_update",
        "insufficient_funds": "delayed_retry",
        "authentication_failure": "payment_method_update",
        "bank_decline": "delayed_retry",
        "invalid_payment_method": "payment_method_update",
        "temporary_failure": "immediate_payment_retry",
        "customer_hesitation": "friendly_reminder",
        "transaction_limit_exceeded": "delayed_retry",
        # Never auto-retried: a risk/compliance decline stays a human call,
        # regardless of how reliable this customer's history looks.
        "risk_or_compliance_decline": "human_escalation",
        "unknown": "human_escalation",
    }
    # A viable second-choice strategy per root cause, so the strategy
    # optimizer (app/policies/optimizer.py) has more than one real
    # candidate to rank once historical performance data exists.
    secondary_by_cause = {
        "expired_card": "payment_link",
        "insufficient_funds": "friendly_reminder",
        "authentication_failure": "payment_link",
        "bank_decline": "stronger_reminder",
        "invalid_payment_method": "payment_link",
        "temporary_failure": "delayed_retry",
        "customer_hesitation": "payment_link",
        "transaction_limit_exceeded": "friendly_reminder",
        "risk_or_compliance_decline": None,
        "unknown": None,
    }
    strategy = strategy_by_cause[root_cause]
    secondary = secondary_by_cause.get(root_cause)

    escalate = False
    reason = None
    if root_cause == "unknown" and not reliable:
        escalate = True
        reason = "AI could not determine root cause with sufficient confidence"
    elif root_cause == "risk_or_compliance_decline":
        escalate = True
        reason = "Risk or compliance decline. Never auto-retried, requires human review"

    summary = (
        f"Customer has {successes} successful payment(s) and {failures} prior failure(s), "
        f"making them a {'reliable' if reliable else 'higher-risk'} payer."
    )
    reasoning = (
        f"Diagnosed as {root_cause.replace('_',' ')}. "
        f"{'Historical behavior indicates a high-probability recoverable failure.' if reliable else 'Limited or mixed history, proceeding cautiously.'}"
    )

    scores = [
        {"strategy": strategy, "score": 0.91 if reliable else 0.6, "reason": "Best fit for diagnosed root cause"},
    ]
    if secondary:
        scores.append({"strategy": secondary, "score": 0.55, "reason": "Viable fallback intervention"})
    scores.append({"strategy": "human_escalation", "score": 0.1 if reliable else 0.55, "reason": "Fallback if automation stalls"})

    return Decision(root_cause, confidence, summary, strategy, reasoning, escalate, reason, scores)


def _diagnose_checkout_abandonment(customer_ctx: dict, amount: float) -> Decision:
    prior_msgs = customer_ctx.get("previous_recovery_messages", 0)
    if prior_msgs >= 2:
        return Decision(
            "checkout_abandonment", 0.7,
            "Customer has already received multiple recovery messages for this cart.",
            "human_escalation", "Repeated abandonment despite outreach, needs human judgment.",
            True, "Exceeded automated re-engagement attempts",
            [{"strategy": "human_escalation", "score": 0.7, "reason": "Automation exhausted"}],
        )
    strategy = "checkout_recovery_message"
    return Decision(
        "checkout_abandonment", 0.85,
        "Customer reached checkout and entered payment details but did not complete purchase.",
        strategy,
        "Cart abandonment after payment details were entered suggests hesitation rather than a hard blocker. A gentle reminder is likely to convert.",
        False, None,
        [{"strategy": strategy, "score": 0.78, "reason": "High-intent abandonment"}],
    )


def _diagnose_invoice_overdue(customer_ctx: dict, days_overdue: int, amount: float) -> Decision:
    on_time_rate = customer_ctx.get("on_time_invoice_rate", 1.0)
    reliable = on_time_rate >= 0.8

    if days_overdue <= 5:
        strategy = "friendly_reminder"
        cause = "likely_administrative_delay" if reliable else "repeated_late_payer"
    elif days_overdue <= 14:
        strategy = "stronger_reminder" if reliable else "promise_to_pay_request"
        cause = "likely_administrative_delay" if reliable else "repeated_late_payer"
    else:
        strategy = "invoice_escalation"
        cause = "repeated_late_payer"

    escalate = amount >= 100000 and days_overdue > 14
    summary = f"B2B customer with {on_time_rate*100:.0f}% on-time invoice history, currently {days_overdue} days overdue."
    reasoning = (
        f"{'Track record suggests this is administrative, not a payment-ability issue.' if reliable else 'Payment history suggests this needs firmer follow-up.'}"
    )
    return Decision(
        cause, 0.8 if reliable else 0.6, summary, strategy, reasoning,
        escalate, "Large overdue B2B balance requires human sign-off" if escalate else None,
        [{"strategy": strategy, "score": 0.75, "reason": "Matches overdue severity and payer reliability"}],
    )


def diagnose(source_type: str, customer_ctx: dict, **kwargs) -> Decision:
    """Entry point used by the orchestrator. `source_type` selects the
    sub-diagnosis routine; kwargs carries event-specific fields."""
    if USE_LLM:
        llm_decision = _diagnose_via_llm(source_type, customer_ctx, **kwargs)
        if llm_decision is not None:
            return llm_decision
        # Any LLM/validation failure falls through to the deterministic
        # engine below rather than executing an unvalidated action.
    if source_type == "payment_failed" or source_type == "subscription_failed":
        return _diagnose_payment_failure(customer_ctx, kwargs.get("failure_reason", "unknown"))
    if source_type == "checkout_abandoned":
        return _diagnose_checkout_abandonment(customer_ctx, kwargs.get("amount", 0))
    if source_type == "invoice_overdue":
        return _diagnose_invoice_overdue(customer_ctx, kwargs.get("days_overdue", 0), kwargs.get("amount", 0))
    return Decision("unknown", 0.3, "Insufficient context.", "human_escalation",
                     "Unrecognized event source.", True, "Unrecognized source_type")


# ---- real-LLM path (structured output, validated, safe-fallback) -----------

ALLOWED_ROOT_CAUSES = {
    "temporary_failure", "insufficient_funds", "expired_card", "invalid_payment_method",
    "bank_decline", "authentication_failure", "checkout_abandonment", "customer_hesitation",
    "subscription_churn_risk", "invoice_overdue", "likely_administrative_delay",
    "repeated_late_payer", "broken_mandate", "transaction_limit_exceeded",
    "risk_or_compliance_decline", "unknown",
}
ALLOWED_STRATEGIES = {
    "immediate_payment_retry", "delayed_retry", "payment_method_update", "friendly_reminder",
    "stronger_reminder", "checkout_recovery_message", "payment_link", "promise_to_pay_request",
    "invoice_escalation", "human_escalation",
}

DIAGNOSIS_PROMPT = """You are the diagnosis component of a revenue-recovery agent. \
Given customer context and event details, return ONLY a JSON object (no prose, no markdown fences) \
matching exactly this schema:

{{
  "root_cause": one of {root_causes},
  "root_cause_confidence": float 0-1,
  "customer_context_summary": short factual sentence,
  "recommended_strategy": one of {strategies},
  "reasoning_summary": short business-safe rationale (no chain-of-thought, no threats, no fabricated claims),
  "human_escalation_required": boolean,
  "escalation_reason": string or null
}}

Event source_type: {source_type}
Customer context: {customer_ctx}
Event details: {event_kwargs}
"""


def _diagnose_via_llm(source_type: str, customer_ctx: dict, **kwargs) -> "Decision | None":
    """Calls the configured LLM provider (Anthropic's Messages API, or Groq's
    OpenAI-compatible chat/completions API) for diagnosis. Returns None
    (triggering fallback to the rule engine) on any network, parsing, or
    schema-validation failure — an LLM decision is never trusted without
    validation, and the agent must never stall or act unsafely just because
    a model call failed."""
    try:
        import urllib.request

        prompt = DIAGNOSIS_PROMPT.format(
            root_causes=sorted(ALLOWED_ROOT_CAUSES), strategies=sorted(ALLOWED_STRATEGIES),
            source_type=source_type, customer_ctx=json.dumps(customer_ctx), event_kwargs=json.dumps(kwargs),
        )

        if LLM_PROVIDER == "groq":
            body = json.dumps({
                "model": LLM_MODEL, "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                # gpt-oss models spend max_tokens on hidden reasoning first;
                # "low" leaves enough budget for the actual JSON content.
                "reasoning_effort": "low",
            }).encode()
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/chat/completions", data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {os.environ['LLM_API_KEY']}",
                    # Groq's Cloudflare front-end blocks urllib's default
                    # "Python-urllib/x.y" User-Agent as a bot signature
                    # (403, Cloudflare error 1010) — a real UA is required.
                    "User-Agent": "RecoveryOS/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = json.loads(resp.read())
            text = raw["choices"][0]["message"]["content"]
        else:
            body = json.dumps({
                "model": LLM_MODEL, "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}],
            }).encode()
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages", data=body,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": os.environ["LLM_API_KEY"],
                    "anthropic-version": "2023-06-01",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = json.loads(resp.read())
            text = "".join(b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text")

        parsed = json.loads(text.strip().strip("`").removeprefix("json").strip())

        root_cause = parsed.get("root_cause")
        strategy = parsed.get("recommended_strategy")
        if root_cause not in ALLOWED_ROOT_CAUSES or strategy not in ALLOWED_STRATEGIES:
            return None  # schema violation -> safe fallback, never execute unvalidated output
        confidence = float(parsed.get("root_cause_confidence", 0))
        if not (0 <= confidence <= 1):
            return None

        return Decision(
            root_cause=root_cause,
            root_cause_confidence=confidence,
            customer_context_summary=str(parsed.get("customer_context_summary", "")),
            recommended_strategy=strategy,
            reasoning_summary=str(parsed.get("reasoning_summary", "")),
            human_escalation_required=bool(parsed.get("human_escalation_required", False)),
            escalation_reason=parsed.get("escalation_reason"),
            strategy_scores=[{"strategy": strategy, "score": confidence, "reason": "LLM diagnosis"}],
        )
    except Exception:
        # Fail closed: any error routes to the deterministic engine, which
        # is always available and demo-safe. Never propagate an exception
        # up into the action layer, and never execute on partial output.
        return None


# ---- prioritization and summarization (spec section 24: dedicated AI service functions) ----

def prioritize_cases(cases: list) -> list:
    """Ranks open cases by recovery priority — plain arithmetic, same
    "deterministic factors in code" pattern as the strategy optimizer
    (app/policies/optimizer.py rank_recovery_strategies). Returns cases
    sorted highest-priority first with a `priority_score`.

    Favors larger amounts, higher diagnosis confidence, fewer attempts
    spent, cases open longer (so old ones don't starve), and customers
    with a weaker recovery health score — a customer who rarely self-cures
    a failed payment needs the agent to act, while a customer with a
    strong payment history is more likely to resolve it on their own."""
    from app.models import utcnow

    scored = []
    for c in cases:
        amount_score = min((c.amount_at_risk or 0) / 100000, 1.0)  # normalize around ~1L
        confidence_score = c.root_cause_confidence or 0.3
        attempts_left_score = max(0, 1 - (c.attempt_count or 0) / 3)
        age_days = (utcnow() - c.created_at).days if c.created_at else 0
        staleness_score = min(age_days / 7, 1.0)
        health = customer_health_score(c.customer) if c.customer else None
        health_risk_score = (100 - health["score"]) / 100 if health else 0.5

        priority = (
            0.35 * amount_score +
            0.25 * confidence_score +
            0.15 * attempts_left_score +
            0.10 * staleness_score +
            0.15 * health_risk_score
        )
        scored.append((round(priority, 4), c))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{"case": c, "priority_score": score} for score, c in scored]


def summarize_case(case) -> str:
    """Short, business-safe one-line summary for a case — no chain-of-thought,
    just the facts a human reviewer needs at a glance."""
    parts = [f"{case.currency} {case.amount_at_risk:,.0f} at risk"]
    if case.root_cause:
        parts.append(f"cause: {case.root_cause.replace('_', ' ')}")
    if case.recommended_strategy:
        parts.append(f"strategy: {case.recommended_strategy.replace('_', ' ')}")
    parts.append(f"status: {case.status}")
    if case.attempt_count:
        parts.append(f"{case.attempt_count} attempt(s)")
    return ". ".join(parts)


# ---- Customer Recovery Health Score (creative addition) --------------------

def customer_health_score(customer) -> dict:
    """0-100 score for how likely this customer is to self-resolve payment
    issues, from real history — not a black box. Gives a reviewer instant
    context without reading the full payment history. Informational only,
    doesn't gate policy or actions."""
    payments = customer.payments
    total = len(payments)
    successful = len([p for p in payments if p.status == "succeeded"])
    failed = len([p for p in payments if p.status == "failed"])

    if total == 0:
        return {"score": 50, "band": "unknown", "reason": "No payment history yet. Neutral score."}

    success_rate = successful / total
    recency_bonus = 10 if failed == 0 else 0
    volume_confidence = min(total / 10, 1.0) * 10  # more history = more confident signal

    raw_score = success_rate * 80 + recency_bonus + volume_confidence
    score = round(min(max(raw_score, 0), 100))

    if score >= 80:
        band, reason = "excellent", f"{successful}/{total} payments succeeded. Highly reliable payer."
    elif score >= 60:
        band, reason = "good", f"{successful}/{total} payments succeeded. Generally reliable."
    elif score >= 40:
        band, reason = "fair", f"{successful}/{total} payments succeeded. Mixed history, worth a closer look."
    else:
        band, reason = "at-risk", f"Only {successful}/{total} payments succeeded. Poor payment reliability."

    return {"score": score, "band": band, "reason": reason}


# ---- Recovery-probability ML model (spec sections 13-17, 47) ---------------
# Lazily loaded once per process. If the model artifacts aren't present
# (e.g. `python -m ml.train` hasn't been run), predict_recovery_probability
# returns None rather than crashing or fabricating a number — callers must
# handle the "no model available" case explicitly.

_ML_MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "models", "recovery_model_v1")
_ml_model = None
_ml_scaler = None
_ml_feature_columns = None
_ml_load_attempted = False


def _load_ml_model():
    global _ml_model, _ml_scaler, _ml_feature_columns, _ml_load_attempted
    if _ml_load_attempted:
        return
    _ml_load_attempted = True
    try:
        import joblib
        model_path = os.path.join(_ML_MODEL_DIR, "model.joblib")
        scaler_path = os.path.join(_ML_MODEL_DIR, "scaler.joblib")
        columns_path = os.path.join(_ML_MODEL_DIR, "feature_columns.json")
        if not (os.path.exists(model_path) and os.path.exists(scaler_path) and os.path.exists(columns_path)):
            return
        _ml_model = joblib.load(model_path)
        _ml_scaler = joblib.load(scaler_path)
        with open(columns_path) as f:
            _ml_feature_columns = json.load(f)
    except Exception:
        _ml_model = None  # fail closed — caller falls back to the rule-based confidence score


def predict_recovery_probability(customer_success_rate: float, customer_payment_count: int,
                                  amount: float, root_cause: str, strategy: str,
                                  attempt_count: int, days_since_last_payment: int) -> dict | None:
    """Returns {"probability": float, "explanation": [str, ...]} from the
    trained logistic-regression baseline, or None if untrained. Explanation
    comes from the model's real coefficients (coef * scaled value = that
    feature's contribution) — not a hand-written justification."""
    _load_ml_model()
    if _ml_model is None:
        return None

    import pandas as pd
    row = {
        "customer_success_rate": customer_success_rate,
        "customer_payment_count": customer_payment_count,
        "amount": amount,
        "attempt_count": attempt_count,
        "days_since_last_payment": days_since_last_payment,
    }
    for col in _ml_feature_columns:
        if col.startswith("root_cause_"):
            row[col] = 1 if col == f"root_cause_{root_cause}" else 0
        elif col.startswith("strategy_"):
            row[col] = 1 if col == f"strategy_{strategy}" else 0
    X = pd.DataFrame([row])[_ml_feature_columns]
    X_scaled = _ml_scaler.transform(X)

    probability = float(_ml_model.predict_proba(X_scaled)[0, 1])

    # Explainability: per-feature contribution = coefficient * scaled value.
    contributions = list(zip(_ml_feature_columns, _ml_model.coef_[0] * X_scaled[0]))
    contributions.sort(key=lambda c: abs(c[1]), reverse=True)
    explanation = []
    label_map = {
        "customer_success_rate": "customer's payment success rate",
        "customer_payment_count": "customer's payment history length",
        "amount": "transaction amount",
        "attempt_count": "number of prior recovery attempts",
        "days_since_last_payment": "recency of last payment",
    }
    for feature, contribution in contributions[:3]:
        direction = "increases" if contribution > 0 else "decreases"
        clean_name = label_map.get(feature, feature.replace("root_cause_", "root cause: ")
                                    .replace("strategy_", "strategy: ").replace("_", " "))
        explanation.append(f"{clean_name} {direction} recovery likelihood")

    return {"probability": round(probability, 4), "explanation": explanation}
