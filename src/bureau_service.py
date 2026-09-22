"""
Stub for credit bureau / identity verification integration.

Real integration requires a formal data-sharing agreement with a
CBN-licensed credit bureau (CRC Credit Bureau, CreditRegistry, or
XDS Credit Bureau) or an identity verification provider with
NIMC/NIBSS access (Prembly/QoreID, VerifyMe, Youverify). These are
not self-serve APIs — onboarding involves business registration,
security review, and per-query cost.

This module is a clearly-labeled MOCK so the rest of the system is
architected correctly and a real provider can be swapped in later
without touching anything else.
"""
import hashlib

BUREAUS = ["CRC Credit Bureau", "CreditRegistry", "XDS Credit Bureau"]


def verify_identity(bvn: str, nin: str) -> dict:
    """MOCK — real version calls an identity provider against NIBSS/NIMC."""
    is_valid = bool(bvn) and bvn.isdigit() and len(bvn) == 11 and bool(nin) and nin.isdigit() and len(nin) == 11
    return {
        "verified": is_valid,
        "source": "MOCK — no real bureau/NIBSS/NIMC call made",
    }

def get_bureau_scores(bvn: str) -> dict:
    """
    MOCK — real version queries CRC/CreditRegistry/XDS with the BVN
    and returns a 3-digit score (300-850 scale) per bureau.
    Deterministic per BVN so repeated lookups in the demo are consistent.
    """
    result = {"source": "MOCK — no real bureau calls made", "bureaus": []}
    for i, bureau in enumerate(BUREAUS, start=1):
        seed = int(hashlib.sha256(f"{bvn}-{bureau}".encode()).hexdigest(), 16)
        score = 300 + (seed % 551)  # deterministic value in [300, 850]
        result[f"EXT_SOURCE_{i}"] = round((score - 300) / 550, 4)
        result["bureaus"].append({"name": bureau, "score": score, "range": "300-850"})
    return result