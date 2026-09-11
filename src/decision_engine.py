"""
Step 4: the actual decision engine.
Combines the ML risk score with a rules layer to produce
a final decision + human-readable reasons.
"""
import pandas as pd
import numpy as np
import joblib

model = joblib.load("src/model.joblib")
scaler = joblib.load("src/scaler.joblib")
feature_columns = joblib.load("src/feature_columns.joblib")

REFER_THRESHOLD = 0.53    # ~70th percentile
DECLINE_THRESHOLD = 0.71  # ~90th percentile


def score_applicant(applicant: dict):
    row = pd.DataFrame([applicant])
    cat_cols = row.select_dtypes(include=["object"]).columns.tolist()
    row = pd.get_dummies(row, columns=cat_cols, drop_first=True)
    row = row.reindex(columns=feature_columns, fill_value=0)

    row_scaled = scaler.transform(row)
    probability = model.predict_proba(row_scaled)[0, 1]

    contributions = model.coef_[0] * row_scaled[0]
    top_idx = np.argsort(contributions)[::-1][:3]
    reasons = [feature_columns[i] for i in top_idx if contributions[i] > 0]

    if probability >= DECLINE_THRESHOLD:
        ml_band = "High"
    elif probability >= REFER_THRESHOLD:
        ml_band = "Medium"
    else:
        ml_band = "Low"

    decision = None
    rule_triggered = None

    if applicant.get("INCOME_OUTLIER_FLAG", 0) == 1:
        decision = "Refer"
        rule_triggered = "Income value could not be verified (data quality flag)"
    elif applicant.get("AGE_YEARS", 30) < 21:
        decision = "Decline"
        rule_triggered = "Applicant below minimum lending age (21)"

    if decision is None:
        decision = {"Low": "Approve", "Medium": "Refer", "High": "Decline"}[ml_band]

    return {
        "probability": round(float(probability), 4),
        "risk_band": ml_band,
        "decision": decision,
        "rule_triggered": rule_triggered,
        "top_risk_reasons": reasons,
    }


def apply_bank_statement_overlay(result: dict, statement_summary: dict) -> dict:
    """
    Layers bank-statement signals on top of the base ML+rules decision.
    Statement data can escalate a decision but never silently
    downgrades a Decline back to Approve.
    """
    if not statement_summary.get("parsed_successfully"):
        result["decision"] = "Refer" if result["decision"] == "Approve" else result["decision"]
        result["rule_triggered"] = result["rule_triggered"] or "Bank statement could not be verified"
        return result

    bounced = statement_summary.get("bounced_count", 0)

    if bounced >= 3 and result["decision"] != "Decline":
        result["decision"] = "Decline"
        result["rule_triggered"] = f"{bounced} bounced/reversed transactions found in bank statement"
    elif bounced >= 1 and result["decision"] == "Approve":
        result["decision"] = "Refer"
        result["rule_triggered"] = f"{bounced} bounced transaction(s) found — needs manual review"

    result["bank_statement_summary"] = statement_summary
    return result


if __name__ == "__main__":
    df = pd.read_csv("data/features_engineered.csv")
    sample = df.drop(columns=["TARGET"]).iloc[0].to_dict()
    result = score_applicant(sample)
    print(result)


def apply_bank_statement_overlay(result: dict, statement_summary: dict) -> dict:
    """
    Layers bank-statement signals on top of the base ML+rules decision.
    Statement data can escalate a decision but never silently
    downgrades a Decline back to Approve.
    """
    if not statement_summary.get("parsed_successfully"):
        result["decision"] = "Refer" if result["decision"] == "Approve" else result["decision"]
        result["rule_triggered"] = result["rule_triggered"] or "Bank statement could not be verified"
        return result

    bounced = statement_summary.get("bounced_count", 0)

    if bounced >= 3 and result["decision"] != "Decline":
        result["decision"] = "Decline"
        result["rule_triggered"] = f"{bounced} bounced/reversed transactions found in bank statement"
    elif bounced >= 1 and result["decision"] == "Approve":
        result["decision"] = "Refer"
        result["rule_triggered"] = f"{bounced} bounced transaction(s) found — needs manual review"

    # --- Behavioral profile signals (Gemini-derived) ---
    # These are heuristic, LLM-inferred judgments from unstructured transaction
    # text — never confidently verified facts. They can only push a decision
    # to "Refer" for human review, never to "Decline" on their own, since an
    # unexplainable categorical label (e.g. "gambling: High") is not a
    # defensible basis for an automated denial.
    profile = statement_summary.get("behavioral_profile")
    if profile:
        advisory_flags = []
        if profile.get("min_balance", 0) < 5000 or profile.get("days_thin_buffer", 0) > 5:
            advisory_flags.append("Thin cash buffer / frequently low balance")
        if profile.get("gambling_involvement_level") == "High":
            advisory_flags.append("High gambling-platform activity detected")
        if profile.get("loan_stacking_detected"):
            advisory_flags.append("Possible loan stacking across digital lenders")
        if profile.get("sweeper_behavior_detected") and profile.get("sweeper_destination_type") == "Gambling/Betting":
            advisory_flags.append("Sweeper behavior toward gambling platforms")

        if advisory_flags and result["decision"] == "Approve":
            result["decision"] = "Refer"
            result["rule_triggered"] = "Behavioral review flag(s): " + "; ".join(advisory_flags)

        result["behavioral_advisory_flags"] = advisory_flags

    result["bank_statement_summary"] = statement_summary
    return result

def recommend_loan_amount(applicant: dict, result: dict) -> dict:
    """
    Estimates how much this applicant can affordably borrow, using a
    risk-band-adjusted debt-to-income (DTI) cap, scaled by the same
    credit-to-annuity ratio implied by their own requested loan
    (i.e. assumes a similar term/rate structure to what they asked for).
    Never recommends more than what was actually requested.
    """
    income = applicant.get("AMT_INCOME_TOTAL", 0)
    requested_credit = applicant.get("AMT_CREDIT", 0)
    requested_annuity = applicant.get("AMT_ANNUITY", 0)

    dti_caps = {"Low": 0.40, "Medium": 0.30, "High": 0.15}
    band = result.get("risk_band", "High")
    dti_cap = dti_caps.get(band, 0.15)

    max_monthly_repayment = income * dti_cap

    if requested_annuity > 0 and requested_credit > 0:
        implied_ratio = requested_credit / requested_annuity
        max_affordable_credit = max_monthly_repayment * implied_ratio
    else:
        max_affordable_credit = 0

        recommended_amount = min(max_affordable_credit, requested_credit) if requested_credit else max_affordable_credit

    implied_tenure_months = round(requested_credit / requested_annuity) if requested_annuity > 0 else 0
    tenure_caps = {"Low": 60, "Medium": 24, "High": 0}
    tenure_cap = tenure_caps.get(band, 0)
    recommended_tenure = min(implied_tenure_months, tenure_cap) if tenure_cap else 0

    if result.get("decision") == "Decline":
        recommended_amount = 0
        recommended_tenure = 0

    return {
        "recommended_max_credit": round(recommended_amount, 2),
        "recommended_tenure_months": recommended_tenure,
        "max_affordable_monthly_repayment": round(max_monthly_repayment, 2),
        "dti_cap_used": dti_cap,
        "note": (
            "Not eligible — application declined."
            if result.get("decision") == "Decline"
            else "Tenure and amount assume a similar loan structure to what was requested, "
                 "scaled by a risk-based debt-to-income cap."
        ),
    }

    
REASON_EXPLANATIONS = {
    "EXT_SOURCE_1": "External credit bureau score (Source 1) — a lower score reflects a weaker credit history per bureau data.",
    "EXT_SOURCE_2": "External credit bureau score (Source 2) — a lower score reflects a weaker credit history per bureau data.",
    "EXT_SOURCE_3": "External credit bureau score (Source 3) — a lower score reflects a weaker credit history per bureau data.",
    "AMT_ANNUITY": "The proposed repayment amount is high in absolute terms, increasing repayment burden.",
    "AMT_CREDIT": "The requested loan amount is large, increasing overall exposure.",
    "AMT_GOODS_PRICE": "The value of the asset/goods being financed relative to other applicants.",
    "AMT_INCOME_TOTAL": "Declared income level relative to other applicants in the model.",
    "CREDIT_INCOME_RATIO": "The requested loan is a large multiple of declared income.",
    "ANNUITY_INCOME_RATIO": "The repayment amount takes up a large share of monthly income, leaving less room for other expenses.",
    "CREDIT_GOODS_RATIO": "The requested credit is high relative to the value of the asset being financed.",
    "AGE_YEARS": "Applicant age — younger applicants show a different historical repayment pattern in this model.",
    "YEARS_EMPLOYED": "Length of time in current employment — shorter tenure is historically associated with higher risk.",
    "CNT_CHILDREN": "Number of dependents — more dependents can mean less disposable income.",
    "REGION_POPULATION_RELATIVE": "A measure of how densely populated the applicant's region is. This reflects a statistical pattern in historical data, not a judgment about the applicant personally.",
    "REGION_RATING_CLIENT": "An internal regional risk rating tied to where the applicant lives.",
    "REG_CITY_NOT_LIVE_CITY": "Registered address city differs from city of residence — a verification/identity risk signal.",
    "REG_REGION_NOT_LIVE_REGION": "Registered address region differs from region of residence — a verification/identity risk signal.",
    "INCOME_OUTLIER_FLAG": "The declared income figure is unusually high and could not be verified.",
    "IS_UNEMPLOYED_OR_RETIRED": "The applicant is currently not in active employment.",
    "NAME_FAMILY_STATUS": "Marital/family status.",
    "NAME_EDUCATION_TYPE": "Education level.",
    "NAME_INCOME_TYPE": "Employment/income category.",
    "OCCUPATION_TYPE": "Occupation category.",
    "FLAG_OWN_CAR": "Car ownership.",
    "FLAG_OWN_REALTY": "Real estate ownership.",
    "ORGANIZATION_TYPE": "Employer/organization sector.",
    "EXT_SOURCE_1_MISSING": "No external bureau score (Source 1) was available for this applicant.",
    "EXT_SOURCE_2_MISSING": "No external bureau score (Source 2) was available for this applicant.",
    "EXT_SOURCE_3_MISSING": "No external bureau score (Source 3) was available for this applicant.",
}

DECISION_VERB = {"Approve": "approved", "Decline": "declined", "Refer": "referred for manual review"}


def explain_feature(feature_name: str) -> str:
    if feature_name in REASON_EXPLANATIONS:
        return REASON_EXPLANATIONS[feature_name]
    for prefix, explanation in REASON_EXPLANATIONS.items():
        if feature_name.startswith(prefix + "_"):
            value = feature_name[len(prefix) + 1:]
            return f"{explanation} (value: {value})"
    return "This factor contributed to the risk assessment."


def build_decision_explanation(result: dict, loan_rec: dict = None) -> dict:
    reasons = result.get("top_risk_reasons", [])
    reasons_explained = [{"factor": r, "explanation": explain_feature(r)} for r in reasons]

    verb = DECISION_VERB.get(result.get("decision"), result.get("decision", "").lower())

    if result.get("rule_triggered"):
        summary = f"This application was {verb} primarily due to a policy rule: {result['rule_triggered']}."
    else:
        pct = round(result.get("probability", 0) * 100)
        summary = f"This application was {verb} based on a modeled default risk of {pct}% ({result.get('risk_band')} risk band)."
        if reasons_explained:
            summary += f" The strongest contributing factor: {reasons_explained[0]['explanation']}"

    if loan_rec and loan_rec.get("recommended_tenure_months", 0) > 0:
        summary += f" If proceeding, a tenure of up to {loan_rec['recommended_tenure_months']} months is suggested."

    return {"summary": summary, "key_factors": reasons_explained}