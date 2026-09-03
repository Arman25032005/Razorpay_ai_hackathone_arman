import os
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException, Request, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import engine, get_db, SessionLocal
from app import models
from app.models import RecoveryCase, Customer, AuditEvent, RecoveryAction, Invoice, Payment, utcnow
from app.agents.orchestrator import execute_next_action, analyze_case
from app.simulation import engine as sim_engine
from app.simulation.engine import summarize_run
from app.policies.engine import DEFAULT_POLICY, get_active_policy
from app import webhooks as webhook_module
from app.models import PromiseToPay
from app.policies.optimizer import strategy_performance
from app.security import require_api_key, verify_webhook_signature, rate_limit
from app.agents.ai_service import summarize_case
from app.agents import ai_service

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="RecoverAI", description="AI Revenue Recovery Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


def case_to_dict(c: RecoveryCase) -> dict:
    return {
        "id": c.id,
        "customer_id": c.customer_id,
        "customer_name": c.customer.name if c.customer else None,
        "customer_type": c.customer.customer_type if c.customer else None,
        "source_type": c.source_type,
        "amount_at_risk": c.amount_at_risk,
        "currency": c.currency,
        "status": c.status,
        "risk_level": c.risk_level,
        "root_cause": c.root_cause,
        "root_cause_confidence": c.root_cause_confidence,
        "reasoning_summary": c.reasoning_summary,
        "recommended_strategy": c.recommended_strategy,
        "attempt_count": c.attempt_count,
        "amount_recovered": c.amount_recovered,
        "stop_reason": c.stop_reason,
        "human_escalation_required": c.human_escalation_required,
        "escalation_reason": c.escalation_reason,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
    }


# ---------------------------------------------------------------- dashboard
@app.get("/api/dashboard")
def dashboard(merchant_id: str | None = None, db: Session = Depends(get_db)):
    q = db.query(RecoveryCase)
    if merchant_id:
        q = q.join(Customer).filter(Customer.merchant_id == merchant_id)
    cases = q.all()
    if not cases:
        return {
            "revenue_at_risk": 0, "revenue_recovered": 0, "recovery_rate": 0,
            "active_cases": 0, "escalations": 0, "by_strategy": [], "by_status": [],
            "recent_actions": [], "total_action_cost": 0, "net_recovery_roi": 0, "roi_multiple": None,
        }
    at_risk = sum(c.amount_at_risk for c in cases if c.status not in ("RECOVERED",))
    recovered = sum(c.amount_recovered for c in cases)
    analyzed = sum(c.amount_at_risk for c in cases)
    active = len([c for c in cases if c.status in ("OPEN", "ANALYZING", "ACTION_READY", "EXECUTING")])
    escalations = len([c for c in cases if c.status == "ESCALATED"])

    # Recovery ROI: net of the actual operational cost of every action taken
    # (message sends, retry attempts, escalations) — not just gross revenue
    # recovered. Every action has a cost whether or not it worked, so this
    # sums over ALL actions on these cases, not just the ones that succeeded.
    from app.policies.expected_value import ACTION_COST_BY_TYPE
    case_ids = [c.id for c in cases]
    actions = (db.query(RecoveryAction.action_type, func.count(RecoveryAction.id))
               .filter(RecoveryAction.case_id.in_(case_ids))
               .group_by(RecoveryAction.action_type).all()) if case_ids else []
    total_action_cost = sum(ACTION_COST_BY_TYPE.get(action_type, 1.0) * count for action_type, count in actions)
    net_roi = recovered - total_action_cost

    by_strategy = {}
    for c in cases:
        if c.status == "RECOVERED" and c.recommended_strategy:
            by_strategy[c.recommended_strategy] = by_strategy.get(c.recommended_strategy, 0) + c.amount_recovered
    by_strategy_list = [{"strategy": k, "amount": round(v, 2)} for k, v in sorted(by_strategy.items(), key=lambda x: -x[1])]

    by_status = {}
    for c in cases:
        by_status[c.status] = by_status.get(c.status, 0) + 1
    by_status_list = [{"status": k, "count": v} for k, v in by_status.items()]

    if merchant_id:
        case_ids = [c.id for c in cases]
        recent = (db.query(AuditEvent).filter(AuditEvent.case_id.in_(case_ids))
                  .order_by(AuditEvent.timestamp.desc()).limit(15).all())
    else:
        recent = db.query(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(15).all()
    recent_list = [{
        "case_id": e.case_id, "actor_type": e.actor_type, "action": e.action,
        "description": e.description, "timestamp": e.timestamp.isoformat(),
    } for e in recent]

    return {
        "revenue_at_risk": round(at_risk, 2),
        "revenue_recovered": round(recovered, 2),
        "revenue_analyzed": round(analyzed, 2),
        "recovery_rate": round((recovered / analyzed * 100), 1) if analyzed else 0,
        "active_cases": active,
        "escalations": escalations,
        "total_cases": len(cases),
        "total_action_cost": round(total_action_cost, 2),
        "net_recovery_roi": round(net_roi, 2),
        "roi_multiple": round(recovered / total_action_cost, 1) if total_action_cost else None,
        "by_strategy": by_strategy_list,
        "by_status": by_status_list,
        "recent_actions": recent_list,
    }


# ------------------------------------------------------------ recovery cases
@app.get("/api/recovery-cases")
def list_cases(status: str | None = None, source_type: str | None = None, merchant_id: str | None = None,
                prioritized: bool = False, db: Session = Depends(get_db)):
    q = db.query(RecoveryCase)
    if merchant_id:
        q = q.join(Customer).filter(Customer.merchant_id == merchant_id)
    if status:
        q = q.filter(RecoveryCase.status == status)
    if source_type:
        q = q.filter(RecoveryCase.source_type == source_type)
    cases = q.order_by(RecoveryCase.created_at.desc()).all()
    if prioritized:
        from app.agents.ai_service import prioritize_cases
        ranked = prioritize_cases(cases)
        return [{**case_to_dict(r["case"]), "priority_score": r["priority_score"]} for r in ranked]
    return [case_to_dict(c) for c in cases]


@app.get("/api/recovery-cases/export.csv")
def export_cases_csv(status: str | None = None, source_type: str | None = None,
                      merchant_id: str | None = None, db: Session = Depends(get_db)):
    """Exports the current case list as CSV — for handing recovery data to
    finance/ops teams outside the dashboard. Registered before the
    /{case_id} route so 'export.csv' is never mistaken for a case ID."""
    import csv
    import io

    q = db.query(RecoveryCase)
    if merchant_id:
        q = q.join(Customer).filter(Customer.merchant_id == merchant_id)
    if status:
        q = q.filter(RecoveryCase.status == status)
    if source_type:
        q = q.filter(RecoveryCase.source_type == source_type)
    cases = q.order_by(RecoveryCase.created_at.desc()).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "case_id", "customer_name", "customer_type", "source_type", "amount_at_risk",
        "currency", "status", "risk_level", "root_cause", "root_cause_confidence",
        "recommended_strategy", "attempt_count", "amount_recovered", "stop_reason",
        "human_escalation_required", "escalation_reason", "created_at", "resolved_at",
    ])
    for c in cases:
        writer.writerow([
            c.id, c.customer.name if c.customer else "", c.customer.customer_type if c.customer else "",
            c.source_type, c.amount_at_risk, c.currency, c.status, c.risk_level, c.root_cause or "",
            c.root_cause_confidence or "", c.recommended_strategy or "", c.attempt_count,
            c.amount_recovered, c.stop_reason or "", c.human_escalation_required,
            c.escalation_reason or "", c.created_at.isoformat() if c.created_at else "",
            c.resolved_at.isoformat() if c.resolved_at else "",
        ])

    return Response(
        content=buffer.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=recoverai_cases_{utcnow().strftime('%Y%m%d_%H%M%S')}.csv"},
    )


@app.get("/api/recovery-cases/{case_id}")
def get_case(case_id: str, db: Session = Depends(get_db)):
    c = db.query(RecoveryCase).filter(RecoveryCase.id == case_id).first()
    if not c:
        raise HTTPException(404, "Case not found")
    actions = db.query(RecoveryAction).filter(RecoveryAction.case_id == case_id).order_by(RecoveryAction.executed_at).all()
    audit = db.query(AuditEvent).filter(AuditEvent.case_id == case_id).order_by(AuditEvent.timestamp).all()
    customer = c.customer
    from app.agents.ai_service import customer_health_score, predict_recovery_probability
    from app.agents.orchestrator import build_customer_context

    ml_prediction = None
    if customer and c.root_cause:
        ctx = build_customer_context(db, customer)
        ml_prediction = predict_recovery_probability(
            customer_success_rate=ctx["success_rate"], customer_payment_count=ctx["payment_count"],
            amount=c.amount_at_risk, root_cause=c.root_cause,
            strategy=c.recommended_strategy or "delayed_retry",
            attempt_count=c.attempt_count, days_since_last_payment=ctx["days_since_last_payment"],
        )

    expected_value = None
    if c.recommended_strategy:
        from app.policies.expected_value import compute_expected_value
        from app.agents.orchestrator import ACTION_TYPE_BY_STRATEGY
        probability = ml_prediction["probability"] if ml_prediction else (c.root_cause_confidence or 0.5)
        action_type = ACTION_TYPE_BY_STRATEGY.get(c.recommended_strategy, "send_message")
        ev = compute_expected_value(probability, c.amount_at_risk, action_type,
                                     strategy=c.recommended_strategy, prior_attempts=c.attempt_count)
        expected_value = {
            "expected_value": ev.expected_value, "probability_used": ev.probability_used,
            "action_cost": ev.action_cost, "annoyance_cost": ev.annoyance_cost,
            "risk_cost": ev.risk_cost, "recommendation": ev.recommendation,
        }

    return {
        **case_to_dict(c),
        "customer": {
            "id": customer.id, "name": customer.name, "email": customer.email,
            "company": customer.company, "lifetime_value": customer.lifetime_value,
            "risk_profile": customer.risk_profile,
            "health_score": customer_health_score(customer),
        } if customer else None,
        "ml_prediction": ml_prediction,
        "expected_value": expected_value,
        "actions": [{
            "id": a.id, "action_type": a.action_type, "channel": a.channel, "status": a.status,
            "message_body": a.message_body, "result": a.result, "amount_recovered": a.amount_recovered,
            "executed_at": a.executed_at.isoformat(),
        } for a in actions],
        "audit_trail": [{
            "actor_type": e.actor_type, "action": e.action, "description": e.description,
            "timestamp": e.timestamp.isoformat(), "metadata": e.event_metadata,
        } for e in audit],
        "policy": get_active_policy(db),
        "summary": summarize_case(c),
    }


@app.get("/api/models/current")
def current_model():
    """Spec section 45: reports the deployed model's version, training date,
    and real eval metrics — read straight from metrics.json (python -m
    ml.train), never fabricated. Says so honestly if nothing's trained yet."""
    import json
    metrics_path = os.path.join("models", "recovery_model_v1", "metrics.json")
    if not os.path.exists(metrics_path):
        return {"status": "no_model_trained", "message": "Run `python -m ml.train` to train the recovery-probability baseline."}
    with open(metrics_path) as f:
        metrics = json.load(f)
    return {
        "status": "active",
        "model_version": "recovery_model_v1",
        "model_type": metrics["model"],
        "trained_at": metrics["trained_at"],
        "training_rows": metrics["n_train"],
        "test_rows": metrics["n_test"],
        "split_method": metrics["split_method"],
        "metrics": metrics["test_metrics"],
        "business_metrics": metrics["business_metrics_on_test_set"],
    }


@app.post("/api/recovery-cases/{case_id}/analyze", dependencies=[Depends(require_api_key)])
def analyze(case_id: str, db: Session = Depends(get_db)):
    c = db.query(RecoveryCase).filter(RecoveryCase.id == case_id).first()
    if not c:
        raise HTTPException(404, "Case not found")
    kwargs = {}
    if c.source_type == "payment_failed":
        p = db.query(Payment).filter(Payment.id == c.source_id).first()
        kwargs["failure_reason"] = p.failure_reason if p else None
    elif c.source_type == "invoice_overdue":
        inv = db.query(Invoice).filter(Invoice.id == c.source_id).first()
        kwargs["days_overdue"] = inv.days_overdue if inv else 0
    analyze_case(db, c, **kwargs)
    return case_to_dict(c)


@app.post("/api/recovery-cases/{case_id}/execute", dependencies=[Depends(require_api_key)])
def execute(case_id: str, db: Session = Depends(get_db)):
    c = db.query(RecoveryCase).filter(RecoveryCase.id == case_id).first()
    if not c:
        raise HTTPException(404, "Case not found")
    execute_next_action(db, c)
    return case_to_dict(c)


@app.post("/api/recovery-cases/{case_id}/approve", dependencies=[Depends(require_api_key)])
def approve(case_id: str, db: Session = Depends(get_db)):
    c = db.query(RecoveryCase).filter(RecoveryCase.id == case_id).first()
    if not c:
        raise HTTPException(404, "Case not found")
    c.human_escalation_required = False
    c.human_approved = True
    c.status = "ACTION_READY"
    db.commit()
    ev = AuditEvent(case_id=c.id, actor_type="USER", action="human_approved",
                     description="Human reviewer approved continued action")
    db.add(ev)
    db.commit()
    execute_next_action(db, c)
    return case_to_dict(c)


@app.post("/api/recovery-cases/{case_id}/reject", dependencies=[Depends(require_api_key)])
def reject(case_id: str, db: Session = Depends(get_db)):
    c = db.query(RecoveryCase).filter(RecoveryCase.id == case_id).first()
    if not c:
        raise HTTPException(404, "Case not found")
    c.status = "STOPPED"
    c.stop_reason = "HUMAN_REJECTED"
    db.commit()
    ev = AuditEvent(case_id=c.id, actor_type="USER", action="human_rejected",
                     description="Human reviewer closed the case without further action")
    db.add(ev)
    db.commit()
    return case_to_dict(c)


@app.post("/api/recovery-cases/{case_id}/send-payment-link", dependencies=[Depends(require_api_key)])
def send_payment_link(case_id: str, db: Session = Depends(get_db)):
    """Manually triggers a payment link for this case — the same
    create_payment_link() call the agent uses internally, exposed as a
    direct action so a human reviewer can send one on demand (e.g. from
    the escalation queue) without waiting for the next automated attempt.
    Uses whichever provider is active: real Razorpay if RAZORPAY_KEY_ID /
    RAZORPAY_KEY_SECRET are set, otherwise the mock provider."""
    from app.providers.payment import payment_provider
    c = db.query(RecoveryCase).filter(RecoveryCase.id == case_id).first()
    if not c:
        raise HTTPException(404, "Case not found")

    customer = {"name": c.customer.name, "email": c.customer.email, "contact": c.customer.phone}
    result = payment_provider.create_payment_link(
        c.amount_at_risk, c.currency, customer=customer, description=f"Payment recovery for {c.id}")

    action = RecoveryAction(case_id=c.id, action_type="send_payment_link", channel="email",
                             status="sent" if result.get("status") != "failed" else "failed",
                             result=result.get("status"), provider_response=result)
    db.add(action)
    db.commit()

    ev = AuditEvent(case_id=c.id, actor_type="USER", action="payment_link_sent",
                     description=f"Human reviewer manually sent a payment link ({result.get('provider')}): {result.get('link', 'link generation failed')}",
                     event_metadata=result)
    db.add(ev)
    db.commit()
    return {"case_id": c.id, **result}


@app.post("/api/create-order", dependencies=[Depends(require_api_key)])
def create_order(payload: dict):
    """Creates a Razorpay Order for Standard Checkout (the inline JS modal,
    as opposed to send-payment-link's hosted redirect link). Body:
    { amount (paise, int, >=100), currency (default INR), receipt (optional) }.
    Returns the order id plus the public key_id the frontend needs to open
    the checkout modal — key_secret never leaves the server."""
    from app.providers.payment import payment_provider, RazorpayPaymentProvider
    if not isinstance(payment_provider, RazorpayPaymentProvider):
        raise HTTPException(500, "Razorpay is not configured (set RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET)")

    amount = payload.get("amount")
    currency = payload.get("currency", "INR")
    receipt = payload.get("receipt")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 100:
        raise HTTPException(400, "amount must be an integer number of paise, >= 100")

    result = payment_provider.create_order(amount, currency, receipt)
    if result.get("status") == "failed":
        status_code = 401 if result.get("status_code") == 401 else 500
        raise HTTPException(status_code, (result.get("error") or {}).get("description", "Order creation failed"))
    return {
        "order_id": result["order_id"],
        "amount": result["amount"],
        "currency": result["currency"],
        "key_id": payment_provider.key_id,
    }


@app.post("/api/verify-payment", dependencies=[Depends(require_api_key)])
def verify_payment(payload: dict):
    """Verifies the razorpay_signature returned by Standard Checkout after
    a successful payment. Body: { razorpay_order_id, razorpay_payment_id,
    razorpay_signature }. Returns success only if the HMAC-SHA256 signature
    matches — a mismatch means the payment must NOT be trusted as paid."""
    from app.providers.payment import payment_provider, RazorpayPaymentProvider, verify_payment_signature
    order_id = payload.get("razorpay_order_id")
    payment_id = payload.get("razorpay_payment_id")
    signature = payload.get("razorpay_signature")
    if not order_id or not payment_id or not signature:
        raise HTTPException(400, "razorpay_order_id, razorpay_payment_id and razorpay_signature are required")
    if not isinstance(payment_provider, RazorpayPaymentProvider):
        raise HTTPException(500, "Razorpay is not configured (set RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET)")

    if not verify_payment_signature(order_id, payment_id, signature, payment_provider.key_secret):
        raise HTTPException(400, "Payment signature verification failed")
    return {"verified": True, "order_id": order_id, "payment_id": payment_id}


# --------------------------------------------------------------------- audit
@app.get("/api/audit")
def audit_log(case_id: str | None = None, db: Session = Depends(get_db)):
    q = db.query(AuditEvent)
    if case_id:
        q = q.filter(AuditEvent.case_id == case_id)
    events = q.order_by(AuditEvent.timestamp.desc()).limit(200).all()
    return [{
        "case_id": e.case_id, "actor_type": e.actor_type, "action": e.action,
        "description": e.description, "timestamp": e.timestamp.isoformat(),
    } for e in events]


# ----------------------------------------------------------------- analytics
@app.get("/api/analytics")
def analytics(merchant_id: str | None = None, db: Session = Depends(get_db)):
    q = db.query(RecoveryCase)
    if merchant_id:
        q = q.join(Customer).filter(Customer.merchant_id == merchant_id)
    cases = q.all()
    by_segment = {}
    for c in cases:
        seg = c.customer.customer_type if c.customer else "unknown"
        by_segment.setdefault(seg, {"at_risk": 0, "recovered": 0})
        by_segment[seg]["recovered"] += c.amount_recovered
        if c.status != "RECOVERED":
            by_segment[seg]["at_risk"] += c.amount_at_risk

    automated = len([c for c in cases if c.status == "RECOVERED"])
    escalated = len([c for c in cases if c.status == "ESCALATED"])
    total_resolved = automated + escalated
    return {
        "by_segment": [{"segment": k, **v} for k, v in by_segment.items()],
        "automated_vs_human": {
            "automated": automated, "human_escalated": escalated,
            "automated_rate": round(automated / total_resolved * 100, 1) if total_resolved else 0,
        },
        "strategy_performance": strategy_performance(db),
        "summary": summarize_run(db),
    }


@app.get("/api/analytics/strategy-performance")
def strategy_performance_endpoint(db: Session = Depends(get_db)):
    """Recovery Policy Optimizer: statistical (non-ML) ranking of which
    strategies actually recover the most money, computed from real case
    outcomes."""
    return strategy_performance(db)


@app.get("/api/integrations/status")
def integrations_status():
    """Reports real, live configuration state — not a hardcoded display
    array. Each entry reflects an actual environment variable check, so
    the Integrations tab tells the truth about what's connected."""
    razorpay_configured = bool(os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET"))
    from app.providers.payment import payment_provider, RazorpayPaymentProvider
    active_payment_provider = "razorpay" if isinstance(payment_provider, RazorpayPaymentProvider) else "mock"
    return {
        "payment_provider": {
            "active": active_payment_provider,
            "razorpay_configured": razorpay_configured,
            "mode": "live/test (per your Razorpay dashboard keys)" if razorpay_configured else "demo (mock)",
        },
        "webhook_signature_verification": {
            "enabled": bool(os.getenv("PAYMENT_WEBHOOK_SECRET")),
        },
        "llm_diagnosis": {
            "enabled": bool(os.getenv("LLM_API_KEY")),
            "provider": ai_service.LLM_PROVIDER if os.getenv("LLM_API_KEY") else None,
            "mode": (f"real LLM ({ai_service.LLM_PROVIDER})" if os.getenv("LLM_API_KEY")
                     else "deterministic rule engine"),
        },
        "api_auth": {
            "enabled": bool(os.getenv("API_KEY")),
        },
        "communication_provider": {
            "email": "sendgrid" if os.getenv("SENDGRID_API_KEY") and os.getenv("SENDGRID_FROM_EMAIL") else "mock",
            "whatsapp": (
                "meta_cloud_api" if os.getenv("META_WHATSAPP_TOKEN") and os.getenv("META_WHATSAPP_PHONE_NUMBER_ID")
                else "twilio" if os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN") and os.getenv("TWILIO_WHATSAPP_FROM")
                else "mock"
            ),
        },
        "database": {
            "type": "postgresql" if "postgres" in os.getenv("DATABASE_URL", "sqlite") else "sqlite",
        },
    }


# ------------------------------------------------------------------ policies
@app.get("/api/policies")
def get_policies(db: Session = Depends(get_db)):
    from app.policies.engine import get_active_policy
    return get_active_policy(db)


@app.put("/api/policies", dependencies=[Depends(require_api_key)])
def update_policies(payload: dict, db: Session = Depends(get_db)):
    """Persists operator-edited policy limits. Takes effect on the next
    policy check — see app.policies.engine.get_active_policy(). Values not
    present in the payload keep their current setting."""
    from app.models import RecoveryPolicy
    row = db.query(RecoveryPolicy).filter(RecoveryPolicy.name == "default").first()
    if not row:
        row = RecoveryPolicy(name="default")
        db.add(row)

    if "max_attempts" in payload:
        row.max_attempts = int(payload["max_attempts"])
    if "max_workflow_days" in payload:
        row.max_days = int(payload["max_workflow_days"])
    if "max_discount_percent" in payload:
        row.max_discount_percent = int(payload["max_discount_percent"])
    if "allowed_channels" in payload:
        row.allowed_channels = payload["allowed_channels"]
    if "require_human_approval_for_discount" in payload:
        row.require_human_approval_for_discount = bool(payload["require_human_approval_for_discount"])
    if "require_human_approval_for_large_amount" in payload:
        row.require_human_approval_for_large_amount = bool(payload["require_human_approval_for_large_amount"])
    if "large_amount_threshold" in payload:
        row.large_amount_threshold = float(payload["large_amount_threshold"])

    db.commit()
    from app.policies.engine import get_active_policy
    return get_active_policy(db)


# ------------------------------------------------------------------ merchants
@app.get("/api/merchants")
def list_merchants(db: Session = Depends(get_db)):
    """Multi-tenant merchant list, with a live per-merchant case count so
    the UI's merchant switcher can show 'Zenith Fashion Co. (14 cases)'
    rather than a blind dropdown."""
    merchants = db.query(models.Merchant).order_by(models.Merchant.created_at).all()
    result = []
    for m in merchants:
        case_count = (db.query(RecoveryCase).join(Customer)
                      .filter(Customer.merchant_id == m.id).count())
        customer_count = db.query(Customer).filter(Customer.merchant_id == m.id).count()
        result.append({
            "id": m.id, "name": m.name, "industry": m.industry,
            "razorpay_account_id": m.razorpay_account_id,
            "customer_count": customer_count, "case_count": case_count,
        })
    return result


# ---------------------------------------------------------- outbound webhooks
VALID_MERCHANT_EVENT_TYPES = {"case.opened", "case.recovered", "case.escalated", "case.stopped"}


@app.get("/api/merchants/{merchant_id}/webhooks")
def list_merchant_webhooks(merchant_id: str, db: Session = Depends(get_db)):
    """Lists this merchant's outbound webhook subscriptions. Secrets are
    never returned after creation — a subscription's HMAC key is shown once,
    at creation time, same as most webhook providers (Stripe, Razorpay
    included) handle it."""
    subs = (db.query(models.MerchantWebhookSubscription)
            .filter(models.MerchantWebhookSubscription.merchant_id == merchant_id)
            .order_by(models.MerchantWebhookSubscription.created_at.desc()).all())
    return [{
        "id": s.id, "url": s.url, "event_types": s.event_types, "active": s.active,
        "created_at": s.created_at.isoformat(),
    } for s in subs]


@app.post("/api/merchants/{merchant_id}/webhooks", dependencies=[Depends(require_api_key)])
def create_merchant_webhook(merchant_id: str, payload: dict, db: Session = Depends(get_db)):
    merchant = db.query(models.Merchant).filter(models.Merchant.id == merchant_id).first()
    if not merchant:
        raise HTTPException(404, "Merchant not found")
    url = payload.get("url")
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "A valid http(s) 'url' is required")
    event_types = payload.get("event_types") or list(VALID_MERCHANT_EVENT_TYPES)
    invalid = set(event_types) - VALID_MERCHANT_EVENT_TYPES
    if invalid:
        raise HTTPException(400, f"Unknown event_types: {sorted(invalid)}. Valid: {sorted(VALID_MERCHANT_EVENT_TYPES)}")

    from app.outbound_webhooks import generate_secret
    secret = generate_secret()
    sub = models.MerchantWebhookSubscription(
        merchant_id=merchant_id, url=url, secret=secret, event_types=event_types)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {
        "id": sub.id, "url": sub.url, "event_types": sub.event_types, "active": sub.active,
        "secret": secret,  # shown once — store it now, it won't be returned again
    }


@app.delete("/api/merchants/{merchant_id}/webhooks/{webhook_id}", dependencies=[Depends(require_api_key)])
def delete_merchant_webhook(merchant_id: str, webhook_id: str, db: Session = Depends(get_db)):
    sub = (db.query(models.MerchantWebhookSubscription)
           .filter(models.MerchantWebhookSubscription.id == webhook_id,
                    models.MerchantWebhookSubscription.merchant_id == merchant_id).first())
    if not sub:
        raise HTTPException(404, "Webhook subscription not found")
    db.delete(sub)
    db.commit()
    return {"status": "deleted"}


@app.get("/api/merchants/{merchant_id}/webhooks/{webhook_id}/deliveries")
def list_webhook_deliveries(merchant_id: str, webhook_id: str, db: Session = Depends(get_db)):
    sub = (db.query(models.MerchantWebhookSubscription)
           .filter(models.MerchantWebhookSubscription.id == webhook_id,
                    models.MerchantWebhookSubscription.merchant_id == merchant_id).first())
    if not sub:
        raise HTTPException(404, "Webhook subscription not found")
    deliveries = (db.query(models.WebhookDelivery)
                  .filter(models.WebhookDelivery.subscription_id == webhook_id)
                  .order_by(models.WebhookDelivery.attempted_at.desc()).limit(50).all())
    return [{
        "id": d.id, "event_type": d.event_type, "case_id": d.case_id, "success": d.success,
        "status_code": d.status_code, "error": d.error, "attempted_at": d.attempted_at.isoformat(),
    } for d in deliveries]


# ------------------------------------------------------------------ customers
@app.get("/api/customers/{customer_id}")
def get_customer(customer_id: str, db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    return {
        "id": c.id, "name": c.name, "email": c.email, "company": c.company,
        "customer_type": c.customer_type, "lifetime_value": c.lifetime_value,
        "risk_profile": c.risk_profile,
        "payments": [{"amount": p.amount, "status": p.status, "created_at": p.created_at.isoformat()} for p in c.payments],
    }


@app.get("/api/invoices")
def list_invoices(status: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    q = db.query(Invoice)
    if status:
        q = q.filter(Invoice.status == status)
    invoices = q.order_by(Invoice.due_date.desc()).limit(limit).all()
    return [{
        "id": i.id, "customer_id": i.customer_id,
        "customer_name": i.customer.name if i.customer else None,
        "invoice_number": i.invoice_number, "amount": i.amount, "currency": i.currency,
        "issue_date": i.issue_date.isoformat() if i.issue_date else None,
        "due_date": i.due_date.isoformat() if i.due_date else None,
        "status": i.status, "days_overdue": i.days_overdue,
    } for i in invoices]


@app.get("/api/payments")
def list_payments(status: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    q = db.query(Payment)
    if status:
        q = q.filter(Payment.status == status)
    payments = q.order_by(Payment.created_at.desc()).limit(limit).all()
    return [{
        "id": p.id, "customer_id": p.customer_id,
        "customer_name": p.customer.name if p.customer else None,
        "amount": p.amount, "currency": p.currency, "status": p.status,
        "failure_reason": p.failure_reason, "provider": p.provider,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    } for p in payments]


# --------------------------------------------------------------- webhooks
# Webhook-first architecture: external systems notify RecoverAI of revenue
# events; normalized into internal event types (see app/webhooks.py).
# Idempotent by payload["event_id"] — a webhook delivered twice never
# double-charges or opens a duplicate case.

@app.post("/api/webhooks/payment")
async def webhook_payment(request: Request, db: Session = Depends(get_db),
                           x_webhook_signature: str | None = Header(default=None),
                           x_razorpay_signature: str | None = Header(default=None)):
    """Expects our simplified shape ({event_type, provider, event_id,
    customer_email, amount, currency, failure_reason, payment_id,
    subscription_id?}) OR a genuine Razorpay payment-event webhook payload
    (auto-detected and normalized — see app/webhooks.py). If
    PAYMENT_WEBHOOK_SECRET is set, requires a valid signature via either
    X-Webhook-Signature (generic) or X-Razorpay-Signature (Razorpay's
    actual header name), HMAC-SHA256 of the raw body."""
    rate_limit(request, "webhook")
    raw = await request.body()
    verify_webhook_signature(raw, x_razorpay_signature or x_webhook_signature)
    import json
    payload = json.loads(raw)
    return webhook_module.ingest_payment_event(db, payload)


@app.post("/api/webhooks/checkout")
async def webhook_checkout(request: Request, db: Session = Depends(get_db),
                            x_webhook_signature: str | None = Header(default=None),
                            x_razorpay_signature: str | None = Header(default=None)):
    """Expects: {provider, event_id, customer_email, amount, currency, cart_id}"""
    rate_limit(request, "webhook")
    raw = await request.body()
    verify_webhook_signature(raw, x_razorpay_signature or x_webhook_signature)
    import json
    payload = json.loads(raw)
    return webhook_module.ingest_checkout_event(db, payload)


@app.post("/api/webhooks/invoice")
async def webhook_invoice(request: Request, db: Session = Depends(get_db),
                           x_webhook_signature: str | None = Header(default=None),
                           x_razorpay_signature: str | None = Header(default=None)):
    """Expects: {provider, event_id, customer_email, company, amount,
    currency, invoice_number, due_date, days_overdue}"""
    rate_limit(request, "webhook")
    raw = await request.body()
    verify_webhook_signature(raw, x_razorpay_signature or x_webhook_signature)
    import json
    payload = json.loads(raw)
    return webhook_module.ingest_invoice_event(db, payload)


@app.post("/api/events")
async def generic_event(request: Request, db: Session = Depends(get_db),
                         x_webhook_signature: str | None = Header(default=None),
                         x_razorpay_signature: str | None = Header(default=None)):
    """Generic ingestion point that routes by event_type prefix."""
    rate_limit(request, "webhook")
    raw = await request.body()
    verify_webhook_signature(raw, x_razorpay_signature or x_webhook_signature)
    import json
    payload = json.loads(raw)
    et = payload.get("event_type", "")
    if et.startswith("payment.") or et.startswith("subscription."):
        return webhook_module.ingest_payment_event(db, payload)
    if et.startswith("checkout."):
        return webhook_module.ingest_checkout_event(db, payload)
    if et.startswith("invoice."):
        return webhook_module.ingest_invoice_event(db, payload)
    raise HTTPException(400, f"Unrecognized event_type: {et}")


# ----------------------------------------------------------- promise to pay
@app.post("/api/recovery-cases/{case_id}/promise-to-pay", dependencies=[Depends(require_api_key)])
def create_promise(case_id: str, promised_amount: float, promised_date: str, db: Session = Depends(get_db)):
    c = db.query(RecoveryCase).filter(RecoveryCase.id == case_id).first()
    if not c:
        raise HTTPException(404, "Case not found")
    ptp = PromiseToPay(case_id=case_id, promised_amount=promised_amount,
                        promised_date=datetime.fromisoformat(promised_date), status="open")
    db.add(ptp)
    db.commit()
    ev = AuditEvent(case_id=case_id, actor_type="CUSTOMER", action="promise_to_pay_created",
                     description=f"Customer promised to pay {c.currency} {promised_amount:,.0f} by {promised_date}")
    db.add(ev)
    db.commit()
    return {"id": ptp.id, "status": ptp.status}


@app.post("/api/promises/{promise_id}/fulfill", dependencies=[Depends(require_api_key)])
def fulfill_promise(promise_id: str, db: Session = Depends(get_db)):
    ptp = db.query(PromiseToPay).filter(PromiseToPay.id == promise_id).first()
    if not ptp:
        raise HTTPException(404, "Promise not found")
    ptp.status = "fulfilled"
    ptp.fulfilled_at = utcnow()
    c = db.query(RecoveryCase).filter(RecoveryCase.id == ptp.case_id).first()
    if c:
        c.status = "RECOVERED"
        c.amount_recovered = ptp.promised_amount
        c.stop_reason = "PAYMENT_RECOVERED"
        c.resolved_at = utcnow()
        db.add(AuditEvent(case_id=c.id, actor_type="CUSTOMER", action="promise_fulfilled",
                           description=f"Promise-to-pay fulfilled — {c.currency} {ptp.promised_amount:,.0f} recovered"))
        db.add(AuditEvent(case_id=c.id, actor_type="SYSTEM", action="workflow_stopped",
                           description="Workflow stopped. Reason: PAYMENT_RECOVERED"))
    db.commit()
    return {"status": "fulfilled"}


@app.post("/api/promises/{promise_id}/break", dependencies=[Depends(require_api_key)])
def break_promise(promise_id: str, db: Session = Depends(get_db)):
    """Promise broken -> agent re-evaluates: stronger action or escalation."""
    ptp = db.query(PromiseToPay).filter(PromiseToPay.id == promise_id).first()
    if not ptp:
        raise HTTPException(404, "Promise not found")
    ptp.status = "broken"
    c = db.query(RecoveryCase).filter(RecoveryCase.id == ptp.case_id).first()
    if c:
        db.add(AuditEvent(case_id=c.id, actor_type="SYSTEM", action="promise_broken",
                           description="Promise-to-pay was not honored by the promised date"))
        db.commit()
        c.recommended_strategy = "invoice_escalation"
        execute_next_action(db, c)
    db.commit()
    return {"status": "broken", "case_status": c.status if c else None}


# ---------------------------------------------------------------- simulation
@app.post("/api/simulation/run")
def run_simulation(request: Request, n_customers: int = 60, db: Session = Depends(get_db)):
    rate_limit(request, "simulation")
    n_customers = min(n_customers, 2000)  # guardrail against accidental/abusive huge batches
    batch = sim_engine.generate_batch(db, n_customers=n_customers)
    result = sim_engine.run_agent_on_batch(db, batch["cases"])
    result["events_created"] = batch["events_created"]
    result["customers_created"] = batch["customers_created"]
    return result


@app.post("/api/simulation/reset", dependencies=[Depends(require_api_key)])
def reset_simulation(db: Session = Depends(get_db)):
    for model in [models.WebhookEvent, models.AuditEvent, models.RecoveryAction, models.PromiseToPay,
                  models.RecoveryCase, models.Invoice, models.Payment,
                  models.Subscription, models.Customer]:
        db.query(model).delete()
    db.commit()
    return {"status": "reset"}


@app.get("/api/simulation/hero-case")
def hero_case(db: Session = Depends(get_db)):
    c = db.query(RecoveryCase).join(Customer).filter(Customer.name == "Acme Software").order_by(RecoveryCase.created_at.desc()).first()
    if not c:
        raise HTTPException(404, "Run a simulation first")
    return get_case(c.id, db)


@app.post("/api/simulation/replay-best")
def replay_best(db: Session = Depends(get_db)):
    """Runs the hero case (Acme Software) through the full loop from
    scratch — approve + execute to completion — so the demo can show
    detect -> diagnose -> decide -> policy -> act -> payment -> recover -> stop
    in one call. Returns the full case detail with timeline."""
    c = db.query(RecoveryCase).join(Customer).filter(Customer.name == "Acme Software").order_by(RecoveryCase.created_at.desc()).first()
    if not c:
        raise HTTPException(404, "Run a simulation first to generate the hero case")
    if c.status not in ("RECOVERED",):
        if c.human_escalation_required or c.status == "ESCALATED":
            approve(c.id, db)
        guard = 0
        while c.status not in ("RECOVERED", "STOPPED") and guard < 5:
            execute_next_action(db, c)
            guard += 1
    return get_case(c.id, db)
