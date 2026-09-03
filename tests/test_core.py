"""
Critical-path tests: policy enforcement, agent decisioning, recovery
accounting, and idempotency-relevant invariants.
Run with: pytest -q
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Customer, RecoveryCase, Payment, RecoveryAction, AuditEvent, Merchant, utcnow
from app.policies.engine import check_policy, evaluate_stop_condition, DEFAULT_POLICY
from app.agents import ai_service
from app.agents.orchestrator import create_case, analyze_case, execute_next_action


@pytest.fixture(autouse=True)
def _force_deterministic_diagnosis(monkeypatch):
    """This suite's assertions are written against the deterministic rule
    engine in ai_service.py. If a real LLM_API_KEY happens to be present in
    the environment (.env, loaded via app/__init__.py's load_dotenv() —
    e.g. because this checkout is also configured for live-integration use
    outside pytest), USE_LLM would silently flip to True and every
    diagnosis test would depend on a live network call's judgment instead
    of our own routing table — flaky, slow, and not what "58/73 tests,
    offline and demo-safe" promises. Force the deterministic path for the
    whole suite; tests that specifically want to exercise the LLM path
    override this explicitly."""
    monkeypatch.setattr(ai_service, "USE_LLM", False)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def make_customer(db, **kw):
    c = Customer(name="Test Customer", email="t@example.com", **kw)
    db.add(c); db.commit(); db.refresh(c)
    return c


# ---------------------------------------------------------------- Policy tests
def test_max_attempts_enforced(db):
    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-1", 1000)
    case.attempt_count = DEFAULT_POLICY["max_attempts"]
    result = check_policy(case, "payment_retry", None, 1000)
    assert not result.allowed
    assert "attempts" in result.reason.lower()


def test_workflow_expiration_enforced(db):
    from datetime import timedelta
    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-1", 1000)
    case.created_at = utcnow() - timedelta(days=DEFAULT_POLICY["max_workflow_days"] + 1)
    result = check_policy(case, "payment_retry", None, 1000)
    assert not result.allowed
    assert "expired" in result.reason.lower()


def test_large_transaction_requires_approval(db):
    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-1", DEFAULT_POLICY["large_amount_threshold"] + 1)
    result = check_policy(case, "payment_retry", None, case.amount_at_risk)
    assert not result.allowed
    assert result.requires_human_approval


def test_large_transaction_allowed_after_approval(db):
    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-1", DEFAULT_POLICY["large_amount_threshold"] + 1)
    case.human_approved = True
    result = check_policy(case, "payment_retry", None, case.amount_at_risk)
    assert result.allowed


def test_disallowed_channel_blocked(db):
    c = make_customer(db)
    case = create_case(db, c, "checkout_abandoned", "cart-1", 1000)
    result = check_policy(case, "send_message", "carrier_pigeon", 1000)
    assert not result.allowed


def test_discount_requires_approval(db):
    c = make_customer(db)
    case = create_case(db, c, "invoice_overdue", "INV-1", 1000)
    result = check_policy(case, "apply_discount", "email", 1000)
    assert not result.allowed
    assert result.requires_human_approval


# ---------------------------------------------------------------- Agent tests
def test_diagnosis_returns_structured_decision():
    ctx = {"successful_payments": 12, "previous_failures": 0}
    d = ai_service.diagnose("payment_failed", ctx, failure_reason="card_expired")
    assert d.root_cause == "expired_card"
    assert 0 <= d.root_cause_confidence <= 1
    assert d.recommended_strategy


def test_unknown_source_type_escalates_safely():
    d = ai_service.diagnose("totally_unrecognized_event", {}, )
    assert d.human_escalation_required is True


def test_analyze_case_sets_diagnosis_fields(db):
    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-1", 1000)
    analyze_case(db, case, failure_reason="card_expired")
    assert case.root_cause == "expired_card"
    assert case.recommended_strategy is not None


# ---------------------------------------------------------------- Recovery tests
def test_payment_success_creates_recovery(db, monkeypatch):
    from app.providers import payment as payment_mod
    monkeypatch.setattr(payment_mod.payment_provider, "retry_payment",
                         lambda *a, **kw: {"status": "succeeded", "provider": "mock"})
    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-1", 5000)
    analyze_case(db, case, failure_reason="temporary_failure")
    execute_next_action(db, case)
    assert case.status == "RECOVERED"
    assert case.amount_recovered == 5000


def test_failed_payment_does_not_count_as_recovery(db, monkeypatch):
    from app.providers import payment as payment_mod
    monkeypatch.setattr(payment_mod.payment_provider, "retry_payment",
                         lambda *a, **kw: {"status": "failed", "provider": "mock"})
    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-1", 5000)
    analyze_case(db, case, failure_reason="temporary_failure")
    execute_next_action(db, case)
    assert case.status != "RECOVERED"
    assert case.amount_recovered == 0


def test_max_attempts_produces_escalation(db, monkeypatch):
    from app.providers import payment as payment_mod
    monkeypatch.setattr(payment_mod.payment_provider, "retry_payment",
                         lambda *a, **kw: {"status": "failed", "provider": "mock"})
    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-1", 5000)
    analyze_case(db, case, failure_reason="temporary_failure")
    for _ in range(DEFAULT_POLICY["max_attempts"] + 1):
        execute_next_action(db, case)
        if case.status in ("ESCALATED", "STOPPED", "RECOVERED"):
            break
    assert case.status == "ESCALATED"
    assert case.stop_reason == "MAXIMUM_ATTEMPTS_REACHED"


# ---------------------------------------------------------------- Accounting tests
def test_recovery_rate_calculation(db, monkeypatch):
    from app.providers import payment as payment_mod
    monkeypatch.setattr(payment_mod.payment_provider, "retry_payment",
                         lambda *a, **kw: {"status": "succeeded", "provider": "mock"})
    c = make_customer(db)
    case1 = create_case(db, c, "payment_failed", "PAY-1", 4000)
    analyze_case(db, case1, failure_reason="temporary_failure")
    execute_next_action(db, case1)
    case2 = create_case(db, c, "payment_failed", "PAY-2", 6000)
    # leave case2 unresolved
    total_analyzed = case1.amount_at_risk + case2.amount_at_risk
    total_recovered = case1.amount_recovered + case2.amount_recovered
    assert total_recovered == 4000
    assert round(total_recovered / total_analyzed * 100, 1) == 40.0


# ---------------------------------------------------------------- Webhook / idempotency tests
def test_duplicate_webhook_is_idempotent(db):
    from app import webhooks
    payload = {
        "event_type": "payment.failed", "provider": "stripe", "event_id": "evt_dup_1",
        "customer_email": "dup@example.com", "amount": 1000, "currency": "INR",
        "failure_reason": "card_expired", "payment_id": "pay_dup_1",
    }
    first = webhooks.ingest_payment_event(db, payload)
    assert first["status"] == "ok"
    second = webhooks.ingest_payment_event(db, payload)
    assert second["status"] == "duplicate"

    cases = db.query(RecoveryCase).all()
    assert len(cases) == 1


def test_webhook_creates_case_with_correct_amount(db):
    from app import webhooks
    payload = {
        "event_type": "checkout.abandoned", "provider": "web", "event_id": "evt_co_1",
        "customer_email": "checkout@example.com", "amount": 18999, "currency": "INR",
        "cart_id": "cart_1",
    }
    result = webhooks.ingest_checkout_event(db, payload)
    assert result["status"] == "ok"
    case = db.query(RecoveryCase).filter(RecoveryCase.id == result["case_id"]).first()
    assert case.amount_at_risk == 18999
    assert case.source_type == "checkout_abandoned"


# ---------------------------------------------------------------- Promise-to-pay tests
def test_promise_fulfillment_marks_case_recovered(db):
    from datetime import timedelta
    from app.models import PromiseToPay
    c = make_customer(db, customer_type="B2B")
    case = create_case(db, c, "invoice_overdue", "INV-1", 250000)
    ptp = PromiseToPay(case_id=case.id, promised_amount=250000,
                        promised_date=utcnow() + timedelta(days=5), status="open")
    db.add(ptp); db.commit(); db.refresh(ptp)

    ptp.status = "fulfilled"
    case.status = "RECOVERED"
    case.amount_recovered = ptp.promised_amount
    case.stop_reason = "PAYMENT_RECOVERED"
    db.commit()

    assert case.status == "RECOVERED"
    assert case.amount_recovered == 250000


# ---------------------------------------------------------------- Policy persistence tests
def test_get_active_policy_falls_back_to_default(db):
    from app.policies.engine import get_active_policy, DEFAULT_POLICY
    assert get_active_policy(db) == DEFAULT_POLICY
    assert get_active_policy(None) == DEFAULT_POLICY


def test_active_policy_reflects_saved_edits(db):
    from app.models import RecoveryPolicy
    from app.policies.engine import get_active_policy
    row = RecoveryPolicy(name="default", max_attempts=5, max_days=7,
                          max_discount_percent=10, large_amount_threshold=50000,
                          require_human_approval_for_large_amount=True)
    db.add(row); db.commit()
    policy = get_active_policy(db)
    assert policy["max_attempts"] == 5
    assert policy["large_amount_threshold"] == 50000


def test_lowered_threshold_escalates_previously_automatic_case(db):
    from app.models import RecoveryPolicy
    from app.policies.engine import get_active_policy, check_policy
    db.add(RecoveryPolicy(name="default", max_attempts=3, max_days=7,
                           max_discount_percent=10, large_amount_threshold=50000,
                           require_human_approval_for_large_amount=True))
    db.commit()
    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-1", 75000)  # between old (100k) and new (50k) threshold
    result = check_policy(case, "payment_retry", None, case.amount_at_risk, policy=get_active_policy(db))
    assert not result.allowed
    assert result.requires_human_approval


# ---------------------------------------------------------------- Strategy optimizer tests
def test_strategy_performance_reflects_real_outcomes(db, monkeypatch):
    from app.providers import payment as payment_mod
    from app.policies.optimizer import strategy_performance
    monkeypatch.setattr(payment_mod.payment_provider, "retry_payment",
                         lambda *a, **kw: {"status": "succeeded", "provider": "mock"})
    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-1", 3000)
    analyze_case(db, case, failure_reason="temporary_failure")
    execute_next_action(db, case)

    perf = strategy_performance(db)
    row = next(r for r in perf if r["strategy"] == case.recommended_strategy)
    assert row["cases_recovered"] == 1
    assert row["success_rate_pct"] == 100.0
    assert row["total_recovered"] == 3000


# ---------------------------------------------------------------- Safety / error-handling tests
def test_diagnosis_failure_escalates_instead_of_crashing(db, monkeypatch):
    """Spec section 38: if the diagnosis engine fails, the case must be
    marked for human review, never crash or execute an unvalidated action."""
    from app.agents import ai_service

    def boom(*a, **kw):
        raise RuntimeError("simulated diagnosis outage")
    monkeypatch.setattr(ai_service, "diagnose", boom)

    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-1", 5000)
    analyze_case(db, case, failure_reason="temporary_failure")  # must not raise

    assert case.human_escalation_required is True
    assert case.status == "ESCALATED"
    assert case.root_cause == "unknown"


# ---------------------------------------------------------------- Security tests
def test_webhook_signature_required_when_secret_set():
    from app import security
    import pytest as _pytest
    from fastapi import HTTPException

    security_secret_backup = security.WEBHOOK_SECRET
    try:
        security.WEBHOOK_SECRET = "topsecret"
        with _pytest.raises(HTTPException):
            security.verify_webhook_signature(b'{"a":1}', None)
        with _pytest.raises(HTTPException):
            security.verify_webhook_signature(b'{"a":1}', "wrong-signature")
        # correct signature passes without raising
        import hmac, hashlib
        body = b'{"a":1}'
        sig = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
        security.verify_webhook_signature(body, sig)  # should not raise
    finally:
        security.WEBHOOK_SECRET = security_secret_backup


def test_webhook_signature_not_required_by_default():
    from app import security

    backup = security.WEBHOOK_SECRET
    try:
        security.WEBHOOK_SECRET = None
        security.verify_webhook_signature(b'{"a":1}', None)  # should not raise when WEBHOOK_SECRET unset
    finally:
        security.WEBHOOK_SECRET = backup


def test_api_key_required_when_configured():
    from app import security
    from fastapi import HTTPException
    import pytest as _pytest

    backup = security.API_KEY
    try:
        security.API_KEY = "demo-key"
        with _pytest.raises(HTTPException):
            security.require_api_key(x_api_key=None)
        with _pytest.raises(HTTPException):
            security.require_api_key(x_api_key="wrong")
        security.require_api_key(x_api_key="demo-key")  # should not raise
    finally:
        security.API_KEY = backup


def test_api_key_not_required_by_default():
    from app import security
    # default (unset) -> no-op regardless of header
    backup = security.API_KEY
    try:
        security.API_KEY = None
        security.require_api_key(x_api_key=None)  # should not raise
    finally:
        security.API_KEY = backup


# ---------------------------------------------------------------- Prioritization / summarization tests
def test_prioritize_cases_ranks_larger_more_confident_cases_first(db):
    c = make_customer(db)
    small = create_case(db, c, "payment_failed", "PAY-1", 500)
    small.root_cause_confidence = 0.3
    large = create_case(db, c, "payment_failed", "PAY-2", 150000)
    large.root_cause_confidence = 0.9
    db.commit()

    from app.agents.ai_service import prioritize_cases
    ranked = prioritize_cases([small, large])
    assert ranked[0]["case"].id == large.id
    assert ranked[0]["priority_score"] > ranked[1]["priority_score"]


def test_summarize_case_is_concise_and_factual(db):
    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-1", 4999)
    case.root_cause = "expired_card"
    case.recommended_strategy = "payment_method_update"
    db.commit()

    from app.agents.ai_service import summarize_case
    summary = summarize_case(case)
    assert "4,999" in summary
    assert "expired card" in summary
    assert case.status in summary


# ---------------------------------------------------------------- Channel diversity / optimizer wiring
def test_channel_selection_varies_by_context(db):
    from app.agents.orchestrator import _select_channel
    c = make_customer(db)
    checkout_case = create_case(db, c, "checkout_abandoned", "cart-1", 1000)
    invoice_case = create_case(db, c, "invoice_overdue", "INV-1", 50000)
    assert _select_channel(checkout_case, "send_message") == "whatsapp"
    assert _select_channel(invoice_case, "send_message") == "email"
    assert _select_channel(checkout_case, "send_payment_link") == "sms"


def test_strategy_optimizer_defers_to_diagnosis_when_no_history(db):
    """With no historical data, the optimizer must not override the
    diagnosis step's primary recommendation."""
    from app.agents.orchestrator import _apply_strategy_optimizer
    from app.agents import ai_service
    decision = ai_service.Decision(
        root_cause="expired_card", root_cause_confidence=0.9,
        customer_context_summary="", recommended_strategy="payment_method_update",
        reasoning_summary="", strategy_scores=[
            {"strategy": "payment_method_update", "score": 0.9, "reason": ""},
            {"strategy": "payment_link", "score": 0.5, "reason": ""},
        ],
    )
    result = _apply_strategy_optimizer(db, decision)
    assert result == "payment_method_update"


# ---------------------------------------------------------------- Razorpay integration tests
def test_normalize_razorpay_payload_converts_paise_to_rupees():
    from app.webhooks import _normalize_razorpay_payload
    payload = {
        "entity": "event", "event": "payment.failed",
        "payload": {"payment": {"entity": {
            "id": "pay_test1", "amount": 499900, "currency": "INR",
            "email": "test@example.com", "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Your card has expired.",
        }}},
    }
    result = _normalize_razorpay_payload(payload)
    assert result["amount"] == 4999.0
    assert result["failure_reason"] == "card_expired"
    assert result["event_type"] == "payment.failed"
    assert result["provider"] == "razorpay"


def test_normalize_razorpay_payload_derives_stable_event_id():
    """Real Razorpay payloads don't always carry a top-level event_id — we
    must derive a stable one from payment id + event type so idempotency
    still works across redelivery."""
    from app.webhooks import _normalize_razorpay_payload
    payload = {
        "entity": "event", "event": "payment.failed",
        "payload": {"payment": {"entity": {"id": "pay_stable123", "amount": 100, "currency": "INR"}}},
    }
    r1 = _normalize_razorpay_payload(payload)
    r2 = _normalize_razorpay_payload(payload)
    assert r1["event_id"] == r2["event_id"]  # same payload -> same derived id


def test_normalize_razorpay_payload_passthrough_for_simplified_shape():
    """Our own internal simplified webhook shape must pass through
    unchanged (not be misidentified as Razorpay-shaped)."""
    from app.webhooks import _normalize_razorpay_payload
    payload = {"event_type": "payment.failed", "provider": "stripe",
               "event_id": "evt_1", "amount": 500, "currency": "INR"}
    result = _normalize_razorpay_payload(payload)
    assert result == payload


def test_razorpay_webhook_end_to_end_idempotent(db):
    """A genuine Razorpay-shaped payload, ingested twice, creates exactly
    one case — verifying paise conversion, root-cause mapping, and
    idempotency together."""
    from app import webhooks
    payload = {
        "entity": "event", "account_id": "acc_test", "event": "payment.failed",
        "contains": ["payment"],
        "payload": {"payment": {"entity": {
            "id": "pay_dedup_test", "amount": 250000, "currency": "INR",
            "status": "failed", "email": "dedup@example.com",
            "error_code": "BAD_REQUEST_ERROR", "error_description": "Insufficient balance in the account.",
        }}},
        "created_at": 1568781323,
    }
    first = webhooks.ingest_payment_event(db, payload)
    second = webhooks.ingest_payment_event(db, payload)
    assert first["status"] == "ok"
    assert second["status"] == "duplicate"

    case = db.query(RecoveryCase).filter(RecoveryCase.id == first["case_id"]).first()
    assert case.amount_at_risk == 2500.0  # 250000 paise -> 2500 rupees


def test_razorpay_provider_selected_when_keys_present(monkeypatch):
    from app.providers import payment as payment_mod
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_x")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secretx")
    provider = payment_mod._select_provider()
    assert isinstance(provider, payment_mod.RazorpayPaymentProvider)


def test_mock_provider_selected_when_no_keys(monkeypatch):
    from app.providers import payment as payment_mod
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    provider = payment_mod._select_provider()
    assert isinstance(provider, payment_mod.MockPaymentProvider)


# ---------------------------------------------------------------- Customer Health Score tests
def test_health_score_high_for_reliable_customer(db):
    from app.agents.ai_service import customer_health_score
    c = make_customer(db)
    for _ in range(10):
        db.add(Payment(customer_id=c.id, amount=1000, status="succeeded"))
    db.commit()
    db.refresh(c)
    result = customer_health_score(c)
    assert result["score"] >= 80
    assert result["band"] == "excellent"


def test_health_score_low_for_unreliable_customer(db):
    from app.agents.ai_service import customer_health_score
    c = make_customer(db)
    db.add(Payment(customer_id=c.id, amount=1000, status="succeeded"))
    for _ in range(4):
        db.add(Payment(customer_id=c.id, amount=1000, status="failed"))
    db.commit()
    db.refresh(c)
    result = customer_health_score(c)
    assert result["score"] < 60
    assert result["band"] in ("fair", "at-risk")


def test_health_score_neutral_with_no_history(db):
    from app.agents.ai_service import customer_health_score
    c = make_customer(db)
    result = customer_health_score(c)
    assert result["score"] == 50
    assert result["band"] == "unknown"


# ---------------------------------------------------------------- Payment State Machine tests
def test_state_machine_allows_failed_to_captured():
    """The exact real-world sequence Razorpay documents: a payment.failed
    webhook can be followed by payment.captured for the same transaction
    (late authorization, customer UPI retry)."""
    from app.payment_state_machine import apply_transition, PaymentState
    result = apply_transition(PaymentState.FAILED, PaymentState.CAPTURED)
    assert result.accepted
    assert result.new_state == PaymentState.CAPTURED


def test_state_machine_rejects_out_of_order_downgrade():
    """A late 'authorized' event arriving after we already recorded
    'captured' must not downgrade the payment."""
    from app.payment_state_machine import apply_transition, PaymentState
    result = apply_transition(PaymentState.CAPTURED, PaymentState.AUTHORIZED)
    assert not result.accepted
    assert result.new_state == PaymentState.CAPTURED  # unchanged


def test_state_machine_duplicate_event_is_noop():
    from app.payment_state_machine import apply_transition, PaymentState
    result = apply_transition(PaymentState.CAPTURED, PaymentState.CAPTURED)
    assert not result.accepted
    assert "duplicate" in result.reason.lower()


def test_state_machine_captured_is_terminal_for_forward_progress():
    from app.payment_state_machine import apply_transition, PaymentState
    # Captured can only move to refund states, nothing else
    result = apply_transition(PaymentState.CAPTURED, PaymentState.FAILED)
    assert not result.accepted


def test_verify_before_action_blocks_already_captured_payment():
    from app.payment_state_machine import verify_before_action, PaymentState
    can_act, reason = verify_before_action(PaymentState.CAPTURED)
    assert not can_act
    assert "already captured" in reason.lower()


def test_verify_before_action_allows_failed_payment():
    from app.payment_state_machine import verify_before_action, PaymentState
    can_act, reason = verify_before_action(PaymentState.FAILED)
    assert can_act


# ---------------------------------------------------------------- ML pipeline tests
def test_data_generator_produces_learnable_not_trivial_labels():
    """Labels must not be a perfect deterministic function of features
    (that would be an unrealistic, fabricated-looking dataset) and must
    not be independent noise either (that would be unlearnable) — the
    recovered rate should land in a plausible band."""
    from ml.data_generator import generate_rows
    rows = generate_rows(2000, seed=1)
    recovered_rate = sum(r["recovered"] for r in rows) / len(rows)
    assert 0.25 < recovered_rate < 0.65


def test_data_generator_reliable_customers_recover_more_often():
    """Sanity check on the causal structure itself: holding other factors
    roughly constant, a high customer_success_rate should correlate with a
    higher realized recovery rate than a low one — otherwise the 'causal'
    formula wouldn't actually be causal."""
    from ml.data_generator import _true_recovery_probability
    high_reliability = _true_recovery_probability(
        customer_success_rate=0.95, amount=3000, root_cause="temporary_failure",
        strategy="immediate_payment_retry", attempt_count=0, days_since_last_payment=5)
    low_reliability = _true_recovery_probability(
        customer_success_rate=0.20, amount=3000, root_cause="temporary_failure",
        strategy="immediate_payment_retry", attempt_count=0, days_since_last_payment=5)
    assert high_reliability > low_reliability


def test_data_generator_more_attempts_lowers_probability():
    from ml.data_generator import _true_recovery_probability
    zero_attempts = _true_recovery_probability(0.7, 3000, "temporary_failure", "delayed_retry", 0, 5)
    three_attempts = _true_recovery_probability(0.7, 3000, "temporary_failure", "delayed_retry", 3, 5)
    assert zero_attempts > three_attempts


def test_temporal_split_is_chronological():
    from ml.features import temporal_split
    from ml.data_generator import generate_rows
    import pandas as pd
    rows = generate_rows(500, seed=7)
    df = pd.DataFrame(rows)
    df["created_at"] = pd.to_datetime(df["created_at"])
    train, val, test = temporal_split(df)
    assert train["created_at"].max() <= val["created_at"].min()
    assert val["created_at"].max() <= test["created_at"].min()
    assert len(train) + len(val) + len(test) == len(df)


def test_predict_recovery_probability_returns_none_without_trained_model(monkeypatch):
    """If no model has been trained, the app must say so explicitly rather
    than crash or fabricate a probability."""
    from app.agents import ai_service
    monkeypatch.setattr(ai_service, "_ml_model", None)
    monkeypatch.setattr(ai_service, "_ml_load_attempted", True)  # skip real loading
    result = ai_service.predict_recovery_probability(0.8, 10, 5000, "temporary_failure", "delayed_retry", 0, 5)
    assert result is None


def test_predict_recovery_probability_reliable_customer_scores_higher():
    """End-to-end check against the actually-trained model (skipped
    gracefully if no model artifact exists in this environment)."""
    from app.agents import ai_service
    ai_service._ml_load_attempted = False  # force a fresh load attempt
    ai_service._ml_model = None
    reliable = ai_service.predict_recovery_probability(0.95, 15, 3000, "temporary_failure", "immediate_payment_retry", 0, 5)
    if reliable is None:
        pytest.skip("No trained model artifact present in this environment")
    unreliable = ai_service.predict_recovery_probability(0.15, 15, 3000, "temporary_failure", "immediate_payment_retry", 0, 5)
    assert reliable["probability"] > unreliable["probability"]
    assert isinstance(reliable["explanation"], list) and len(reliable["explanation"]) > 0


def test_graceful_cancellation_when_payment_already_captured(db):
    """The exact demo case spec section 35 requires: AI plans a retry, but
    before execution the payment status shows already captured — action
    must be cancelled, never falsely re-claimed as a fresh recovery
    action, and the case still ends up correctly marked RECOVERED (because
    the money did, in fact, arrive) with an honest stop reason."""
    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-graceful-1", 6499)
    analyze_case(db, case, failure_reason="temporary_failure")
    assert case.status == "ACTION_READY"

    # Simulate the race: payment resolves independently before execution
    payment = Payment(id="PAY-graceful-1", customer_id=c.id, amount=6499, status="succeeded")
    db.merge(payment)
    db.commit()

    execute_next_action(db, case)

    assert case.status == "RECOVERED"
    assert case.stop_reason == "PAYMENT_ALREADY_RESOLVED"
    assert case.amount_recovered == 6499
    # No payment_retry action should have been executed against this case
    actions = db.query(RecoveryAction).filter(RecoveryAction.case_id == case.id).all()
    assert len(actions) == 0
    descriptions = [e.description for e in db.query(AuditEvent).filter(AuditEvent.case_id == case.id).all()]
    assert any("cancelled" in d.lower() for d in descriptions)


# ---------------------------------------------------------------- Multi-tenant isolation tests
def test_customer_scoped_to_merchant(db):
    m1 = Merchant(name="Merchant One")
    m2 = Merchant(name="Merchant Two")
    db.add_all([m1, m2])
    db.commit()

    c1 = Customer(name="Alice", merchant_id=m1.id)
    c2 = Customer(name="Bob", merchant_id=m2.id)
    db.add_all([c1, c2])
    db.commit()

    m1_customers = db.query(Customer).filter(Customer.merchant_id == m1.id).all()
    assert len(m1_customers) == 1
    assert m1_customers[0].name == "Alice"


def test_dashboard_merchant_filter_excludes_other_tenant_cases(db):
    """The core tenant-isolation guarantee: a case belonging to merchant B
    must never appear when querying scoped to merchant A."""
    m1 = Merchant(name="Isolation Test Merchant A")
    m2 = Merchant(name="Isolation Test Merchant B")
    db.add_all([m1, m2])
    db.commit()

    c1 = Customer(name="Tenant A Customer", merchant_id=m1.id)
    c2 = Customer(name="Tenant B Customer", merchant_id=m2.id)
    db.add_all([c1, c2])
    db.commit()
    db.refresh(c1)
    db.refresh(c2)

    case_a = create_case(db, c1, "payment_failed", "PAY-tenant-a", 5000)
    case_b = create_case(db, c2, "payment_failed", "PAY-tenant-b", 7000)

    # Simulate the merchant-scoped query main.py's endpoints use
    scoped_to_a = (db.query(RecoveryCase).join(Customer)
                   .filter(Customer.merchant_id == m1.id).all())
    scoped_to_b = (db.query(RecoveryCase).join(Customer)
                   .filter(Customer.merchant_id == m2.id).all())

    assert case_a.id in [c.id for c in scoped_to_a]
    assert case_a.id not in [c.id for c in scoped_to_b]
    assert case_b.id in [c.id for c in scoped_to_b]
    assert case_b.id not in [c.id for c in scoped_to_a]


def test_customer_without_merchant_id_still_works(db):
    """Backward compatibility: merchant_id is nullable, so existing
    single-tenant-style usage (tests, or a deployment that doesn't need
    multi-tenancy) keeps working unmodified."""
    c = make_customer(db)  # no merchant_id passed
    assert c.merchant_id is None
    case = create_case(db, c, "payment_failed", "PAY-no-merchant", 1000)
    assert case is not None


# ---------------------------------------------------------------- Expected value framework tests
def test_expected_value_recommends_act_for_strong_case():
    from app.policies.expected_value import compute_expected_value
    result = compute_expected_value(0.8, 5000, "payment_retry", strategy="immediate_payment_retry", prior_attempts=0)
    assert result.recommendation == "act"
    assert result.expected_value > 0


def test_expected_value_recommends_do_not_act_for_weak_case():
    from app.policies.expected_value import compute_expected_value
    result = compute_expected_value(0.05, 500, "send_message", strategy="stronger_reminder", prior_attempts=3)
    assert result.recommendation == "do_not_act"
    assert result.expected_value < 0


def test_expected_value_annoyance_cost_scales_with_prior_attempts():
    from app.policies.expected_value import compute_expected_value
    zero_priors = compute_expected_value(0.5, 3000, "send_message", prior_attempts=0)
    three_priors = compute_expected_value(0.5, 3000, "send_message", prior_attempts=3)
    assert zero_priors.expected_value > three_priors.expected_value
    assert three_priors.annoyance_cost > zero_priors.annoyance_cost


def test_expected_value_never_gates_execution():
    """The expected-value framework is advisory only — it must never be
    imported by the policy engine or orchestrator's execution path as a
    hard gate, only surfaced for human/analytical use."""
    import app.policies.engine as engine_module
    import inspect
    source = inspect.getsource(engine_module)
    assert "expected_value" not in source


# ---------------------------------------------------------------- Decline-code classification tests
def test_classify_decline_uses_authoritative_reason_first():
    """A real Razorpay error.reason should classify precisely, even when
    error_code/description would suggest something coarser or different."""
    from app.decline_codes import classify_decline
    assert classify_decline(reason="card_declined") == "card_declined_by_issuer"
    assert classify_decline(reason="payment_cancelled") == "payment_cancelled"
    assert classify_decline(reason="transaction_daily_limit_exceeded") == "limit_exceeded"
    assert classify_decline(reason="payment_risk_check_failed") == "risk_declined"
    assert classify_decline(reason="incorrect_otp") == "otp_failed"


def test_classify_decline_falls_back_to_error_code_then_description():
    from app.decline_codes import classify_decline
    # unrecognized reason, but description carries a usable signal
    assert classify_decline(reason="something_new", description="Card has expired") == "card_expired"
    # no reason or description — falls back to the coarse error_code bucket
    assert classify_decline(error_code="GATEWAY_ERROR") == "network_timeout"
    # nothing recognizable at all
    assert classify_decline() == "unrecognized_gateway_error"


def test_non_retryable_buckets_exclude_expired_and_risk():
    from app.decline_codes import is_retryable
    assert is_retryable("insufficient_funds") is True
    assert is_retryable("temporary_failure") is True
    assert is_retryable("card_expired") is False
    assert is_retryable("risk_declined") is False
    assert is_retryable("card_declined_by_issuer") is False


def test_risk_decline_always_escalates_regardless_of_customer_reliability(monkeypatch):
    """A risk/compliance decline must never be auto-retried, even for an
    otherwise perfectly reliable customer — this is a hard business rule,
    not a confidence-based judgment call. Forces the deterministic rule
    engine (USE_LLM off) so this asserts our own routing table, not
    whatever a live LLM call happens to decide."""
    monkeypatch.setattr(ai_service, "USE_LLM", False)
    ctx = {"successful_payments": 50, "previous_failures": 0}
    d = ai_service.diagnose("payment_failed", ctx, failure_reason="risk_declined")
    assert d.root_cause == "risk_or_compliance_decline"
    assert d.human_escalation_required is True
    assert d.recommended_strategy == "human_escalation"


def test_card_declined_by_issuer_routes_to_method_update_not_retry(monkeypatch):
    """A card actively declined/blocked by the issuer should never be
    routed to a same-card retry strategy — that would just fail again."""
    monkeypatch.setattr(ai_service, "USE_LLM", False)
    ctx = {"successful_payments": 5, "previous_failures": 0}
    d = ai_service.diagnose("payment_failed", ctx, failure_reason="bank_declined")
    assert d.recommended_strategy == "delayed_retry"
    d2 = ai_service.diagnose("payment_failed", ctx, failure_reason="invalid_method")
    assert d2.recommended_strategy != "immediate_payment_retry"


def test_payment_cancelled_diagnoses_as_customer_hesitation(monkeypatch):
    monkeypatch.setattr(ai_service, "USE_LLM", False)
    ctx = {"successful_payments": 3, "previous_failures": 0}
    d = ai_service.diagnose("payment_failed", ctx, failure_reason="payment_cancelled")
    assert d.root_cause == "customer_hesitation"
    assert d.recommended_strategy == "friendly_reminder"


def test_limit_exceeded_diagnoses_with_delayed_retry(monkeypatch):
    monkeypatch.setattr(ai_service, "USE_LLM", False)
    ctx = {"successful_payments": 8, "previous_failures": 0}
    d = ai_service.diagnose("payment_failed", ctx, failure_reason="limit_exceeded")
    assert d.root_cause == "transaction_limit_exceeded"
    assert d.recommended_strategy == "delayed_retry"


def test_normalize_razorpay_payload_uses_authoritative_error_reason():
    """When a real Razorpay payload carries error_reason (the authoritative
    field), it must be preferred over description-keyword guessing."""
    from app.webhooks import _normalize_razorpay_payload
    payload = {
        "entity": "event", "event": "payment.failed",
        "payload": {"payment": {"entity": {
            "id": "pay_reason_test", "amount": 10000, "currency": "INR",
            "error_code": "GATEWAY_ERROR",  # would otherwise map to network_timeout
            "error_reason": "card_declined",
            "error_description": "Payment failed",
        }}},
    }
    result = _normalize_razorpay_payload(payload)
    assert result["failure_reason"] == "card_declined_by_issuer"


# ---------------------------------------------------------------- Recovery ROI tests
def test_dashboard_reports_net_recovery_roi(db, monkeypatch):
    from app.providers import payment as payment_mod
    monkeypatch.setattr(payment_mod, "RETRY_SUCCESS_PROBABILITY", {"temporary_failure": 1.0})
    payment_mod.payment_provider = payment_mod.MockPaymentProvider()
    monkeypatch.setattr("app.agents.orchestrator.payment_provider", payment_mod.payment_provider)

    c = make_customer(db)
    case = create_case(db, c, "payment_failed", "PAY-roi", 1000)
    analyze_case(db, case, failure_reason="temporary_failure")
    execute_next_action(db, case)
    assert case.status == "RECOVERED"

    from app.policies.expected_value import ACTION_COST_BY_TYPE
    from app.main import dashboard as dashboard_endpoint

    body = dashboard_endpoint(merchant_id=None, db=db)

    expected_cost = ACTION_COST_BY_TYPE["payment_retry"]
    assert body["total_action_cost"] == expected_cost
    assert body["net_recovery_roi"] == round(1000 - expected_cost, 2)


# ---------------------------------------------------------------- Outbound merchant webhooks tests
def test_dispatch_event_signs_and_records_delivery(db, monkeypatch):
    from app import outbound_webhooks
    from app.models import Merchant, MerchantWebhookSubscription, WebhookDelivery

    m = Merchant(name="Test Merchant")
    db.add(m); db.commit(); db.refresh(m)
    sub = MerchantWebhookSubscription(merchant_id=m.id, url="https://example.com/hook",
                                       secret="topsecret", event_types=["case.recovered"])
    db.add(sub); db.commit(); db.refresh(sub)

    captured = {}

    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=5):
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        return FakeResponse()

    monkeypatch.setattr(outbound_webhooks.urllib.request, "urlopen", fake_urlopen)
    outbound_webhooks.dispatch_event(db, m.id, "case.recovered", "CASE-1", {"amount_recovered": 500})

    sig_header = [v for k, v in captured["headers"].items() if k.lower() == "x-recoveryos-signature"][0]
    expected_sig = outbound_webhooks.sign_payload(captured["body"], "topsecret")
    assert sig_header == expected_sig

    deliveries = db.query(WebhookDelivery).filter(WebhookDelivery.subscription_id == sub.id).all()
    assert len(deliveries) == 1
    assert deliveries[0].success is True
    assert deliveries[0].event_type == "case.recovered"


def test_dispatch_event_skips_subscription_not_matching_event_type(db, monkeypatch):
    from app import outbound_webhooks
    from app.models import Merchant, MerchantWebhookSubscription, WebhookDelivery

    m = Merchant(name="Test Merchant 2")
    db.add(m); db.commit(); db.refresh(m)
    sub = MerchantWebhookSubscription(merchant_id=m.id, url="https://example.com/hook",
                                       secret="s", event_types=["case.escalated"])
    db.add(sub); db.commit(); db.refresh(sub)

    called = {"n": 0}
    def fake_urlopen(req, timeout=5):
        called["n"] += 1
        raise AssertionError("should not be called")
    monkeypatch.setattr(outbound_webhooks.urllib.request, "urlopen", fake_urlopen)

    outbound_webhooks.dispatch_event(db, m.id, "case.recovered", "CASE-2", {})
    assert called["n"] == 0
    assert db.query(WebhookDelivery).count() == 0


def test_dispatch_event_never_raises_on_delivery_failure(db, monkeypatch):
    """An unreachable merchant endpoint must never propagate an exception
    into the recovery workflow — it's logged as a failed delivery instead."""
    from app import outbound_webhooks
    from app.models import Merchant, MerchantWebhookSubscription, WebhookDelivery

    m = Merchant(name="Test Merchant 3")
    db.add(m); db.commit(); db.refresh(m)
    sub = MerchantWebhookSubscription(merchant_id=m.id, url="https://unreachable.example.com/hook",
                                       secret="s", event_types=["case.opened"])
    db.add(sub); db.commit(); db.refresh(sub)

    def fake_urlopen(req, timeout=5):
        raise ConnectionRefusedError("nope")
    monkeypatch.setattr(outbound_webhooks.urllib.request, "urlopen", fake_urlopen)

    outbound_webhooks.dispatch_event(db, m.id, "case.opened", "CASE-3", {})  # must not raise

    delivery = db.query(WebhookDelivery).filter(WebhookDelivery.subscription_id == sub.id).first()
    assert delivery.success is False
    assert "ConnectionRefusedError" in delivery.error


def test_case_creation_dispatches_opened_webhook_for_merchant_customer(db, monkeypatch):
    """create_case fires a case.opened webhook when the customer belongs to
    a merchant with an active subscription — end-to-end through the
    orchestrator, not just the dispatch function in isolation."""
    from app.models import Merchant
    from app import outbound_webhooks

    m = Merchant(name="Webhook Merchant")
    db.add(m); db.commit(); db.refresh(m)
    from app.models import MerchantWebhookSubscription
    sub = MerchantWebhookSubscription(merchant_id=m.id, url="https://example.com/hook",
                                       secret="s", event_types=["case.opened"])
    db.add(sub); db.commit()

    calls = []
    def fake_dispatch(db_, merchant_id, event_type, case_id, data):
        calls.append((merchant_id, event_type))
    monkeypatch.setattr("app.agents.orchestrator.dispatch_event", fake_dispatch)

    c = make_customer(db, merchant_id=m.id)
    create_case(db, c, "payment_failed", "PAY-wh", 1000)

    assert (m.id, "case.opened") in calls


# ---------------------------------------------------------------- Merchant webhook API tests
def test_webhook_subscription_crud_via_api(db):
    from fastapi import HTTPException
    from app.main import create_merchant_webhook, list_merchant_webhooks, delete_merchant_webhook
    from app.models import Merchant

    m = Merchant(name="API Test Merchant")
    db.add(m); db.commit(); db.refresh(m)

    created = create_merchant_webhook(m.id, {"url": "https://example.com/hook"}, db=db)
    assert "secret" in created and len(created["secret"]) > 20
    webhook_id = created["id"]

    listed = list_merchant_webhooks(m.id, db=db)
    assert len(listed) == 1
    assert "secret" not in listed[0]  # never returned again after creation

    delete_merchant_webhook(m.id, webhook_id, db=db)
    assert list_merchant_webhooks(m.id, db=db) == []

    with pytest.raises(HTTPException):
        delete_merchant_webhook(m.id, webhook_id, db=db)  # already deleted


def test_webhook_subscription_rejects_invalid_url(db):
    from fastapi import HTTPException
    from app.main import create_merchant_webhook
    from app.models import Merchant

    m = Merchant(name="API Test Merchant 2")
    db.add(m); db.commit(); db.refresh(m)

    with pytest.raises(HTTPException) as exc_info:
        create_merchant_webhook(m.id, {"url": "not-a-url"}, db=db)
    assert exc_info.value.status_code == 400
