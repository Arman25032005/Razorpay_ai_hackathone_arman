# RecoveryOS — Product Document

## What problem this solves

Merchants using Razorpay (or any payment gateway) lose revenue in small,
distributed events: a card expires mid-subscription, a checkout is
abandoned, an invoice goes unpaid. Individually each is minor. In
aggregate, across thousands of transactions, this is real, recoverable
revenue that most merchants have no systematic process for winning back.

Existing tooling (payment gateway dashboards, generic CRM automations)
tells a merchant "this payment failed." It does not investigate why, does
not decide what to do, does not verify the outcome, and does not stop
itself. RecoveryOS does all four, inside explicit safety bounds.

## Why it matters 

Razorpay processes payments for merchants; failed payments are Razorpay's
own visible surface, but the *recovery* of that revenue is currently the
merchant's problem to solve, usually manually or not at all. A revenue
recovery layer that sits on top of Razorpay's existing payment/webhook
infrastructure — without requiring any change to Razorpay's own backend —
is additive value for merchants and, by extension, for transaction volume
retained on the Razorpay platform.

## What is actually built vs. what is roadmap

This is the section spec section 54 ("do not cheat — never claim a live
integration when only mock integration exists") requires, made explicit
and impossible to miss.

### Built, real, tested Web app

- Full agent loop: detect -> diagnose -> decide -> policy-check -> act ->
  verify -> measure -> stop/escalate
- Deterministic, editable policy engine (bounded autonomy)
- Real Razorpay integration: Payment Links API (request shape verified AND
  a live test-mode call executed against `api.razorpay.com`), payment
  status API, webhook ingestion (verified against Razorpay's actual
  documented payload), HMAC signature verification, idempotent webhook
  redelivery handling — see `docs/RAZORPAY_INTEGRATION.md` for the exact
  endpoint-by-endpoint breakdown of what's real vs. what's a documented
  mock fallback
- Decline-code-driven diagnosis (`app/decline_codes.py`): failure
  strategy selection is keyed off Razorpay's actual ~90-entry documented
  `error.reason` taxonomy (card expired vs. issuer-declined vs. risk/
  compliance decline vs. transient gateway failure, etc.), not a single
  generic "payment failed" bucket — including a hard rule that risk/
  compliance declines are never auto-retried, regardless of customer
  history
- Explicit `PaymentStateMachine` handling out-of-order/duplicate webhook
  events and Razorpay's documented `failed -> captured` sequence
- A real, honestly-evaluated logistic regression recovery-probability
  model — temporal train/val/test split, real precision/recall/ROC-AUC/
  calibration metrics computed from actual execution (`ml/train.py`,
  `ml/evaluate.py`), with per-prediction explainability surfaced in the
  case-detail UI
- Cost-sensitive expected-value decision framework, plus a portfolio-level
  Net Recovery ROI metric on the dashboard (gross revenue recovered minus
  the real operational cost of every action taken, successful or not)
- Outbound merchant webhooks (`app/outbound_webhooks.py`): a merchant's
  own systems can subscribe to case.opened/recovered/escalated/stopped
  events, HMAC-SHA256-signed with the same scheme this app requires of
  Razorpay's inbound webhooks
- Lightweight multi-tenant isolation (Merchant -> Customer -> everything
  downstream), verified with a live cross-tenant leak test
- Full audit trail, human review queue, CSV export, live dashboard
- Guaranteed demo scenarios including the graceful-failure case (spec
  section 35): an agent decision cancelled honestly when the payment
  turns out to have already resolved

### Explicitly NOT built — documented as roadmap, not faked

- **XGBoost / MLflow-scale ML pipeline.** What's built is a real,
  evaluated logistic regression baseline on a modest (8,000-row) honestly-
  generated synthetic dataset — not
  disguised as more than it is. Roadmap: swap in XGBoost, add MLflow
  experiment tracking, retrain on real (not synthetic) outcome data as it
  accumulates.
- **Next.js / React frontend.** The current frontend is server-rendered
  vanilla HTML/CSS/JS served by FastAPI — chosen deliberately for
  reliability within the timeline over a framework rewrite that risked not
  finishing. Functionally complete (8 pages, live charts, editable
  policies, multi-tenant switcher) but not built on the spec's originally
  preferred stack.
- **MongoDB.** Current persistence is SQLAlchemy (SQLite for local demo,
  Postgres-ready via `DATABASE_URL` — see `docker-compose.yml`). Document-
  store flexibility wasn't needed for the current schema; a relational
  model made the actual entity relationships (merchant -> customer ->
  case -> action -> audit event) easier to enforce with real foreign keys
  and joins, which is what powered the tenant-isolation guarantee.
- **LangGraph / explicit agent-graph orchestration.** The agent loop is a
  linear, explicit Python state machine (`app/agents/orchestrator.py`) —
  functionally equivalent bounded-autonomy behavior (no step the LLM/rule
  engine can skip past policy validation), without the LangGraph
  dependency.
- **Redis / task queue.** Not needed yet at this scale; the simulation
  engine processes hundreds of cases synchronously in seconds. Roadmap
  item once webhook volume requires async processing.
- **Full RBAC/JWT auth, CI/CD pipeline, OpenTelemetry/Prometheus.**
  Lightweight API-key auth exists (`app/security.py`); the rest are
  documented as next steps in `docs/SECURITY.md`, not implemented.
- ~~**Live Razorpay test-mode round-trip.**~~ Done — verified with real
  test-mode credentials: `RazorpayPaymentProvider.create_payment_link()`
  executed against `api.razorpay.com` and returned a genuine payment link
  (`https://rzp.io/rzp/...`) with a real `payment_link_id`. Still open: a
  full round-trip including a completed test-mode payment and inbound
  webhook delivery with a Razorpay-issued signature (see
  `docs/RAZORPAY_INTEGRATION.md` section 8 for the current "verified vs.
  not yet verified" breakdown).


