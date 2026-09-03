"""
Provider adapter pattern. `PaymentProvider` is the interface; `MockPaymentProvider`
is the demo-mode implementation. A real Stripe/Razorpay adapter would implement
the same interface and be swapped in via config — the agent never talks to a
provider SDK directly.
"""
import os
import random
from abc import ABC, abstractmethod

# Retry success probabilities by root cause — used ONLY by the mock provider to
# decide whether a *simulated* retry succeeds. The agent/business logic never
# invents a recovered amount; it only records what this provider reports back.
RETRY_SUCCESS_PROBABILITY = {
    "temporary_failure": 0.75,
    "insufficient_funds": 0.20,
    "expired_card": 0.0,          # a retry can never fix an expired card
    "invalid_payment_method": 0.05,
    "bank_decline": 0.30,
    "authentication_failure": 0.40,
    "customer_hesitation": 0.50,          # payment_cancelled: often just needs a nudge
    "transaction_limit_exceeded": 0.35,   # clears after a cooldown, not instantly
    "risk_or_compliance_decline": 0.0,    # never auto-retried — see ai_service escalation
    "unknown": 0.15,
}

# After the customer takes the requested action (updates card / confirms
# checkout / promises to pay), probability that the *next* retry succeeds.
POST_INTERVENTION_SUCCESS = {
    "payment_method_update": 0.80,
    "checkout_recovery_message": 0.55,
    "friendly_reminder": 0.45,
    "stronger_reminder": 0.35,
    "promise_to_pay_request": 0.60,
    "invoice_escalation": 0.50,
}


class PaymentProvider(ABC):
    @abstractmethod
    def retry_payment(self, payment_id: str, amount: float, root_cause: str) -> dict:
        ...

    @abstractmethod
    def create_payment_link(self, amount: float, currency: str, customer: dict | None = None,
                             description: str = "Payment recovery") -> dict:
        ...

    @abstractmethod
    def get_payment_status(self, payment_id: str) -> dict:
        ...


class MockPaymentProvider(PaymentProvider):
    """Simulates a real payment gateway deterministically-enough for a demo,
    while still being probabilistic so the same case doesn't always play out
    identically."""

    def retry_payment(self, payment_id: str, amount: float, root_cause: str,
                       intervention: str | None = None) -> dict:
        if intervention and intervention in POST_INTERVENTION_SUCCESS:
            p = POST_INTERVENTION_SUCCESS[intervention]
        else:
            p = RETRY_SUCCESS_PROBABILITY.get(root_cause, 0.15)
        success = random.random() < p
        return {
            "provider": "mock",
            "provider_payment_id": payment_id,
            "status": "succeeded" if success else "failed",
            "amount": amount,
            "success_probability_used": p,
        }

    def create_payment_link(self, amount: float, currency: str, customer: dict | None = None,
                             description: str = "Payment recovery") -> dict:
        return {
            "provider": "mock",
            "link": f"https://pay.mock.recoverai.dev/{random.randint(100000,999999)}",
            "amount": amount,
            "currency": currency,
            "status": "created",
        }

    def get_payment_status(self, payment_id: str) -> dict:
        return {"provider_payment_id": payment_id, "status": "unknown"}


class RazorpayPaymentProvider(PaymentProvider):
    """Real Razorpay integration (test or live mode, whichever API keys are
    configured) against the documented REST API — https://api.razorpay.com/v1/
    — with HTTP Basic Auth (key_id:key_secret), no SDK needed.

    Razorpay has no server-initiated retry API — only the customer can retry
    a failed payment. So `retry_payment` issues a fresh Payment Link instead
    and reports it "pending", never an instant success. `get_payment_status`
    can then be polled (webhook or follow-up check) to see if they paid.
    """

    BASE_URL = "https://api.razorpay.com/v1"

    def __init__(self, key_id: str, key_secret: str):
        self.key_id = key_id
        self.key_secret = key_secret

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        import base64
        import json
        import urllib.request
        import urllib.error

        url = f"{self.BASE_URL}{path}"
        data = json.dumps(body).encode() if body is not None else None
        auth = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode()
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = json.loads(e.read())
            return {"error": error_body.get("error", {}), "status_code": e.code}

    def retry_payment(self, payment_id: str, amount: float, root_cause: str,
                       intervention: str | None = None) -> dict:
        # see class docstring — no retry API, so issue a fresh link instead
        link_result = self.create_payment_link(amount, "INR")
        return {
            "provider": "razorpay",
            "provider_payment_id": payment_id,
            "status": "pending_customer_action",
            "amount": amount,
            "payment_link": link_result.get("link"),
            "note": "Razorpay has no server-initiated retry; a fresh payment link was issued instead.",
        }

    def create_payment_link(self, amount: float, currency: str, customer: dict | None = None,
                             description: str = "Payment recovery") -> dict:
        # Razorpay amounts are in the smallest currency subunit (paise for INR).
        payload = {
            "amount": int(round(amount * 100)),
            "currency": currency,
            "description": description,
            "notify": {"sms": True, "email": True},
            "reminder_enable": True,
        }
        if customer:
            payload["customer"] = {k: v for k, v in customer.items() if v}
        result = self._request("POST", "/payment_links/", payload)
        if "error" in result:
            return {"provider": "razorpay", "status": "failed", "error": result["error"]}
        return {
            "provider": "razorpay",
            "link": result.get("short_url"),
            "payment_link_id": result.get("id"),
            "amount": amount,
            "currency": currency,
            "status": result.get("status"),
        }

    def get_payment_status(self, payment_id: str) -> dict:
        result = self._request("GET", f"/payments/{payment_id}")
        if "error" in result:
            return {"provider_payment_id": payment_id, "status": "unknown", "error": result["error"]}
        return {
            "provider_payment_id": payment_id,
            "status": result.get("status"),  # created/authorized/captured/refunded/failed
            "amount": (result.get("amount") or 0) / 100,
            "method": result.get("method"),
        }

    def create_order(self, amount_paise: int, currency: str = "INR", receipt: str | None = None) -> dict:
        """Creates a Razorpay Order for Standard (inline modal) Checkout —
        distinct from create_payment_link(), which issues a hosted/redirect
        link. `amount_paise` is already in the smallest currency subunit
        (matches what the Checkout JS SDK and the Orders API both expect),
        unlike create_payment_link()'s rupee-amount convention."""
        payload = {"amount": amount_paise, "currency": currency}
        if receipt:
            payload["receipt"] = receipt
        result = self._request("POST", "/orders", payload)
        if "error" in result:
            return {"provider": "razorpay", "status": "failed", "error": result["error"],
                     "status_code": result.get("status_code")}
        return {
            "provider": "razorpay",
            "order_id": result.get("id"),
            "amount": result.get("amount"),
            "currency": result.get("currency"),
            "status": result.get("status"),
        }


def verify_payment_signature(order_id: str, payment_id: str, signature: str, key_secret: str) -> bool:
    """Verifies the HMAC-SHA256 signature Razorpay Standard Checkout returns
    on payment success: HMAC-SHA256("{order_id}|{payment_id}", key_secret),
    compared in constant time. See
    https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/build-integration/#3-verify-payment-signature"""
    import hashlib
    import hmac

    expected = hmac.new(key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _select_provider() -> PaymentProvider:
    key_id = os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")
    if key_id and key_secret:
        return RazorpayPaymentProvider(key_id, key_secret)
    return MockPaymentProvider()


payment_provider: PaymentProvider = _select_provider()
