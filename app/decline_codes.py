"""
Razorpay payment-failure decline-code classification.

Real payment gateways (Razorpay included) don't return one generic "payment
failed" — every failed payment carries a specific `error.reason` value from
a documented taxonomy (https://razorpay.com/docs/errors/payments/list/,
~90 distinct reasons across BAD_REQUEST_ERROR and GATEWAY_ERROR categories).
Treating "insufficient funds" and "card blocked by issuer" as the same
generic failure throws away the single most useful signal for deciding
*how* to recover a payment: some reasons are worth an immediate retry,
some need a different card, some need customer action, and some (risk/
compliance declines) must never be auto-retried at all.

This module maps Razorpay's real documented reason codes down to
RecoveryOS's internal `failure_reason` vocabulary (consumed by
app.agents.ai_service._diagnose_payment_failure), so diagnosis and
strategy selection are driven by the actual decline semantics rather than
a single catch-all bucket.
"""

# Razorpay error.reason -> our internal failure_reason bucket.
# Grouped by what actually happened, not by Razorpay's error.code (which is
# too coarse — BAD_REQUEST_ERROR alone covers card expiry, wrong CVV, and
# risk declines, which need completely different recovery strategies).
RAZORPAY_REASON_TO_FAILURE_BUCKET = {
    # -- expired / bad card details: no retry can fix these, need a new card --
    "card_expired": "card_expired",
    "card_number_invalid": "card_expired",
    "incorrect_card_expiry_date": "card_expired",
    "incorrect_cardholder_name": "card_expired",
    "card_type_invalid": "card_expired",

    # -- insufficient funds: worth retrying later, not right now --
    "insufficient_funds": "insufficient_funds",

    # -- authentication (OTP/3DS/PIN) failed: customer-side, often fixable on next try --
    "authentication_failed": "auth_failed",
    "incorrect_otp": "otp_failed",
    "otp_expired": "otp_failed",
    "otp_attempts_exceeded": "otp_failed",
    "incorrect_cvv": "auth_failed",
    "incorrect_pin": "auth_failed",
    "pin_attempts_exceeded": "otp_failed",
    "pin_not_set": "auth_failed",

    # -- card/instrument actively declined or blocked by the issuer: needs a different card --
    "card_declined": "card_declined_by_issuer",
    "debit_instrument_blocked": "card_declined_by_issuer",
    "debit_instrument_inactive": "card_declined_by_issuer",
    "debit_declined": "card_declined_by_issuer",
    "credit_not_permitted": "card_declined_by_issuer",
    "card_network_not_enabled": "card_declined_by_issuer",
    "card_not_enrolled": "card_declined_by_issuer",

    # -- generic bank/issuer decline without a more specific reason --
    "payment_declined": "bank_declined",
    "payment_failed": "bank_declined",
    "credit_limit_exceeded": "bank_declined",
    "credit_limit_expired": "bank_declined",
    "credit_limit_inactive": "bank_declined",
    "credit_limit_not_approved": "bank_declined",

    # -- invalid payment method / VPA / bank details on our side --
    "invalid_vpa": "invalid_method",
    "vpa_resolution_failed": "invalid_method",
    "bank_account_invalid": "invalid_method",
    "bank_account_validation_failed": "invalid_method",
    "invalid_amount": "invalid_method",
    "invalid_currency": "invalid_method",
    "input_validation_failed": "invalid_method",

    # -- customer explicitly backed out: not a failure, just hesitation --
    "payment_cancelled": "payment_cancelled",
    "payment_timed_out": "payment_cancelled",
    "payment_session_expired": "payment_cancelled",
    "collect_request_pending": "payment_cancelled",
    "payment_collect_request_expired": "payment_cancelled",

    # -- transaction/velocity limits: will very likely clear after a cooldown --
    "transaction_daily_limit_exceeded": "limit_exceeded",
    "transaction_limit_exceeded": "limit_exceeded",
    "transaction_frequency_limit_exceeded": "limit_exceeded",
    "transaction_daily_count_exceeded": "limit_exceeded",
    "mcc_amount_limit_exceeded": "limit_exceeded",
    "emi_greater_than_max_amount": "limit_exceeded",

    # -- risk / compliance declines: must never be auto-retried --
    "payment_risk_check_failed": "risk_declined",
    "compliance_violation": "risk_declined",
    "international_transaction_not_allowed": "risk_declined",
    "transaction_on_vpa_restricted": "risk_declined",

    # -- transient infrastructure failures at the bank/gateway/issuer: retry soon --
    "bank_technical_error": "network_timeout",
    "gateway_technical_error": "network_timeout",
    "issuer_technical_error": "network_timeout",
    "server_error": "network_timeout",
    "request_timed_out": "network_timeout",
    "bank_not_available": "network_timeout",
    "bank_cutoff_in_progress": "network_timeout",
    "psp_not_available": "network_timeout",
    "psp_app_not_available": "network_timeout",
    "invalid_response_from_gateway": "network_timeout",
    "payment_declined_due_to_high_traffic": "network_timeout",
}

# error.code (Razorpay's coarse top-level category) -> fallback bucket, used
# only when error.reason is missing or unrecognized.
RAZORPAY_ERROR_CODE_FALLBACK = {
    "BAD_REQUEST_ERROR": "unrecognized_gateway_error",
    "GATEWAY_ERROR": "network_timeout",
    "SERVER_ERROR": "network_timeout",
}

# Buckets that must never be selected for a payment_retry / immediate-retry
# strategy — retrying without a different intervention would just fail
# again (or, for risk_declined, could compound a compliance problem).
NON_RETRYABLE_BUCKETS = {
    "card_expired", "card_declined_by_issuer", "invalid_method", "risk_declined",
}

# Human-readable label for each bucket — used in audit-trail descriptions so
# a reviewer sees the real decline reason, not just an internal slug.
BUCKET_DESCRIPTIONS = {
    "card_expired": "Card expired or has invalid/unreadable details",
    "insufficient_funds": "Customer had insufficient funds at time of charge",
    "auth_failed": "3D Secure / CVV / PIN authentication failed",
    "otp_failed": "OTP authentication failed, expired, or attempts exceeded",
    "card_declined_by_issuer": "Card actively declined or blocked by the issuing bank",
    "bank_declined": "Payment declined by the bank/issuer for an unspecified reason",
    "invalid_method": "Invalid payment method details (VPA, bank account, or malformed request)",
    "payment_cancelled": "Customer abandoned or cancelled the payment before completion",
    "limit_exceeded": "Transaction, daily, or velocity limit exceeded on the card/account",
    "risk_declined": "Blocked by risk or compliance checks — requires human review",
    "network_timeout": "Transient technical failure at the bank, gateway, or issuer",
    "temporary_failure": "Transient technical failure",
    "unrecognized_gateway_error": "Gateway returned an error outside our known taxonomy",
}


def classify_decline(reason: str | None = None, error_code: str | None = None,
                      description: str | None = None) -> str:
    """Maps a Razorpay payment-failure error into our internal
    `failure_reason` vocabulary. Tries, in order: the authoritative
    `error.reason` code (exact match against real Razorpay reason strings),
    then the coarser `error.code` category, then a keyword search over the
    human-readable `error.description` as a last resort — so an
    unrecognized-but-descriptive payload still classifies sensibly instead
    of collapsing to "unknown"."""
    if reason and reason in RAZORPAY_REASON_TO_FAILURE_BUCKET:
        return RAZORPAY_REASON_TO_FAILURE_BUCKET[reason]

    if error_code and error_code in RAZORPAY_ERROR_CODE_FALLBACK:
        bucket = RAZORPAY_ERROR_CODE_FALLBACK[error_code]
    else:
        bucket = None

    if description:
        desc = description.lower()
        keyword_fallback = [
            ("insufficient", "insufficient_funds"),
            ("expired", "card_expired"),
            ("otp", "otp_failed"),
            ("authentication", "auth_failed"),
            ("declined", "bank_declined"),
            ("blocked", "card_declined_by_issuer"),
            ("cancel", "payment_cancelled"),
            ("limit", "limit_exceeded"),
            ("risk", "risk_declined"),
            ("compliance", "risk_declined"),
            ("invalid", "invalid_method"),
            ("timeout", "network_timeout"),
            ("timed out", "network_timeout"),
        ]
        for keyword, mapped_bucket in keyword_fallback:
            if keyword in desc:
                return mapped_bucket

    return bucket or "unrecognized_gateway_error"


def is_retryable(bucket: str) -> bool:
    """Whether a same-card/same-method immediate retry could plausibly
    succeed for this decline bucket. Strategy selection in
    app.agents.ai_service uses this to route non-retryable buckets to a
    payment-method-update or human-escalation strategy instead of wasting
    an attempt on a doomed retry."""
    return bucket not in NON_RETRYABLE_BUCKETS


def describe(bucket: str) -> str:
    return BUCKET_DESCRIPTIONS.get(bucket, bucket.replace("_", " "))
