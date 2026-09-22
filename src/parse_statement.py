"""
Parses an uploaded bank statement (PDF/CSV/DOCX/image) into a full report:
transaction count, bounced-payment count, inflow/outflow, ending balance,
and a spend-by-category breakdown — for both the operator's standalone
"Check Information" report and the rules layer.

Deterministic parsers run first (fast, free, no API call); the LLM
extractor is only used as a fallback for formats that fail, or for
images, which have no deterministic path.
"""
import pdfplumber
import re
from datetime import datetime
from collections import defaultdict

BOUNCE_KEYWORDS = ["reversal", "insufficient", "nsf", "declined", "returned", "bounced"]
DATE_FORMATS = ["%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"]

CATEGORY_KEYWORDS = {
    "Betting/Gambling": ["bet9ja", "sportybet", "nairabet", "1xbet", "betking", "betting", "wager", "stake"],
    "Loan Repayment": ["carbon", "fairmoney", "renmoney", "branch", "aella", "palmcredit", "loan repay", "loan disbursement", "loan"],
    "Hotel/Hospitality": ["hotel", "resort", "suites", "lodge"],
    "POS": ["pos ", "point of sale", "pos/"],
    "Withdrawal": ["withdrawal", "atm", "cash out", "cash withdrawal"],
    "Airtime/Utility": ["airtime", "data bundle", "mtn", "glo", "airtel", "9mobile", "electricity", "dstv", "gotv", "recharge"],
    "Salary/Income": ["salary", "payroll"],
    "Transfer": ["transfer", "trf", "nip"],
}


def categorize(description: str) -> str:
    desc = description.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(k in desc for k in keywords):
            return category
    return "Other"


def decrypt_pdf_if_needed(input_path: str, password: str, output_path: str) -> str:
    """
    If the PDF is password-protected, decrypts it to output_path and
    returns that path. If it's not encrypted, returns input_path unchanged.
    Raises ValueError if the PDF is encrypted and the password is wrong/missing.
    """
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(input_path)
    if not reader.is_encrypted:
        return input_path

    if not password:
        raise ValueError("This PDF is password-protected — a password is required")

    result = reader.decrypt(password)
    if result == 0:
        raise ValueError("Incorrect password for this PDF")

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    with open(output_path, "wb") as f:
        writer.write(f)
    return output_path


def _parse_amount(value: str):
    if not value or value.strip().lower() in ("nan", "none", ""):
        return None
    cleaned = value.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(value: str):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def _build_report(transactions: list) -> dict:
    """
    transactions: list of dicts with keys date (datetime|None), description (str),
    credit (float|None), debit (float|None), balance (float|None)
    """
    total_inflow = sum(t["credit"] for t in transactions if t.get("credit"))
    total_outflow = sum(t["debit"] for t in transactions if t.get("debit"))

    category_totals = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for t in transactions:
        cat = categorize(t["description"])
        amt = t.get("credit") or t.get("debit") or 0
        category_totals[cat]["amount"] += amt
        category_totals[cat]["count"] += 1

    category_breakdown = [
        {"category": cat, "amount": round(v["amount"], 2), "count": v["count"]}
        for cat, v in sorted(category_totals.items(), key=lambda x: -x[1]["amount"])
    ]

    dated_balances = [t for t in transactions if t.get("balance") is not None and t.get("date")]
    ending_balance = None
    if dated_balances:
        ending_balance = sorted(dated_balances, key=lambda t: t["date"])[-1]["balance"]

    bounced_count = sum(
        1 for t in transactions
        if any(re.search(rf"\b{k}\b", t["description"]) for k in BOUNCE_KEYWORDS)
    )

    monthly_credits = defaultdict(float)
    for t in transactions:
        if t.get("credit") and t.get("date"):
            monthly_credits[(t["date"].year, t["date"].month)] += t["credit"]
    avg_monthly_inflow = sum(monthly_credits.values()) / len(monthly_credits) if monthly_credits else 0.0

    return {
        "parsed_successfully": True,
        "source": "deterministic",
        "num_transactions": len(transactions),
        "bounced_count": bounced_count,
        "total_inflow": round(total_inflow, 2),
        "total_outflow": round(total_outflow, 2),
        "total_amount_through_account": round(total_inflow + total_outflow, 2),
        "ending_balance": ending_balance,
        "avg_monthly_inflow": round(avg_monthly_inflow, 2),
        "months_covered": len(monthly_credits),
        "category_breakdown": category_breakdown,
    }


def parse_bank_statement(pdf_path: str) -> dict:
    transactions = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table or len(table) < 2:
                    continue
                header = [str(h).strip().lower() if h else "" for h in table[0]]

                def find_col(*names):
                    for i, h in enumerate(header):
                        if any(n in h for n in names):
                            return i
                    return None

                date_idx = find_col("date")
                desc_idx = find_col("description", "narration", "details")
                debit_idx = find_col("debit")
                credit_idx = find_col("credit")
                balance_idx = find_col("balance")

                if date_idx is None or desc_idx is None:
                    continue

                for row in table[1:]:
                    if not row or len(row) <= max(date_idx, desc_idx):
                        continue
                    date = _parse_date(row[date_idx] or "")
                    desc = (row[desc_idx] or "").lower()
                    credit = _parse_amount(row[credit_idx]) if credit_idx is not None else None
                    debit = _parse_amount(row[debit_idx]) if debit_idx is not None else None
                    balance = _parse_amount(row[balance_idx]) if balance_idx is not None else None

                    transactions.append({
                        "date": date, "description": desc,
                        "credit": credit, "debit": debit, "balance": balance,
                    })

    if not transactions:
        return {"parsed_successfully": False, "num_transactions": 0, "bounced_count": 0, "avg_monthly_inflow": 0.0}

    return _build_report(transactions)


def _parse_csv(file_path: str) -> dict:
    import pandas as pd
    try:
        df = pd.read_csv(file_path)
    except Exception:
        return {"parsed_successfully": False, "num_transactions": 0, "bounced_count": 0, "avg_monthly_inflow": 0.0}

    cols = {c.lower(): c for c in df.columns}
    date_col = next((cols[c] for c in cols if "date" in c), None)
    desc_col = next((cols[c] for c in cols if any(k in c for k in ["desc", "narration", "details"])), None)
    credit_col = next((cols[c] for c in cols if "credit" in c), None)
    debit_col = next((cols[c] for c in cols if "debit" in c), None)
    balance_col = next((cols[c] for c in cols if "balance" in c), None)
    amount_col = cols.get("amount")

    if date_col is None or desc_col is None:
        return {"parsed_successfully": False, "num_transactions": 0, "bounced_count": 0, "avg_monthly_inflow": 0.0}

    transactions = []
    for _, row in df.iterrows():
        date = _parse_date(str(row[date_col]))
        desc = str(row[desc_col]).lower()
        credit = _parse_amount(str(row[credit_col])) if credit_col else None
        debit = _parse_amount(str(row[debit_col])) if debit_col else None
        if credit is None and debit is None and amount_col:
            val = _parse_amount(str(row[amount_col]))
            if val is not None:
                credit, debit = (val, None) if val > 0 else (None, -val)
        balance = _parse_amount(str(row[balance_col])) if balance_col else None
        transactions.append({"date": date, "description": desc, "credit": credit, "debit": debit, "balance": balance})

    if not transactions:
        return {"parsed_successfully": False, "num_transactions": 0, "bounced_count": 0, "avg_monthly_inflow": 0.0}

    return _build_report(transactions)


def _parse_docx(file_path: str) -> dict:
    from docx import Document
    doc = Document(file_path)
    text_parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            text_parts.append(" | ".join(cell.text for cell in row.cells))
    raw_text = "\n".join(text_parts)
    return {"parsed_successfully": False, "num_transactions": 0, "bounced_count": 0,
             "avg_monthly_inflow": 0.0, "raw_text": raw_text}


def parse_financial_document(file_path: str, filename: str, password: str = None) -> dict:
    """
    Unified entry point: routes to a deterministic parser first
    (fast, free, no API call), and only falls back to the LLM
    extractor when that fails or the file is an image.
    """
    ext = filename.lower().rsplit(".", 1)[-1]

    if ext == "pdf":
        try:
            decrypted_path = decrypt_pdf_if_needed(file_path, password, file_path + ".decrypted.pdf")
        except ValueError as e:
            return {"parsed_successfully": False, "num_transactions": 0, "bounced_count": 0,
                     "avg_monthly_inflow": 0.0, "error": str(e)}
        result = parse_bank_statement(decrypted_path)
    elif ext == "csv":
        result = _parse_csv(file_path)
    elif ext == "docx":
        result = _parse_docx(file_path)
    else:
        result = None

    needs_llm_fallback = (
        result is None
        or not result.get("parsed_successfully")
        or result.get("num_transactions", 0) == 0
        or ext in ("png", "jpg", "jpeg")
    )

    if needs_llm_fallback:
        from src.llm_extractor import extract_with_llm
        if ext == "docx" and result and result.get("raw_text"):
            return extract_with_llm(file_path, ext, raw_text=result["raw_text"])
        return extract_with_llm(file_path, ext)

    return result


def merge_reports(r1: dict, r2: dict) -> dict:
    """
    Combines two parsed bank statement reports (e.g. two accounts for the
    same applicant) into one. Numeric totals are summed; balances are
    summed to represent combined liquid position across both accounts;
    behavioral flags are combined conservatively (OR for booleans, the
    riskier value for severity levels).
    """
    if not r1.get("parsed_successfully"):
        return r2
    if not r2.get("parsed_successfully"):
        return r1

    merged = {
        "parsed_successfully": True,
        "source": f"merged({r1.get('source')}+{r2.get('source')})",
        "num_transactions": r1.get("num_transactions", 0) + r2.get("num_transactions", 0),
        "bounced_count": r1.get("bounced_count", 0) + r2.get("bounced_count", 0),
        "total_inflow": round(r1.get("total_inflow", 0) + r2.get("total_inflow", 0), 2),
        "total_outflow": round(r1.get("total_outflow", 0) + r2.get("total_outflow", 0), 2),
        "avg_monthly_inflow": round(r1.get("avg_monthly_inflow", 0) + r2.get("avg_monthly_inflow", 0), 2),
        "months_covered": max(r1.get("months_covered", 0), r2.get("months_covered", 0)),
    }
    merged["total_amount_through_account"] = round(merged["total_inflow"] + merged["total_outflow"], 2)

    eb1, eb2 = r1.get("ending_balance"), r2.get("ending_balance")
    merged["ending_balance"] = (
        round((eb1 or 0) + (eb2 or 0), 2) if (eb1 is not None or eb2 is not None) else None
    )

    cat_totals = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for rep in (r1, r2):
        for c in rep.get("category_breakdown", []):
            cat_totals[c["category"]]["amount"] += c["amount"]
            cat_totals[c["category"]]["count"] += c["count"]
    merged["category_breakdown"] = [
        {"category": k, "amount": round(v["amount"], 2), "count": v["count"]}
        for k, v in sorted(cat_totals.items(), key=lambda x: -x[1]["amount"])
    ]

    bp1, bp2 = r1.get("behavioral_profile"), r2.get("behavioral_profile")
    if bp1 or bp2:
        bp1, bp2 = bp1 or {}, bp2 or {}
        severity = {"None": 0, "Low": 1, "High": 2}
        gambling = max(
            [bp1.get("gambling_involvement_level", "None"), bp2.get("gambling_involvement_level", "None")],
            key=lambda x: severity.get(x, 0),
        )
        merged["behavioral_profile"] = {
            "estimated_stable_monthly_salary": max(bp1.get("estimated_stable_monthly_salary", 0), bp2.get("estimated_stable_monthly_salary", 0)),
            "has_active_side_hustle": bp1.get("has_active_side_hustle", False) or bp2.get("has_active_side_hustle", False),
            "sweeper_behavior_detected": bp1.get("sweeper_behavior_detected", False) or bp2.get("sweeper_behavior_detected", False),
            "sweeper_destination_type": bp1.get("sweeper_destination_type") or bp2.get("sweeper_destination_type") or "None",
            "min_balance": min(bp1.get("min_balance", 0) or 0, bp2.get("min_balance", 0) or 0),
            "days_thin_buffer": max(bp1.get("days_thin_buffer", 0), bp2.get("days_thin_buffer", 0)),
            "gambling_involvement_level": gambling,
            "loan_stacking_detected": bp1.get("loan_stacking_detected", False) or bp2.get("loan_stacking_detected", False),
            "sustainability_verdict": " | ".join(filter(None, [bp1.get("sustainability_verdict", ""), bp2.get("sustainability_verdict", "")])),
        }

    return merged
