"""
Step 4: the actual decision engine.

Architecture: every signal available — the ML model's own probability,
loan-to-income affordability, location tier, bank-statement behavior, and
credit-report history — contributes a calibrated ADJUSTMENT to one running
"composite probability of default", rather than firing as a separate
override that either replaces the model's answer entirely or does nothing.
That composite number is what drives the decision, band, offer amount,
and tenure together.

Two things remain absolute hard stops instead of blended adjustments,
because they are categorically different from "how risky is this" —
they're "are we even allowed/able to assess this at all" questions:
  - Applicant below minimum lending age (a legal/regulatory gate)
  - Unverifiable income data (we cannot honestly assess risk without it)
Everything else — including written-off loans and severe delinquency —
is a (heavily weighted) blended adjustment, not a rigid override, so a
single old, small negative can't automatically sink an otherwise
excellent applicant the way a hard rule would.
"""
import pandas as pd
import numpy as np
import joblib

model = joblib.load("src/model.joblib")           # calibrated — used for probability
base_model = joblib.load("src/base_model.joblib")  # uncalibrated — used for reason codes only
scaler = joblib.load("src/scaler.joblib")
feature_columns = joblib.load("src/feature_columns.joblib")

# Interim percentile-based thresholds from the calibration evaluation.
# Replace with expected-loss thresholds after lending outcome data exists.
REFER_THRESHOLD = 0.0907
DECLINE_THRESHOLD = 0.1723


def _decision_from_composite(cp: float):
    if cp >= DECLINE_THRESHOLD:
        return "Decline", "High"
    if cp >= REFER_THRESHOLD:
        return "Refer", "Medium"
    return "Approve", "Low"


def _affordability_adjustment(applicant: dict, employment_status: str = None) -> tuple:
    """Apply a stronger affordability adjustment to self-employed income."""
    income = applicant.get("AMT_INCOME_TOTAL", 0)
    credit = applicant.get("AMT_CREDIT", 0)
    if income <= 0 or credit <= 0:
        return 0.0, None
    ratio = credit / (income * 12)
    is_business = employment_status == "Self-employed"
    start = 1.5 if is_business else 2.0
    slope = 0.08 if is_business else 0.06
    adj = min(0.85, max(0.0, (ratio - start) * slope))
    reason = f"Requested credit is {ratio:.1f}x annual income" if adj > 0 else None
    return adj, reason


def _location_adjustment(applicant: dict) -> tuple:
    tier = applicant.get("REGION_RATING_CLIENT", 1)
    weights = {1: 0.0, 2: 0.02, 3: 0.08, 4: 0.15}
    adj = weights.get(tier, 0.0)
    reason = f"Applicant's state is in economic-activity Tier {tier}" if adj > 0 else None
    return adj, reason


def score_applicant(applicant: dict, profile: dict = None):
    profile = profile or {}
    employment_status = profile.get("EMPLOYMENT_STATUS")

    row = pd.DataFrame([applicant])
    cat_cols = row.select_dtypes(include=["object"]).columns.tolist()
    row = pd.get_dummies(row, columns=cat_cols, drop_first=True)
    row = row.reindex(columns=feature_columns, fill_value=0)

    row_scaled = scaler.transform(row)
    probability = float(model.predict_proba(row_scaled)[0, 1])

    contributions = base_model.coef_[0] * row_scaled[0]
    top_idx = np.argsort(contributions)[::-1][:3]
    reasons = [feature_columns[i] for i in top_idx if contributions[i] > 0]

    adjustments = []
    composite = probability

    afford_adj, afford_reason = _affordability_adjustment(applicant, employment_status)
    if afford_adj > 0:
        adjustments.append({"source": "affordability", "adjustment": round(afford_adj, 3), "reason": afford_reason})
    composite += afford_adj

    loc_adj, loc_reason = _location_adjustment(applicant)
    if loc_adj > 0:
        adjustments.append({"source": "location", "adjustment": round(loc_adj, 3), "reason": loc_reason})
    composite += loc_adj

    composite = min(1.0, max(0.0, composite))

    hard_stop = False
    decision, rule_triggered = None, None

    if applicant.get("INCOME_OUTLIER_FLAG", 0) == 1:
        decision, rule_triggered, hard_stop = "Refer", "Income value could not be verified (data quality flag) — cannot honestly assess risk without it", True
    elif applicant.get("AGE_YEARS", 30) < 21:
        decision, rule_triggered, hard_stop = "Decline", "Applicant below minimum lending age (21)", True

    if decision is None:
        decision, band = _decision_from_composite(composite)
        if rule_triggered is None and adjustments:
            worst = max(adjustments, key=lambda a: a["adjustment"])
            if decision != "Approve":
                rule_triggered = worst["reason"]
    else:
        _, band = _decision_from_composite(composite)

    return {
        "probability": round(probability, 4),
        "composite_probability": round(composite, 4),
        "default_likelihood_percent": round(composite * 100, 1),
        "risk_band": band,
        "decision": decision,
        "rule_triggered": rule_triggered,
        "top_risk_reasons": reasons,
        "adjustments": adjustments,
        "hard_stop": hard_stop,
        "employment_status": employment_status,
    }


if __name__ == "__main__":
    df = pd.read_csv("data/features_engineered.csv")
    sample = df.drop(columns=["TARGET"]).iloc[0].to_dict()
    result = score_applicant(sample)
    print(result)


def apply_bank_statement_overlay(result: dict, statement_summary: dict, applicant: dict = None) -> dict:
    result.setdefault("adjustments", [])
    result.setdefault("composite_probability", result.get("probability", 0))

    adj_total = 0.0
    advisory_flags = []

    if not statement_summary.get("parsed_successfully"):
        adj_total += 0.10
        advisory_flags.append("Bank statement could not be verified")
    else:
        bounced = statement_summary.get("bounced_count", 0)
        if bounced > 0:
            adj_total += min(0.30, bounced * 0.06)
            advisory_flags.append(f"{bounced} bounced/reversed transaction(s) found in bank statement")

        total_outflow = statement_summary.get("total_outflow", 0) or 0
        gambling_pct = 0.0
        if total_outflow > 0:
            for cat in statement_summary.get("category_breakdown", []):
                if cat.get("category") == "Betting/Gambling":
                    gambling_pct = cat.get("amount", 0) / total_outflow
                    if gambling_pct > 0.15:
                        adj_total += min(0.30, 0.10 + (gambling_pct - 0.15) * 1.0)
                        advisory_flags.append(f"Gambling spend is {gambling_pct*100:.0f}% of total outflow (₦{cat['amount']:,.0f})")

        profile = statement_summary.get("behavioral_profile")
        if profile:
            if profile.get("min_balance", 0) < 5000 or profile.get("days_thin_buffer", 0) > 5:
                adj_total += 0.08
                advisory_flags.append("Thin cash buffer / frequently low balance")
            if profile.get("gambling_involvement_level") == "High" and gambling_pct <= 0.15:
                adj_total += 0.10
                advisory_flags.append("High gambling-platform activity detected")
            if profile.get("loan_stacking_detected"):
                adj_total += 0.10
                advisory_flags.append("Possible loan stacking across digital lenders")
            if profile.get("sweeper_behavior_detected") and profile.get("sweeper_destination_type") == "Gambling/Betting":
                adj_total += 0.10
                advisory_flags.append("Sweeper behavior toward gambling platforms")

        if applicant and result.get("employment_status") == "Self-employed":
            months_covered = statement_summary.get("months_covered", 0) or 0
            declared_income = applicant.get("AMT_INCOME_TOTAL", 0)
            if months_covered and declared_income > 0:
                avg_outflow = (statement_summary.get("total_outflow", 0) or 0) / months_covered
                avg_inflow = statement_summary.get("avg_monthly_inflow", 0) or 0
                verified_cash_flow = max(0, avg_inflow - avg_outflow)
                if verified_cash_flow < declared_income * 0.5:
                    adj_total += 0.15
                    advisory_flags.append("Self-employed applicant's verified cash flow is significantly below declared income")
                elif verified_cash_flow >= declared_income * 0.8:
                    adj_total -= 0.05
                    advisory_flags.append("Self-employed applicant's declared income is corroborated by verified cash flow")

    if adj_total != 0:
        result["adjustments"].append({"source": "bank_statement", "adjustment": round(adj_total, 3), "reason": "; ".join(advisory_flags)})
    result["composite_probability"] = min(1.0, max(0.0, result["composite_probability"] + adj_total))

    if not result.get("hard_stop"):
        decision, band = _decision_from_composite(result["composite_probability"])
        result["decision"] = decision
        result["risk_band"] = band
        if decision != "Approve" and advisory_flags:
            result["rule_triggered"] = "; ".join(advisory_flags)

    result["default_likelihood_percent"] = round(result["composite_probability"] * 100, 1)
    result["behavioral_advisory_flags"] = advisory_flags
    result["bank_statement_summary"] = statement_summary
    return result


def apply_credit_report_overlay(result: dict, credit_report: dict, applicant: dict = None) -> dict:
    result.setdefault("adjustments", [])
    result.setdefault("composite_probability", result.get("probability", 0))

    if not credit_report.get("parsed_successfully"):
        result["adjustments"].append({"source": "credit_report", "adjustment": 0.10, "reason": "Credit report could not be read"})
        result["composite_probability"] = min(1.0, result["composite_probability"] + 0.10)
        if not result.get("hard_stop"):
            decision, band = _decision_from_composite(result["composite_probability"])
            result["decision"] = decision
            result["risk_band"] = band
            if decision != "Approve":
                result["rule_triggered"] = "Credit report could not be read"
        result["default_likelihood_percent"] = round(result["composite_probability"] * 100, 1)
        result["credit_report"] = credit_report
        return result

    written_off = credit_report.get("written_off_accounts", 0)
    outstanding = credit_report.get("total_outstanding_balance", 0)
    enquiries = credit_report.get("recent_enquiries_count", 0)

    def _is_delinquent(loan):
        status = (loan.get("status") or "").lower()
        return (
            loan.get("months_in_arrears", 0) > 0
            or loan.get("overdue_amount", 0) > 0
            or any(k in status for k in ("delinquent", "arrears", "default"))
        )

    delinquent_loans = [l for l in credit_report.get("loans", []) if _is_delinquent(l)]

    adj_total = 0.0
    reasons = []

    if written_off > 0:
        adj_total += min(0.70, written_off * 0.35)
        reasons.append(f"{written_off} previously written-off account(s) on credit report")

    if delinquent_loans:
        adj_total += min(0.36, len(delinquent_loans) * 0.12)
        worst = max(delinquent_loans, key=lambda l: (l.get("overdue_amount", 0), l.get("months_in_arrears", 0)))
        reasons.append(f"{len(delinquent_loans)} account(s) delinquent/in arrears (e.g. {worst.get('lender_name')}, ₦{worst.get('overdue_amount', 0):,.0f} overdue)")

    if enquiries > 3:
        adj_total += min(0.15, (enquiries - 3) * 0.03)
        reasons.append(f"{enquiries} recent credit enquiries — possible loan-stacking / credit-seeking behavior")

    if applicant and outstanding > 0:
        income = applicant.get("AMT_INCOME_TOTAL", 0)
        if income:
            ratio = outstanding / (income * 12)
            if ratio > 1:
                adj_total += min(0.15, (ratio - 1) * 0.05)
                reasons.append(f"Existing debt (₦{outstanding:,.0f}) is high relative to declared income")

    if adj_total > 0:
        result["adjustments"].append({"source": "credit_report", "adjustment": round(adj_total, 3), "reason": "; ".join(reasons)})
    result["composite_probability"] = min(1.0, max(0.0, result["composite_probability"] + adj_total))

    if not result.get("hard_stop"):
        decision, band = _decision_from_composite(result["composite_probability"])
        result["decision"] = decision
        result["risk_band"] = band
        if decision != "Approve" and reasons:
            result["rule_triggered"] = "; ".join(reasons)

    result["default_likelihood_percent"] = round(result["composite_probability"] * 100, 1)
    result["credit_report"] = credit_report
    return result


def _tenure_cap_from_composite(cp: float) -> float:
    if cp >= DECLINE_THRESHOLD:
        return 0
    if cp >= 0.65:
        return 0.25
    if cp >= REFER_THRESHOLD:
        return 0.5
    if cp >= 0.40:
        return 1
    if cp >= 0.25:
        return 2
    if cp >= 0.10:
        return 4
    return 6


def recommend_loan_amount(applicant: dict, result: dict, profile: dict = None) -> dict:
    profile = profile or {}
    income = applicant.get("AMT_INCOME_TOTAL", 0)
    requested_credit = applicant.get("AMT_CREDIT", 0)

    composite = result.get("composite_probability", result.get("probability", 0))
    band = result.get("risk_band", "High")

    dti_caps = {"Low": 0.40, "Medium": 0.30, "High": 0.15}
    dti_cap = dti_caps.get(band, 0.15)

    statement = result.get("bank_statement_summary") or {}
    behavioral = statement.get("behavioral_profile") or {}
    statement_verified = bool(statement.get("parsed_successfully"))
    months_covered = statement.get("months_covered", 0) or 0
    avg_monthly_inflow = statement.get("avg_monthly_inflow", 0) or 0
    avg_monthly_outflow = (
        (statement.get("total_outflow", 0) or 0) / months_covered
        if months_covered else 0
    )
    verified_net_cash_flow = max(0, avg_monthly_inflow - avg_monthly_outflow)

    employment_status = profile.get("EMPLOYMENT_STATUS", "")
    is_business = employment_status == "Self-employed"
    stable_salary = behavioral.get("estimated_stable_monthly_salary", 0) or 0
    declared_business_profit = max(
        0,
        (profile.get("AVG_MONTHLY_REVENUE", 0) or 0)
        - (profile.get("AVG_MONTHLY_EXPENSES", 0) or 0),
    )

    if is_business:
        income_basis = declared_business_profit or income
        if statement_verified and verified_net_cash_flow:
            income_basis = min(income_basis, verified_net_cash_flow)
        income_basis_label = "business profit and verified net cash flow"
    else:
        income_basis = income
        if stable_salary:
            income_basis = min(income_basis, stable_salary)
        income_basis_label = "declared income and verified salary"

    max_monthly_repayment = income_basis * dti_cap
    if statement_verified and months_covered and avg_monthly_inflow > 0:
        max_monthly_repayment = min(max_monthly_repayment, verified_net_cash_flow * 0.50)

    if statement_verified and months_covered and verified_net_cash_flow <= 0:
        if result.get("decision") == "Approve":
            result["decision"] = "Refer"
            result["rule_triggered"] = "Verified monthly cash flow leaves no repayment buffer"
        max_monthly_repayment = 0

    tenure_cap = _tenure_cap_from_composite(composite)
    max_affordable_credit = max_monthly_repayment * tenure_cap if tenure_cap else 0

    recommended_amount = min(max_affordable_credit, requested_credit) if requested_credit else max_affordable_credit

    credit_report = result.get("credit_report") or {}
    sanctioned_limit = credit_report.get("total_sanctioned_limit", 0) or 0
    if sanctioned_limit > 0:
        recommended_amount = min(recommended_amount, sanctioned_limit)

    if result.get("decision") == "Decline":
        recommended_amount = 0
        max_monthly_repayment = 0

    if tenure_cap >= 1:
        recommended_tenure = (
            min(max(1, int(np.ceil(recommended_amount / max_monthly_repayment))), tenure_cap)
            if recommended_amount > 0 and max_monthly_repayment > 0
            else 0
        )
    else:
        recommended_tenure = tenure_cap if recommended_amount > 0 else 0

    if recommended_amount > 0 and recommended_tenure > 0:
        if recommended_tenure >= 1:
            max_monthly_repayment = recommended_amount / recommended_tenure
        else:
            max_monthly_repayment = recommended_amount

    tenure_labels = {6: "6 months", 4: "4 months", 2: "2 months", 1: "1 month", 0.5: "2 weeks", 0.25: "1 week", 0: "N/A"}

    return {
        "recommended_max_credit": round(recommended_amount, 2),
        "recommended_tenure_months": recommended_tenure,
        "recommended_tenure_label": tenure_labels.get(recommended_tenure, f"{recommended_tenure} months"),
        "maximum_tenure_months": tenure_cap if recommended_amount > 0 else 0,
        "maximum_tenure_label": tenure_labels.get(tenure_cap if recommended_amount > 0 else 0, f"{tenure_cap} months"),
        "credit_report_sanctioned_limit": round(sanctioned_limit, 2),
        "max_affordable_monthly_repayment": round(max_monthly_repayment, 2),
        "dti_cap_used": dti_cap,
        "income_basis": round(income_basis, 2),
        "income_basis_label": income_basis_label,
        "verified_avg_monthly_inflow": round(avg_monthly_inflow, 2),
        "verified_avg_monthly_outflow": round(avg_monthly_outflow, 2),
        "verified_monthly_net_cash_flow": round(verified_net_cash_flow, 2),
        "cash_flow_buffer_used": 0.50 if statement_verified and months_covered else None,
        "note": (
            "Not eligible — application declined."
            if result.get("decision") == "Decline"
            else "No repayment capacity identified from the verified statement. Manual review is required."
            if result.get("decision") == "Refer" and max_monthly_repayment == 0
            else "Offer, amount and tenure are all driven by the same composite risk score — income, affordability, location, bank statement, and credit report combined."
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
    composite_pct = round(result.get("composite_probability", result.get("probability", 0)) * 100)
    base_pct = round(result.get("probability", 0) * 100)

    if result.get("hard_stop"):
        summary = f"This application was {verb} due to a policy gate: {result.get('rule_triggered')}."
    else:
        summary = f"This application was {verb} based on a composite default risk of {composite_pct}% ({result.get('risk_band')} risk band), starting from a {base_pct}% base model score."
        adjustments = result.get("adjustments", [])
        if adjustments:
            top = max(adjustments, key=lambda a: a["adjustment"])
            summary += f" Largest contributing adjustment: {top['reason']} (+{top['adjustment']*100:.0f} pts)."
        elif reasons_explained:
            summary += f" The strongest model factor: {reasons_explained[0]['explanation']}"

    if loan_rec and loan_rec.get("recommended_tenure_label") and loan_rec.get("recommended_max_credit", 0) > 0:
        summary += f" If proceeding, a tenure of {loan_rec['recommended_tenure_label']} is suggested."

    return {
        "summary": summary,
        "key_factors": reasons_explained,
        "adjustment_breakdown": result.get("adjustments", []),
    }
