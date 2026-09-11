"""
LLM-based fallback extraction for financial documents (PDF/image/text)
that the deterministic parser could not confidently read.

Uses Google Gemini (via Google AI Studio) with structured JSON output,
wrapped in retry + exponential backoff so transient "service is busy"
errors (HTTP 503 model-overloaded / 429 rate-limit) don't fail the request.

Requires: pip install google-genai python-dotenv
Requires: GOOGLE_API_KEY in .env
Optional .env knobs:
    GEMINI_MODEL=gemini-3.6-flash
    GEMINI_FALLBACK_MODELS=gemini-2.5-flash,gemini-2.0-flash   # comma-separated, tried in order
"""
from dotenv import load_dotenv
load_dotenv()

import os
import time
import random
import logging
from datetime import datetime
from collections import defaultdict
from typing import List, Optional

from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from google.genai import errors as genai_errors

logger = logging.getLogger(__name__)

client = genai.Client(
    api_key=os.getenv("GOOGLE_API_KEY"),
    # Optional: fail a single hung call after 60s instead of hanging forever.
    # http_options=types.HttpOptions(timeout=60_000),  # milliseconds
)

# --- Retry / model config (tune via .env or here) ---
PRIMARY_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
FALLBACK_MODELS = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]
MODELS_TO_TRY = [PRIMARY_MODEL] + FALLBACK_MODELS

MAX_RETRIES_PER_MODEL = 4      # attempts per model before moving to the next
BASE_DELAY = 1.0               # seconds; first backoff
MAX_DELAY = 16.0               # seconds; cap on any single backoff

# HTTP status codes worth retrying (transient): overloaded, rate-limited, gateway errors
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Network-level errors also worth retrying (assembled defensively — httpx ships with google-genai)
_NETWORK_ERRORS = [ConnectionError, TimeoutError]
try:
    import httpx
    _NETWORK_ERRORS += [httpx.TimeoutException, httpx.TransportError]
except Exception:
    pass
RETRYABLE_NETWORK_ERRORS = tuple(_NETWORK_ERRORS)


class Transaction(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    description: str
    amount: float = Field(description="positive for credit/inflow, negative for debit/outflow")
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


EXTRACTION_PROMPT = """You are analyzing a Nigerian bank statement for credit underwriting.

Extract every transaction you can identify, AND build a behavioral profile of the
account holder's cash-flow habits, using the context of Nigeria's fintech and
digital lending ecosystem.

Be conservative: if you are not confident about a signal (e.g. gambling
involvement), prefer 'None'/'Low' over overstating risk. These labels will be
used only as advisory flags for a human reviewer, not as automatic decision
triggers, so honesty about uncertainty matters more than a confident-sounding
answer.

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


def _sleep_backoff(attempt: int) -> None:
    """Exponential backoff with full jitter: min(MAX_DELAY, BASE * 2**attempt) * random(0..1)."""
    ceiling = min(MAX_DELAY, BASE_DELAY * (2 ** attempt))
    time.sleep(random.uniform(0, ceiling))


def _generate(model: str, contents) -> Optional[ExtractionResult]:
    """One Gemini call. Returns the parsed ExtractionResult (or None if unparseable)."""
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractionResult,
        ),
    )
    return response.parsed


def _generate_with_retry(contents) -> Optional[ExtractionResult]:
    """
    Try each model in MODELS_TO_TRY. Retry transient failures (503 overloaded /
    429 rate-limit / 5xx gateway / network blips) with exponential backoff.
    Fail fast on non-transient errors (400/401/403/404). Returns a parsed
    ExtractionResult, or None if every attempt was exhausted.
    """
    last_error = None
    for model in MODELS_TO_TRY:
        for attempt in range(MAX_RETRIES_PER_MODEL):
            try:
                return _generate(model, contents)
            except genai_errors.APIError as e:
                last_error = e
                code = getattr(e, "code", None)
                if code in RETRYABLE_STATUS:
                    logger.warning(
                        "Gemini '%s' busy (HTTP %s), attempt %d/%d — backing off",
                        model, code, attempt + 1, MAX_RETRIES_PER_MODEL,
                    )
                    _sleep_backoff(attempt)
                    continue
                # Non-transient (bad request / auth / not found) — don't hammer this model
                logger.error("Gemini '%s' non-retryable error (HTTP %s): %s", model, code, e)
                break
            except RETRYABLE_NETWORK_ERRORS as e:
                last_error = e
                logger.warning(
                    "Network error calling Gemini '%s', attempt %d/%d — backing off: %s",
                    model, attempt + 1, MAX_RETRIES_PER_MODEL, e,
                )
                _sleep_backoff(attempt)
                continue
        logger.warning("Model '%s' exhausted; trying next fallback (if any).", model)

    if last_error is not None:
        logger.error("All Gemini attempts failed. Last error: %s", last_error)
    return None


def _unavailable_result() -> dict:
    return {
        "parsed_successfully": False,
        "num_transactions": 0,
        "bounced_count": 0,
        "avg_monthly_inflow": 0.0,
        "source": "llm_unavailable",
        "error": "The document-reading service is temporarily busy. Please try again in a moment.",
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

    result: Optional[ExtractionResult] = _generate_with_retry(contents)

    if result is None:
        # All retries/fallbacks exhausted — degrade gracefully instead of 500-ing.
        return _unavailable_result()

    transactions = result.transactions
    if not transactions:
        return {"parsed_successfully": False, "num_transactions": 0, "bounced_count": 0,
                 "avg_monthly_inflow": 0.0, "source": "llm_empty",
                 "behavioral_profile": result.behavioral_profile.model_dump()}

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
        "parsed_successfully": True,
        "num_transactions": len(transactions),
        "bounced_count": bounced_count,
        "avg_monthly_inflow": round(avg_monthly_inflow, 2),
        "months_covered": len(monthly_credits),
        "source": "llm",
        "behavioral_profile": result.behavioral_profile.model_dump(),
    }