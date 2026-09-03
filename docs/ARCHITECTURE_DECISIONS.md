# Architecture Decision Records

Each ADR below records a choice, the reasoning behind it, and what was
given up to make it. These reflect decisions actually embodied in the
current code — verified against the implementation while writing this
document, not aspirational.

---

## ADR-001 — Deterministic Safety Layer

**Decision**: Every action with a financial or communication side effect
passes through a deterministic, plain-Python policy check
(`app/policies/engine.py::check_policy`) immediately before execution.
This is the *only* code path that can authorize an action — the
diagnosis layer (rule engine or LLM) can recommend, but cannot execute.

**Why**: An LLM is a statistical process. It can be prompted well, it can
be validated on the way out, but it cannot be made to *guarantee* it will
never recommend "retry this payment for the 47th time" or "apply a 90%
discount" under some input it hasn't been tested against. A payments
system needs a guarantee, not a strong tendency. Putting the hard limits
(max attempts, amount thresholds, channel allow-list, workflow
expiration) in code that a human wrote and a test suite exercises means
the worst an LLM failure can do is recommend something the policy layer
then blocks — never something that actually happens.

**Consequence**: `app/agents/orchestrator.py::execute_next_action` always
calls `check_policy()` before touching `payment_provider` or
`communication_provider`, regardless of which strategy was recommended or
by what. There is no code path that skips this check.

**Verified by**: `tests/test_core.py::test_max_attempts_enforced`,
`test_large_transaction_requires_approval`,
`test_disallowed_channel_blocked`, `test_discount_requires_approval`.

---

## ADR-002 — AI as an Advisory Component

**Decision**: The LLM (or its rule-engine stand-in) returns a structured
decision object — root cause, confidence, a recommended strategy from a
closed vocabulary, and a plain-language explanation. It never calls a
provider, never sees provider credentials, and never learns the outcome
of its own recommendation within the same call.

**Why**: The alternative — giving the model tool-calling access to
`payment_provider.retry_payment()` directly — is a common pattern for
"agentic" demos, and a bad one for a system that moves money. It
collapses the "what should happen" decision and the "make it happen"
execution into one step with no inspection point between them. Keeping
them separate means every recommendation is inspectable, loggable, and
overridable *before* it has any real-world effect.

**Consequence**: `app/agents/ai_service.py` has zero imports from
`app.providers`. The orchestrator is the only module that imports both
the diagnosis layer and the provider layer, and it only ever calls a
provider after `check_policy()` has returned `allowed=True`.

**Trade-off accepted**: This adds a layer of indirection an LLM-with-tools
architecture wouldn't have — the diagnosis result has to be translated
into an internal strategy/action-type, then re-validated, before
anything happens. That's the cost of the guarantee in ADR-001.

---

## ADR-003 — Expected-Value Strategy Selection, Advisory Not Authoritative

**Decision**: The system computes an expected recovery value for the
selected strategy (`app/policies/expected_value.py`) and surfaces it —
in the audit log and the dashboard — but it does not gate execution. The
deterministic policy engine (ADR-001) is the only gate.

**Why this over "just always retry"**: Because not every recoverable
failure is worth acting on. A ₹50 recovery attempt with a 10% success
chance against a ₹15 action cost and repeated-contact annoyance is a
worse outcome than not acting — the system should be able to say so, not
just "can I do this" but "should I."

**Why advisory rather than a hard gate**: A hard EV gate would mean a
soft, model-derived probability estimate (from a logistic regression
trained on 8,000 synthetic rows — see ADR-005) gets veto power over a
payment action, with the exact same authority as a fixed, auditable rule
like "max 3 attempts." Those are different kinds of confidence and
shouldn't be treated identically. Keeping EV advisory means: a human
reviewer sees the number and can act on it; the strategy optimizer uses
historical performance (not EV directly) to re-rank candidates once
enough data exists; but a single bad probability estimate can never, by
itself, either force an action through or block one the policy layer
would otherwise allow.

**Consequence**: `check_policy()`'s source does not import
`expected_value.py` — this is a structural fact of the code, not just a
documented intention, and it's asserted directly by
`tests/test_core.py::test_expected_value_never_gates_execution` (it
`inspect.getsource()`s the policy module and asserts the string
`"expected_value"` never appears in it).

**Known gap**: Because EV is advisory, a case with negative expected
value that nonetheless passes the deterministic policy checks *will*
execute. The negative-EV number is visible to a human reviewer via the
case-detail view, but nothing currently stops automated execution on it.
Closing this gap — if desired — would mean either (a) adding an explicit
policy rule like "block if EV < 0" to `check_policy()` (keeping the
single-gate architecture intact), or (b) accepting EV as a second,
independent gate (a real architectural change, not a bug fix). This
project chose not to make that change without deciding which of those
two paths is correct, rather than picking one under time pressure.

---

## ADR-004 — Idempotent Recovery

**Decision**: Every inbound webhook event is recorded (`WebhookEvent`,
keyed by the provider's `event_id`) *before* any side effect happens. A
redelivered event — Razorpay's own docs describe at-least-once delivery,
not exactly-once — is detected and short-circuited before it can create
a second case, send a duplicate message, or double-count recovered
revenue.

**Why**: Webhook redelivery is a documented, expected behavior of the
provider, not a bug to work around defensively after the fact. Treating
it as a first-class case in the ingestion path (`app/webhooks.py::_record_event`
raises `DuplicateEvent` before any `Customer`/`Payment`/`RecoveryCase` row
is touched) is simpler and more reliable than trying to make every
downstream side effect independently idempotent.

**Consequence**: A duplicate event returns `{"status": "duplicate", ...}`
and creates nothing. The *action* layer has a second, independent
idempotency concern — a case that's already `RECOVERED`/`STOPPED`/
`ESCALATED`/`EXPIRED` short-circuits at the top of
`execute_next_action()` regardless of how it's invoked again.

**Verified by**: `test_duplicate_webhook_is_idempotent`,
`test_razorpay_webhook_end_to_end_idempotent`.

---

## ADR-005 — ML Prediction Boundary

**Decision**: The trained model (`models/recovery_model_v1/`, a logistic
regression) predicts exactly one number: **the probability that a given
recovery action, on a given case, results in the payment being
recovered** (`app/agents/ai_service.py::predict_recovery_probability`).

**What it does not decide**:
- It does not choose the root cause (the rule engine/LLM does that,
  upstream of the model).
- It does not choose the strategy (same).
- It does not authorize the action (the policy engine does, downstream,
  and doesn't consult the model at all — see ADR-001).
- It does not decide whether to act (that's the advisory EV calculation
  in ADR-003, which *uses* this probability as one input but is itself
  advisory).

**Why keep the boundary this narrow**: A model that predicts one
well-defined thing can be evaluated honestly (precision/recall/ROC-AUC/
calibration against a temporal holdout — see `docs/ML.md`). A model that
implicitly also decided strategy or authorization would be much harder to
evaluate, and any weakness in it would propagate directly into a payment
action instead of being caught by a downstream deterministic check.

**Consequence**: If the model is untrained or its artifacts are missing,
`predict_recovery_probability` returns `None` — never a fabricated
number — and every caller falls back to the diagnosis-stage confidence
score instead. This is a fail-closed contract, tested by
`test_predict_recovery_probability_returns_none_without_trained_model`.

---

## ADR-006 — Simulation vs. Production Data

**Decision**: The "Run Recovery Simulation" flow generates synthetic
customers, payments, and cases with `fake.safe_email()` (guaranteed
IANA-reserved, undeliverable addresses — `example.com`/`.org`/`.net`,
never a real domain) and routes every action through
`MockPaymentProvider`/`MockCommunicationProvider` for the duration of the
batch, **regardless of what real credentials are configured** in the
environment (`app/simulation/engine.py::run_agent_on_batch`).

**Why**: A demo that can accidentally send a real SMS or WhatsApp message
to a fabricated phone number, or a real email to a domain that happens to
exist, is not a demo — it's a production incident waiting to happen the
first time someone runs it with live keys configured. The simulation
batch explicitly swaps providers rather than relying on the fake data
being harmless by chance.

**What stays real even during simulation**: The *decision logic* is
identical — the same `analyze_case`/`execute_next_action` functions run,
the same policy engine gates the same way, the same ML model (if trained)
scores the same way. Only the payment/communication I/O is mocked. This
means the simulation is a genuine exercise of the decision engine, not a
scripted fake — the numbers differ on every run because the underlying
computation is real, over randomized (causally-structured) inputs.

**What is honestly still unverified**: A single manual action on an
individual simulated case (clicking "Execute next action" on a case from
a past simulation run, outside the batch flow) uses whatever provider is
*actually* configured — it is not automatically mocked. This is a known,
accepted edge case, not a gap that was missed: `fake.safe_email()`
guarantees any such send is undeliverable, so the residual risk is
"wasted send quota to a nonexistent address," not "a real person receives
a fake message." See `docs/PRODUCT.md` for the current honest
built-vs-roadmap breakdown of what has and hasn't been verified against
Razorpay's live servers.

**The model itself is trained on simulated data.** `ml/data_generator.py`
produces synthetic training rows with a documented causal structure (not
random noise — see `docs/ML.md`), evaluated with a real temporal
train/val/test split and real metrics. The model has **not** been
validated against real merchant outcome data, because none exists yet for
this project. Any claim about the model's real-world accuracy would be
fabricated; the honest claim is: it is a correctly-evaluated baseline on
the only data available, and retraining on real outcomes is roadmap, not
done.
