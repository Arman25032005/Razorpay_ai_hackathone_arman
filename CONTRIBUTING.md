# Contributing

## Setup

```bash
./run.sh
```

or manually:
```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## Before opening a PR

```bash
./venv/bin/python -m pytest tests/ -q      # all tests must pass
node --check static/app.js                 # frontend syntax check
```

There is no CI pipeline yet (see `docs/DEPLOYMENT.md`'s honest gap
section) — these checks are currently manual. Please run both before
submitting.

## Code organization

See `docs/ARCHITECTURE.md` for the directory layout and the reasoning
behind it before adding new modules.

## Principles this codebase tries to hold to (worth reading before a PR)

- **The LLM/rule engine never executes anything directly.** It returns a
  structured decision; only `app/policies/engine.py` gates action
  execution, in plain deterministic code. If you're adding a new
  AI-driven capability, keep this separation.
- **Never claim a live integration when only a mock exists.** If you add
  a new external integration and can't verify the live round-trip in your
  environment, say so explicitly in the code comments and in
  `docs/RAZORPAY_INTEGRATION.md` (or the relevant doc) — see how the
  current Razorpay integration documents "verified request-shape,
  not-yet-verified live round-trip" as its own honest state.
- **Never claim a metric without computing it.** If you touch `ml/`, run
  `python -m ml.train` and let the real output speak — don't hand-edit
  `metrics.json`.
- **Keep the docs honest.** If you build something that deviates from
  what a doc currently claims, update the doc in the same PR. Several
  docs in this repo were caught out-of-sync with the code during
  development and corrected before merging — the standard is to verify a
  claim against the actual running code before writing it down, not
  after.

## Tests

New behavior should come with a test. See `docs/TESTING.md` for the
current coverage shape and what "meaningful coverage" has meant in this
project so far — not chasing 100%, but covering the decisions that matter
for correctness and safety.
