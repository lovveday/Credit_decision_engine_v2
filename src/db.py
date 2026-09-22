"""
PostgreSQL persistence: applications, users, and login history.
Requires DATABASE_URL in .env.

Note: BVN/NIN are stored in plaintext for demo simplicity — a production
system would encrypt this at rest and restrict access.
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
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ,
            operator_id INTEGER REFERENCES users(id),
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS login_log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            email TEXT,
            login_time TIMESTAMPTZ
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


# ---------------- Users / auth ----------------

def create_user(email: str, password_hash: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (%s,%s,%s) RETURNING id",
        (email, password_hash, datetime.now(timezone.utc)),
    )
    user_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return user_id


def get_user_by_email(email: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, email, password_hash FROM users WHERE email = %s", (email,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "email": row[1], "password_hash": row[2]}


def log_login(user_id: int, email: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO login_log (user_id, email, login_time) VALUES (%s,%s,%s)",
        (user_id, email, datetime.now(timezone.utc)),
    )
    conn.commit()
    cur.close()
    conn.close()


# ---------------- Applications ----------------

def save_application(applicant: dict, result: dict, loan_rec: dict, profile: dict = None, operator_id: int = None):
    profile = profile or {}
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO applications
           (timestamp, operator_id, first_name, last_name, phone_number, bvn, nin, employment_status, occupation,
            decision, risk_band, probability, recommended_amount, raw_result, profile_extra)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            datetime.now(timezone.utc),
            operator_id,
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
        SELECT a.id, a.timestamp, u.email AS operator_email, a.first_name, a.last_name, a.phone_number,
               a.employment_status, a.occupation, a.decision, a.risk_band, a.probability, a.recommended_amount
        FROM applications a
        LEFT JOIN users u ON a.operator_id = u.id
        ORDER BY a.id DESC
    """)
    columns = [desc[0] for desc in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()
    for r in rows:
        r["timestamp"] = r["timestamp"].isoformat() if r["timestamp"] else None
    return rows
