"""
Feature engineering for the recovery-probability model.

Every feature listed here is something we would genuinely know BEFORE
attempting a recovery action — none of them peek at the outcome. This is
the leakage check spec section 11 asks for, made explicit:

| Feature | Known before recovery attempt? |
|---|---|
| customer_success_rate | Yes — computed from PAST payments only |
| customer_payment_count | Yes |
| amount | Yes — the failed payment's own amount |
| root_cause | Yes — diagnosed before any action is chosen |
| strategy | Yes — chosen before execution |
| attempt_count | Yes — how many prior attempts on *this* case |
| days_since_last_payment | Yes |

Explicitly NOT a feature: `true_probability` (present in the synthetic
dataset for debugging/audit only) and anything derived from the outcome
itself.
"""
import pandas as pd

CATEGORICAL_FEATURES = ["root_cause", "strategy"]
NUMERIC_FEATURES = ["customer_success_rate", "customer_payment_count", "amount",
                     "attempt_count", "days_since_last_payment"]
TARGET = "recovered"


def load_dataset(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["created_at"])
    return df


def temporal_split(df: pd.DataFrame, train_frac: float = 0.7, val_frac: float = 0.15):
    """Splits by time, not randomly — train on older transactions, validate
    and test on progressively more recent ones (spec section 13's explicit
    requirement to avoid a model that's implicitly cheating by seeing
    'future' transactions during training)."""
    df = df.sort_values("created_at").reset_index(drop=True)
    n = len(df)
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))
    return df.iloc[:train_end], df.iloc[train_end:val_end], df.iloc[val_end:]


def build_feature_matrix(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    X = pd.get_dummies(df[CATEGORICAL_FEATURES + NUMERIC_FEATURES], columns=CATEGORICAL_FEATURES)
    y = df[TARGET]
    return X, y


def align_columns(X: pd.DataFrame, reference_columns: list[str]) -> pd.DataFrame:
    """Ensures val/test feature matrices have exactly the same one-hot
    columns as the training matrix (a category that doesn't appear in a
    given split shouldn't silently break inference)."""
    for col in reference_columns:
        if col not in X.columns:
            X[col] = 0
    return X[reference_columns]
