"""
Retrains the credit risk model WITH proper probability calibration.
Run this after data/features_engineered.csv already exists.

Fixes a real issue: LogisticRegression(class_weight="balanced") gives
good RANKING (ROC-AUC) but poorly calibrated probabilities — a raw
score of "70%" doesn't mean a real-world 70% default rate.
"""
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.metrics import roc_auc_score, brier_score_loss
import joblib

df = pd.read_csv("data/features_engineered.csv")
y = df["TARGET"]
X = df.drop(columns=["TARGET"])
cat_cols = X.select_dtypes(include=["object"]).columns.tolist()
X = pd.get_dummies(X, columns=cat_cols, drop_first=True)

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_test_scaled = scaler.transform(X_test)

# Base model: kept separately (uncalibrated) purely so reason codes
# still work — calibration changes the probability, not which
# features matter for explaining a specific decision.
base_model = LogisticRegression(max_iter=1000, class_weight="balanced")
base_model.fit(X_train_scaled, y_train)

raw_probs_test = base_model.predict_proba(X_test_scaled)[:, 1]
print("BEFORE calibration:")
print(f"  ROC-AUC: {roc_auc_score(y_test, raw_probs_test):.4f}")
print(f"  Mean predicted probability: {raw_probs_test.mean():.4f}")
print(f"  Actual default rate: {y_test.mean():.4f}")
print(f"  Brier score: {brier_score_loss(y_test, raw_probs_test):.4f}")

# CalibratedClassifierCV with cv=5 handles the calibration split
# internally via cross-validation on the training data — no separate
# manual calibration split needed, and it's the current recommended
# scikit-learn approach (the older cv="prefit" pattern is deprecated).
calibrated_model = CalibratedClassifierCV(
    LogisticRegression(max_iter=1000, class_weight="balanced"),
    method="sigmoid",
    cv=5,
)
calibrated_model.fit(X_train_scaled, y_train)

calib_probs_test = calibrated_model.predict_proba(X_test_scaled)[:, 1]
print("\nAFTER calibration (Platt/sigmoid scaling, 5-fold internal CV):")
print(f"  ROC-AUC: {roc_auc_score(y_test, calib_probs_test):.4f}  (ranking should barely change)")
print(f"  Mean predicted probability: {calib_probs_test.mean():.4f}")
print(f"  Actual default rate: {y_test.mean():.4f}")
print(f"  Brier score: {brier_score_loss(y_test, calib_probs_test):.4f}  (should be lower/better)")

print("\nCalibration curve (10 buckets) — predicted vs actual:")
frac_pos, mean_pred = calibration_curve(y_test, calib_probs_test, n_bins=10)
for p, a in zip(mean_pred, frac_pos):
    print(f"  Predicted ~{p:.1%}  ->  Actual {a:.1%}")

p70 = np.percentile(calib_probs_test, 70)
p90 = np.percentile(calib_probs_test, 90)
print("\nSuggested interim thresholds on CALIBRATED scores:")
print(f"  REFER_THRESHOLD   ~ {p70:.4f}  (was 0.53 on raw scores — DO NOT reuse the old number)")
print(f"  DECLINE_THRESHOLD ~ {p90:.4f}  (was 0.71 on raw scores)")
print("  These are percentile-based, not expected-loss-optimized. For a real")
print("  threshold, you need: interest margin, recovery rate on defaults,")
print("  cost of capital, and max acceptable portfolio loss.")

joblib.dump(base_model, "src/base_model.joblib")
joblib.dump(calibrated_model, "src/model.joblib")
joblib.dump(scaler, "src/scaler.joblib")
joblib.dump(X_train.columns.tolist(), "src/feature_columns.joblib")
print("\nSaved: src/model.joblib (calibrated), src/base_model.joblib (for reason codes), src/scaler.joblib, src/feature_columns.joblib")
