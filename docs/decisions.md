# Engineering Decisions

A quick-reference decision log. Each entry is a summary — full reasoning,
trade-offs, and the exact code/test that backs each claim live in
[`docs/ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md) (ADR-001
through ADR-006). This file exists for someone who wants the "what and
why" in thirty seconds, not the full ADR.

---

## Deterministic policy enforcement

**Decision**: Every action with a financial or communication side effect
passes through `app/policies/engine.py::check_policy` before it executes.
The diagnosis layer (rule engine or LLM) can recommend an action; it
cannot authorize one.

**Reason**: An LLM can be prompted carefully and validated on the way out,
but it can't be made to *guarantee* it will never recommend something
unsafe on an input it hasn't seen. A payments system needs a guarantee.
Putting the hard limits (max attempts, amount thresholds, channel
allow-list) in plain Python that a test suite exercises means the worst
an AI failure can do is get blocked, not executed.

**Trade-off**: An extra translation step — every recommendation has to be
mapped to an internal strategy/action-type and re-validated before
anything happens, instead of the model calling a provider directly. Full
detail: ADR-001, ADR-002.

---

## Expected value is advisory, not a gate

**Decision**: The system computes an expected recovery value per strategy
and shows it, but only the deterministic policy engine can block
execution.

**Reason**: A hard EV gate would give a soft, model-derived probability
estimate the same veto power as a fixed rule like "max 3 attempts."
Those are different kinds of confidence and shouldn't be treated
identically.

**Trade-off**: A negative-EV action that clears policy checks still
executes today. This is a known, undecided gap — see
[`docs/known-limitations.md`](known-limitations.md) — not an oversight.
Full detail: ADR-003.

---

## ML predicts one narrow thing

**Decision**: The trained model predicts exactly one number — the
probability a given action, on a given case, recovers the payment. It
does not choose root cause, strategy, or whether to act.

**Reason**: A model with one well-defined target can be evaluated
honestly against a temporal holdout. A model that implicitly also picked
strategy or authorization would be harder to evaluate, and any weakness
in it would flow straight into a payment action instead of being caught
by a downstream check.

**Trade-off**: The model is a smaller piece of the system than an
end-to-end "AI decides everything" design would use it for — less
impressive in a demo, easier to reason about and test. Full detail:
ADR-005.

---

## Logistic regression as the ML baseline

**Decision**: Use `sklearn.linear_model.LogisticRegression` with
standardized features, not a gradient-boosted or deep model.

**Reason**: The available training data is synthetic (see
[`docs/ML.md`](ML.md)) — there's no real merchant outcome data yet to
justify a more expressive model, and a model whose coefficients can be
read directly gives per-prediction explainability (the "top 3
contributing factors" shown in the dashboard) for free, without a
separate SHAP/LIME step.

**Trade-off**: A logistic regression can't capture non-linear interactions
between features the way a tree ensemble could. Acceptable for a baseline
that's explicitly evaluated as one (ROC-AUC 0.72 on a temporal holdout,
not claimed as production-grade). Revisit once real outcome data exists
and there's something to actually gain from more capacity.

---

## Simulated data never touches a real provider

**Decision**: The "Run Recovery Simulation" flow generates fake customers
with `fake.safe_email()` (guaranteed undeliverable) and swaps in
`MockPaymentProvider`/`MockCommunicationProvider` for the whole batch,
regardless of what real credentials are configured.

**Reason**: A demo that could accidentally send a real message or touch a
real payment the first time someone runs it with live keys configured
isn't a demo — it's an incident waiting to happen.

**Trade-off**: A single manual action on an *individual* case outside the
batch flow does use whatever provider is actually configured — this is a
narrower, accepted edge case, not something that was missed. See
`docs/known-limitations.md`. Full detail: ADR-006.

---

## Server-rendered frontend, no build step

**Decision**: The dashboard is plain HTML/CSS/JS served from `static/`,
with no bundler, framework, or build pipeline.

**Reason**: The dashboard's job is to make the agent's reasoning visible —
the timeline, the diagnosis, the policy check that blocked or allowed an
action. None of that needs client-side routing, component state
management, or a build step to render well; adding one would be
complexity spent on the wrong problem for a project this size.

**Trade-off**: No component reuse across pages beyond hand-written JS
functions, and no type checking on the frontend. Acceptable at the
current size (~900 lines of `app.js`); would be worth reconsidering if
the dashboard grew substantially more views or state.

---

## SQLite by default, Postgres via `DATABASE_URL`

**Decision**: SQLAlchemy ORM with SQLite as the zero-setup default,
switching to Postgres purely by setting an environment variable.

**Reason**: A relational model matches the actual entity relationships
here (merchant → customer → case → action → audit) and enforces them with
real foreign keys. SQLite means `git clone && pip install && run` works
with no external service; Postgres is a one-variable swap for anything
beyond a single-instance demo.

**Trade-off**: SQLite's relaxed durability settings (`PRAGMA
synchronous=NORMAL`, see `app/db.py`) trade a sliver of crash-durability
for faster simulation runs — an explicit, documented choice for demo
data, not something that should carry over to a production Postgres
deployment without reconsidering it.
