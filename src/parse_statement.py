"""
Parses an uploaded bank statement PDF into summary signals
for the rules layer: transaction count, bounced-payment count,
and average monthly inflow.

This is a best-effort parser for well-structured, text-based PDFs.
It will NOT reliably handle every bank's format or scanned/image PDFs.
A production system would use a bank statement/open banking API
(e.g. Mono, Okra) instead of parsing raw PDFs.
"""
import pdfplumber
import re
from datetime import datetime
from collections import defaultdict

BOUNCE_KEYWORDS = ["reversal", "insufficient", "nsf", "declined", "returned", "bounced"]

DATE_FORMATS = ["%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"]


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

                if date_idx is None or desc_idx is None:
                    continue

                for row in table[1:]:
                    if not row or len(row) <= max(date_idx, desc_idx):
                        continue
                    date = _parse_date(row[date_idx] or "")
                    desc = (row[desc_idx] or "").lower()
                    credit = _parse_amount(row[credit_idx]) if credit_idx is not None else None
                    debit = _parse_amount(row[debit_idx]) if debit_idx is not None else None

                    transactions.append({
                        "date": date,
                        "description": desc,
                        "credit": credit,
                        "debit": debit,
                    })

    if not transactions:
        return {
            "parsed_successfully": False,
            "num_transactions": 0,
            "bounced_count": 0,
            "avg_monthly_inflow": 0.0,
        }

    bounced_count = sum(
        1 for t in transactions
        if any(re.search(rf"\b{k}\b", t["description"]) for k in BOUNCE_KEYWORDS)
    )

    monthly_credits = defaultdict(float)
    for t in transactions:
        if t["credit"] and t["date"]:
            key = (t["date"].year, t["date"].month)
            monthly_credits[key] += t["credit"]

    avg_monthly_inflow = (
        sum(monthly_credits.values()) / len(monthly_credits) if monthly_credits else 0.0
    )

    return {
        "parsed_successfully": True,
        "num_transactions": len(transactions),
        "bounced_count": bounced_count,
        "avg_monthly_inflow": round(avg_monthly_inflow, 2),
        "months_covered": len(monthly_credits),
        "source": "deterministic",
    }


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
    amount_col = cols.get("amount")

    if date_col is None or desc_col is None:
        return {"parsed_successfully": False, "num_transactions": 0, "bounced_count": 0, "avg_monthly_inflow": 0.0}

    transactions = []
    for _, row in df.iterrows():
        date = _parse_date(str(row[date_col]))
        desc = str(row[desc_col]).lower()
        credit = None
        if credit_col:
            credit = _parse_amount(str(row[credit_col]))
        elif amount_col:
            val = _parse_amount(str(row[amount_col]))
            credit = val if val and val > 0 else None
        transactions.append({"date": date, "description": desc, "credit": credit})

    if not transactions:
        return {"parsed_successfully": False, "num_transactions": 0, "bounced_count": 0, "avg_monthly_inflow": 0.0}

    bounced_count = sum(
        1 for t in transactions
        if any(re.search(rf"\b{k}\b", t["description"]) for k in BOUNCE_KEYWORDS)
    )
    monthly_credits = defaultdict(float)
    for t in transactions:
        if t["credit"] and t["date"]:
            monthly_credits[(t["date"].year, t["date"].month)] += t["credit"]
    avg_monthly_inflow = sum(monthly_credits.values()) / len(monthly_credits) if monthly_credits else 0.0

    return {
        "parsed_successfully": True,
        "num_transactions": len(transactions),
        "bounced_count": bounced_count,
        "avg_monthly_inflow": round(avg_monthly_inflow, 2),
        "months_covered": len(monthly_credits),
        "source": "deterministic",
    }


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