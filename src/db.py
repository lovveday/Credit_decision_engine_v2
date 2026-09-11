"""
PostgreSQL persistence for application history.
Requires a running Postgres instance and a DATABASE_URL in your .env, e.g.:
DATABASE_URL=postgresql://postgres:yourpassword@localhost:5432/credit_decision_engine

Note: BVN/NIN are stored in plaintext here for demo simplicity.
A production system would encrypt this data at rest and restrict access.
"""
from dotenv import load_dotenv
load_dotenv()

import os
import json
from datetime import datetime, timezone
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ,
            first_name TEXT,
            last_name TEXT,
            phone_number TEXT,
            bvn TEXT,
            nin TEXT,
            employment_status TEXT,
            occupation TEXT,
            decision TEXT,
            risk_band TEXT,
            probability REAL,
            recommended_amount REAL,
            raw_result JSONB,
            profile_extra JSONB
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def save_application(applicant: dict, result: dict, loan_rec: dict, profile: dict = None):
    profile = profile or {}
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO applications
           (timestamp, first_name, last_name, phone_number, bvn, nin, employment_status, occupation,
            decision, risk_band, probability, recommended_amount, raw_result, profile_extra)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            datetime.now(timezone.utc),
            profile.get("FIRST_NAME"),
            profile.get("LAST_NAME"),
            applicant.get("PHONE_NUMBER"),
            applicant.get("BVN"),
            applicant.get("NIN"),
            profile.get("EMPLOYMENT_STATUS"),
            profile.get("OCCUPATION_DISPLAY"),
            result.get("decision"),
            result.get("risk_band"),
            result.get("probability"),
            loan_rec.get("recommended_max_credit"),
            json.dumps(result),
            json.dumps(profile),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()


def get_all_applications():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, timestamp, first_name, last_name, phone_number, employment_status,
               occupation, decision, risk_band, probability, recommended_amount
        FROM applications ORDER BY id DESC
    """)
    columns = [desc[0] for desc in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()
    for r in rows:
        r["timestamp"] = r["timestamp"].isoformat() if r["timestamp"] else None
    return rows