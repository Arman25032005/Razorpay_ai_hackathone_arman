# Known Limitations

A single, honest place to look before deciding whether this is ready for
something it wasn't built for. Everything here is either stated in
`README.md` §11, in `docs/SECURITY.md`, in `docs/ARCHITECTURE_DECISIONS.md`,
or found during a direct read of the current code — nothing below is
speculative.

## Status

This is a hackathon-stage prototype with a working backend, a trained ML
baseline, and a real test suite — not a system that has processed real
merchant traffic. Treat every claim below about "what would need to
change" as the honest gap between that and a production deployment.

## Data and ML

- **No real merchant outcome data.** The ML model is trained and
  evaluated entirely on synthetic data with a documented causal structure
  (`ml/data_generator.py`, `docs/ML.md`) — a temporal train/val/test
  split and real precision/recall/ROC-AUC numbers, but against fabricated
  cases, not real recovery outcomes. Any claim about real-world accuracy
  would be dishonest until it's retrained on real data.
- **Logistic regression, by design.** Chosen as an explainable, honestly-
  evaluated baseline over a heavier model — see
  [`docs/decisions.md`](decisions.md). It cannot capture non-linear
  feature interactions a gradient-boosted model could; that's an accepted
  trade-off for a baseline, not an oversight.
- **A prior calibration bug is documented, not hidden** — see
  [`docs/development-notes.md`](development-notes.md). It was caught and
  fixed, but its existence is a reminder that the generator's own formula
  hasn't been independently reviewed beyond this project's own testing.

## Policy and decision logic

- **Expected value is advisory, not a gate** (ADR-003). A case with
  negative expected value that clears the deterministic policy checks
  still executes today. This is a known, deliberately undecided design
  question — see `docs/ARCHITECTURE_DECISIONS.md` for the two possible
  resolutions that weren't picked under time pressure.
- **Hard-coded policy thresholds.** Max attempts, workflow age, and the
  large-amount approval threshold are fixed defaults
  (`app/policies/engine.py::DEFAULT_POLICY`), editable per-deployment via
  the Policies tab but not per-merchant-segment or per-customer-risk-tier
  automatically.
- **No downstream-failure audit trail.** `execute_next_action`
  (`app/agents/orchestrator.py`) does not wrap its calls to
  `payment_provider`/`communication_provider` in a try/except the way
  `analyze_case` explicitly fails closed on diagnosis errors. If a live
  provider call raises (a network error against a real Razorpay/SendGrid
  outage, for example), the uncommitted database changes for that attempt
  are discarded when the session closes — so no partial or double-counted
  state persists — but the case is simply left in its prior state with no
  audit event explaining what happened, and the API caller sees a generic
  500. Diagnosis failures are handled more gracefully than execution
  failures today; that asymmetry hasn't been closed.

## Payments and messaging

- **Live Razorpay verification is partial.** Payment Links creation is
  verified live against `api.razorpay.com` with real test-mode
  credentials; a full round-trip including a completed test-mode payment
  and inbound webhook delivery with a Razorpay-issued signature has not
  been executed end to end. See `docs/RAZORPAY_INTEGRATION.md`.
- **Outbound webhook delivery has no retry queue.** A failed delivery to
  a merchant-registered endpoint is logged (`WebhookDelivery.success =
  False`), not retried.
- **A single manual action outside the simulation batch uses whatever
  provider is actually configured** — it is not automatically mocked the
  way a full simulation run is (ADR-006). The residual risk is bounded
  (`fake.safe_email()` guarantees any such send is undeliverable), but
  it's worth knowing this edge case exists rather than assuming every
  code path is simulation-safe.

## Access and multi-tenancy

- **No RBAC.** Auth is one shared credential (`API_KEY` and/or
  `DASHBOARD_PASSWORD`) per deployment, not per-user identity. A second
  human on the same merchant account has the same access as the first.
- **Session tokens aren't individually revocable.** A token from
  `/api/auth/login` is valid until its expiry regardless of logout
  elsewhere; the only revocation lever is rotating `SESSION_SECRET`,
  which invalidates every outstanding token at once.
- **Tenant isolation is data-scoped, not credential-scoped.**
  Customer-scoped queries filter through `Customer.merchant_id` (verified
  by `test_dashboard_merchant_filter_excludes_other_tenant_cases`), but a
  case-mutating endpoint checks that *a* valid credential was presented,
  not that it belongs specifically to *that case's* merchant.

## Operational

- **CORS is wide open** (`allow_origins=["*"]`) — a deliberate demo
  default that must be locked down to specific origins before production.
- **Rate limiting is in-memory and per-process** — fine for a single
  instance, not a substitute for an edge/gateway limiter behind a load
  balancer.
- **No dependency vulnerability scanning** configured in CI.
- **No third-party security review.** `docs/SECURITY.md` is a
  self-assessment against the current code, not an independent audit.

## What this list is for

Every item above is something a reviewer, a judge, or a future
contributor should be able to find on their own by reading the code —
this file just saves them the trip. If something on this list stops being
true, update it here rather than letting the code and the docs drift
apart.
