"""
Demo-mode simulation engine. Generates realistic customers, payment history,
invoices and revenue-loss events so the product works with zero external
credentials. Also runs the full agent loop across a batch for the one-click
"Run Recovery Simulation" demo.
"""
import random
from datetime import timedelta

from faker import Faker
from sqlalchemy.orm import Session

from app.models import Customer, Payment, Invoice, RecoveryCase, Merchant, utcnow
from app.agents import orchestrator
from app.agents.orchestrator import create_case, run_case_to_completion
from app.providers.payment import MockPaymentProvider
from app.providers.communication import MockCommunicationProvider

fake = Faker()

FAILURE_REASONS = (
    ["temporary_failure"] * 45 +
    ["card_expired"] * 20 +
    ["auth_failed"] * 15 +
    ["insufficient_funds"] * 10 +
    ["network_timeout"] * 5 +
    ["invalid_method"] * 5
)

NAMED_HERO_SCENARIOS = [
    {"name": "Acme Software", "company": "Acme Software Pvt Ltd", "customer_type": "B2B",
     "amount": 124000, "successful_payments": 18, "failures": 1, "failure_reason": "temporary_failure"},
]


DEMO_MERCHANTS = [
    {"name": "Zenith Fashion Co.", "industry": "e-commerce (fashion)"},
    {"name": "Bharat SaaS Solutions", "industry": "B2B SaaS"},
    {"name": "QuickCart Grocers", "industry": "e-commerce (grocery/subscriptions)"},
]


def ensure_demo_merchants(db: Session) -> list[Merchant]:
    """Idempotent: creates the demo merchants if they don't already exist,
    returns all of them either way. Real multi-tenant isolation only means
    something if there's more than one merchant to isolate against."""
    merchants = []
    for spec in DEMO_MERCHANTS:
        existing = db.query(Merchant).filter(Merchant.name == spec["name"]).first()
        if existing:
            merchants.append(existing)
            continue
        m = Merchant(name=spec["name"], industry=spec["industry"])
        db.add(m)
        db.commit()
        db.refresh(m)
        merchants.append(m)
    return merchants


def _make_customer(db: Session, b2b: bool = False, merchant_id: str | None = None) -> Customer:
    name = fake.company() if b2b else fake.name()
    c = Customer(
        name=name,
        merchant_id=merchant_id,
        email=fake.email(),
        phone=fake.phone_number()[:15],
        company=fake.company() if b2b else None,
        customer_type="B2B" if b2b else "B2C",
        lifetime_value=round(random.uniform(2000, 200000), 2),
        risk_profile=random.choice(["low", "low", "low", "medium", "high"]),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _seed_payment_history(db: Session, customer: Customer, n_success: int, n_fail: int):
    for _ in range(n_success):
        db.add(Payment(customer_id=customer.id, amount=round(random.uniform(499, 9999), 2),
                        status="succeeded", provider="mock",
                        created_at=utcnow() - timedelta(days=random.randint(30, 400))))
    for _ in range(n_fail):
        db.add(Payment(customer_id=customer.id, amount=round(random.uniform(499, 9999), 2),
                        status="failed", failure_reason=random.choice(FAILURE_REASONS), provider="mock",
                        created_at=utcnow() - timedelta(days=random.randint(30, 400))))
    db.commit()


def _seed_invoice_history(db: Session, customer: Customer, n_on_time: int, n_late: int):
    for _ in range(n_on_time):
        issue = utcnow() - timedelta(days=random.randint(60, 300))
        db.add(Invoice(customer_id=customer.id, invoice_number=fake.bothify("INV-####"),
                        amount=round(random.uniform(20000, 300000), 2), issue_date=issue,
                        due_date=issue + timedelta(days=30), status="paid", days_overdue=0))
    for _ in range(n_late):
        issue = utcnow() - timedelta(days=random.randint(60, 300))
        db.add(Invoice(customer_id=customer.id, invoice_number=fake.bothify("INV-####"),
                        amount=round(random.uniform(20000, 300000), 2), issue_date=issue,
                        due_date=issue + timedelta(days=30), status="paid", days_overdue=random.randint(1, 20)))
    db.commit()


CURATED_SCENARIOS = [
    # (label, customer_name, customer_type, source_type, amount, kwargs, history)
    ("Scenario A — Easy win", "Priya Raghavan (loyal customer)", "B2C", "payment_failed",
     2499, {"failure_reason": "network_timeout"}, {"n_success": 14, "n_fail": 0}),
    ("Scenario B — Expired card", "Daniel Okafor", "B2C", "payment_failed",
     3999, {"failure_reason": "card_expired"}, {"n_success": 9, "n_fail": 1}),
    ("Scenario C — Checkout abandonment", "Meera Iyer", "B2C", "checkout_abandoned",
     18999, {}, {"n_success": 3, "n_fail": 0}),
    ("Scenario D — Overdue B2B invoice", "Sundown Logistics", "B2B", "invoice_overdue",
     250000, {"days_overdue": 12}, {"n_success": 5, "n_fail": 0, "invoices": (6, 1)}),
    ("Scenario E — Repeated failure", "Marcus Webb", "B2C", "payment_failed",
     1899, {"failure_reason": "invalid_method"}, {"n_success": 1, "n_fail": 3}),
    ("Scenario F — High-value case", "Northwind Manufacturing", "B2B", "payment_failed",
     185000, {"failure_reason": "temporary_failure"}, {"n_success": 22, "n_fail": 0}),
    ("Scenario G — Low-confidence case", "New Signup — Alex Chen", "B2C", "payment_failed",
     799, {"failure_reason": "unrecognized_gateway_error"}, {"n_success": 0, "n_fail": 0}),
]

# Scenario H is handled separately (not in the generic loop above) because
# it needs a post-creation side effect the generic loop doesn't support:
# the underlying payment resolving independently *after* the case is
# opened but *before* the agent acts on it — the exact race condition spec
# section 35 asks the demo to prove is handled gracefully.
SCENARIO_H = ("Scenario H — Graceful cancellation", "Fatima Al-Rashid", "B2C", "payment_failed",
              6499, {"failure_reason": "temporary_failure"}, {"n_success": 11, "n_fail": 0})


def create_curated_scenarios(db: Session, merchant_id: str | None = None) -> list:
    """Guarantees the 7 named demo scenarios from the spec (section 30) exist
    on every simulation run, so judges always see at least one of each
    pattern rather than relying purely on the probabilistic batch. Outcomes
    still run through the real mock-provider probabilities — these are
    representative setups, not scripted results."""
    scenario_cases = []
    for label, name, ctype, source_type, amount, kwargs, history in CURATED_SCENARIOS:
        customer = Customer(
            name=name, merchant_id=merchant_id, email=fake.email(), company=name if ctype == "B2B" else None,
            customer_type=ctype, lifetime_value=round(amount * random.uniform(3, 15), 2),
            risk_profile="low" if history["n_fail"] == 0 else "medium",
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)
        _seed_payment_history(db, customer, history["n_success"], history["n_fail"])
        if "invoices" in history:
            n_on_time, n_late = history["invoices"]
            _seed_invoice_history(db, customer, n_on_time, n_late)

        if source_type == "payment_failed":
            p = Payment(customer_id=customer.id, amount=amount, status="failed",
                        failure_reason=kwargs.get("failure_reason"), provider="mock")
            db.add(p); db.commit(); db.refresh(p)
            case = create_case(db, customer, "payment_failed", p.id, amount,
                                risk_level="high" if amount >= 100000 else "medium")
        elif source_type == "checkout_abandoned":
            case = create_case(db, customer, "checkout_abandoned", f"cart-{customer.id}", amount, risk_level="medium")
        elif source_type == "invoice_overdue":
            days_overdue = kwargs.get("days_overdue", 0)
            inv = Invoice(customer_id=customer.id, invoice_number=fake.bothify("INV-####"), amount=amount,
                           issue_date=utcnow() - timedelta(days=30 + days_overdue),
                           due_date=utcnow() - timedelta(days=days_overdue),
                           status="overdue", days_overdue=days_overdue)
            db.add(inv); db.commit(); db.refresh(inv)
            case = create_case(db, customer, "invoice_overdue", inv.id, amount,
                                risk_level="high" if amount >= 100000 else "medium")
        scenario_cases.append((case, kwargs))
    return scenario_cases


def create_graceful_cancellation_scenario(db: Session, merchant_id: str | None = None) -> tuple:
    """Scenario H: the payment fails, a recovery case is opened and
    diagnosed as a strong retry candidate — but before the agent's retry
    executes, the payment is independently resolved (customer paid through
    a different channel, or a late webhook arrived). This simulates that
    race condition deterministically and lets execute_next_action's
    pre-action verification (app/payment_state_machine.py) catch it and
    cancel gracefully instead of falsely claiming a recovery."""
    label, name, ctype, source_type, amount, kwargs, history = SCENARIO_H
    customer = Customer(
        name=name, merchant_id=merchant_id, email=fake.email(), customer_type=ctype,
        lifetime_value=round(amount * random.uniform(3, 15), 2), risk_profile="low",
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    _seed_payment_history(db, customer, history["n_success"], history["n_fail"])

    payment = Payment(customer_id=customer.id, amount=amount, status="failed",
                       failure_reason=kwargs.get("failure_reason"), provider="mock")
    db.add(payment)
    db.commit()
    db.refresh(payment)
    case = create_case(db, customer, "payment_failed", payment.id, amount, risk_level="medium")

    # The race condition: independently of our agent, the payment resolves.
    # This models a late-authorization webhook or a customer completing
    # payment through a different channel — Razorpay explicitly documents
    # this sequence as real (see docs/RAZORPAY_INTEGRATION.md).
    payment.status = "succeeded"
    db.commit()

    return case, kwargs


def generate_batch(db: Session, n_customers: int = 60, seed_events: bool = True) -> dict:
    """Generates customers + history, and opens a realistic mix of
    revenue-loss cases. Returns counts for the caller."""
    merchants = ensure_demo_merchants(db)
    customers = []
    for i in range(n_customers):
        b2b = i % 5 == 0
        merchant = merchants[i % len(merchants)]  # spread evenly across demo merchants
        c = _make_customer(db, b2b=b2b, merchant_id=merchant.id)
        n_success = random.randint(0, 18)
        n_fail = random.choices([0, 1, 2, 3], weights=[55, 25, 12, 8])[0]
        _seed_payment_history(db, c, n_success, n_fail)
        if b2b:
            _seed_invoice_history(db, c, random.randint(2, 8), random.randint(0, 2))
        customers.append(c)

    events_created = {"payment_failed": 0, "checkout_abandoned": 0, "invoice_overdue": 0, "subscription_failed": 0}
    cases = []
    if seed_events:
        for c in customers:
            r = random.random()
            if r < 0.35:
                reason = random.choice(FAILURE_REASONS)
                amount = round(random.uniform(499, 15000), 2)
                p = Payment(customer_id=c.id, amount=amount, status="failed", failure_reason=reason, provider="mock")
                db.add(p)
                db.commit()
                db.refresh(p)
                case = create_case(db, c, "payment_failed", p.id, amount,
                                    risk_level="high" if amount > 5000 else "medium")
                cases.append((case, {"failure_reason": reason}))
                events_created["payment_failed"] += 1
            elif r < 0.55:
                amount = round(random.uniform(999, 25000), 2)
                case = create_case(db, c, "checkout_abandoned", f"cart-{c.id}", amount, risk_level="medium")
                cases.append((case, {}))
                events_created["checkout_abandoned"] += 1
            elif r < 0.65 and c.customer_type == "B2B":
                days_overdue = random.choice([3, 7, 12, 20, 35])
                amount = round(random.uniform(50000, 300000), 2)
                inv = Invoice(customer_id=c.id, invoice_number=fake.bothify("INV-####"), amount=amount,
                               issue_date=utcnow() - timedelta(days=30 + days_overdue),
                               due_date=utcnow() - timedelta(days=days_overdue),
                               status="overdue", days_overdue=days_overdue)
                db.add(inv)
                db.commit()
                db.refresh(inv)
                case = create_case(db, c, "invoice_overdue", inv.id, amount, risk_level="high" if amount > 100000 else "medium")
                cases.append((case, {"days_overdue": days_overdue}))
                events_created["invoice_overdue"] += 1

    # Curated scenarios A-G (spec section 30), guaranteed every run.
    # Assigned to the first demo merchant so all seven live under one
    # coherent tenant for the demo narrative.
    scenario_cases = create_curated_scenarios(db, merchant_id=merchants[0].id)
    cases.extend(scenario_cases)
    for case, _ in scenario_cases:
        events_created[case.source_type] = events_created.get(case.source_type, 0) + 1

    # Scenario H: graceful cancellation (spec section 35's required demo
    # case) — assigned to the second merchant, so switching merchants in
    # the UI demonstrates both a different revenue picture AND genuine
    # data isolation (this case is invisible under merchant 1).
    h_case, h_kwargs = create_graceful_cancellation_scenario(db, merchant_id=merchants[1].id)
    cases.append((h_case, h_kwargs))
    events_created["payment_failed"] = events_created.get("payment_failed", 0) + 1

    # Hero case: Acme Software, guaranteed impressive multi-step recovery
    hero_customer = Customer(name="Acme Software", merchant_id=merchants[0].id,
                              email="billing@acmesoftware.example",
                              company="Acme Software Pvt Ltd", customer_type="B2B",
                              lifetime_value=850000, risk_profile="low")
    db.add(hero_customer)
    db.commit()
    db.refresh(hero_customer)
    _seed_payment_history(db, hero_customer, 18, 0)
    hero_payment = Payment(customer_id=hero_customer.id, amount=124000, status="failed",
                            failure_reason="temporary_failure", provider="mock")
    db.add(hero_payment)
    db.commit()
    db.refresh(hero_payment)
    hero_case = create_case(db, hero_customer, "payment_failed", hero_payment.id, 124000, risk_level="high")
    cases.append((hero_case, {"failure_reason": "temporary_failure"}))
    events_created["payment_failed"] += 1

    return {"customers_created": len(customers) + 2 + len(CURATED_SCENARIOS), "cases": cases, "events_created": events_created}


def run_agent_on_batch(db: Session, cases: list) -> dict:
    # Simulated customers have fake, randomly-generated contact details
    # (see fake.email() above) — never route them through real Razorpay/
    # SendGrid/Twilio even if those are configured for real cases. Swap in
    # mocks for the duration of the batch, then restore whatever was live.
    real_payment_provider = orchestrator.payment_provider
    real_communication_provider = orchestrator.communication_provider
    orchestrator.payment_provider = MockPaymentProvider()
    orchestrator.communication_provider = MockCommunicationProvider()
    try:
        for case, kwargs in cases:
            run_case_to_completion(db, case, **kwargs)
    finally:
        orchestrator.payment_provider = real_payment_provider
        orchestrator.communication_provider = real_communication_provider
    return summarize_run(db)


def summarize_run(db: Session) -> dict:
    all_cases = db.query(RecoveryCase).all()
    at_risk = sum(c.amount_at_risk for c in all_cases if c.status not in ("RECOVERED",))
    recovered = sum(c.amount_recovered for c in all_cases)
    analyzed = sum(c.amount_at_risk for c in all_cases)
    automated = len([c for c in all_cases if c.status == "RECOVERED"])
    escalated = len([c for c in all_cases if c.status == "ESCALATED"])
    stopped_safely = len([c for c in all_cases if c.status in ("STOPPED", "RECOVERED")])
    recovery_times = [
        (c.resolved_at - c.created_at).total_seconds() for c in all_cases
        if c.resolved_at and c.status == "RECOVERED"
    ]
    avg_recovery_seconds = sum(recovery_times) / len(recovery_times) if recovery_times else 0
    return {
        "revenue_analyzed": round(analyzed, 2),
        "revenue_at_risk": round(at_risk, 2),
        "revenue_recovered": round(recovered, 2),
        "recovery_rate": round((recovered / analyzed * 100), 1) if analyzed else 0,
        "cases_processed": len(all_cases),
        "automated_recoveries": automated,
        "human_escalations": escalated,
        "stopped_safely": stopped_safely,
        "avg_recovery_time_seconds": round(avg_recovery_seconds, 1),
    }
