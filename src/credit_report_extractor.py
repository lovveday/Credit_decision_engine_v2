"""
Extracts structured data from an uploaded credit bureau report PDF
(CRC Credit Bureau, CreditRegistry, or XDS — formats vary a lot between
them and even between report types from the same bureau, so unlike bank
statements there's no reliable simple-table fast path here — this goes
straight to Gemini's structured extraction).

Requires: pip install google-genai python-dotenv
Requires: GOOGLE_API_KEY in .env
"""
from dotenv import load_dotenv
load_dotenv()

import os
import json
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))


class LoanAccount(BaseModel):
    lender_name: str
    account_type: str = Field(description="e.g. Personal Loan, Credit Card, Mortgage, Auto Loan, Overdraft")
    amount_disbursed: float = Field(description="original loan/credit amount, 0 if unknown")
    outstanding_balance: float = Field(description="current amount still owed, 0 if closed/fully repaid")
    status: str = Field(description="'Open' | 'Closed' | 'Written Off' | 'Delinquent'")
    overdue_amount: float = Field(description="amount currently in arrears, 0 if current")
    months_in_arrears: int = Field(description="0 if not in arrears")
    date_opened: str = Field(description="YYYY-MM-DD if shown, else empty string")


class CreditReportExtraction(BaseModel):
    bureau_name: str = Field(description="e.g. CRC Credit Bureau, CreditRegistry, XDS Credit Bureau, or 'Unknown'")
    report_reference: str = Field(default="", description="report reference or ID")
    report_date: str = Field(default="", description="report date as shown")
    report_purpose: str = Field(default="", description="stated purpose of the report")
    credit_score: Optional[int] = Field(description="the applicant's score as shown on the report, null if not present")
    risk_grade: str = Field(default="", description="risk grade or rating, e.g. A - Low Risk / Prime")
    total_accounts: int
    open_accounts: int
    closed_accounts: int
    active_facilities: int = Field(default=0, description="number of active borrowing facilities")
    total_sanctioned_limit: float = Field(default=0, description="total sanctioned credit limit")
    written_off_accounts: int
    total_outstanding_balance: float
    total_overdue_amount: float
    historic_max_days_past_due: int = Field(default=0, description="maximum historical days past due")
    credit_utilization_percent: Optional[float] = Field(default=None, description="credit utilization percentage")
    overall_bureau_standing: str = Field(default="", description="overall bureau standing, e.g. Performing / Prime")
    recent_enquiries_count: int = Field(description="number of credit enquiries/searches shown in recent history, 0 if not shown")
    loans: List[LoanAccount]
    summary: str = Field(description="2-3 sentence plain-language summary of this applicant's credit behavior, for a loan operator who won't read the raw report")


EXTRACTION_PROMPT = """You are reading a Nigerian credit bureau report (from CRC Credit Bureau,
CreditRegistry, or XDS Credit Bureau) to help a loan operator quickly understand
an applicant's credit history, without reading the raw report themselves.

Extract every loan/credit account listed, its status, outstanding balance, and
whether it's overdue or written off. Also extract the overall credit score if
shown, risk grade, sanctioned limit, utilization, maximum historical DPD,
overall bureau standing, report reference/date/purpose, and the number of recent
credit enquiries (how many times other lenders have checked this person recently
— a high count can indicate loan shopping).

Be conservative: if a field isn't clearly present in the document, use 0 /
empty string / null rather than guessing. If you cannot read the document at
all, return zeros/empty values and say so plainly in the summary.
"""


def _media_type_for(ext: str) -> str:
    return {
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
    }.get(ext, "application/octet-stream")


def extract_credit_report(file_path: str, ext: str) -> dict:
    try:
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
                response_schema=CreditReportExtraction,
            ),
        )

        result: CreditReportExtraction = response.parsed
        if result is None and response.text:
            result = CreditReportExtraction.model_validate(json.loads(response.text))
        if result is None:
            return {
                "parsed_successfully": False,
                "error": "Gemini returned no structured credit-report data. The file may be unreadable or blank.",
            }
    except json.JSONDecodeError:
        return {"parsed_successfully": False, "error": "Gemini returned an invalid credit-report response."}
    except Exception as exc:
        return {"parsed_successfully": False, "error": f"Credit-report extraction failed: {exc}"}

    data = result.model_dump()
    data["parsed_successfully"] = True
    data["source"] = "llm"
    return data
