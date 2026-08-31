# RecoverAI — Demo Script

A ready-to-read script for presenting RecoverAI to judges. Pair with the
live dashboard at `http://localhost:8000` (or your deployed URL).

---

## 30-second opening

> Businesses don't lose revenue in one clean step. A card fails, a checkout
> is abandoned, or an invoice goes overdue. Existing systems detect the
> problem, but someone still has to recover the money.
>
> RecoverAI is an agent that does the recovering — not just the detecting.

---

## Live demo flow

**1. Open the Executive Dashboard.** It's empty or shows the previous run.

> "This is a fresh RecoverAI instance. Right now it knows nothing — no
> customers, no revenue events, nothing to recover."

**2. Click "Run Recovery Simulation."**

> "This generates a realistic batch of customers — payment histories,
> B2B invoice histories, checkout carts — and opens revenue-loss cases for
> the ones with problems: failed payments, abandoned checkouts, overdue
> invoices. Then it runs every single one through the full agent loop,
> live."

Wait for the run-complete banner. Read the numbers directly off the screen:

> "RecoverAI detected ₹X at risk. It diagnosed the likely cause for every
> case. It chose an intervention. It executed it. ₹Y was **actually**
> recovered — not predicted, actually recovered, because every dollar here
> is backed by a simulated payment-success event, not an AI guess."

**3. Click "Replay Best Recovery"** (the Acme Software hero case).

> "Let's walk through one case in detail. Acme Software — an 18-payment,
> zero-prior-failure B2B customer — had a ₹1,24,000 subscription payment
> fail."

Point at the case timeline as it opens:

> "The agent retrieved their history, diagnosed this as a temporary
> failure — high recovery probability given their track record — and
> recommended an immediate retry. But because ₹1,24,000 crosses our
> large-amount policy threshold, it stopped and asked a human to approve
> before doing anything. That's not a suggestion — it's a hard rule in
> code, not something the AI can talk its way around."

Scroll to the approval + retry sequence:

> "Once approved: first retry failed. Second retry succeeded. ₹1,24,000
> recovered. Workflow stopped automatically — reason: PAYMENT_RECOVERED.
> Every one of those steps is in the audit trail below, timestamped,
> nothing hidden."

**4. Go to the Human Review Queue.**

> "Not every case gets automated. Here are the cases RecoverAI escalated —
> large amounts, low-confidence diagnoses, or cases that hit their maximum
> retry attempts without success. The agent doesn't guess when it
> shouldn't; it hands off."

**5. Go to Analytics → Recovery Policy Optimizer.**

> "This is a live, statistical view of which strategies actually recover
> money — success rate and total recovered, computed from real outcomes
> in this run, not machine-learning predictions. The agent uses this to
> prefer strategies with a track record once there's enough data."

**6. (Optional) Go to Policies.**

> "These aren't just documentation — they're editable, and they're
> enforced. Watch: I'll drop the large-amount threshold from ₹1,00,000 to
> ₹50,000..."

Edit and save, then point at a mid-range case that flips to ESCALATED.

> "...and a case that would have auto-processed a moment ago now requires
> human sign-off. The policy engine reads this on every check — it's not
> cosmetic."

---

## Closing

> "Traditional systems say: 'Payment failed.'
>
> RecoverAI says: '₹X is at risk. We analyzed why. We selected an
> intervention. We executed it. ₹Y was actually recovered. We stopped
> safely when it worked, and escalated the cases that needed a human.
> Every decision is auditable.'
>
> This isn't an AI wrapper around a dashboard. It's an agent operating a
> bounded, policy-gated revenue-recovery workflow — and you just watched
> it work."

---

## Fallback answers for likely judge questions

- **"Is any of this real money movement?"** No — demo mode runs entirely
  on `MockPaymentProvider`/`MockCommunicationProvider` with realistic,
  root-cause-dependent success probabilities. The architecture is a
  provider-adapter pattern (`app/providers/`), so a real Stripe/Razorpay
  integration is a swap-in, not a rewrite — see the Live Integration Mode
  section of the README.
- **"What stops the AI from doing something unsafe?"** The policy engine
  (`app/policies/engine.py`) is plain deterministic code — max attempts,
  workflow expiration, channel allowlist, discount/large-amount approval —
  checked before every single action. The LLM/diagnosis layer only ever
  returns a structured decision; it never calls a provider directly.
- **"What happens if the AI/LLM fails?"** The case is marked for human
  review rather than the agent guessing or crashing — verified by a test
  (`test_diagnosis_failure_escalates_instead_of_crashing`).
- **"Does this scale?"** Verified end-to-end at 1,000 customers / 500+
  cases in ~15 seconds on SQLite; the ORM is Postgres-ready via
  `DATABASE_URL` for production.
