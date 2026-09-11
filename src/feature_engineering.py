"""
turn raw columns into model-ready features.
Run: python src/feature_engineering.py
"""
import pandas as pd
import numpy as np

df = pd.read_csv("data/application_train.csv")

FEATURES = [
    "TARGET",
    "AMT_INCOME_TOTAL", "AMT_CREDIT", "AMT_ANNUITY", "AMT_GOODS_PRICE",
    "EXT_SOURCE_1", "EXT_SOURCE_2", "EXT_SOURCE_3",
    "DAYS_BIRTH", "DAYS_EMPLOYED",
    "NAME_EDUCATION_TYPE", "NAME_FAMILY_STATUS", "NAME_INCOME_TYPE", "OCCUPATION_TYPE",
    "FLAG_OWN_CAR", "FLAG_OWN_REALTY", "CNT_CHILDREN",
    "REGION_POPULATION_RELATIVE", "REGION_RATING_CLIENT",
    "REG_CITY_NOT_LIVE_CITY", "REG_REGION_NOT_LIVE_REGION",
    "ORGANIZATION_TYPE",
]

df = df[FEATURES].copy()

# --- DAYS_BIRTH: convert to age in years (it's negative in the raw data) ---
df["AGE_YEARS"] = (-df["DAYS_BIRTH"] / 365).astype(int)
df.drop(columns=["DAYS_BIRTH"], inplace=True)

# --- DAYS_EMPLOYED: fix the 365243 placeholder (means "not employed"/retired) ---
df["IS_UNEMPLOYED_OR_RETIRED"] = (df["DAYS_EMPLOYED"] == 365243).astype(int)
df["YEARS_EMPLOYED"] = np.where(
    df["DAYS_EMPLOYED"] == 365243,
    0,
    -df["DAYS_EMPLOYED"] / 365
)
df.drop(columns=["DAYS_EMPLOYED"], inplace=True)

# --- Financial ratios: these usually matter more than raw amounts ---
df["CREDIT_INCOME_RATIO"] = df["AMT_CREDIT"] / df["AMT_INCOME_TOTAL"]
df["ANNUITY_INCOME_RATIO"] = df["AMT_ANNUITY"] / df["AMT_INCOME_TOTAL"]
df["CREDIT_GOODS_RATIO"] = df["AMT_CREDIT"] / df["AMT_GOODS_PRICE"]

# --- Flag rows with the obvious income outlier we spotted in EDA ---
df["INCOME_OUTLIER_FLAG"] = (df["AMT_INCOME_TOTAL"] > 10_000_000).astype(int)

# --- EXT_SOURCE_1/2/3 have missing values — flag missingness, then fill with median ---
for col in ["EXT_SOURCE_1", "EXT_SOURCE_2", "EXT_SOURCE_3"]:
    df[f"{col}_MISSING"] = df[col].isnull().astype(int)
    df[col] = df[col].fillna(df[col].median())

# --- Everything else numeric: fill any remaining nulls with median ---
numeric_cols = df.select_dtypes(include=[np.number]).columns
df[numeric_cols] = df[numeric_cols].fillna(df[numeric_cols].median())

# --- Categorical columns: fill missing with "Unknown" ---
cat_cols = df.select_dtypes(include=["object"]).columns
df[cat_cols] = df[cat_cols].fillna("Unknown")

print("Final shape:", df.shape)
print(df.head())
print("\nAny nulls left?", df.isnull().sum().sum())

df.to_csv("data/features_engineered.csv", index=False)
print("Saved to data/features_engineered.csv")