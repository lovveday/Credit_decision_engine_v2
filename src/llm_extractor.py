"""
LLM-based fallback extraction for financial documents (PDF/image/text)
that the deterministic parser could not confidently read.

Uses Google Gemini (via Google AI Studio) with structured JSON output.
Now also categorizes each transaction and computes inflow/outflow/balance
so the full bank statement report can be shown to the operator.

Requires: pip install google-genai python-dotenv
Requires: GOOGLE_API_KEY in .env
"""
from dotenv import load_dotenv
load_dotenv()

import os
from datetime import datetime
from collections import defaultdict
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

CATEGORIES = ["Transfer", "Withdrawal", "POS", "Hotel/Hospitality", "Betting/Gambling",
              "Loan Repayment", "Airtime/Utility", "Salary/Income", "Other"]


class Transaction(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    description: str
    amount: float = Field(description="positive for credit/inflow, negative for debit/outflow")
    category: str = Field(description=f"one of: {', '.join(CATEGORIES)}")
    balance: Optional[float] = Field(description="running balance after this transaction, if shown on the statement, else null")
    bounced: bool = Field(description="true only if reversed/returned/declined/insufficient funds")


class FintechSavingsInflow(BaseModel):
    platform_name: str = Field(description="e.g., PiggyVest, Cowrywise, Wealth.ng, Kuda Safe")
    total_amount_moved: float
    frequency_per_month: int


class DigitalLoanTrack(BaseModel):
    lender_name: str = Field(description="e.g., Carbon, FairMoney, Renmoney, Branch, Aella, Palmcredit")
    transaction_type: str = Field(description="'Disbursement' or 'Repayment'")
    amount: float


class BehavioralProfile(BaseModel):
    estimated_stable_monthly_salary: float = Field(description="detected primary salary block, 0 if none found")
    has_active_side_hustle: bool = Field(description="true if regular, business-like inflows exist outside salary")

    sweeper_behavior_detected: bool = Field(description="true if incoming funds consistently leave within ~48 hours")
    sweeper_destination_type: str = Field(description="'Fintech Savings' | 'Gambling/Betting' | 'Business Inventory' | 'Unknown Cashout' | 'None'")

    avg_end_of_day_balance: float = Field(description="average balance across the statement period")
    min_balance: float = Field(description="lowest balance point observed in the statement")
    days_thin_buffer: int = Field(description="number of days balance appears to fall below a safe cash buffer (~5,000 NGN)")

    gambling_involvement_level: str = Field(description="'None' | 'Low' | 'High' — based on betting platform activity (Bet9ja, SportyBet, NairaBet, 1xBet, BetKing, etc.)")
    loan_stacking_detected: bool = Field(description="true if customer appears to borrow from one digital lender to repay another")

    detected_fintech_savings: List[FintechSavingsInflow]
    active_digital_loans: List[DigitalLoanTrack]

    sustainability_verdict: str = Field(description="concise plain-language summary of why this account looks stable or risky")


class ExtractionResult(BaseModel):
    transactions: List[Transaction]
    behavioral_profile: BehavioralProfile


EXTRACTION_PROMPT = f"""You are analyzing a Nigerian bank statement for credit underwriting.

Extract every transaction you can identify, classifying each into exactly one category:
{', '.join(CATEGORIES)}

Also capture the running balance after each transaction if the statement shows one
(a "Balance" column) — otherwise leave it null, do not guess it.

Build a behavioral profile of the account holder's cash-flow habits, using the
context of Nigeria's fintech and digital lending ecosystem.

Be conservative: if you are not confident about a signal (e.g. gambling
involvement), prefer 'None'/'Low' over overstating risk. These labels are
advisory flags for a human reviewer, not automatic decision triggers, so
honesty about uncertainty matters more than a confident-sounding answer.

If you cannot read the document at all, return an empty transactions list and
a behavioral_profile with all numeric fields at 0, booleans false, and
sustainability_verdict explaining that the document could not be read.
"""


def _media_type_for(ext: str) -> str:
    return {
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
    }.get(ext, "application/octet-stream")


def _build_report(transactions: List[Transaction]) -> dict:
    total_inflow = sum(t.amount for t in transactions if t.amount > 0)
    total_outflow = sum(-t.amount for t in transactions if t.amount < 0)

    category_totals = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for t in transactions:
        cat = t.category if t.category in CATEGORIES else "Other"
        category_totals[cat]["amount"] += abs(t.amount)
        category_totals[cat]["count"] += 1

    category_breakdown = [
        {"category": cat, "amount": round(v["amount"], 2), "count": v["count"]}
        for cat, v in sorted(category_totals.items(), key=lambda x: -x[1]["amount"])
    ]

    # ending balance = balance on the most recent transaction that reported one
    dated = [t for t in transactions if t.balance is not None]
    ending_balance = None
    if dated:
        dated_sorted = sorted(dated, key=lambda t: t.date)
        ending_balance = dated_sorted[-1].balance

    bounced_count = sum(1 for t in transactions if t.bounced)

    monthly_credits = defaultdict(float)
    for t in transactions:
        if t.amount > 0:
            try:
                d = datetime.strptime(t.date, "%Y-%m-%d")
                monthly_credits[(d.year, d.month)] += t.amount
            except ValueError:
                continue
    avg_monthly_inflow = sum(monthly_credits.values()) / len(monthly_credits) if monthly_credits else 0.0

    return {
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


def extract_with_llm(file_path: str, ext: str, raw_text: str = None) -> dict:
    if raw_text is not None:
        contents = [EXTRACTION_PROMPT + "\n\nDocument text:\n" + raw_text]
    else:
        with open(file_path, "rb") as f:
            data = f.read()
        contents = [
            types.Part.from_bytes(data=data, mime_type=_media_type_for(ext)),
            EXTRACTION_PROMPT,
        ]

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractionResult,
        ),
    )

    result: ExtractionResult = response.parsed
    if result is None or not result.transactions:
        return {
            "parsed_successfully": False, "num_transactions": 0, "bounced_count": 0,
            "avg_monthly_inflow": 0.0, "source": "llm_failed",
            "behavioral_profile": result.behavioral_profile.model_dump() if result else {},
        }

    report = _build_report(result.transactions)
    report["parsed_successfully"] = True
    report["source"] = "llm"
    report["behavioral_profile"] = result.behavioral_profile.model_dump()
    return report
