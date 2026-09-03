# Product Decisions

This document records the product-level decisions behind RecoveryOS — the
choices a human made about *what the system should do*, independent of
how any particular function happens to implement it. Where the codebase
doesn't yet fully match a decision recorded here, that gap is called out
explicitly rather than glossed over.

## The problem

Merchants lose revenue when legitimate payment failures are not
intelligently recovered. A payment gateway dashboard reports that a
payment failed and stops there — it doesn't investigate why, decide what
to do about it, verify the outcome, or know when to stop trying. That
work is left to the merchant, manually, or not done at all.

The system's job: identify which failures are worth recovering, estimate
whether recovery is worthwhile, select an appropriate recovery strategy,
execute it safely, and measure the outcome — with every step
explainable after the fact.

## 1. What constitutes a recoverable payment?

Not every failure deserves the same response, and blind retry-on-failure
is actively harmful (it wastes gateway-retry budget on failures that can
never succeed, and annoys customers with repeated contact for issues a
retry can't fix). The system classifies every failure into one of four
conceptual categories before deciding anything:

| Category | Meaning | Example decline reasons | Response |
|---|---|---|---|
| **Transient failure** | Infrastructure hiccup, not a real problem with the payment | `bank_technical_error`, `gateway_technical_error`, `request_timed_out`, `payment_declined_due_to_high_traffic` | Retry — often immediately |
| **Recoverable customer/method issue** | A real problem, but one the customer can fix | `insufficient_funds`, `card_expired`, `incorrect_otp`, `payment_cancelled`, `transaction_daily_limit_exceeded` | A targeted intervention (method update, reminder, delayed retry) — never a same-card immediate retry for the ones a retry can't fix |
| **Non-recoverable failure** | No customer- or merchant-side action changes the outcome, or acting again risks compliance/reputational harm | `payment_risk_check_failed`, `compliance_violation`, `international_transaction_not_allowed`, a card actively blocked by the issuer | Escalate to a human, or route to a method-update path — never auto-retried |
| **Uncertain failure** | Root cause can't be determined with confidence | An unrecognized gateway error, or a diagnosis-engine failure | Escalate to a human rather than guess |

This mapping is implemented in `app/decline_codes.py` (~90 real Razorpay
`error.reason` codes classified into internal buckets) and
`app/agents/ai_service.py`'s `cause_map`/`strategy_by_cause` tables. The
decisive rule: **a retry is never the chosen strategy for a category
where a retry provably cannot help.** `app/providers/payment.py`'s
`RETRY_SUCCESS_PROBABILITY` encodes this even at the simulation level —
an expired card has a `0.0` retry-success probability by construction,
not by chance.

## 2. What makes an action worthwhile?

The system computes an explicit expected value for the leading candidate
action before deciding whether it's worth attempting:

```
Expected Recovery Value =
    P(recovery) × recoverable_amount
    − action_cost
    − customer_annoyance_cost
    − risk_cost
```

Implemented in `app/policies/expected_value.py::compute_expected_value`.
`P(recovery)` is the ML model's prediction when a trained model is
available, falling back to the diagnosis confidence otherwise (see
ADR-005 for what the model does and doesn't decide). `action_cost` is a
per-action-type operational cost (gateway retry fee, message-send cost);
`annoyance_cost` grows with how many times this customer has already
been contacted for this case; `risk_cost` applies to strategies that
carry reputational risk if used carelessly on a low-confidence diagnosis.

**Deliberate design choice: this number is advisory, not a hard gate.**
See ADR-003 for the full reasoning — in short, a single scalar EV number
is a useful decision-support signal for a human reviewer and the
strategy re-ranker, but making it an autonomous kill-switch on payment
actions would hand a soft, model-derived number the same authority as the
deterministic policy engine's exact rules (max attempts, amount
thresholds), which is a category error this project deliberately avoids.
`tests/test_core.py::test_expected_value_never_gates_execution` asserts
the policy engine's source never even imports this module.

## 3. What can the AI/LLM do?

The LLM (or the deterministic rule engine that stands in for it in demo
mode — see below) may:

- Interpret a failure and propose a root cause.
- Explain its reasoning in a short, human-readable summary.
- Recommend a recovery strategy from a fixed, closed vocabulary.
- Contribute a confidence score for its own diagnosis.

The LLM must **not**, and in this implementation cannot, do any of the
following:

- Call a payment provider, communication provider, or any other external
  API directly. `app/agents/ai_service.py` has no import of
  `app.providers.*` — structurally, not just by convention.
- Choose a root cause or strategy outside a fixed allow-list
  (`ALLOWED_ROOT_CAUSES`, `ALLOWED_STRATEGIES` in `ai_service.py`). Output
  outside that list is discarded, not coerced or partially trusted.
- Bypass the deterministic policy engine. Every action, regardless of
  which layer recommended it, passes through `check_policy()` before
  execution — see ADR-001.
- Silently take over on failure. If the LLM call fails, times out, or
  returns malformed output, the system falls back to the deterministic
  rule engine, which is always available. It never stalls, crashes, or
  guesses (`tests/test_core.py::test_diagnosis_failure_escalates_instead_of_crashing`).

## 4. What does the deterministic system control?

Everything with a financial or safety consequence is deterministic, plain
Python code — never an LLM call, and never advisory-only:

| Control | Why it must be deterministic | Where |
|---|---|---|
| Maximum retry count | An unbounded retry loop can drain a customer's patience or a merchant's gateway-fee budget indefinitely; a fixed, auditable ceiling is a promise the system can actually keep | `app/policies/engine.py::check_policy` (`max_attempts`) |
| Payment state transitions | A `captured` payment must never be treated as `failed` again — this is the exact class of bug that causes double-charges or false "recovered" claims | `app/payment_state_machine.py` |
| Webhook validation | An unauthenticated or replayed webhook is an attacker's easiest way to fabricate a "payment succeeded" or trigger unwanted actions | `app/security.py::verify_webhook_signature`, `app/webhooks.py` idempotency |
| Authorization | A machine caller (`X-API-Key`) or a logged-in human (session token) must be provably who they claim, not merely "an LLM decided to trust this request" | `app/security.py`, `app/auth.py` |
| Tenant isolation | One merchant must never see or act on another merchant's data — a data model concern, not a judgment call an LLM could safely make case-by-case | Merchant → Customer FK scoping, `app/main.py` query filters |
| Allowed recovery strategies | The LLM proposes from a closed list; it cannot invent a new action type the executor doesn't know how to run safely | `ALLOWED_STRATEGIES`, `ACTION_TYPE_BY_STRATEGY` |
| Financial thresholds | A large-amount or discount-bearing action requires a human sign-off by fixed rule, not by asking the LLM if it feels confident | `app/policies/engine.py` (`large_amount_threshold`, discount gates) |
| Idempotency | The same webhook event, delivered twice (a real-world guarantee, not an edge case), must never create two cases or send two messages | `WebhookEvent.event_id` uniqueness, checked before any side effect |
| Pre-action safety check | A diagnosis made seconds or minutes ago can be stale by the time the action fires — re-verify the payment's actual current state immediately before acting | `app/payment_state_machine.py::verify_before_action`, called at the top of `execute_next_action` |

## Demo mode vs. what a merchant would actually configure

Every integration above (Razorpay, the LLM, webhook signing, dashboard
login) is **off by default** and enabled by setting one environment
variable — see `.env.example`. This is a deliberate choice: the system
must be evaluable with zero external credentials, and "off" must mean
genuinely off (a mock/rule-engine fallback), not a silently degraded
version of "on." See `docs/ARCHITECTURE_DECISIONS.md` (ADR-006) for how
simulated data is kept structurally separate from anything a live
integration would touch.
