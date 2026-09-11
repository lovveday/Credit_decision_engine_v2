"""
Step 1: look at the data before touching a model.
Run: python3 src/eda.py
"""
import pandas as pd

df = pd.read_csv("data/application_train.csv")

print("=" * 50)
print("SHAPE:", df.shape)

print("=" * 50)
print("TARGET balance (0 = repaid, 1 = defaulted):")
print(df["TARGET"].value_counts(normalize=True))

print("=" * 50)
print("Missing values (top 10 columns):")
print(df.isnull().sum().sort_values(ascending=False).head(10))

print("=" * 50)
print("Numeric summary:")
print(df.describe().T[["mean", "std", "min", "max"]])