# Razorpay Integration

This document is the single source of truth for what our Razorpay
integration actually does, verified against Razorpay's official public
documentation (razorpay.com/docs) as of this writing. Nothing here is
invented or assumed — where the API doesn't support something we need, that
is stated explicitly, with the mock fallback clearly labeled.

**Test Mode vs Live Mode**: identical code path. `RazorpayPaymentProvider`
is instantiated with whichever `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET`
pair is in the environment — Razorpay's test-mode and live-mode keys use
the same API shape, so there is no code branching between them, only key
rotation. **This project has been developed and tested entirely against
the mock provider; the Razorpay code paths below have been verified for
request-shape correctness (see "How we verified this" per section) but not
against a live `api.razorpay.com` round-trip, since no test-mode
credentials were available during development.** Wiring in real test-mode
keys and confirming the live round-trip is the first thing to do before
demoing this integration as "live."

---

## 1. Authentication

- **Method**: HTTP Basic Auth, `key_id:key_secret`, base64-encoded in the
  `Authorization` header.
- **Where obtained**: Razorpay Dashboard → Settings → API Keys. Test-mode
  keys are generated instantly, no business verification required.
- **Our implementation**: `RazorpayPaymentProvider.__init__(key_id, key_secret)`
  in `app/providers/payment.py`; `_request()` builds the Basic Auth header
  on every call. Keys are read from environment variables only
  (`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`) — never hardcoded, never
  logged.

## 2. Payment Links API — `create_payment_link()`

| | |
|---|---|
| Endpoint | `POST https://api.razorpay.com/v1/payment_links/` |
| Auth | HTTP Basic (key_id:key_secret) |
| Request fields used | `amount` (integer, **paise** — smallest currency subunit), `currency`, `description`, `customer.{name,contact,email}`, `notify.{sms,email}`, `reminder_enable` |
| Response fields used | `id` (`plink_...`), `short_url`, `status` |
| Test-mode behavior | Fully functional in test mode; generates a real, clickable test checkout link that accepts Razorpay's documented test card/UPI/netbanking credentials. |
| Automatable? | **Yes** — this is the core live action our agent takes. |
| Our implementation | `RazorpayPaymentProvider.create_payment_link()` in `app/providers/payment.py`, wired to `POST /api/recovery-cases/{id}/send-payment-link` |

**Important detail we got wrong on the first pass and fixed**: amounts
must be in the smallest currency subunit (paise for INR), not rupees. A
₹4,999 payment link is `"amount": 499900`, not `499`. `create_payment_link()`
does `int(round(amount * 100))` and this is covered by a test
(`test_normalize_razorpay_payload_converts_paise_to_rupees` and the
provider-level request-shape test) that asserts the exact conversion.

## 3. Payments API — `get_payment_status()`

| | |
|---|---|
| Endpoint | `GET https://api.razorpay.com/v1/payments/{id}` |
| Auth | HTTP Basic |
| Response fields used | `status` (`created` / `authorized` / `captured` / `refunded` / `failed`), `amount` (paise), `method` |
| Automatable? | **Yes** — read-only status check. |
| Our implementation | `RazorpayPaymentProvider.get_payment_status()`. This is the call the pre-action verification gate (`app/payment_state_machine.py::verify_before_action`) is *designed* to use in live mode before executing any recovery action — see section 8 below on what's mocked vs real. |

## 4. "Retry a failed payment" — NOT a real Razorpay API

**This does not exist.** Razorpay has no server-initiated "retry this
payment" endpoint — a failed payment can only be retried by the customer
themselves, through a checkout flow. We verified this against Razorpay's
documentation and did not find, and are not assuming the existence of, any
merchant-initiated retry API.

**What we do instead (clearly labeled, not faked)**: in live mode,
`RazorpayPaymentProvider.retry_payment()` issues a **fresh Payment Link**
for the same amount (the realistic live-mode equivalent of "give the
customer another chance to pay") and returns
`{"status": "pending_customer_action", ...}` — never an instant fabricated
success. The mock provider's `retry_payment()` (`MockPaymentProvider`, used
in demo mode) *does* simulate an instant probabilistic outcome, and is
explicitly documented as simulation-only in its docstring — this
distinction is the whole point of the mock/live split.

## 5. Webhooks — real payload shape, not invented

**Endpoint we expose**: `POST /api/webhooks/payment` (also `/api/webhooks/checkout`,
`/api/webhooks/invoice` for non-Razorpay event sources, and a generic
`/api/events`).

**Razorpay's actual documented webhook payload** (payment.failed example,
taken directly from their published docs):

```json
{
  "entity": "event",
  "account_id": "acc_DDiURNtiQ5kFsb",
  "event": "payment.failed",
  "contains": ["payment"],
  "payload": {
    "payment": {
      "entity": {
        "id": "pay_DJX28SME8U3BJ3",
        "amount": 500,
        "currency": "INR",
        "status": "failed",
        "method": "card",
        "email": "gaurav.kumar@example.com",
        "contact": "+919999999998",
        "customer_id": "cust_DG44HEGMbfRm1N",
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Payment failed",
        "created_at": 1568781321
      }
    }
  },
  "created_at": 1568781323
}
```

`app/webhooks.py::_normalize_razorpay_payload()` detects this exact shape
(`entity == "event"`) and flattens it into our internal event format:
converts `amount` from paise to rupees, and maps `error_code` /
`error_description` to our internal root-cause vocabulary (checking the
specific `error_description` text first, since Razorpay's `error_code` is
a coarse top-level class — `BAD_REQUEST_ERROR` alone covers many distinct
underlying reasons including card expiry, invalid CVV, etc. — and falling
back to `error_code` only when no description is present).

**Events we handle**: `payment.failed`, `payment.captured` (mapped to our
internal PAYMENT_SUCCEEDED). Razorpay's full event list includes many more
(`payment.authorized`, `refund.created`, `payment.dispute.created`, etc.)
that we do not currently subscribe to or process — documented here as
scope, not silently ignored.

**Signature validation**: Razorpay signs webhook payloads with HMAC-SHA256
over the raw request body, sent as the `X-Razorpay-Signature` header, using
a secret you configure separately per-webhook in the Razorpay Dashboard
(Settings → Webhooks) — this secret does **not** have to match your API
key secret. Our `app/security.py::verify_webhook_signature()` implements
exactly this scheme (HMAC-SHA256, constant-time comparison via
`hmac.compare_digest`), and `main.py`'s webhook endpoints accept the
signature via either `X-Razorpay-Signature` or the generic
`X-Webhook-Signature` header. **No-op if `PAYMENT_WEBHOOK_SECRET` is unset**
— set it to require signed webhooks.

**Idempotency**: raw Razorpay webhook payloads don't always carry a
distinct top-level `event_id` the way our simplified internal format does.
We derive a stable one: `f"razorpay:{payment_id}:{event_type}"`. The same
payload delivered twice (Razorpay explicitly documents delivery retries
with exponential backoff on non-2xx responses) produces the same derived
ID and is rejected as a duplicate before any side effect — verified by
`test_razorpay_webhook_end_to_end_idempotent`.

## 6. Payment lifecycle / state reconciliation

Razorpay's actual documented payment states: `created` → `authorized` →
`captured`, with `failed` and `refunded`/`partially_refunded` as
additional states. Critically, **Razorpay explicitly documents that a
`payment.failed` webhook can be followed by a `payment.captured` webhook
for the *same* transaction** — late authorization, or a customer retrying
a UPI payment through their app after an initial decline. This is not an
edge case we're guessing at; it's in their FAQ.

Our `app/payment_state_machine.py::PaymentState` enum and
`apply_transition()` function encode this directly: `FAILED → CAPTURED` is
a legal transition, but a late `AUTHORIZED` event arriving after we've
already recorded `CAPTURED` is correctly rejected as stale (lower rank),
never allowed to downgrade the payment. See `docs/DATABASE.md` §3 for how
this maps onto our stored `Payment.status` field.

## 7. What this means for the agent: verify-before-act

Because failed payments can resolve independently of our agent (customer
retries on their own, a late webhook arrives), **every recovery action
re-checks the payment's current status immediately before executing** —
see `execute_next_action()` in `app/agents/orchestrator.py`. If the check
shows the payment is already `captured`, the action is cancelled and the
case is marked recovered with stop reason `PAYMENT_ALREADY_RESOLVED` —
never silently executed against a stale assumption, and never claimed as
"our agent recovered this" when it was actually a customer self-resolution.
This is exercised end-to-end by "Scenario H" in the demo simulation and
covered by `test_graceful_cancellation_when_payment_already_captured`.

## 8. Honest summary: mock vs. real, right now

| Capability | Real Razorpay API used? | Status |
|---|---|---|
| Create Payment Link | Yes, when `RAZORPAY_KEY_ID`/`SECRET` set | Request shape verified against docs; not yet round-tripped against live `api.razorpay.com` |
| Get payment status | Yes, when keys set | Implemented, same caveat |
| Webhook ingestion (Razorpay shape) | Yes — parses their real documented payload | Verified against their actual published example payload |
| Webhook signature verification | Yes — their real HMAC-SHA256 scheme | Implemented, tested with a self-computed signature (not a live Razorpay-issued one) |
| "Retry a failed payment" | **No such API exists** — see section 4 | Live mode issues a fresh Payment Link instead; mock mode simulates a probabilistic instant outcome, explicitly labeled |
| SMS/WhatsApp notifications | No | Mock only — no live provider wired in yet (documented roadmap item) |

**Next step to make this fully "live"**: obtain a Razorpay test-mode key
pair, set `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`, point a test webhook at
this app's `/api/webhooks/payment`, and confirm the round trip against
Razorpay's own test-mode simulated payments. Everything on our side is
built to Razorpay's documented shape and ready for that step; it simply
hasn't been executed against their live servers in this environment.
