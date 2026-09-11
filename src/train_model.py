"""
train the logistic regression model.
Run: python src/train_model.py
"""
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, classification_report
import joblib

df = pd.read_csv("data/features_engineered.csv")

y = df["TARGET"]
X = df.drop(columns=["TARGET"])

# --- One-hot encode categorical columns ---
cat_cols = X.select_dtypes(include=["object"]).columns.tolist()
X = pd.get_dummies(X, columns=cat_cols, drop_first=True)

# --- Train/test split, stratified so both sets keep the same 8% default rate ---
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

# --- Scale numeric features (logistic regression is sensitive to feature scale) ---
scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_test_scaled = scaler.transform(X_test)

# --- Train, with class_weight="balanced" to counter the 92/8 imbalance ---
model = LogisticRegression(max_iter=1000, class_weight="balanced")
model.fit(X_train_scaled, y_train)

# --- Evaluate with ROC-AUC, not accuracy ---
y_pred_proba = model.predict_proba(X_test_scaled)[:, 1]
y_pred = model.predict(X_test_scaled)

print("ROC-AUC:", roc_auc_score(y_test, y_pred_proba))
print("\nClassification report:")
print(classification_report(y_test, y_pred))

# --- Save everything we'll need for the API later ---
joblib.dump(model, "src/model.joblib")
joblib.dump(scaler, "src/scaler.joblib")
joblib.dump(X_train.columns.tolist(), "src/feature_columns.joblib")
print("\nModel, scaler, and column list saved.")