"""
Look at the actual probability distribution before picking risk-band cutoffs.
Run: python src/analyze_scores.py
"""
import pandas as pd
import numpy as np
import joblib
from sklearn.model_selection import train_test_split

df = pd.read_csv("data/features_engineered.csv")
y = df["TARGET"]
X = df.drop(columns=["TARGET"])
cat_cols = X.select_dtypes(include=["object"]).columns.tolist()
X = pd.get_dummies(X, columns=cat_cols, drop_first=True)

model = joblib.load("src/model.joblib")
scaler = joblib.load("src/scaler.joblib")
feature_columns = joblib.load("src/feature_columns.joblib")

# align columns exactly like training
X = X.reindex(columns=feature_columns, fill_value=0)

_, X_test, _, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
X_test_scaled = scaler.transform(X_test)
probs = model.predict_proba(X_test_scaled)[:, 1]

print("Probability distribution (percentiles):")
for p in [10, 25, 50, 70, 80, 90, 95, 99]:
    print(f"  {p}th percentile: {np.percentile(probs, p):.4f}")

print(f"\nMean predicted probability: {probs.mean():.4f}")
print(f"Actual default rate in test set: {y_test.mean():.4f}")