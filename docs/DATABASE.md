# Database

## Engine

SQLAlchemy ORM. SQLite by default (`sqlite:///./recoverai.db`, zero setup
for local demo), Postgres-ready via `DATABASE_URL` — same models, same
code, no branching (see `docker-compose.yml` for a Postgres-backed local
setup). Spec called for MongoDB; this uses a relational schema instead —
see `docs/PRODUCT.md` for the explicit reasoning (real foreign keys made
the tenant-isolation guarantee straightforward to enforce and test).

## Conceptual ER relationships

```
Merchant (1) ---- (many) Customer
Customer (1) ---- (many) Payment
Customer (1) ---- (many) Invoice
Customer (1) ---- (many) Subscription
Customer (1) ---- (many) RecoveryCase
RecoveryCase (1) ---- (many) RecoveryAction
RecoveryCase (1) ---- (many) AuditEvent
RecoveryCase (1) ---- (many) PromiseToPay
WebhookEvent            (standalone — dedup ledger, referenced by resulting_case_id)
RecoveryPolicy           (standalone — one active row named "default", editable)
```

## Tables

### merchants
Tenant root. `id, name, razorpay_account_id, industry, created_at`.

### customers
`id, merchant_id (FK, nullable), name, email, phone, company, customer_type
(B2C/B2B), lifetime_value, risk_profile, created_at`. `merchant_id` is
indexed (`index=True`) since every tenant-scoped query filters on it.
Nullable for backward compatibility with non-multi-tenant usage.

### payments
`id, customer_id (FK), subscription_id (FK, nullable), amount, currency,
status, failure_reason, provider, provider_payment_id, created_at`.
`status` values used: `pending, failed, succeeded` internally — mapped
to/from Razorpay's real lifecycle by `app/payment_state_machine.py` (see
`docs/RAZORPAY_INTEGRATION.md` section 6).

### subscriptions
`id, customer_id (FK), plan, amount, billing_cycle, status, next_billing_date`.

### invoices
`id, customer_id (FK), invoice_number, amount, currency, issue_date,
due_date, status, days_overdue`.

### recovery_cases
`id, customer_id (FK), source_type, source_id, amount_at_risk, currency,
status, risk_level, root_cause, root_cause_confidence, reasoning_summary,
recommended_strategy, current_step, attempt_count, amount_recovered,
stop_reason, human_escalation_required, escalation_reason, human_approved,
created_at, updated_at, resolved_at`.

`status` values: `OPEN, ANALYZING, ACTION_READY, EXECUTING, RECOVERED,
FAILED, ESCALATED, STOPPED, EXPIRED` (see `docs/AGENT.md` for how these map
onto the spec's suggested case-management statuses).

### recovery_actions
`id, case_id (FK), action_type, channel, status, reason, message_body,
executed_at, result, amount_recovered, provider_response (JSON)`.

### recovery_policies
`id, name, max_attempts, max_days, max_discount_percent, allowed_channels
(JSON), require_human_approval_for_discount, require_human_approval_for_large_amount,
large_amount_threshold`. One row named `"default"` is the live, editable
policy (`app.policies.engine.get_active_policy()` reads it; falls back to
the hardcoded `DEFAULT_POLICY` dict if no row exists yet).

### promises_to_pay
`id, case_id (FK), promised_amount, promised_date, status, created_at,
fulfilled_at`.

### audit_events
`id, case_id (FK), actor_type (SYSTEM/AI_AGENT/USER/PAYMENT_PROVIDER/CUSTOMER),
action, description, event_metadata (JSON), timestamp`. Immutable by
convention — the application never updates or deletes an `AuditEvent` row
once written.

### webhook_events
`id, provider, event_id (unique, indexed), event_type, payload (JSON),
resulting_case_id, received_at`. This is the idempotency ledger — see
`app/webhooks.py`.

## Indexes and constraints actually implemented

- `webhook_events.event_id`: `unique=True, index=True` — the actual
  enforcement mechanism behind webhook idempotency.
- `customers.merchant_id`: `index=True` — every tenant-scoped query
  filters on this.
- Primary keys are all application-generated string IDs (`gen_id()` —
  16 hex characters, chosen after an 8-character version produced a real
  collision at ~1000-customer simulation scale during testing), not
  auto-increment integers, so IDs are stable and predictable across
  environments without needing to know a DB-assigned sequence value.

## Honest gaps

- **No formal schema migration tool** (no Alembic). Schema changes during
  this build were applied by dropping and recreating the SQLite file
  (`rm recoverai.db*`), which is fine for a demo but would need a real
  migration strategy before a production deployment with persistent data.
- **No explicit soft-deletion** — nothing in this schema is ever soft- or
  hard-deleted except via the full `POST /api/simulation/reset` (which
  hard-deletes all demo data, not a production-safe operation).
- **No formal data-retention job** — see `docs/PRIVACY.md`.
