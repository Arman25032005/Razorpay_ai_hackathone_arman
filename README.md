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

## Table of contents

- [30-second pitch](#30-second-pitch)
- [What's genuinely real vs. what's honest roadmap](#whats-genuinely-real-vs-what-s-honest-roadmap)
- [Features](#features)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Quickstart](#quickstart)
- [Running locally, step by step](#running-locally-step-by-step)
- [Environment variables](#environment-variables)
- [The agent loop](#the-agent-loop)
- [Three things worth trying yourself](#what-makes-this-a-real-system-not-a-wrapper--three-things-worth-trying-yourself-not-just-reading-about)
- [Running the ML pipeline](#running-the-ml-pipeline)
- [Tests](#tests)
- [Scale](#scale)
- [Safety boundaries](#safety-boundaries-in-one-place)
- [Documentation index](#documentation-index)
- [Troubleshooting](#troubleshooting)
- [License](#license)

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

## Features

- **Full closed-loop agent**: detect → diagnose → decide → policy-check →
  act → verify → measure → stop/escalate, with an immutable audit event
  written at every step.
- **Deterministic, editable policy engine** (`app/policies/engine.py`) —
  max attempts, workflow expiration, channel allow-list, large-amount /
  discount human-approval thresholds. This is the *only* code path that
  gates action execution; the reasoning layer (rule engine or LLM) can
  never bypass it.
- **Pre-action payment-status verification** — every action re-checks the
  payment's authoritative current status immediately before firing, so a
  stale diagnosis never causes a duplicate or false action
  (`app/payment_state_machine.py`).
- **Real Razorpay integration** — Payment Links API, payment status API,
  webhook ingestion verified against Razorpay's documented payload shape,
  HMAC-SHA256 signature verification, idempotent webhook handling. Falls
  back to a mock provider with zero configuration.
- **Cost-sensitive expected-value decision framework** — advisory only,
  never a hidden second policy gate (enforced by a test).
- **Recovery-probability ML model** — logistic regression with a temporal
  train/val/test split, real precision/recall/ROC-AUC/calibration metrics,
  and per-prediction explainability (`ml/train.py`, `ml/evaluate.py`).
- **Multi-tenant isolation** — Merchant → Customer → everything downstream,
  enforced at the query layer and covered by a live cross-tenant leak
  test.
- **Human-in-the-loop review queue** — approve/reject actions, full audit
  trail, CSV export, live dashboard with charts.
- **Promise-to-pay workflow** — create, fulfill, or break payment
  promises as part of a recovery case.
- **Guaranteed graceful-failure demo case** — an agent decision cancelled
  honestly when the payment turns out to have already resolved
  independently.

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| API / backend | FastAPI + Uvicorn | async-capable, auto-generated OpenAPI docs at `/docs` |
| ORM / DB | SQLAlchemy | SQLite for local demo, Postgres-ready via `DATABASE_URL` |
| Frontend | Server-rendered vanilla HTML/CSS/JS (`static/`) | 8 pages, live charts, editable policies, multi-tenant switcher — no build step |
| ML | scikit-learn (logistic regression), pandas, numpy | trained model committed under `models/` so no training step is required to run the app |
| Payments | Razorpay REST API, with a documented `MockPaymentProvider` fallback | |
| Messaging | SendGrid (email), Meta WhatsApp Cloud API / Twilio (WhatsApp) | all optional, mock fallback if unset |
| Auth | Lightweight `X-API-Key` header check | no-op unless `API_KEY` is set |
| Tests | pytest | 58 tests |

## Project structure

```
app/
  main.py                 FastAPI app: routes, dashboard, case actions, webhooks
  models.py                SQLAlchemy models (Merchant, Customer, RecoveryCase, ...)
  db.py                    Engine/session setup
  security.py              API-key auth, webhook HMAC verification, rate limiting
  payment_state_machine.py Payment state transitions + pre-action verification
  webhooks.py               Webhook payload normalization/ingestion
  agents/
    orchestrator.py        Core agent loop (detect -> ... -> audit log)
    ai_service.py           Diagnosis (rule engine or real LLM), ML prediction
  policies/
    engine.py               Deterministic policy checks + stop conditions
    expected_value.py       Cost-sensitive expected-value framework (advisory)
    optimizer.py             Re-ranks strategies by real historical performance
  providers/
    payment.py               Razorpay / mock payment provider
    communication.py         Email/SMS/WhatsApp senders + message templates
  simulation/
    engine.py                Synthetic demo-data simulation runner
ml/
  data_generator.py         Synthetic training data generator
  features.py                Feature engineering
  train.py                   Trains + evaluates the recovery-probability model
  evaluate.py                 Reprints saved metrics without retraining
static/                     Vanilla HTML/CSS/JS dashboard frontend
tests/test_core.py          58 tests covering the agent loop, policy, tenancy, etc.
docs/                        Full documentation set (see index below)
models/recovery_model_v1/   Committed trained model + metrics.json
data/synthetic_recovery_data.csv
```

## Quickstart

```bash
git clone https://github.com/<your-org>/<this-repo>.git
cd <this-repo>
./run.sh
```

Creates a virtualenv, installs dependencies, copies `.env.example` to
`.env`, starts the server at **http://localhost:8000** — works immediately
with SQLite and the mock payment provider, zero external credentials
required. Then click **"Run Recovery Simulation"** in the sidebar.

## Running locally, step by step

**Prerequisites**: Python 3.11+ (developed/tested on 3.12), `pip`, and
`git`. No database, message broker, or Node.js toolchain required.

1. **Clone the repo**
   ```bash
   git clone https://github.com/<your-org>/<this-repo>.git
   cd <this-repo>
   ```

2. **Create and activate a virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate        # macOS/Linux
   # venv\Scripts\activate         # Windows (cmd/PowerShell)
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables** (optional — everything works with
   defaults)
   ```bash
   cp .env.example .env
   ```
   Leave every value blank for a fully offline demo: SQLite database,
   mock payment provider, deterministic rule-based diagnosis, no auth. See
   [Environment variables](#environment-variables) below to wire up real
   Razorpay/LLM/messaging credentials.

5. **Start the server**
   ```bash
   uvicorn app.main:app --reload
   ```
   or just run `./run.sh`, which does steps 2–5 for you and is safe to
   re-run (it reuses the existing `venv` and `.env` if present).

6. **Open the app**
   - Dashboard: **http://localhost:8000**
   - Auto-generated API docs (Swagger UI, always in sync with the code):
     **http://localhost:8000/docs**

7. **Seed some data** — click **"Run Recovery Simulation"** in the
   sidebar to generate simulated customers and recovery cases so the
   dashboard isn't empty.

8. **(Optional) Run the test suite**
   ```bash
   pytest tests/ -q
   ```

### Running with Docker instead

```bash
docker build -t recoveryos .
docker run -p 8000:8000 recoveryos
```

Or for a Postgres-backed setup:

```bash
docker compose up --build
```

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for Render/Railway
deployment.

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
| `SENDGRID_API_KEY` / `SENDGRID_FROM_EMAIL` | Real email delivery instead of the mock provider |
| `META_WHATSAPP_TOKEN` / `META_WHATSAPP_PHONE_NUMBER_ID` | Real WhatsApp delivery via Meta's Cloud API |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_FROM` | Real WhatsApp delivery via Twilio (used only if Meta credentials are unset) |

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
pytest tests/ -q
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

## Troubleshooting

- **`./run.sh: Permission denied`** — run `chmod +x run.sh` once, or invoke
  it as `bash run.sh`.
- **Port 8000 already in use** — stop whatever is bound to it, or start
  uvicorn on another port: `uvicorn app.main:app --reload --port 8001`.
- **Dashboard loads but is empty** — click **"Run Recovery Simulation"**
  in the sidebar to generate demo data; a fresh database has no cases yet.
- **Changes to `.env` not taking effect** — restart the server; env vars
  are read once at process startup.
- **`ModuleNotFoundError` after pulling new changes** — re-run
  `pip install -r requirements.txt` inside the activated virtualenv; a
  dependency may have been added.
- **Want to reset all demo data** — use the reset-simulation action in the
  dashboard, or delete `recoverai.db` (and its `-shm`/`-wal` files) while
  the server is stopped and restart it; the schema is recreated
  automatically.

## License

See `LICENSE`.
