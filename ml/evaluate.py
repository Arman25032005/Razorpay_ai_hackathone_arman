"""
Reproducible evaluation pipeline (spec section 33).

Loads the already-trained model and its saved metrics — does NOT retrain,
does NOT fabricate numbers. If no trained model exists yet, it says so and
tells you to run `python -m ml.train` first, rather than inventing output.

Run: `python -m ml.evaluate`
"""
import json
import os

MODEL_DIR = "models/recovery_model_v1"


def main():
    metrics_path = os.path.join(MODEL_DIR, "metrics.json")
    if not os.path.exists(metrics_path):
        print(f"No trained model found at {MODEL_DIR}. Run `python -m ml.train` first.")
        return

    with open(metrics_path) as f:
        metrics = json.load(f)

    tm = metrics["test_metrics"]
    bm = metrics["business_metrics_on_test_set"]

    print(f"Model:              {metrics['model']}")
    print(f"Trained at:         {metrics['trained_at']}")
    print(f"Dataset:            {metrics['dataset']}")
    print(f"Split:              {metrics['split_method']}")
    print(f"Train / Val / Test: {metrics['n_train']} / {metrics['n_val']} / {metrics['n_test']} rows")
    print()
    print("--- Test-set metrics (held-out, most recent time slice) ---")
    print(f"Precision:   {tm['precision']}")
    print(f"Recall:      {tm['recall']}")
    print(f"F1:          {tm['f1']}")
    print(f"ROC-AUC:     {tm['roc_auc']}")
    print(f"PR-AUC:      {tm['pr_auc']}")
    print(f"Brier score: {tm['brier_score']}  (lower is better, 0 = perfectly calibrated)")
    cm = tm["confusion_matrix"]
    print(f"Confusion matrix:  TP={cm['tp']}  FP={cm['fp']}  TN={cm['tn']}  FN={cm['fn']}")
    print()
    print("--- Business metrics on test set (from actual outcomes, not projected) ---")
    print(f"Total amount at risk in test set:  {bm['total_amount_at_risk']:,.2f}")
    print(f"Actually recovered amount:          {bm['actual_recovered_amount']:,.2f}")
    print(f"Model-flagged-recoverable amount:   {bm['amount_model_would_flag_as_recoverable']:,.2f}")
    print(f"False-positive amount (cost of acting on wrong predictions): {bm['false_positive_amount']:,.2f}")
    print()
    print("--- Calibration curve (predicted probability bucket -> actual recovery rate) ---")
    for point in metrics["calibration_curve"]:
        print(f"  predicted ~{point['mean_predicted']:.2f}  ->  actual {point['fraction_actually_recovered']:.2f}")


if __name__ == "__main__":
    main()
