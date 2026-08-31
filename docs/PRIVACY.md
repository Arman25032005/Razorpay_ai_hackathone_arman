# Privacy

## What is stored, and why

| Data | Why stored | Where |
|---|---|---|
| Customer name, email, phone | Needed to send a recovery message / payment link to the right person | `customers` table |
| Payment amount, currency, status, failure reason | Core to detecting and diagnosing revenue-at-risk | `payments` table |
| Customer payment history (counts, success rate) | Needed to diagnose root cause and compute recovery probability | derived from `payments` at query time, not separately stored |
| Recovery messages sent | Audit requirement — merchant needs to see what was said to their customer | `recovery_actions.message_body` |
| Full audit trail of every agent decision | Explainability + compliance requirement (spec section 25) | `audit_events` |

## What is explicitly NOT stored

- **Full card numbers, CVV, or any sensitive authentication data.** This
  system never receives or stores raw card data — Razorpay's Payment
  Links flow means the customer enters payment details directly with
  Razorpay's hosted checkout, never through this application. We only
  ever see `payment_id` and payment status.
- **Unnecessary PII beyond what's needed for the identified use above.**
  No storage of addresses, government IDs, or other identity documents.

## Known customer vs. unknown customer (spec section 10)

Not every payment event has a persistent customer identity — a one-off
UPI QR payment may not be tied to a returning customer. The system does
not fabricate identity: `Customer` records are created from whatever
identity information is genuinely present in the event (email/phone), and
`app.agents.ai_service.customer_health_score()` explicitly returns a
neutral score with reason "No payment history yet" rather than inventing
a plausible-sounding history for a customer we don't actually know.

## Retention (documented policy, not yet enforced in code)

**Honest gap**: retention and deletion are currently only documented
intent, not an implemented job. A production deployment should add:

- Recovery cases and audit events: retain for the length of any legal/
  compliance requirement for the merchant's jurisdiction (commonly
  5-7 years for financial records) — not yet configurable.
- Customer records with no activity for an extended period (e.g. 3 years):
  candidate for anonymization (retain aggregate statistics, drop
  name/email/phone) — not yet implemented.
- No automatic deletion job currently exists. This is a roadmap item, not
  a claimed feature.

## Anonymization for the demo/test dataset

The synthetic ML training dataset (`ml/data_generator.py`) contains **no
real customer data at all** — every row is procedurally generated, never
sourced from real Razorpay merchant data, precisely because real
production data was never available or used during this build (per the
constraint "never use real customer/payment data during development").

## Masked identifiers

Where a customer identifier appears in logs or audit descriptions, it is
the internal generated ID (`CUST-...`) or the customer's own provided
email/name — never a payment gateway's internal token exposed
unnecessarily. Full payment provider IDs (`pay_...`) are stored (needed
for status verification) but never logged alongside sensitive
authentication data, because none is ever received.
