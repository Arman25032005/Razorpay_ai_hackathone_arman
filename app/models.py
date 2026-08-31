"""
RecoverAI — SQLAlchemy data model.
PostgreSQL in production (see DATABASE_URL in .env.example); SQLite locally
for zero-setup hackathon demoing — the ORM layer is DB-agnostic.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, JSON
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def gen_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16].upper()}"


def utcnow() -> datetime:
    """Naive UTC now, via the non-deprecated datetime.now(timezone.utc) API.
    Stripped back to naive because our DateTime columns and existing
    comparisons (e.g. `utcnow() - case.created_at`) are naive throughout —
    mixing in an aware value would raise on subtraction."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CaseStatus(str, enum.Enum):
    OPEN = "OPEN"
    ANALYZING = "ANALYZING"
    ACTION_READY = "ACTION_READY"
    EXECUTING = "EXECUTING"
    RECOVERED = "RECOVERED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"
    STOPPED = "STOPPED"
    EXPIRED = "EXPIRED"


class Merchant(Base):
    """Multi-tenant root entity. Every Customer belongs to exactly one
    Merchant; every downstream Payment/Invoice/RecoveryCase/AuditEvent is
    reachable only through its Customer, so tenant isolation is enforced
    by joining through Customer.merchant_id at the query layer (see
    docs/ARCHITECTURE.md for the explicit rationale for this design vs.
    duplicating merchant_id onto every table)."""
    __tablename__ = "merchants"
    id = Column(String, primary_key=True, default=lambda: gen_id("MERCH"))
    name = Column(String, nullable=False)
    razorpay_account_id = Column(String, nullable=True)
    industry = Column(String, default="general")
    created_at = Column(DateTime, default=utcnow)

    customers = relationship("Customer", back_populates="merchant")


class Customer(Base):
    __tablename__ = "customers"
    id = Column(String, primary_key=True, default=lambda: gen_id("CUST"))
    merchant_id = Column(String, ForeignKey("merchants.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String)
    phone = Column(String)
    company = Column(String)
    customer_type = Column(String, default="B2C")  # B2C or B2B
    lifetime_value = Column(Float, default=0)
    risk_profile = Column(String, default="low")  # low/medium/high
    created_at = Column(DateTime, default=utcnow)

    merchant = relationship("Merchant", back_populates="customers")
    payments = relationship("Payment", back_populates="customer")
    invoices = relationship("Invoice", back_populates="customer")
    cases = relationship("RecoveryCase", back_populates="customer")


class Payment(Base):
    __tablename__ = "payments"
    id = Column(String, primary_key=True, default=lambda: gen_id("PAY"))
    customer_id = Column(String, ForeignKey("customers.id"))
    subscription_id = Column(String, ForeignKey("subscriptions.id"), nullable=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="INR")
    status = Column(String, default="pending")  # succeeded/failed/pending
    failure_reason = Column(String, nullable=True)
    provider = Column(String, default="mock")
    provider_payment_id = Column(String, default=lambda: gen_id("prov_pay"))
    created_at = Column(DateTime, default=utcnow)

    customer = relationship("Customer", back_populates="payments")


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(String, primary_key=True, default=lambda: gen_id("SUB"))
    customer_id = Column(String, ForeignKey("customers.id"))
    plan = Column(String)
    amount = Column(Float)
    billing_cycle = Column(String, default="monthly")
    status = Column(String, default="active")
    next_billing_date = Column(DateTime)


class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(String, primary_key=True, default=lambda: gen_id("INV"))
    customer_id = Column(String, ForeignKey("customers.id"))
    invoice_number = Column(String)
    amount = Column(Float)
    currency = Column(String, default="INR")
    issue_date = Column(DateTime)
    due_date = Column(DateTime)
    status = Column(String, default="open")  # open/paid/overdue
    days_overdue = Column(Integer, default=0)

    customer = relationship("Customer", back_populates="invoices")


class RecoveryCase(Base):
    __tablename__ = "recovery_cases"
    id = Column(String, primary_key=True, default=lambda: gen_id("CASE"))
    customer_id = Column(String, ForeignKey("customers.id"))
    source_type = Column(String)  # payment_failed / checkout_abandoned / invoice_overdue / subscription_failed
    source_id = Column(String)
    amount_at_risk = Column(Float)
    currency = Column(String, default="INR")
    status = Column(String, default=CaseStatus.OPEN.value)
    risk_level = Column(String, default="medium")
    root_cause = Column(String, nullable=True)
    root_cause_confidence = Column(Float, nullable=True)
    reasoning_summary = Column(Text, nullable=True)
    recommended_strategy = Column(String, nullable=True)
    current_step = Column(String, default="new")
    attempt_count = Column(Integer, default=0)
    amount_recovered = Column(Float, default=0)
    stop_reason = Column(String, nullable=True)
    human_escalation_required = Column(Boolean, default=False)
    escalation_reason = Column(String, nullable=True)
    human_approved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)
    resolved_at = Column(DateTime, nullable=True)

    customer = relationship("Customer", back_populates="cases")
    actions = relationship("RecoveryAction", back_populates="case")
    audit_events = relationship("AuditEvent", back_populates="case")
    promises = relationship("PromiseToPay", back_populates="case")


class RecoveryAction(Base):
    __tablename__ = "recovery_actions"
    id = Column(String, primary_key=True, default=lambda: gen_id("ACT"))
    case_id = Column(String, ForeignKey("recovery_cases.id"))
    action_type = Column(String)
    channel = Column(String, nullable=True)
    status = Column(String, default="pending")  # pending/sent/succeeded/failed
    reason = Column(Text, nullable=True)
    message_body = Column(Text, nullable=True)
    executed_at = Column(DateTime, default=utcnow)
    result = Column(String, nullable=True)
    amount_recovered = Column(Float, default=0)
    provider_response = Column(JSON, nullable=True)

    case = relationship("RecoveryCase", back_populates="actions")


class RecoveryPolicy(Base):
    __tablename__ = "recovery_policies"
    id = Column(String, primary_key=True, default=lambda: gen_id("POL"))
    name = Column(String, default="default")
    max_attempts = Column(Integer, default=3)
    max_days = Column(Integer, default=7)
    max_discount_percent = Column(Integer, default=10)
    allowed_channels = Column(JSON, default=lambda: ["email", "sms", "whatsapp"])
    require_human_approval_for_discount = Column(Boolean, default=True)
    require_human_approval_for_large_amount = Column(Boolean, default=True)
    large_amount_threshold = Column(Float, default=100000)


class PromiseToPay(Base):
    __tablename__ = "promises_to_pay"
    id = Column(String, primary_key=True, default=lambda: gen_id("PTP"))
    case_id = Column(String, ForeignKey("recovery_cases.id"))
    promised_amount = Column(Float)
    promised_date = Column(DateTime)
    status = Column(String, default="open")  # open/fulfilled/broken
    created_at = Column(DateTime, default=utcnow)
    fulfilled_at = Column(DateTime, nullable=True)

    case = relationship("RecoveryCase", back_populates="promises")


class WebhookEvent(Base):
    """Tracks processed provider event_ids for idempotent webhook ingestion."""
    __tablename__ = "webhook_events"
    id = Column(String, primary_key=True, default=lambda: gen_id("WHE"))
    provider = Column(String)
    event_id = Column(String, unique=True, index=True)  # provider's event_id — dedup key
    event_type = Column(String)
    payload = Column(JSON, nullable=True)
    resulting_case_id = Column(String, nullable=True)
    received_at = Column(DateTime, default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(String, primary_key=True, default=lambda: gen_id("AUD"))
    case_id = Column(String, ForeignKey("recovery_cases.id"))
    actor_type = Column(String)  # SYSTEM / AI_AGENT / USER / PAYMENT_PROVIDER / CUSTOMER
    action = Column(String)
    description = Column(Text)
    event_metadata = Column(JSON, nullable=True)
    timestamp = Column(DateTime, default=utcnow)

    case = relationship("RecoveryCase", back_populates="audit_events")
