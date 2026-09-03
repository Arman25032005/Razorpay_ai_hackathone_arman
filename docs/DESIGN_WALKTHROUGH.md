# Design Walkthrough

A component-by-component explanation of the system, written for a
technical hackathon judge — what each piece does, why it exists in this
shape, what else was possible, and what happens when it fails. Paired
with `docs/PRODUCT_DECISIONS.md` (the business reasoning) and
`docs/ARCHITECTURE_DECISIONS.md` (the ADRs this walkthrough references).

## The agent loop

```
EVENT (webhook or simulation)
  → normalization → risk detection → customer context retrieval
  → diagnosis (rule engine, or LLM if LLM_API_KEY is set)
  → recovery-probability prediction (trained, evaluated logistic regression)
  → expected-value calculation (advisory)
  → strategy selection (re-ranked by historical performance once data exists)
  → pre-action payment-status verification
  → policy check (deterministic, code-enforced)
  → action execution (real Razorpay API, or mock)
  → observe result → measurement → stop / escalate
  → audit log (every step, immutable)
```

This is implemented, not aspirational — `app/agents/orchestrator.py` is
the file that runs this loop, and every arrow above corresponds to a
function call or a logged `AuditEvent` in that file.

### Event normalization (`app/webhooks.py`)

**What it does**: Accepts either a genuine Razorpay webhook payload or a
simplified internal shape, and converts both into one internal event
format — including converting paise to rupees and classifying the
decline reason (`app/decline_codes.py`).

**Why this shape**: Two payload formats exist so the system can be
exercised end-to-end without live Razorpay webhooks (the simplified
shape, used by the simulation and by tests) while still being provably
correct against Razorpay's actual documented payload (the real shape,
verified with real field names and paise conversion).

**Alternative considered**: A single canonical webhook format the
simulation would also have to conform to. Rejected because it would make
tests and the simulation depend on faithfully reconstructing Razorpay's
exact nested JSON shape for every scenario, adding noise without adding
confidence.

**On failure**: An unrecognized `error_code`/`error_reason` falls back to
a keyword search over the description, then to
`"unrecognized_gateway_error"` — never a crash, never a fabricated
category.

### Diagnosis (`app/agents/ai_service.py`)

**What it does**: Given a failure reason and customer context, returns a
root cause, a confidence score, a recommended strategy, and a
plain-language explanation.

**Why an LLM is used here at all**: The rule-engine path already handles
every case deterministically — so the honest answer is that the LLM adds
*explanation quality* and *generalization to decline reasons the rule
table doesn't explicitly enumerate*, not correctness the rule engine
lacks. The rule-engine fallback is not a degraded mode; it's a fully
functional, tested decision path the LLM path must match the output
contract of.

**Why not let the LLM call providers directly**: See ADR-002.

**On failure**: Any exception, timeout, malformed JSON, or output outside
the allowed root-cause/strategy vocabulary causes `_diagnose_via_llm` to
return `None`, which falls through to the deterministic rule engine.
Nothing upstream of this function can tell, from the returned `Decision`
object alone, whether it came from a real model call or the fallback —
by design, so the rest of the pipeline doesn't need a special case for
"the LLM was down."

### Policy engine (`app/policies/engine.py`)

**What it does**: The one function (`check_policy`) that decides whether
a proposed action is allowed to execute, given the case's attempt count,
age, amount, channel, and whether it's already been human-approved.

**Why deterministic code and not a config-driven rules engine (e.g. a
rules DSL, OPA, a decision table service)**: At this scale — a handful of
threshold checks — a general rules engine would be an abstraction with a
learning curve and a dependency, for the same expressive power as five
`if` statements a reviewer can read start to finish in thirty seconds.
`RecoveryPolicy` (a DB row, editable via `/api/policies`) already
provides the "merchant-configurable without a code change" property a
rules engine would be for, without the indirection.

**On failure**: There's no external call in this function — it's pure
Python over already-loaded data — so "failure" here means a bug, not a
runtime fault. That's deliberate: the one function with veto power over
every payment action has the smallest possible failure surface.

### Payment state machine (`app/payment_state_machine.py`)

**What it does**: Encodes which payment-state transitions are valid
(`failed → captured` is Razorpay's documented real sequence; `captured →
failed` is not a legitimate downgrade) and re-verifies a payment's
*current* authoritative status immediately before any action executes.

**Why this exists as its own module**: The single most damaging failure
mode for a recovery agent is acting on a stale assumption — diagnosing a
payment as recoverable, then executing against it minutes later after
the customer already paid through another channel. Making "verify before
acting" a named, tested, reusable function (`verify_before_action`)
rather than an inline check means it can't be silently skipped when a new
action type is added later.

**On failure**: If the pre-action check finds the payment already
resolved, the case is marked `RECOVERED` (if actually captured) or
`STOPPED` (if in some other terminal non-recoverable state) — the
planned action is cancelled, never executed, and the audit trail records
exactly why. This is the "graceful cancellation" scenario referenced
throughout the docs and demo script.

### ML component (`ml/`, `app/agents/ai_service.py::predict_recovery_probability`)

See ADR-005 for the prediction boundary. Practically: a logistic
regression trained on features including customer success rate, payment
count, transaction amount, attempt count, days since last payment, and
one-hot-encoded root cause/strategy, evaluated on a **temporal** (not
random) train/val/test split — because a recovery-probability model
tested on data that leaks future information about the same customer
would look better than it actually is.

**Explainability**: Each prediction returns not just a probability but
the top-3 contributing features (`coefficient × scaled_value`), computed
from the model's real learned weights — not a canned explanation string.

**On failure**: Untrained or missing model artifacts → `None`, and every
caller falls back to the rule engine's own confidence score. No code
path fabricates a probability.

### Outbound webhooks (`app/outbound_webhooks.py`)

**What it does**: Lets a merchant register an endpoint to receive
`case.opened`/`recovered`/`escalated`/`stopped` events, signed with
HMAC-SHA256 — the same authentication scheme this system requires of
Razorpay's *inbound* webhooks to it.

**Why**: A recovery system that only shows results in its own dashboard
is a destination, not infrastructure. Letting a merchant's own systems
(finance reconciliation, CRM, alerting) subscribe to events is the
minimum needed for this to be something a merchant builds on rather than
a UI they have to remember to check.

**On failure**: Delivery is synchronous, best-effort, with a short
timeout — a failed or unreachable merchant endpoint is logged
(`WebhookDelivery`) and never allowed to raise into the recovery
workflow. There is no retry queue; at this project's scale (a demo
sending to at most a handful of registered endpoints), that's an
accepted simplification, not an oversight — see the roadmap in
`docs/PRODUCT.md` for when async delivery would become necessary.

### Dashboard login gate (`app/auth.py`)

**What it does**: A password → short-lived, HMAC-signed session token
exchange. When `DASHBOARD_PASSWORD` is set, every `/api/` route — reads
included — requires either a valid session token or the existing
machine-to-machine `X-API-Key`.

**Why reads too, not just writes**: A login gate that only protects
mutating endpoints still lets an unauthenticated visitor read every
merchant's revenue and customer data through the same dashboard the login
screen is supposedly protecting. That's a common half-measure this
project deliberately didn't ship.

**On failure**: The token is stateless (no server-side session table) —
if `verify_session_token` can't validate the signature or the token has
expired, the request is rejected and the frontend clears its stored token
and returns to the login screen. There is no server outage mode for this
check; it's pure local computation (HMAC verification), not a call to
anything that can be down.

---

## Questions a judge may ask

**Why is this agentic?**
It performs a multi-step loop — diagnose, decide, check policy, act,
verify, measure, stop-or-escalate — without a human in the loop for the
common case, and it can change its own plan mid-flight (the graceful
cancellation case: it diagnosed a retry candidate, then caught that the
payment had already resolved, and cancelled its own plan). It is not
agentic in the sense of an LLM with open-ended tool access; see the next
answer for why that was a deliberate exclusion.

**Why use an LLM here?**
To interpret ambiguous or under-specified failure context and produce a
better plain-language explanation than a template string would, and to
generalize past the rule table's explicit cases. The system is fully
functional with the LLM entirely absent (the deterministic rule engine),
so the honest framing is: the LLM improves diagnosis quality and
explanation, it is not load-bearing for the system to work at all.

**Why not allow the LLM to directly execute payments?**
See ADR-001 and ADR-002. Short version: an LLM can be validated on the
way out (schema-checked, allow-listed), but it can't be made to
*guarantee* it never recommends something unsafe under an input it
hasn't seen. Putting the guarantee in a policy function a human wrote and
a test suite exercises, and never letting the LLM hold provider
credentials, means an LLM failure mode can only ever produce a rejected
recommendation, not an executed one.

**How do you prevent bad AI decisions?**
Three independent layers: (1) output validation — a root cause or
strategy outside the fixed allow-list is discarded, not partially
trusted; (2) the LLM has no provider access, structurally, so it cannot
act even if it wanted to; (3) the policy engine gates every action
regardless of source, checking attempt count, amount thresholds, channel
allow-list, and workflow age — rules an LLM never evaluates.

**How is expected recovery value calculated?**
`P(recovery) × recoverable_amount − action_cost − annoyance_cost −
risk_cost`. See ADR-003 for why it's advisory rather than a hard gate,
and the honest gap that follows from that choice.

**Why use ML? What does the model predict?**
See ADR-005. It predicts one number — the probability a given action on
a given case recovers the payment — and nothing else. It does not choose
strategy, does not authorize actions, and fails closed (`None`, not a
guess) when untrained.

**How would this work with real payment data?**
The model would need retraining on real recovery outcomes, which don't
exist for this project yet — see ADR-006. The decision *pipeline* (policy
engine, state machine, webhook handling) does not depend on the model
being trained on real data; it's already exercised against Razorpay's
real documented API shapes and a live Payment-Links call (see
`docs/RAZORPAY_INTEGRATION.md` for exactly what's verified live vs. not).

**How do you prevent duplicate retries?**
See ADR-004. Inbound webhook events are deduplicated by `event_id` before
any side effect; a case already in a terminal state
(`RECOVERED`/`STOPPED`/`ESCALATED`/`EXPIRED`) short-circuits at the top
of `execute_next_action` regardless of how it's re-invoked.

**How do you handle webhook attacks?**
HMAC-SHA256 signature verification (`PAYMENT_WEBHOOK_SECRET`), constant-
time comparison (`hmac.compare_digest`, not `==`, which would leak timing
information about how much of the signature matched), and a sliding-
window rate limiter on the public webhook endpoints. Signature
verification is opt-in (unset = open, for zero-setup demo mode) — a
production deployment must set the secret; this is stated plainly, not
hidden, in `docs/SECURITY.md`.

**How do you prevent cross-merchant data access?**
Every customer-scoped query filters through `Customer.merchant_id`, and
this is verified with a live test
(`test_dashboard_merchant_filter_excludes_other_tenant_cases`) that
confirms one merchant's data is structurally invisible when the dashboard
is scoped to another. The honest gap: *authorization* for the
case-mutating endpoints (analyze/execute/approve/reject) currently checks
only that a valid credential was presented, not that the credential
belongs to that specific case's merchant — because auth is a single
shared key/password per deployment, not per-merchant identity yet (see
`docs/SECURITY.md`'s RBAC gap).

**What happens if the LLM goes down?**
Nothing observable breaks. `_diagnose_via_llm` catches every exception
and returns `None`; `diagnose()` falls through to the rule engine, which
runs the same downstream policy/execution path. No test, demo, or
production behavior depends on the LLM being reachable.

**What happens if Razorpay goes down?**
`RazorpayPaymentProvider._request` catches `HTTPError` and returns a
structured `{"error": ...}` result rather than raising into the
orchestrator; the calling code treats a failed retry/link-creation the
same way it treats any other "failed" provider result — logged, and the
stop-condition logic (max attempts) still applies. There's no circuit
breaker or automatic provider failover; a sustained Razorpay outage would
show up as a run of failed actions, visible in the audit trail, not a
system crash.

**What are the biggest limitations?**
In priority order, honestly: (1) no RBAC — one shared credential per
deployment, not per-user identity, so case-level actions aren't
merchant-scoped at the authorization layer even though *reads* are; (2)
the ML model is trained and evaluated on simulated data only, with no
real merchant outcomes to validate against yet; (3) expected value is
advisory, so a negative-EV action that clears the policy engine's checks
still executes; (4) outbound webhook delivery has no retry queue; (5) a
live Razorpay round-trip has been verified for Payment Links creation but
not for a completed test-mode payment plus inbound webhook delivery with
a Razorpay-issued signature end to end. The full, current built-vs-
roadmap breakdown lives in `docs/PRODUCT.md`.
