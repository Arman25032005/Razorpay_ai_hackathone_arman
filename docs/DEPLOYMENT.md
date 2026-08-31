# Deployment

## Local development

```bash
./run.sh
```

Creates a virtualenv, installs `requirements.txt`, copies `.env.example`
to `.env` if missing, starts `uvicorn app.main:app --reload` on
`http://localhost:8000`. SQLite, zero external setup.

Manual equivalent:
```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/uvicorn app.main:app --reload
```

## Docker

**Standalone** (SQLite inside the container — fine for a demo, not for
multi-instance production):
```bash
docker build -t recoveryos .
docker run -p 8000:8000 recoveryos
```

**With Postgres** (`docker-compose.yml`):
```bash
docker compose up --build
```
Starts Postgres + the app wired to it via `DATABASE_URL`.

## Cloud deployment

**Render**: `render.yaml` is a ready Blueprint — connect the repo in the
Render dashboard. It provisions a free Postgres instance and a web service
(`uvicorn app.main:app --host 0.0.0.0 --port $PORT`), and prompts for
`LLM_API_KEY` / `API_KEY` / `PAYMENT_WEBHOOK_SECRET` / `RAZORPAY_KEY_ID` /
`RAZORPAY_KEY_SECRET` as optional secrets.

**Railway**: `railway.json` + `Procfile` included — connect the repo or
`railway up`; builds with Nixpacks, same start command. Add a Postgres
plugin and set `DATABASE_URL` from its connection string.

**Any other host** (Fly.io, a plain VM): deploy the container or run
`uvicorn app.main:app --host 0.0.0.0 --port $PORT` directly, point
`DATABASE_URL` at a managed Postgres instance (Supabase, Neon, RDS).

## Environment variables

See `.env.example` for the full annotated list. Everything is optional for
demo mode — the app runs fully functional with zero configuration.

| Variable | Purpose | Demo-mode default behavior if unset |
|---|---|---|
| `DATABASE_URL` | Postgres in production | SQLite (`./recoverai.db`) |
| `LLM_API_KEY` | Route diagnosis through real Claude | Deterministic rule engine |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | Real Razorpay API calls | Mock payment provider |
| `PAYMENT_WEBHOOK_SECRET` | Require signed webhooks | Signatures not required |
| `API_KEY` | Require `X-API-Key` on mutating endpoints | Open (no auth) |

## Honest gap: no CI/CD pipeline

Spec section 44 asks for GitHub Actions (lint/test/build/security checks).
**Not implemented in this build** — every verification in this project
(58 passing tests, live smoke tests) was run manually during development,
not automated on push/PR. This is a real, acknowledged gap for a 3-day
build, not a claimed feature. A minimal first version would be:

```yaml
# .github/workflows/ci.yml (not yet created)
- pip install -r requirements.txt
- pytest tests/ -q
- python -m ml.train  # confirm the ML pipeline still runs end-to-end
```

## Frontend / backend split

Both are served by the single FastAPI process (`static/` mounted at
`/static`, `index.html` served at `/`) — there is no separate frontend
deployment step, unlike the spec's Next.js-on-Vercel assumption. See
`docs/PRODUCT.md` for why.

## Production readiness checklist (spec section 51, filled in honestly)

- [x] Razorpay webhook works — request-shape verified against real docs; live round-trip NOT yet executed (needs real test-mode keys)
- [x] Webhook signature validation works (self-computed signature tested; not tested against a Razorpay-issued one)
- [x] Duplicate webhooks handled
- [x] Out-of-order events handled
- [x] Payment state machine works
- [ ] Database indexes — only `webhook_events.event_id` and `customers.merchant_id` are explicitly indexed; not a full indexing pass
- [x] Tenant isolation tested (live cross-tenant leak test)
- [x] Authentication works (API-key gate)
- [ ] RBAC — not implemented (single shared key)
- [x] ML model trained
- [x] ML model evaluated
- [x] Temporal test split used
- [x] Model calibration evaluated
- [x] Agent works
- [x] LLM structured output validated (allow-list check, fail-closed to rule engine)
- [x] Policy engine works
- [x] LLM cannot bypass policy
- [x] High-value payment requires review
- [x] Payment status verified before action
- [x] Audit trail works
- [x] Mock environment works
- [x] Demo mode works
- [x] Dashboard works
- [x] Tests pass (58/58)
- [x] Docker works
- [ ] CI — not implemented
- [x] Secrets are not committed (`.gitignore` covers `.env`, `*.db`)
- [x] README complete
- [x] Deployment instructions complete (this document)
