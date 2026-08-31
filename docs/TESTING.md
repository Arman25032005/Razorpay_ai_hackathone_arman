# Testing

## Run

```bash
./venv/bin/python -m pytest tests/ -q
```

**58 tests, all passing** on a fresh `pip install -r requirements.txt`,
verified repeatedly throughout this build rather than run once and trusted.

## Coverage by category (extracted directly from `tests/test_core.py`'s
section markers, not hand-summarized separately from the actual file)

| Category | What's covered |
|---|---|
| Policy tests | Max attempts, workflow expiration, large-amount approval, disallowed channels, discount approval |
| Agent tests | Structured diagnosis output, unrecognized event-type safe handling, diagnosis field population |
| Recovery tests | Payment success creates recovery, failed payment doesn't count as recovery, max-attempts escalation |
| Accounting tests | Recovery rate calculation correctness |
| Webhook / idempotency tests | Duplicate webhook creates no second case, correct amount from webhook |
| Promise-to-pay tests | Fulfillment marks case recovered |
| Policy persistence tests | Edited policy limits are actually read back and change agent behavior (not just displayed) |
| Strategy optimizer tests | Defers to diagnosis when no historical data exists |
| Safety / error-handling tests | Diagnosis engine failure escalates to human review instead of crashing |
| Security tests | Webhook signature required/not-required states, API key required/not-required states |
| Prioritization / summarization tests | Larger + higher-confidence cases rank first; summary is factual and concise |
| Channel diversity / optimizer wiring | Channel selection varies by case context; optimizer never overrides diagnosis without real history |
| Razorpay integration tests | Real payload paise conversion, error-code/description mapping precedence, stable event ID derivation, end-to-end idempotency, provider selection by env var |
| Customer Health Score tests | High/low/no-history scoring bands |
| Payment State Machine tests | Legal `failed -> captured` transition, rejected out-of-order downgrade, duplicate no-op, terminal-state enforcement |
| ML pipeline tests | Learnable-not-trivial label distribution, causal-structure sanity checks (reliability and attempt-count direction), chronological temporal split, safe None-handling when no model trained, real model round-trip |
| Multi-tenant isolation tests | Customer correctly scoped to merchant, cross-tenant case query isolation, nullable merchant_id backward compatibility |
| Expected value framework tests | Act/do-not-act recommendation correctness, annoyance-cost scaling, confirmed advisory-only (never imported by the policy engine) |

## What "meaningful coverage" meant in practice for this build

Per spec section 36's explicit instruction not to chase 100% coverage
artificially: the priority was covering the decisions that actually matter
for correctness and safety (policy enforcement, idempotency, the
graceful-cancellation path, tenant isolation) over exhaustively testing
every getter/formatter in the codebase.

## Honest gaps

- **No frontend/UI tests.** `static/app.js` is checked for syntax validity
  (`node --check`) as part of every verification pass in this build, but
  there's no automated browser-level testing (Playwright/Selenium) of the
  actual rendered UI — every UI verification in this build was done by
  manually reading live API responses and reasoning about what the
  corresponding JS would render, not by driving a real browser.
- **No load/performance test suite** beyond the manual 1,000-customer
  simulation timing check documented in `docs/ARCHITECTURE.md` and the
  README.
- **No CI** — see `docs/DEPLOYMENT.md`'s honest gap section. All 58 tests
  have been run manually, repeatedly, but never on an automated
  push/PR trigger.
- **ML tests validate the pipeline's mechanics and causal-structure
  sanity, not the specific numeric metric values** (those are expected to
  vary slightly by random seed and are reported transparently via
  `python -m ml.evaluate` rather than hard-asserted in a test, since
  asserting exact floating-point metric values would make the test suite
  brittle to any reasonable retraining).
