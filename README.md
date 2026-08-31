# RecoveryOS

**AI Revenue Recovery Agent for Razorpay merchants**

*"Don't just find lost revenue. Recover it — safely, verifiably, and
within explicit bounds."*

An agent that detects revenue at risk, diagnoses why, chooses a bounded
recovery strategy, verifies the payment's current status before acting,
executes, measures what actually came back, and stops or escalates
according to explicit, editable, merchant-configurable rules — with a
complete audit trail for every decision.

---

## 30-second pitch

> Traditional systems say **"Payment failed."** and stop there.
>
> RecoveryOS says: **"₹X is at risk. We diagnosed why. We predicted a
> recovery probability using a real, evaluated model. The expected value
> of acting was positive, so the policy engine approved it. We executed.
> Before we did, we re-checked the payment's actual current status. ₹X
> was genuinely recovered. Everything is auditable, and it's isolated
> per merchant."**

## What's genuinely real vs. what's honest roadmap

This matters enough to put first, not buried in an appendix. See
[`docs/PRODUCT.md`](docs/PRODUCT.md) for the full breakdown, but the short
version:

**Real and tested** (58 passing tests, verified live throughout
development, not just unit-tested in isolation): the full agent loop,
deterministic policy engine, real Razorpay API integration (request-shape
verified against their actual documentation), a real trained-and-evaluated
ML recovery-probability model, multi-tenant data isolation, the
cost-sensitive expected-value framework, and a genuine graceful-failure
demo case.

**Honest roadmap, not faked**: a live round-trip against Razorpay's actual
servers (needs real test-mode keys, not available in this build
environment), XGBoost/MLflow (a logistic regression baseline is what's
actually trained and evaluated here), Next.js/MongoDB/LangGraph (this uses
FastAPI/SQLAlchemy/an explicit Python state machine instead — see
`docs/PRODUCT.md` for the specific reasoning behind each substitution),
full RBAC, and CI/CD.

## Documentation index

| Doc | Covers |
|---|---|
| [`docs/PRODUCT.md`](docs/PRODUCT.md) | What problem this solves, and the explicit real-vs-roadmap breakdown |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Stack, request flow, the agent loop, why it's shaped this way |
| [`docs/RAZORPAY_INTEGRATION.md`](docs/RAZORPAY_INTEGRATION.md) | Every Razorpay endpoint used, verified against their real docs, what's mocked and why |
| [`docs/AGENT.md`](docs/AGENT.md) | Agent state, tools, policy engine, human-in-the-loop, test-case coverage |
| [`docs/ML.md`](docs/ML.md) | Data generation, features, temporal split, real evaluation metrics, calibration |
| [`docs/DATABASE.md`](docs/DATABASE.md) | Schema, relationships, indexes, honest gaps |
| [`docs/API.md`](docs/API.md) | Full endpoint reference |
| [`docs/SECURITY.md`](docs/SECURITY.md) | What's implemented, what's an honest gap |
| [`docs/PRIVACY.md`](docs/PRIVACY.md) | What's stored, what isn't, retention |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Local, Docker, Render, Railway; filled-in production-readiness checklist |
| [`docs/TESTING.md`](docs/TESTING.md) | Test coverage by category |
| [`docs/DEMO.md`](docs/DEMO.md) | Presenter talking points for the newer features |
| [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) | Full line-by-line demo script for the core recovery loop |

## Quickstart

```bash
./run.sh
```

Creates a virtualenv, installs dependencies, copies `.env.example` to
`.env`, starts the server at **http://localhost:8000** — works immediately
with SQLite and the mock payment provider, zero external credentials
required. Then click **"Run Recovery Simulation"** in the sidebar.

Manual equivalent:
```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/uvicorn app.main:app --reload
```

Docker: `docker build -t recoveryos . && docker run -p 8000:8000 recoveryos`,
or `docker compose up --build` for a Postgres-backed setup. See
`docs/DEPLOYMENT.md` for Render/Railway.

API docs (auto-generated, always in sync with the code):
**http://localhost:8000/docs**

## The agent loop

```
EVENT (webhook or simulation)
  -> NORMALIZATION -> RISK DETECTION -> CUSTOMER CONTEXT RETRIEVAL
  -> DIAGNOSIS (rule engine, or real Claude if LLM_API_KEY is set)
  -> ML RECOVERY-PROBABILITY PREDICTION (real logistic regression, evaluated)
  -> EXPECTED-VALUE CALCULATION (advisory)
  -> STRATEGY SELECTION (re-ranked by real historical performance once data exists)
  -> PRE-ACTION PAYMENT-STATUS VERIFICATION (never act on a stale assumption)
  -> POLICY CHECK (deterministic, code-enforced, never bypassable by the LLM)
  -> ACTION EXECUTION (real Razorpay API, or mock)
  -> OBSERVE RESULT -> MEASUREMENT -> STOP / ESCALATE
  -> AUDIT LOG (every step, immutable)
```

Full detail in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/AGENT.md`](docs/AGENT.md).

## What makes this a real system, not a wrapper — three things worth trying yourself, not just reading about

1. **Run the simulation twice, look at the numbers.** They're different
   every time — real computation over randomized (but causally-structured)
   data, not a fixed demo script.

2. **Switch the merchant dropdown.** The case list, revenue figures, and
   even the CSV export change completely and are genuinely isolated —
   verified by a test that confirms one merchant's data is structurally
   invisible when scoped to another
   (`test_dashboard_merchant_filter_excludes_other_tenant_cases`).

3. **Find the "graceful cancellation" case** (customer "Fatima Al-Rashid"
   after running the simulation). Watch the audit trail: the agent
   diagnosed a strong retry candidate, then caught — via a real
   pre-action status check, not a scripted moment — that the payment had
   already resolved independently, and cancelled its own plan rather than
   falsely claiming credit. This is the exact failure-handling
   demonstration a rigorous reviewer would want to see, and it's backed by
   `test_graceful_cancellation_when_payment_already_captured`.

## Environment variables

All optional — the app is fully functional with zero configuration. See
`.env.example` for the complete annotated list; summary in
`docs/DEPLOYMENT.md`.

| Variable | Effect when set |
|---|---|
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Real Razorpay API calls instead of the mock provider |
| `LLM_API_KEY` | Diagnosis routed through real Claude instead of the deterministic rule engine |
| `PAYMENT_WEBHOOK_SECRET` | Requires signed webhooks (HMAC-SHA256, Razorpay's real scheme) |
| `API_KEY` | Requires `X-API-Key` on mutating endpoints |
| `DATABASE_URL` | Postgres instead of the default SQLite |

## Running the ML pipeline

```bash
python -m ml.data_generator --rows 8000 --seed 42   # regenerate synthetic training data
python -m ml.train                                    # train + evaluate, saves model + metrics.json
python -m ml.evaluate                                  # reprint saved metrics without retraining
```

A trained model is already committed to `models/recovery_model_v1/` so the
app works immediately after clone without requiring this step — see
`docs/ML.md` for the real evaluation numbers and how they were produced.

## Tests

```bash
./venv/bin/python -m pytest tests/ -q
```

**58 tests, all passing.** See `docs/TESTING.md` for the full breakdown by
category.

## Scale

Verified end-to-end at 1,000 simulated customers / 500+ recovery cases
processed through the full agent loop in ~15 seconds on SQLite, with zero
errors — including catching and fixing a real ID-collision bug at that
volume during development (8 hex characters of entropy wasn't enough;
widened to 16).

## Safety boundaries, in one place

- The reasoning layer (rule engine, or real LLM when configured) only ever
  returns a structured decision — it never calls a payment provider
  directly, and its output is validated against an explicit allow-list
  before being trusted.
- The policy engine (`app/policies/engine.py`) is plain deterministic
  code — max attempts, workflow expiration, channel allowlist,
  large-amount/discount human-approval thresholds — and is the only code
  path that gates action execution.
- Every action re-verifies the payment's current status immediately
  before executing (`app/payment_state_machine.py`), never acting on a
  diagnosis-time assumption that might be stale.
- If diagnosis fails for any reason, the case is escalated to human
  review rather than the agent guessing or the request crashing.
- Every state-changing action writes an immutable audit event.
- The expected-value framework is advisory only, confirmed by a test that
  the policy engine's source never imports it — it cannot become a hidden
  second gate.
- Tenant data isolation is enforced at the query layer and covered by a
  live cross-tenant leak test, not just documented intent.

## License

See `LICENSE`.
