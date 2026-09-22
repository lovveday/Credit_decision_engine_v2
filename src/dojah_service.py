"""
Real BVN identity lookup via Dojah (dojah.io) — auto-populates
name/DOB/phone from a BVN, replacing manual KYC entry.

Requires: pip install requests
Requires in .env: DOJAH_APP_ID, DOJAH_SECRET_KEY, DOJAH_BASE_URL
(sandbox default: https://sandbox.dojah.io — free, returns fixed test
data regardless of the BVN sent. Switch to https://api.dojah.io for
live data, which costs per lookup.)
"""
import os
import requests
from dotenv import load_dotenv
load_dotenv()

DOJAH_BASE_URL = os.getenv("DOJAH_BASE_URL", "https://sandbox.dojah.io")
DOJAH_APP_ID = os.getenv("DOJAH_APP_ID")
DOJAH_SECRET_KEY = os.getenv("DOJAH_SECRET_KEY")
DOJAH_NIN_PATH = os.getenv("DOJAH_NIN_PATH", "/api/v1/kyc/nin")


def _headers():
    return {"AppId": DOJAH_APP_ID, "Authorization": DOJAH_SECRET_KEY}


def _normalize_date(value):
    if not value:
        return None
    value = str(value).strip()
    if len(value) >= 10 and value[4] == "-":
        return value[:10]
    for separator in ("/", "-"):
        parts = value.split(separator)
        if len(parts) == 3 and len(parts[2]) == 4:
            day, month, year = parts
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    return value


def _entity_from_response(payload):
    entity = payload.get("entity") or payload.get("data") or payload
    if isinstance(entity, dict) and isinstance(entity.get("entity"), dict):
        entity = entity["entity"]
    return entity if isinstance(entity, dict) else {}


def _identity_from_response(payload) -> dict:
    data = _entity_from_response(payload)
    first_name = data.get("first_name") or data.get("firstName") or data.get("firstname")
    last_name = data.get("last_name") or data.get("lastName") or data.get("lastname")
    middle_name = data.get("middle_name") or data.get("middleName") or data.get("middlename")
    date_of_birth = data.get("date_of_birth") or data.get("dateOfBirth") or data.get("dob")
    phone_number = (
        data.get("phone_number1") or data.get("phone_number")
        or data.get("phoneNumber") or data.get("mobile") or data.get("phone")
    )
    return {
        "verified": bool(data and (first_name or last_name or date_of_birth or phone_number)),
        "first_name": first_name,
        "last_name": last_name,
        "middle_name": middle_name,
        "gender": data.get("gender"),
        "date_of_birth": _normalize_date(date_of_birth),
        "phone_number": phone_number,
    }


def _lookup(path: str, params: dict) -> dict:
    url = f"{DOJAH_BASE_URL}{path}"
    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    except requests.RequestException as e:
        return {"verified": False, "error": str(e)}

    if resp.status_code != 200:
        error_message = {
            400: "Dojah rejected the identifier format. Check that it has exactly 11 digits.",
            401: "Dojah authentication failed. Check DOJAH_APP_ID and DOJAH_SECRET_KEY.",
            402: "Dojah wallet balance is insufficient for this verification.",
            404: "No identity record was found for this identifier.",
            424: "Dojah's upstream identity service is temporarily unavailable.",
            429: "Dojah rate limit reached. Try again shortly.",
        }.get(resp.status_code, f"Dojah verification failed (HTTP {resp.status_code}).")
        return {"verified": False, "error": error_message, "provider_status": resp.status_code}

    try:
        return _identity_from_response(resp.json())
    except ValueError:
        return {"verified": False, "error": "Dojah returned an invalid JSON response"}


def lookup_bvn(bvn: str) -> dict:
    """
    Real Dojah BVN lookup. Confirm the exact query parameter name and
    grab a sandbox test BVN from docs.dojah.io once logged in —
    'bvn' is our best-documented assumption, verify before relying on it.
    """
    return _lookup("/api/v1/kyc/bvn/full", {"bvn": bvn})


def lookup_nin(nin: str) -> dict:
    """Look up identity fields from a NIN through the configured Dojah path."""
    return _lookup(DOJAH_NIN_PATH, {"nin": nin})


def lookup_identity(bvn: str = "", nin: str = "") -> dict:
    """Use BVN when available, otherwise use NIN."""
    if bvn:
        return lookup_bvn(bvn)
    if nin:
        return lookup_nin(nin)
    return {"verified": False, "error": "A BVN or NIN is required"}
