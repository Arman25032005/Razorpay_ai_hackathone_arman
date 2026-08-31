# Security

## What's implemented and where to find it

| Requirement | Implementation | File |
|---|---|---|
| Secrets in environment variables only | All keys read via `os.getenv()`, never hardcoded | `.env.example`, `app/providers/payment.py`, `app/security.py` |
| No API keys in Git | `.gitignore` excludes `.env`; `.env.example` has placeholders only | `.gitignore` |
| Webhook signature validation | HMAC-SHA256, constant-time comparison, accepts Razorpay's real `X-Razorpay-Signature` header | `app/security.py::verify_webhook_signature` |
| API auth | Optional `X-API-Key` gate on mutating endpoints (case actions, policy edits, promise-to-pay, demo reset) | `app/security.py::require_api_key` |
| Rate limiting | Sliding-window limiter on webhook and simulation endpoints | `app/security.py::rate_limit` |
| Input validation | FastAPI + Pydantic type validation on every endpoint | throughout `app/main.py` |
| SQL injection protection | SQLAlchemy ORM everywhere; no raw string-built queries anywhere in the codebase | — |
| Tenant isolation | Merchant-scoped queries; verified with a live cross-tenant leak test | `app/main.py`, `tests/test_core.py::test_dashboard_merchant_filter_excludes_other_tenant_cases` |
| Audit logging | Every state-changing action writes an immutable `AuditEvent` row | `app/agents/orchestrator.py::_log` |
| Idempotency | Webhook `event_id` deduplication before any side effect | `app/webhooks.py` |
| Fail-closed on AI failure | Diagnosis exceptions route to human escalation, never crash or guess | `app/agents/orchestrator.py::analyze_case` |

## Honest gaps (not implemented, not pretended)

- **No RBAC.** Auth is a single shared API key per deployment, not
  per-user roles/permissions. A production deployment serving real
  merchants would need this before onboarding a second human user per
  merchant.
- **No password/JWT-based session auth.** There is no login UI; the API
  key is a deployment-level secret, not a per-user credential.
- **No encryption-at-rest configuration documented.** SQLite/Postgres
  encryption-at-rest is the responsibility of the hosting platform (e.g.
  RDS/Atlas encryption settings) and hasn't been separately configured or
  verified here.
- **CORS is currently wide open** (`allow_origins=["*"]` in `app/main.py`)
  for local-demo convenience. **Must be locked down to specific origins
  before any production deployment.**
- **No CSRF protection** — not applicable to the current API-key-based
  auth model (CSRF matters for cookie-based session auth, which isn't
  used here), but would need reconsidering if session-based auth is added.
- **Rate limiter is in-memory, per-process.** Fine for a single instance;
  not a substitute for an edge/gateway rate limiter if this scales to
  multiple instances behind a load balancer.
- **No dependency vulnerability scanning configured** (e.g. `pip-audit`,
  Dependabot) — not run as part of this build.
- **No penetration testing or third-party security review** has been
  performed. Everything above is self-assessed against the code, not
  independently verified.

## What a production deployment must add before going live with real payment data

1. Lock down CORS to the actual frontend origin(s).
2. Add per-user auth (JWT or OAuth) + RBAC before any deployment with more
   than one human operator per merchant.
3. Set `PAYMENT_WEBHOOK_SECRET` and `API_KEY` — both are optional/no-op by
   default for demo convenience, which is the correct default for a demo
   but the wrong default for production.
4. Confirm the hosting platform's encryption-at-rest and in-transit (TLS)
   configuration explicitly — don't assume it.
5. Add dependency scanning to CI before merging.
6. Get an actual security review before processing real customer payment
   data — self-assessment (this document) is not a substitute.
