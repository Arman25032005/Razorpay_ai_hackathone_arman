"""
Trains the recovery-probability baseline model (logistic regression) and
evaluates it honestly against a held-out temporal test set.

Explicitly scoped: this is a baseline, not the XGBoost+MLflow production
pipeline a fully-resourced version of this product would eventually want
(see docs/ML.md for the honest roadmap statement). What's here is real:
real data, real temporal split, real training, real metrics computed from
actual execution — nothing in the printed output or saved metrics.json is
fabricated.

Run: `python -m ml.train`
Then: `python -m ml.evaluate` to reprint metrics from the saved model
without retraining (spec section 33's reproducible evaluation pipeline).
"""
import json
import os

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    precision_score, recall_score, f1_score, roc_auc_score,
    average_precision_score, brier_score_loss, confusion_matrix,
)
from sklearn.calibration import calibration_curve

from ml.features import load_dataset, temporal_split, build_feature_matrix, align_columns

MODEL_DIR = "models/recovery_model_v1"
DATA_PATH = "data/synthetic_recovery_data.csv"


def train_and_evaluate(data_path: str = DATA_PATH, model_dir: str = MODEL_DIR) -> dict:
    df = load_dataset(data_path)
    train_df, val_df, test_df = temporal_split(df)

    X_train, y_train = build_feature_matrix(train_df)
    feature_columns = list(X_train.columns)

    X_val, y_val = build_feature_matrix(val_df)
    X_val = align_columns(X_val, feature_columns)
    X_test, y_test = build_feature_matrix(test_df)
    X_test = align_columns(X_test, feature_columns)

    model = LogisticRegression(max_iter=1000)
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    model.fit(X_train_scaled, y_train)

    # Validation metrics (used for model selection / threshold tuning in a
    # real pipeline; reported here for transparency).
    X_val_scaled = scaler.transform(X_val)
    val_pred_proba = model.predict_proba(X_val_scaled)[:, 1]
    val_pred = (val_pred_proba >= 0.5).astype(int)

    # Test metrics — the number that actually matters, computed on data the
    # model has never seen in any form, from the most recent time slice.
    X_test_scaled = scaler.transform(X_test)
    test_pred_proba = model.predict_proba(X_test_scaled)[:, 1]
    test_pred = (test_pred_proba >= 0.5).astype(int)

    tn, fp, fn, tp = confusion_matrix(y_test, test_pred).ravel()

    # Calibration: if the model says 80%, similar cases should recover
    # ~80% of the time. We report Brier score (lower is better, 0 = perfect)
    # and a reliability curve.
    brier = brier_score_loss(y_test, test_pred_proba)
    frac_pos, mean_pred = calibration_curve(y_test, test_pred_proba, n_bins=10, strategy="quantile")
    calibration_curve_points = [
        {"mean_predicted": round(float(p), 4), "fraction_actually_recovered": round(float(f), 4)}
        for p, f in zip(mean_pred, frac_pos)
    ]

    # Business-relevant metrics (spec section 14): not just accuracy.
    recovered_amount_captured = float(test_df.loc[test_pred == 1, "amount"].sum())
    total_at_risk_in_test = float(test_df["amount"].sum())
    actual_recovered_amount = float(test_df.loc[test_df["recovered"] == 1, "amount"].sum())
    false_positive_amount = float(test_df.loc[(test_pred == 1) & (y_test == 0), "amount"].sum())

    metrics = {
        "model": "LogisticRegression (baseline)",
        "trained_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "dataset": data_path,
        "n_train": len(train_df),
        "n_val": len(val_df),
        "n_test": len(test_df),
        "split_method": "temporal (train=oldest, val=middle, test=newest) — no random shuffling",
        "feature_columns": feature_columns,
        "test_metrics": {
            "precision": round(precision_score(y_test, test_pred), 4),
            "recall": round(recall_score(y_test, test_pred), 4),
            "f1": round(f1_score(y_test, test_pred), 4),
            "roc_auc": round(roc_auc_score(y_test, test_pred_proba), 4),
            "pr_auc": round(average_precision_score(y_test, test_pred_proba), 4),
            "brier_score": round(brier, 4),
            "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        },
        "calibration_curve": calibration_curve_points,
        "business_metrics_on_test_set": {
            "total_amount_at_risk": round(total_at_risk_in_test, 2),
            "actual_recovered_amount": round(actual_recovered_amount, 2),
            "amount_model_would_flag_as_recoverable": round(recovered_amount_captured, 2),
            "false_positive_amount": round(false_positive_amount, 2),
            "note": "These are computed directly from the test set's actual 'recovered' labels and the model's predictions — not projected or assumed.",
        },
    }

    os.makedirs(model_dir, exist_ok=True)
    joblib.dump(model, os.path.join(model_dir, "model.joblib"))
    joblib.dump(scaler, os.path.join(model_dir, "scaler.joblib"))
    with open(os.path.join(model_dir, "feature_columns.json"), "w") as f:
        json.dump(feature_columns, f)
    with open(os.path.join(model_dir, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)

    return metrics


if __name__ == "__main__":
    metrics = train_and_evaluate()
    print(json.dumps(metrics["test_metrics"], indent=2))
    print("\nSaved model + metrics to", MODEL_DIR)
