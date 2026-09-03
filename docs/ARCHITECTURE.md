# Architecture

## Stack (as actually built — see docs/PRODUCT.md for what this deviates from and why)

```
Frontend:   Vanilla HTML/CSS/JS, served by FastAPI, Chart.js for charts
Backend:    Python 3.12, FastAPI, Pydantic (via FastAPI), SQLAlchemy ORM
Database:   SQLite (local/demo), Postgres-ready via DATABASE_URL
ML:         scikit-learn (logistic regression baseline), pandas, joblib
Payments:   Real Razorpay REST API integration + MockPaymentProvider fallback
Auth:       Lightweight API-key gate (app/security.py)
Deployment: Docker, docker-compose, Render/Railway configs
```

## Request flow

```
Browser (static/app.js)
        |
        v
FastAPI (app/main.py) — versioned-in-spirit REST endpoints
        |
        +--> app/policies/engine.py       deterministic policy gate
        +--> app/agents/orchestrator.py   the bounded agent loop
        +--> app/agents/ai_service.py     diagnosis + ML prediction
        +--> app/payment_state_machine.py payment status reconciliation
        +--> app/providers/payment.py     Razorpay or Mock, same interface
        +--> app/webhooks.py              inbound event normalization
        |
        v
SQLAlchemy models (app/models.py) <-> SQLite/Postgres
```

## The agent loop, concretely

```
EVENT (webhook or simulation)
  -> create_case()                        RecoveryCase row created
  -> analyze_case()
       -> build_customer_context()        real history from the DB
       -> ai_service.diagnose()           rule engine (or LLM if LLM_API_KEY set)
       -> _apply_strategy_optimizer()     re-rank by real historical performance
  -> execute_next_action()  [one attempt per call, looped by the caller]
       -> PRE-ACTION VERIFICATION         app/payment_state_machine.py:
                                           re-check current payment status;
                                           cancel gracefully if already resolved
       -> check_policy()                  deterministic, code-enforced gate
       -> [if blocked] escalate to human review queue
       -> [if allowed] execute via payment_provider / communication_provider
       -> observe result, update case status
       -> evaluate_stop_condition()       max attempts / workflow expiry / recovered
```

Every step that changes state writes an `AuditEvent`. Nothing executes
without a preceding, logged policy check.

## Why this shape, specifically

**LLM/rule-engine for reasoning, code for hard constraints.** `ai_service.diagnose()`
returns a structured `Decision` — root cause, confidence, recommended
strategy — and nothing more. It cannot call a payment provider, cannot
bypass a policy check, cannot execute anything. `app/policies/engine.py`
is the only code path that gates action execution, and it is plain
deterministic `if` statements, not a model.

**Verify-before-act, always.** Because payments can resolve independently
of the agent (see `docs/RAZORPAY_INTEGRATION.md` section 6 on Razorpay's
documented `failed -> captured` sequence), `execute_next_action()` always
re-checks current payment status immediately before acting — not just at
diagnosis time. This is what makes the graceful-cancellation demo case
(spec section 35) a real system behavior rather than a scripted moment.

**Multi-tenancy via Customer, not duplicated on every table.** `Merchant`
is the tenant root; every `Customer` belongs to exactly one merchant, and
every `Payment`/`Invoice`/`RecoveryCase`/`AuditEvent` is reachable only
through its `Customer`. Tenant-scoped queries join through `Customer.merchant_id`
rather than storing `merchant_id` redundantly on every downstream table.
This was a deliberate simplification for the timeline — the alternative
(denormalized `merchant_id` everywhere) would be the more conventional
production choice if query performance at large scale becomes a concern,
but requires backfilling every insert path.

**Provider adapter pattern.** `PaymentProvider` and `CommunicationProvider`
are abstract interfaces; `MockPaymentProvider` and `RazorpayPaymentProvider`
implement the same interface (`app/providers/payment.py`). `_select_provider()`
picks between them based purely on whether `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`
are set — no other code path changes between demo and live mode.

**One process, synchronous.** No queue/worker split. At the scale this has
been tested (1,000+ simulated customers, 500+ cases processed in ~15
seconds), synchronous processing inside the request/response cycle (or a
single background simulation call) is simpler and more debuggable than
introducing Redis + a worker process for a load this system doesn't yet
have. Documented as a roadmap item in `docs/PRODUCT.md` if/when webhook
volume requires it.

## Directory layout

```
app/
  main.py                  FastAPI app, all HTTP endpoints
  models.py                SQLAlchemy schema
  db.py                    session/engine setup
  security.py              API-key auth, webhook signature verification, rate limiting
  webhooks.py               inbound event normalization (incl. real Razorpay payload parsing)
  payment_state_machine.py  payment status transition validation
  agents/
    ai_service.py           diagnosis, ML prediction, prioritization, summarization
    orchestrator.py         the bounded agent loop
  policies/
    engine.py                deterministic policy gate
    optimizer.py              statistical strategy-performance ranking
    expected_value.py         cost-sensitive expected-value framework
  providers/
    payment.py                PaymentProvider interface + Mock + Razorpay implementations
    communication.py          CommunicationProvider interface + message templates
  simulation/
    engine.py                 synthetic data generation + demo batch runner
ml/
  data_generator.py           synthetic training data with documented causal structure
  features.py                  feature engineering, temporal split
  train.py                     model training + honest evaluation
  evaluate.py                  reproducible re-evaluation without retraining
static/                        frontend (HTML/CSS/JS)
tests/                          73 tests covering policy, agent, ML, webhooks, tenancy, security
docs/                           this document and its siblings
```
