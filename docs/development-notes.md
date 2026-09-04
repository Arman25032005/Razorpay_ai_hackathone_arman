# Development Notes

This isn't a full changelog — it's the subset of the project's git
history that reflects an actual engineering decision or a bug that
shaped the design, kept here so the reasoning isn't lost. Every item
below is traceable to a real commit or a documented fix; nothing here is
reconstructed from memory.

## Scaffolding before features

The repository started as configuration and tooling (`7dd3e90`, "Project
scaffolding: env config, deployment configs, tooling") *before* the
application code landed (`046c580`, "Add RecoverAI application"). Getting
`.env.example`, the Dockerfile, and `render.yaml`/`railway.json` in place
first meant the app was deployable from its first real commit, instead of
deployment being an afterthought bolted on once the demo was "done."

## A documented calibration bug in the ML pipeline

The first version of the synthetic-data generator's amount penalty used
`/20000` instead of `/200000` (see [`docs/ML.md`](ML.md)). That off-by-a-
zero mistake was aggressive enough that a ₹124,000 case for a highly
reliable customer scored a 2.9% recovery probability from the trained
model — directly contradicting the rule engine's own assessment of that
same case as high-probability. It was caught by testing the trained model
against a real application case, not by a unit test on the generator in
isolation, which is itself worth noting: the generator's own tests
(`test_data_generator_produces_learnable_not_trivial_labels`, etc.)
checked that labels were *learnable*, not that the model's predictions on
a specific known case were *reasonable*. Fixed and re-verified; the same
case now scores 74%, consistent with the rule engine.

## Docstring trim pass (2026-08-31)

A run of eight commits — `fbfda18`, `c8ec14e`, `57ee67e`, `48a590c`,
`8ba983e`, `43a2898`, `57b1fba`, `5db4eaf` — went through `ai_service.py`,
`orchestrator.py`, `models.py`, `payment_state_machine.py`,
`expected_value.py`, `security.py`, `payment.py`, and the data generator
specifically to cut over-explained docstrings and one duplicated
explanation of the Razorpay payment lifecycle that had drifted into two
files. One of those commits is labeled directly as an "AI-assisted
cleanup pass" — AI assistance was used throughout this project's
development, and that commit message is left as-is rather than edited to
obscure it.

## Fixing a real timestamp display bug

`83d0dec` ("Fix UTC timestamps displaying as local time") addressed a
genuine class of bug: the backend serializes naive UTC datetimes with no
timezone suffix, and the ECMAScript `Date` constructor parses a
timezone-less string as *local* time, not UTC — so a timestamp of
`19:29` UTC was rendered as `19:29` in the viewer's own timezone instead
of being converted. The fix (`utcDate()` in `static/app.js`) tags the
string as UTC before parsing. This is the kind of bug that only shows up
once you're actually looking at real timestamps in a browser in a
non-UTC timezone, not something a unit test running in UTC on CI would
catch — worth remembering next time a "timestamps look wrong" report
comes in from somewhere other than UTC.

## Correcting a data realism detail

`faedcc4` switched the simulation's fake customers from `fake.email()` to
`fake.safe_email()`. The difference matters: `fake.email()` can produce an
address at a real, existing domain, meaning a demo running with live
SendGrid/Twilio credentials configured could send an actual email or
message to a real inbox that happens to share a domain with a
Faker-generated local part. `fake.safe_email()` guarantees an
IANA-reserved, permanently undeliverable domain instead. Small change,
meaningfully different risk profile for anyone running this with live
keys.

## What this history doesn't include

There's no evidence in this repository's history of A/B-tested design
alternatives, user feedback cycles, or performance benchmarking against
prior versions — this was built and iterated on solo, on a compressed
timeline, without production traffic to learn from. That's stated plainly
rather than invented, and it's also why [`docs/known-limitations.md`](known-limitations.md)
exists as a separate, honest accounting of what hasn't been validated yet.
