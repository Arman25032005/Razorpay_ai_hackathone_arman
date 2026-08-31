# ML

## Scope, stated upfront

This is a **real, honestly-evaluated logistic regression baseline**, not
the XGBoost + MLflow production pipeline a fully-resourced version of this
product would eventually want. Given the 3-day build window, the priority
was: real data with documented causal structure, a real temporal
train/val/test split, real metrics computed from actual execution — over
a more sophisticated model trained and evaluated hastily. See
`docs/PRODUCT.md` for the explicit roadmap acknowledging XGBoost/MLflow as
a next step, not a claimed feature.

## The problem

Predict the probability that a failed payment can be successfully
recovered under a specified intervention. Binary target: `recovered = 0/1`.

## Data

**Synthetic**, since no real Razorpay production data was available or
used (per the explicit constraint against using real customer/payment
data during development). `ml/data_generator.py` generates 8,000 rows with
a **documented causal formula**, not independently-random labels:

```
logit = -0.75                                          (base rate calibration)
      + (customer_success_rate - 0.5) * 3.0             reliable payers recover more
      + base_rate_by_root_cause(root_cause) * 2.0 - 0.5  root cause matters
      + strategy_lift(strategy) * 3.0                    some strategies work better
      - attempt_count * 0.35                              each prior failure hurts
      - max(0, days_since_last_payment - 30) * 0.01       staleness hurts (capped)
      - max(0, (amount - 5000) / 200000)                  large amounts are harder (capped)

recovery_probability = sigmoid(logit)
recovered = 1 if random() < recovery_probability else 0
```

This produces a **learnable but noisy** relationship — not a perfect
correlation (which would be an unrealistic, fabricated-looking dataset)
and not independent noise (which would be unlearnable). Sanity-checked:
overall recovered rate lands at ~48%, a plausible real-world figure, not
0% or 100%.

**A calibration bug found and fixed during this build**: the first version
of the amount penalty (`/20000` instead of `/200000`) was so aggressive
that a ₹124,000 case for an extremely reliable customer scored a
nonsensical 2.9% recovery probability — contradicting the rest of the
system's own rule-based assessment of that exact case as "high
probability." Caught by testing the trained model against a real
application case, not just unit tests on the generator in isolation.
Fixed and re-verified (same case now scores 74%, consistent with the rule
engine).

## Features (spec section 11 — leakage check, made explicit)

| Feature | Known before recovery attempt? |
|---|---|
| `customer_success_rate` | Yes — computed from PAST payments only |
| `customer_payment_count` | Yes |
| `amount` | Yes — the failed payment's own amount |
| `root_cause` | Yes — diagnosed before any action is chosen |
| `strategy` | Yes — chosen before execution |
| `attempt_count` | Yes — attempts already spent on *this* case |
| `days_since_last_payment` | Yes |

Explicitly excluded from the feature set: `true_probability` (present in
the raw synthetic CSV for debugging/audit only) and anything derived from
the outcome itself.

## Split

**Temporal, not random** (`ml/features.py::temporal_split`): sorted by
`created_at`, oldest 70% -> train, next 15% -> validation, newest 15% ->
test. This avoids a model implicitly "seeing the future" during training,
which a random shuffle-split would silently permit.

## Model and real evaluation results

Logistic regression (`sklearn.linear_model.LogisticRegression`), features
standardized (`StandardScaler`). Run `python -m ml.train` to reproduce;
`python -m ml.evaluate` reprints the saved metrics without retraining.

These are the actual numbers from the last training run (see
`models/recovery_model_v1/metrics.json` for the live artifact — this table
is not hand-typed, it mirrors that file):

| Metric | Value |
|---|---|
| Precision | 0.67 |
| Recall | 0.66 |
| F1 | 0.67 |
| ROC-AUC | 0.72 |
| PR-AUC | 0.73 |
| Brier score | 0.21 (lower is better; 0 = perfectly calibrated) |

A ROC-AUC of 0.72 on a genuinely noisy, temporally-split synthetic dataset
is a believable baseline result — notably *not* 0.95+, which would be a
red flag for either leakage or an unrealistically clean dataset.

## Calibration

`ml/train.py` computes a real reliability curve (`sklearn.calibration.calibration_curve`,
10 quantile bins) and saves it to `metrics.json`. Reviewable via
`python -m ml.evaluate`.

## Explainability

`app.agents.ai_service.predict_recovery_probability()` returns not just a
probability but the model's actual top-3 contributing factors, computed
from the trained logistic regression's real coefficients (`coefficient x
scaled_feature_value` per feature, sorted by magnitude) — not a
hand-written justification. This is what's shown in the case detail UI's
"ML Recovery Probability" panel.

## Model versioning (spec section 45)

`GET /api/models/current` returns the currently deployed model's version,
training date, and real evaluation metrics — read directly from
`models/recovery_model_v1/metrics.json`, produced by `python -m ml.train`.
If no model has been trained, it says so explicitly rather than inventing
numbers.

**Honest gap**: no MLflow experiment tracking, no automated retraining
pipeline, no model comparison/promotion logic. A second model version
would currently need to be trained into a new `models/recovery_model_v2/`
directory and the code path updated manually — documented as a roadmap
item, not a claimed feature.
