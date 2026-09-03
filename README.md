# RecoveryOS

**🔗 Live demo: [recoveryos-lsk6.onrender.com](https://recoveryos-lsk6.onrender.com)** — login password: `Arman@2005`
*(Free-tier hosting: the app sleeps after ~15 min idle — the first load after that can take 30–50s to wake up.)*

AI-assisted revenue recovery agent for Razorpay merchants — detects
failed payments and overdue invoices, diagnoses the cause, decides
whether and how to recover it inside deterministic safety bounds,
executes the action, verifies the outcome, and logs every decision for
audit.

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests: 90 passing](https://img.shields.io/badge/tests-90%20passing-brightgreen.svg)](tests/test_core.py)
[![CI](https://github.com/Arman25032005/Razorpay_ai_hackathone_arman/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

## Table of contents

- [1. Problem](#1-problem)
- [2. Solution](#2-solution)
- [3. Product hypothesis](#3-product-hypothesis)
- [4. Architecture](#4-architecture)
- [5. Agent workflow](#5-agent-workflow)
- [6. Recovery decision process](#6-recovery-decision-process)
- [7. ML approach](#7-ml-approach)
- [8. Payment safety](#8-payment-safety)
- [9. Demo flow](#9-demo-flow)
- [10. Testing](#10-testing)
- [11. Limitations](#11-limitations)
- [12. Future improvements](#12-future-improvements)
- [13. Local setup](#13-local-setup)
- [Documentation index](#documentation-index)
- [License](#license)

## 1. Problem

Merchants lose revenue in small, distributed events: an expired card
mid-subscription, a checkout abandoned after payment details were
entered, an invoice going unpaid. A payment gateway dashboard reports
that a payment failed and stops there — it doesn't investigate why,
decide what to do about it, verify the outcome, or know when to stop
trying. That work is left to the merchant, manually, or not done at all.

## 2. Solution

A closed-loop agent that, for every failed payment or overdue invoice:
diagnoses the failure into an explicit category, estimates whether
recovering it is worth the cost, selects a bounded recovery strategy,
verifies the payment's actual current state immediately before acting
(never on a stale assumption), executes the action, and records the full
reasoning chain — auditable after the fact, not just a final status.

The system is not "retry everything." A category-aware, cost-aware
decision is made before any action fires — see
[§6](#6-recovery-decision-process) and
[`docs/PRODUCT_DECISIONS.md`](docs/PRODUCT_DECISIONS.md).

## 3. Product hypothesis

> If failed-payment recovery is diagnosed by category (not treated as one
> generic bucket), scored by expected value before acting, and gated by
> deterministic rules a human can audit — then a merchant recovers more
> revenue than blind retry-on-failure would, without the compliance and
> customer-trust risk of an unbounded automated system.

This hypothesis is only as strong as the parts of it that are actually
implemented and tested — see [§11](#11-limitations) for what's still
unverified, and [`docs/ARCHITECTURE_DECISIONS.md`](docs/ARCHITECTURE_DECISIONS.md)
for the reasoning behind each load-bearing design choice.

## 4. Architecture

| Layer | Choice | Why |
|---|---|---|
| API / backend | FastAPI + Uvicorn | Async-capable, auto-generated OpenAPI docs, minimal ceremony for a REST + webhook surface this size |
| ORM / database | SQLAlchemy — SQLite by default, Postgres via `DATABASE_URL` | Relational model matches the real entity relationships (merchant → customer → case → action → audit) and enforces them with real foreign keys — see ADR discussion in `docs/PRODUCT.md` for why not a document store |
| Frontend | Server-rendered HTML/CSS/JS (`static/`), no build step | The dashboard's job is to make the agent's reasoning visible, not to be a SPA framework showcase |
| Machine learning | scikit-learn, pandas, numpy | A logistic regression baseline, chosen deliberately over a heavier pipeline — see ADR-005 |
| Payments | Razorpay REST API, with a mock provider fallback | Zero-credential evaluation; provider swap is one interface (`app/providers/payment.py`) |
| Messaging | SendGrid (email), Meta WhatsApp Cloud API / Twilio (WhatsApp) | Mock fallback by default; live sends never reachable from simulated demo data — see ADR-006 |
| Auth | `X-API-Key` (machine callers) + password-login session gate (dashboard) | Two different callers, two different credentials — see `app/auth.py`, `app/security.py` |
| Testing | pytest | 90 tests, named around product scenarios, not just code paths — see [§10](#10-testing) |
| CI | GitHub Actions (`.github/workflows/ci.yml`) | Tests + frontend syntax check + ML pipeline smoke test on every push/PR |

```
app/
├── main.py                    FastAPI app: routes, dashboard, case actions, webhooks
├── models.py                  SQLAlchemy models
├── db.py                      Engine/session setup
├── security.py                API-key auth, webhook HMAC verification, rate limiting
├── auth.py                    Dashboard password login -> signed session tokens
├── payment_state_machine.py   Payment state transitions + pre-action verification
├── webhooks.py                Webhook payload normalization/ingestion
├── decline_codes.py           Razorpay decline-code -> internal failure-category classification
├── outbound_webhooks.py       Signed outbound event delivery to merchant-registered endpoints
├── agents/
│   ├── orchestrator.py        Core decision loop (see §5, §6)
│   └── ai_service.py          Diagnosis (rule engine or LLM), ML prediction
├── policies/
│   ├── engine.py               The one deterministic authority on whether an action executes
│   ├── expected_value.py       Expected-value framework (advisory, not a gate — ADR-003)
│   └── optimizer.py            Strategy re-ranking from real historical performance
├── providers/
│   ├── payment.py               Razorpay / mock payment provider
│   └── communication.py         Email/SMS/WhatsApp senders
└── simulation/
    └── engine.py                 Synthetic demo-data simulation (mocked I/O — ADR-006)

ml/                          Training data generation, feature engineering, train/evaluate
static/                      Dashboard frontend
tests/test_core.py           90 tests
docs/                        Full documentation set — see index at the bottom of this file
models/recovery_model_v1/    Committed trained model + metrics.json
data/                        Synthetic training dataset
```

## 5. Agent workflow

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
  → decision + audit log (every step, immutable)
```

Implemented in `app/agents/orchestrator.py` — every arrow above is a real
function call or a logged `AuditEvent`, not an aspirational diagram. Full
walkthrough of each stage, including what happens when it fails:
[`docs/DESIGN_WALKTHROUGH.md`](docs/DESIGN_WALKTHROUGH.md).

## 6. Recovery decision process

Failures are classified into four categories before anything else
happens — **transient**, **recoverable customer/method issue**,
**non-recoverable**, and **uncertain** — keyed off Razorpay's real
documented `error.reason` taxonomy (`app/decline_codes.py`, ~90 codes).
A retry is never the chosen strategy for a category a retry provably
cannot fix (an expired card has a hard-coded `0.0` retry-success
probability, not a low one).

For the leading candidate strategy, the system computes:

```
Expected Recovery Value =
    P(recovery) × recoverable_amount
    − action_cost
    − customer_annoyance_cost
    − risk_cost
```

This number is **advisory** — surfaced in the audit log and dashboard,
not a hard gate. The deterministic policy engine (`app/policies/engine.py`)
is the only authority that can actually block an action: maximum attempts,
workflow age, channel allow-list, and amount/discount thresholds requiring
human approval. It gates every action regardless of which layer
recommended it, and it does not import the expected-value module — a fact
asserted directly by a test, not just documented.

Full reasoning: [`docs/PRODUCT_DECISIONS.md`](docs/PRODUCT_DECISIONS.md)
(the "what" and "why" of each category and rule) and
[`docs/ARCHITECTURE_DECISIONS.md`](docs/ARCHITECTURE_DECISIONS.md)
(ADR-001 through ADR-003, including the honestly-stated gap: a
negative-EV action that clears policy checks still executes today).

## 7. ML approach

**Prediction target**: the probability that a given recovery action, on
a given case, results in the payment being recovered. Nothing else — the
model does not choose root cause, strategy, or whether to act (ADR-005).

**Features**: customer success rate, customer payment count, transaction
amount, attempt count, days since last payment, one-hot-encoded root
cause and strategy.

**Evaluation**: a logistic regression baseline, evaluated on a
**temporal** train/val/test split (not random — a model tested on data
that leaks future customer information would look better than it is),
with real precision/recall/ROC-AUC/calibration metrics and per-prediction
explainability from the model's own learned coefficients.

**Data**: the model is trained and evaluated on simulated data with a
documented causal structure (`ml/data_generator.py`), not real merchant
outcomes — none exist for this project yet. **The current model requires
validation against real merchant data before any production claim about
its accuracy would be honest.** See
[`docs/ML.md`](docs/ML.md) for the full generation/evaluation
methodology and [ADR-006](docs/ARCHITECTURE_DECISIONS.md#adr-006--simulation-vs-production-data)
for how simulated data is kept structurally separate from anything a
live integration would touch.

## 8. Payment safety

- Webhook signatures verified with HMAC-SHA256, constant-time comparison
  (`hmac.compare_digest`), opt-in via `PAYMENT_WEBHOOK_SECRET`.
- Every inbound webhook event is deduplicated by `event_id` before any
  side effect — a redelivered event creates nothing twice.
- Payment state transitions are validated (`app/payment_state_machine.py`)
  — a `captured` payment can never be treated as `failed` again — and the
  payment's actual current status is re-verified immediately before any
  action executes, never on a diagnosis-time assumption.
- Retries are bounded by a fixed `max_attempts` policy, enforced in code.
- Tenant boundaries: every customer-scoped query filters through
  `Customer.merchant_id`, verified by a live cross-tenant leak test. The
  honest gap: case-mutating endpoints check that *a* valid credential was
  presented, not that it belongs to *that case's* merchant specifically —
  see [§11](#11-limitations).
- Secrets are read from environment variables only, never hardcoded;
  `.gitignore` excludes `.env` and `*.db`.
- Simulated demo data never reaches a real payment or messaging provider,
  regardless of what's configured (ADR-006).

Full audit: [`docs/SECURITY.md`](docs/SECURITY.md).

## 9. Demo flow

1. `./run.sh`, open `http://localhost:8000`, click **Run Recovery
   Simulation**.
2. Open any case — the detail view shows the diagnosed root cause, the
   recovery-probability prediction with its top contributing features,
   the expected-value breakdown, the recommended strategy, and the full
   agent timeline (every audit event, in order).
3. Switch merchants from the dropdown — case lists, revenue figures, and
   CSV export change completely, verifying tenant isolation live.
4. Find the "graceful cancellation" case (customer "Fatima Al-Rashid"
   after running the simulation): the agent diagnosed a strong retry
   candidate, then caught — via a real pre-action status check — that the
   payment had already resolved independently, and cancelled its own
   plan rather than falsely claiming credit. Backed by
   `test_graceful_cancellation_when_payment_already_captured`.

Line-by-line script: [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md).

## 10. Testing

```bash
pytest tests/ -q
```

90 tests, named around product scenarios rather than just code paths —
each of the following has a dedicated test, not just incidental coverage:
transient failure retried, permanent failure never blindly retried, retry
limit enforced, duplicate webhook rejected, invalid webhook signature
rejected, tenant isolation enforced, LLM network failure and malformed
output both fall back safely, an LLM recommendation outside the allowed
vocabulary is discarded, the deterministic policy layer still blocks an
AI-recommended action on a large amount, and a successful recovery
updates analytics. Full breakdown: [`docs/TESTING.md`](docs/TESTING.md).

## 11. Limitations

Stated plainly, in priority order:

1. **No RBAC.** Auth is one shared credential (`API_KEY` or
   `DASHBOARD_PASSWORD`) per deployment, not per-user identity — a
   production deployment serving real merchants needs this before
   onboarding a second human per merchant.
2. **ML model validated on simulated data only.** No real merchant
   outcome data exists for this project yet; any claim about real-world
   model accuracy would be fabricated.
3. **Expected value is advisory, not a hard gate** — a negative-EV action
   that clears the deterministic policy checks still executes today
   (ADR-003).
4. **Outbound webhook delivery has no retry queue** — a failed delivery
   is logged, not retried.
5. **Live Razorpay verification is partial**: Payment Links creation is
   verified live against `api.razorpay.com` with real test-mode
   credentials; a full round-trip including a completed test-mode
   payment and inbound webhook delivery with a Razorpay-issued signature
   has not been executed end to end. See
   [`docs/RAZORPAY_INTEGRATION.md`](docs/RAZORPAY_INTEGRATION.md).

## 12. Future improvements

1. Complete the live Razorpay round-trip (a real test-mode payment
   through to webhook delivery with a Razorpay-issued signature).
2. Retrain the ML model on real recovery outcomes as they accumulate.
3. Decide and implement one resolution for the EV-advisory gap in
   ADR-003 (either a policy rule or an independent gate — deliberately
   not decided under time pressure).
4. Full RBAC so a merchant can invite team members with scoped
   permissions.
5. Async webhook/outbound-delivery processing with retries, once volume
   warrants the added complexity.
6. XGBoost/MLflow only if the logistic-regression baseline demonstrably
   underperforms it on real data — not before.

## 13. Local setup

**Prerequisites**: Python 3.11+ (developed on 3.12), `pip`, `git`. No
database, message broker, or Node.js toolchain required.

```bash
git clone https://github.com/Arman25032005/Razorpay_ai_hackathone_arman.git
cd Razorpay_ai_hackathone_arman
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # leave every value blank for a fully offline run
uvicorn app.main:app --reload
```

Or `./run.sh`, which does the same and is safe to re-run. Then open
**http://localhost:8000** (dashboard) and **http://localhost:8000/docs**
(API). Click **Run Recovery Simulation** to generate demo data.

**Docker**: `docker build -t recoveryos . && docker run -p 8000:8000 recoveryos`,
or `docker compose up --build` for a Postgres-backed setup.

**Configuration** (all optional — full list in `.env.example`):

| Variable | Effect when set |
|---|---|
| `DATABASE_URL` | Postgres instead of the default SQLite |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Real Razorpay API calls instead of the mock provider |
| `LLM_API_KEY` | Diagnosis routed through a real LLM instead of the rule engine |
| `PAYMENT_WEBHOOK_SECRET` | Requires HMAC-SHA256-signed webhooks |
| `API_KEY` | Requires `X-API-Key` on mutating endpoints (machine callers) |
| `DASHBOARD_PASSWORD` | Requires a person to log in before the dashboard loads (gates every `/api/` route) |
| `SENDGRID_API_KEY` / `SENDGRID_FROM_EMAIL` | Real email delivery |
| `META_WHATSAPP_TOKEN` / `META_WHATSAPP_PHONE_NUMBER_ID` | Real WhatsApp delivery via Meta Cloud API |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_FROM` | Real WhatsApp delivery via Twilio (used if Meta credentials are unset) |

**ML pipeline** (a trained model is already committed, so this is
optional):

```bash
python -m ml.data_generator --rows 8000 --seed 42
python -m ml.train
python -m ml.evaluate
```

**Deployment**: `render.yaml` / `railway.json` / `Procfile` included.
Full checklist: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Documentation index

| Doc | Covers |
|---|---|
| [`docs/PRODUCT_DECISIONS.md`](docs/PRODUCT_DECISIONS.md) | What the product does and why, in plain language |
| [`docs/ARCHITECTURE_DECISIONS.md`](docs/ARCHITECTURE_DECISIONS.md) | ADRs — why each major architectural choice was made, trade-offs included |
| [`docs/DESIGN_WALKTHROUGH.md`](docs/DESIGN_WALKTHROUGH.md) | Component-by-component explanation + judge Q&A |
| [`docs/PRODUCT.md`](docs/PRODUCT.md) | Built-vs-roadmap breakdown |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Stack, request flow, agent loop |
| [`docs/RAZORPAY_INTEGRATION.md`](docs/RAZORPAY_INTEGRATION.md) | Every Razorpay endpoint used, real vs. mocked |
| [`docs/AGENT.md`](docs/AGENT.md) | Agent state, tools, policy engine, human-in-the-loop |
| [`docs/ML.md`](docs/ML.md) | Data generation, features, evaluation metrics |
| [`docs/DATABASE.md`](docs/DATABASE.md) | Schema, relationships, indexes |
| [`docs/API.md`](docs/API.md) | Full endpoint reference |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Implemented controls, known gaps |
| [`docs/PRIVACY.md`](docs/PRIVACY.md) | Data stored, retention |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Local, Docker, Render, Railway, CI/CD |
| [`docs/TESTING.md`](docs/TESTING.md) | Test coverage by category |
| [`docs/DEMO.md`](docs/DEMO.md) | Demo talking points |
| [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) | Line-by-line demo script |

## License

[MIT](LICENSE)
