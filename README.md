# RecoveryOS

AI-driven revenue recovery agent for Razorpay merchants — detects failed
payments and overdue invoices, diagnoses the cause, decides on a bounded
recovery action, executes it, verifies the outcome, and logs every step
for audit.

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests: 73 passing](https://img.shields.io/badge/tests-73%20passing-brightgreen.svg)](tests/test_core.py)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

## Table of contents

- [Overview](#overview)
- [Features](#features)
- [Tech stack](#tech-stack)
- [Project structure](#project-structure)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Usage](#usage)
- [API](#api)
- [Testing](#testing)
- [ML pipeline](#ml-pipeline)
- [Deployment](#deployment)
- [Architecture notes](#architecture-notes)
- [Scope: implemented vs. roadmap](#scope-implemented-vs-roadmap)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

## Overview

Merchants lose revenue in small, distributed events — an expired card, an
abandoned checkout, an unpaid invoice. Payment gateway dashboards report
that a payment failed; they don't investigate why, decide what to do
about it, verify the outcome, or know when to stop. RecoveryOS is a
closed-loop agent that does all four, inside explicit, editable safety
bounds, and produces a full audit trail for every decision it makes.

It runs standalone against Razorpay's Payment Links and webhook APIs (with
a mock provider fallback), so it can be evaluated with zero external
credentials.

## Features

- **Closed-loop agent** — detect → diagnose → decide → policy-check → act
  → verify → measure → stop/escalate, with an immutable audit event
  written at every step.
- **Deterministic policy engine** (`app/policies/engine.py`) — max
  attempts, workflow expiration, channel allow-list, and
  large-amount/discount human-approval thresholds. This is the only code
  path that can authorize an action; the diagnosis layer (rule engine or
  LLM) cannot bypass it.
- **Pre-action payment verification** — re-checks the payment's current
  status immediately before acting, so a stale diagnosis never causes a
  duplicate or incorrect action (`app/payment_state_machine.py`).
- **Real Razorpay integration** — Payment Links API (request shape
  verified AND a live test-mode call executed against
  `api.razorpay.com`), payment status API, webhook ingestion validated
  against Razorpay's documented payload shape, HMAC-SHA256 webhook
  signature verification, idempotent webhook handling.
- **Decline-code-driven diagnosis** (`app/decline_codes.py`) — strategy
  selection is keyed off Razorpay's real ~90-entry documented
  `error.reason` taxonomy, not a single generic "payment failed" bucket:
  an expired card, an issuer decline, a risk/compliance decline, and a
  transient gateway failure each route to a different recovery strategy,
  and a risk/compliance decline is never auto-retried regardless of
  customer history.
- **Recovery-probability ML model** — logistic regression with a temporal
  train/val/test split and real precision/recall/ROC-AUC/calibration
  metrics (`ml/train.py`, `ml/evaluate.py`), with per-prediction
  explainability surfaced in the case-detail UI.
- **Cost-sensitive expected-value framework** — advisory scoring layer,
  kept structurally separate from the policy engine (enforced by a test)
  — plus a portfolio-level **Net Recovery ROI** metric on the dashboard
  (gross revenue recovered minus the real operational cost of every
  action taken, successful or not).
- **Outbound merchant webhooks** (`app/outbound_webhooks.py`) — a
  merchant's own systems can subscribe to `case.opened` /
  `case.recovered` / `case.escalated` / `case.stopped` events,
  HMAC-SHA256-signed with the same scheme this app requires of Razorpay's
  inbound webhooks.
- **Multi-tenant data isolation** — Merchant → Customer → everything
  downstream, enforced at the query layer and covered by a cross-tenant
  leak test.
- **Human-in-the-loop review** — approve/reject queue, promise-to-pay
  workflow, full audit trail, CSV export, live dashboard with charts.

## Tech stack

| Layer | Choice |
|---|---|
| API / backend | FastAPI + Uvicorn |
| ORM / database | SQLAlchemy — SQLite by default, Postgres via `DATABASE_URL` |
| Frontend | Server-rendered HTML/CSS/JS (`static/`), no build step |
| Machine learning | scikit-learn, pandas, numpy |
| Payments | Razorpay REST API, with a mock provider fallback |
| Messaging | SendGrid (email), Meta WhatsApp Cloud API / Twilio (WhatsApp), mock fallback |
| Auth | `X-API-Key` header check, opt-in via `API_KEY` |
| Testing | pytest |

## Project structure

```
app/
├── main.py                    FastAPI app: routes, dashboard, case actions, webhooks
├── models.py                  SQLAlchemy models
├── db.py                      Engine/session setup
├── security.py                API-key auth, webhook HMAC verification, rate limiting
├── payment_state_machine.py   Payment state transitions + pre-action verification
├── webhooks.py                Webhook payload normalization/ingestion
├── decline_codes.py           Razorpay decline-code -> internal failure-reason classification
├── outbound_webhooks.py       Signed outbound event delivery to merchant-registered endpoints
├── agents/
│   ├── orchestrator.py        Core agent loop
│   └── ai_service.py          Diagnosis (rule engine or LLM), ML prediction
├── policies/
│   ├── engine.py               Policy checks + stop conditions
│   ├── expected_value.py       Expected-value framework (advisory)
│   └── optimizer.py            Strategy re-ranking from historical performance
├── providers/
│   ├── payment.py               Razorpay / mock payment provider
│   └── communication.py         Email/SMS/WhatsApp senders
└── simulation/
    └── engine.py                 Synthetic demo-data simulation

ml/
├── data_generator.py    Synthetic training data generator
├── features.py           Feature engineering
├── train.py               Model training + evaluation
└── evaluate.py             Reprints saved metrics

static/                  Dashboard frontend
tests/test_core.py       73 tests
docs/                    Full documentation set
models/recovery_model_v1/  Committed trained model + metrics.json
data/                    Synthetic training dataset
```

## Getting started

### Prerequisites

- Python 3.11+ (developed on 3.12)
- `pip`, `git`

No database, message broker, or Node.js toolchain is required.

### Installation

```bash
git clone https://github.com/Arman25032005/Razorpay_ai_hackathone_arman.git
cd Razorpay_ai_hackathone_arman
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Leave every value in `.env` blank for a fully offline run: SQLite
database, mock payment provider, deterministic rule-based diagnosis, no
auth. See [Configuration](#configuration) to enable real integrations.

### Run

```bash
uvicorn app.main:app --reload
```

Or use the setup script, which handles the steps above and is safe to
re-run:

```bash
./run.sh
```

Then open:

- App: **http://localhost:8000**
- API docs (Swagger UI): **http://localhost:8000/docs**

Click **Run Recovery Simulation** in the sidebar to generate demo data.

### Run with Docker

```bash
docker build -t recoveryos .
docker run -p 8000:8000 recoveryos
```

Or with Postgres via Compose:

```bash
docker compose up --build
```

## Configuration

All variables are optional; the app is fully functional with none set.
Full annotated list in `.env.example`.

| Variable | Effect when set |
|---|---|
| `DATABASE_URL` | Postgres instead of the default SQLite |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Real Razorpay API calls instead of the mock provider |
| `LLM_API_KEY` | Diagnosis routed through a real LLM instead of the rule engine |
| `PAYMENT_WEBHOOK_SECRET` | Requires HMAC-SHA256-signed webhooks |
| `API_KEY` | Requires `X-API-Key` on mutating endpoints |
| `SENDGRID_API_KEY` / `SENDGRID_FROM_EMAIL` | Real email delivery |
| `META_WHATSAPP_TOKEN` / `META_WHATSAPP_PHONE_NUMBER_ID` | Real WhatsApp delivery via Meta Cloud API |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_FROM` | Real WhatsApp delivery via Twilio (used if Meta credentials are unset) |

## Usage

1. Start the app and open the dashboard.
2. Run a recovery simulation to generate customers, invoices, payments,
   and recovery cases.
3. Open a case to see the agent's diagnosis, recommended strategy,
   recovery-probability prediction, and expected-value calculation.
4. Approve, reject, or let the policy engine auto-execute the next
   action, depending on the case's escalation status.
5. Switch merchants from the dropdown to confirm tenant isolation.
6. Review the audit trail and export cases to CSV from the dashboard.

## API

Auto-generated, always in sync with the code: `http://localhost:8000/docs`.
Full endpoint reference: [`docs/API.md`](docs/API.md). Covers dashboard
and analytics, recovery case actions, promises to pay, webhooks,
merchants, customers/invoices/payments, and policy management.

## Testing

```bash
pytest tests/ -q
```

73 tests covering the agent loop, policy engine, tenant isolation, payment
state machine, and ML pipeline. Breakdown by category in
[`docs/TESTING.md`](docs/TESTING.md).

## ML pipeline

A trained model is already committed to `models/recovery_model_v1/`, so
no training step is required to run the app.

```bash
python -m ml.data_generator --rows 8000 --seed 42   # regenerate synthetic training data
python -m ml.train                                    # train + evaluate, saves model + metrics.json
python -m ml.evaluate                                  # reprint saved metrics without retraining
```

Details on feature engineering, the temporal split, and evaluation
metrics: [`docs/ML.md`](docs/ML.md).

## Deployment

Render and Railway configs are included (`render.yaml`, `railway.json`),
along with a `Procfile` for Heroku-style platforms. Full checklist in
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Architecture notes

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

Safety boundaries:

- The diagnosis layer only ever returns a structured decision; it never
  calls a payment provider directly, and its output is validated against
  an explicit allow-list.
- The policy engine is the only code path that can authorize an action.
- Every action re-verifies payment status immediately before executing.
- A failed diagnosis escalates to human review rather than guessing.
- Every state-changing action writes an immutable audit event.
- The expected-value framework is advisory only — a test asserts the
  policy engine never imports it.
- Tenant data isolation is enforced at the query layer and covered by a
  cross-tenant leak test.

Full detail in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/AGENT.md`](docs/AGENT.md).

## Scope: implemented vs. roadmap

**Implemented and tested**: the full agent loop, deterministic policy
engine, real Razorpay API integration, a trained and evaluated ML
recovery-probability model, multi-tenant data isolation, the
expected-value framework, and a graceful-failure case (an in-flight
decision cancelled when the payment resolves independently before
execution).

**Roadmap, not implemented**: a live round-trip against Razorpay's
production servers (needs real test-mode credentials), an XGBoost/MLflow
upgrade path for the ML model, full RBAC (currently a single API key per
deployment), async webhook processing, and CI/CD. Full reasoning behind
each stack choice and gap: [`docs/PRODUCT.md`](docs/PRODUCT.md).

## Documentation

| Doc | Covers |
|---|---|
| [`docs/PRODUCT.md`](docs/PRODUCT.md) | Problem statement, implemented vs. roadmap |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Stack, request flow, agent loop |
| [`docs/RAZORPAY_INTEGRATION.md`](docs/RAZORPAY_INTEGRATION.md) | Every Razorpay endpoint used, real vs. mocked |
| [`docs/AGENT.md`](docs/AGENT.md) | Agent state, tools, policy engine, human-in-the-loop |
| [`docs/ML.md`](docs/ML.md) | Data generation, features, evaluation metrics |
| [`docs/DATABASE.md`](docs/DATABASE.md) | Schema, relationships, indexes |
| [`docs/API.md`](docs/API.md) | Full endpoint reference |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Implemented controls, known gaps |
| [`docs/PRIVACY.md`](docs/PRIVACY.md) | Data stored, retention |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Local, Docker, Render, Railway |
| [`docs/TESTING.md`](docs/TESTING.md) | Test coverage by category |
| [`docs/DEMO.md`](docs/DEMO.md) | Demo talking points |
| [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) | Line-by-line demo script |

## Contributing

```bash
./run.sh
pytest tests/ -q            # all tests must pass
node --check static/app.js  # frontend syntax check
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for details.

## License

[MIT](LICENSE)
