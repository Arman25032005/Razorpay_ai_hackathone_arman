# Demo

For the full line-by-line presenter script (opening, closing, fallback
answers to likely judge questions), see `DEMO_SCRIPT.md` in the repo root
— that document covers the core recovery-loop narrative in detail. This
document covers the **additional beats added since that script was
written**: multi-tenancy, the ML model, the expected-value framework, and
the graceful-cancellation demo case, plus a suggested extended flow that
incorporates all of it.

## Extended demo flow (RecoveryOS additions)

**1. Start on the Executive Dashboard, merchant switcher visible in the
top bar.**

> "This isn't a single-merchant tool — it's multi-tenant. Right now I'm
> viewing 'All merchants,' an admin view. Let me run the simulation and
> show you real tenant isolation."

**2. Click "Run Recovery Simulation."** Then switch the merchant dropdown
between the two or three demo merchants (Zenith Fashion Co., Bharat SaaS
Solutions, QuickCart Grocers).

> "Notice the revenue-at-risk number, the case list, even the CSV export —
> all change completely when I switch merchants. This isn't a filtered
> view of the same data; it's genuinely isolated. I can prove it: a
> customer visible under this merchant is completely invisible under that
> one." *(This is backed by a real test, not just visual — `test_dashboard_merchant_filter_excludes_other_tenant_cases`.)*

**3. Open any case with a diagnosis, scroll to "ML Recovery Probability."**

> "This percentage isn't the same as the rule-based confidence score above
> it — it's a real, separately-trained logistic regression model,
> evaluated on a held-out test set with actual precision, recall, and
> ROC-AUC numbers you can check yourself at `/api/models/current`. And
> it's explainable — these three factors are the model's own actual
> learned coefficients, not a canned explanation."

**4. Scroll to "Expected Value."**

> "Below that: the business math. Probability times recoverable amount,
> minus the cost of actually taking the action, minus a cost for how many
> times we've already bothered this customer, minus a risk cost for
> aggressive strategies. This is advisory, not a gate — the policy engine
> above it is still the one hard authority on what's allowed — but it's
> exactly the cost-sensitive decision framework a real merchant finance
> team would want to see."

**5. Find the "Fatima Al-Rashid" case (Scenario H) — filter by RECOVERED
with stop reason PAYMENT_ALREADY_RESOLVED, or just search the case list.**

> "This is the failure-handling demo the brief specifically asks for.
> Watch the timeline: the agent diagnosed this as a strong retry
> candidate — 94% confidence, reliable customer. But right before
> executing, it re-checked the payment's actual current status and found
> it had already been captured — the customer paid another way in the
> meantime. So the agent cancelled its own planned action instead of
> executing anyway and claiming credit for something it didn't do. That's
> not a UI trick — it's a real state-machine verification gate that runs
> before every single action, every time."

**6. Mention Razorpay grounding, even without a live key configured.**

> "The payment integration is built directly against Razorpay's real
> documented API — not a generic stand-in. The webhook parser handles
> their actual payload shape, down to converting paise to rupees
> correctly and mapping their real error codes. And it's honest about
> what doesn't exist: Razorpay has no 'retry this payment' API — only the
> customer can retry — so in live mode this issues a fresh Payment Link
> instead of faking an instant success. That distinction is documented in
> `docs/RAZORPAY_INTEGRATION.md`, endpoint by endpoint."

## What to say if asked "is any of this live against real Razorpay servers?"

Be direct: **not yet, in this environment** — the integration is built and
verified for request-shape correctness against Razorpay's real
documentation, but the live round-trip against `api.razorpay.com` with
real test-mode credentials hasn't been executed here (no test-mode keys
were available during development). That's stated plainly in
`docs/RAZORPAY_INTEGRATION.md` section 8 and `docs/PRODUCT.md` — this
project treats "not yet verified live" as a fact to disclose, not a gap to
paper over.

## What to say if asked about the tech-stack deviations (Mongo/Next.js/LangGraph/XGBoost)

> "Given the timeline, I made a deliberate call: get a smaller, real,
> fully-tested system working end to end, rather than a larger,
> higher-risk stack that might not finish. Every deviation is documented
> in `docs/PRODUCT.md` with the specific reasoning, and none of it is
> hidden or claimed as something it isn't."

This honesty is itself a demonstration of engineering judgment, not just a
disclaimer — a system a judge can actually run and verify beats a bigger
promise that doesn't work on the day.
