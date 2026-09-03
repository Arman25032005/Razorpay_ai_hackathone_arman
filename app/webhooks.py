"""
Webhook-first architecture. External systems (Stripe, Razorpay, a checkout
flow, an invoicing system) POST provider-shaped events here; this module
normalizes them into internal event types and hands off to the agent.

Accepts two shapes for payment events:
1. Our simplified internal shape: {event_type, provider, event_id,
   customer_email, amount, currency, failure_reason, payment_id}
2. Genuine Razorpay webhook shape (as documented at
   https://razorpay.com/docs/webhooks/payments/):
   {"entity": "event", "account_id": "acc_...", "event": "payment.failed",
    "contains": ["payment"],
    "payload": {"payment": {"entity": {"id": "pay_...", "amount": 500,
      (paise) "currency": "INR", "status": "failed", "email": "...",
      "contact": "...", "customer_id": "...", "error_code": "...",
      "error_reason": "...", "error_description": "...",
      "created_at": 1568781321}}},
    "created_at": 1568781323}
   `_normalize_razorpay_payload()` converts this into our internal shape
   before processing, including converting paise -> rupees and classifying
   Razorpay's real documented `error_reason` taxonomy (see
   app.decline_codes) into our internal root-cause categories.

Idempotency: every inbound event carries a provider `event_id` (or, for raw
Razorpay payloads with no top-level event_id, we derive one from the
payment id + event type, which is stable across redelivery). We record it
in `webhook_events` before doing anything else — a webhook that arrives
twice (a real-world guarantee, not an edge case) must not create a second
case, double-charge, or send a duplicate message.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Customer, Payment, Invoice, WebhookEvent, utcnow
from app.agents.orchestrator import create_case
from app.decline_codes import classify_decline

INTERNAL_EVENT_TYPES = {
    "payment.failed": "PAYMENT_FAILED",
    "payment.succeeded": "PAYMENT_SUCCEEDED",
    "payment.captured": "PAYMENT_SUCCEEDED",
    "checkout.abandoned": "CHECKOUT_ABANDONED",
    "subscription.failed": "SUBSCRIPTION_FAILED",
    "invoice.overdue": "INVOICE_OVERDUE",
}


def _normalize_razorpay_payload(payload: dict) -> dict:
    """Detects a genuine Razorpay webhook payload (entity == "event") and
    flattens it into our internal simplified shape. Payloads already in our
    simplified shape pass through unchanged."""
    if payload.get("entity") != "event":
        return payload  # already simplified / not Razorpay-shaped

    event_type = payload.get("event", "")
    payment_entity = (
        payload.get("payload", {}).get("payment", {}).get("entity", {})
    )
    # Classified from Razorpay's real documented error taxonomy (see
    # app.decline_codes) — the authoritative `error_reason` field is tried
    # first, falling back to error_code / error_description when a payload
    # doesn't carry it (e.g. older integrations, or our own simplified shape).
    failure_reason = classify_decline(
        reason=payment_entity.get("error_reason"),
        error_code=payment_entity.get("error_code"),
        description=payment_entity.get("error_description"),
    )

    amount_paise = payment_entity.get("amount", 0)
    event_id = f"razorpay:{payment_entity.get('id', 'unknown')}:{event_type}"

    return {
        "event_type": event_type,
        "provider": "razorpay",
        "event_id": event_id,
        "customer_email": payment_entity.get("email"),
        "customer_phone": payment_entity.get("contact"),
        "amount": amount_paise / 100,  # paise -> rupees
        "currency": payment_entity.get("currency", "INR"),
        "failure_reason": failure_reason,
        "payment_id": payment_entity.get("id"),
        "razorpay_customer_id": payment_entity.get("customer_id"),
        "razorpay_method": payment_entity.get("method"),
    }


class DuplicateEvent(Exception):
    pass


def _get_or_create_customer(db: Session, event: dict) -> Customer:
    email = event.get("customer_email")
    customer = None
    if email:
        customer = db.query(Customer).filter(Customer.email == email).first()
    if not customer:
        customer = Customer(
            name=event.get("customer_name") or email or "Unknown customer",
            email=email,
            phone=event.get("customer_phone"),
            company=event.get("company"),
            customer_type=event.get("customer_type", "B2C"),
            lifetime_value=event.get("lifetime_value", 0),
            risk_profile="low",
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)
    return customer


def _record_event(db: Session, provider: str, payload: dict) -> WebhookEvent:
    event_id = payload.get("event_id")
    if event_id:
        existing = db.query(WebhookEvent).filter(WebhookEvent.event_id == event_id).first()
        if existing:
            raise DuplicateEvent(f"Event {event_id} already processed -> case {existing.resulting_case_id}")
    record = WebhookEvent(provider=provider, event_id=event_id,
                           event_type=payload.get("event_type"), payload=payload)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def ingest_payment_event(db: Session, payload: dict) -> dict:
    """Normalizes a provider payment webhook (our simplified shape, or a
    genuine Razorpay-shaped payload — see _normalize_razorpay_payload)
    into PAYMENT_FAILED / PAYMENT_SUCCEEDED and opens or updates a case."""
    payload = _normalize_razorpay_payload(payload)
    try:
        record = _record_event(db, payload.get("provider", "unknown"), payload)
    except DuplicateEvent as e:
        return {"status": "duplicate", "detail": str(e)}

    internal_type = INTERNAL_EVENT_TYPES.get(payload.get("event_type"), "PAYMENT_FAILED")
    customer = _get_or_create_customer(db, payload)
    amount = float(payload.get("amount", 0))
    currency = payload.get("currency", "INR")

    payment = Payment(
        customer_id=customer.id, amount=amount, currency=currency,
        status="failed" if internal_type == "PAYMENT_FAILED" else "succeeded",
        failure_reason=payload.get("failure_reason"),
        provider=payload.get("provider", "unknown"),
        provider_payment_id=payload.get("payment_id", record.id),
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    if internal_type != "PAYMENT_FAILED":
        record.resulting_case_id = None
        db.commit()
        return {"status": "ok", "internal_event_type": internal_type, "case_id": None,
                "note": "Payment succeeded — no recovery case needed"}

    source_type = "subscription_failed" if payload.get("subscription_id") else "payment_failed"
    case = create_case(db, customer, source_type, payment.id, amount, currency,
                        risk_level="high" if amount >= 100000 else "medium")
    record.resulting_case_id = case.id
    db.commit()
    return {"status": "ok", "internal_event_type": internal_type, "case_id": case.id}


def ingest_checkout_event(db: Session, payload: dict) -> dict:
    try:
        record = _record_event(db, payload.get("provider", "checkout"), payload)
    except DuplicateEvent as e:
        return {"status": "duplicate", "detail": str(e)}

    customer = _get_or_create_customer(db, payload)
    amount = float(payload.get("amount", 0))
    currency = payload.get("currency", "INR")
    case = create_case(db, customer, "checkout_abandoned", payload.get("cart_id", record.id),
                        amount, currency, risk_level="medium")
    record.resulting_case_id = case.id
    db.commit()
    return {"status": "ok", "internal_event_type": "CHECKOUT_ABANDONED", "case_id": case.id}


def ingest_invoice_event(db: Session, payload: dict) -> dict:
    try:
        record = _record_event(db, payload.get("provider", "accounting"), payload)
    except DuplicateEvent as e:
        return {"status": "duplicate", "detail": str(e)}

    customer = _get_or_create_customer(db, {**payload, "customer_type": "B2B"})
    amount = float(payload.get("amount", 0))
    currency = payload.get("currency", "INR")
    due_date = payload.get("due_date")
    days_overdue = payload.get("days_overdue", 0)

    invoice = Invoice(
        customer_id=customer.id, invoice_number=payload.get("invoice_number", record.id),
        amount=amount, currency=currency,
        issue_date=utcnow(),
        due_date=datetime.fromisoformat(due_date) if due_date else utcnow(),
        status="overdue", days_overdue=days_overdue,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    case = create_case(db, customer, "invoice_overdue", invoice.id, amount, currency,
                        risk_level="high" if amount >= 100000 else "medium")
    record.resulting_case_id = case.id
    db.commit()
    return {"status": "ok", "internal_event_type": "INVOICE_OVERDUE", "case_id": case.id}
