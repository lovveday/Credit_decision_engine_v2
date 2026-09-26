"""
Step 5: expose the decision engine as a web API.
Run from the project root: Key Factors (plain language):

AMT_CREDIT: The requested loan amount is large, increasing overall exposure.
AMT_ANNUITY: The proposed repayment amount is high in absolute terms, increasing repayment burden.
AMT_INCOME_TOTAL: Declared income level relative to other applicants in the model.
Then open: http://127.0.0.1:8003/app
"""
import shutil
import tempfile
import os
import json
import hashlib
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, EmailStr
from src.decision_engine import score_applicant, apply_bank_statement_overlay, recommend_loan_amount, build_decision_explanation, apply_credit_report_overlay
from src.parse_statement import parse_financial_document, merge_reports, decrypt_pdf_if_needed
from src.credit_report_extractor import extract_credit_report
from src.db import init_db, save_application, get_all_applications, create_user, get_user_by_email, log_login, create_password_reset_token, consume_password_reset_token, delete_password_reset_token
from src.auth import hash_password, verify_password, create_token, decode_token
from src.bureau_service import verify_identity, get_bureau_scores
from src.dojah_service import lookup_identity

app = FastAPI(title="Credit Decision")
logger = logging.getLogger(__name__)


@app.on_event("startup")
def on_startup():
    init_db()


class Applicant(BaseModel):
    PHONE_NUMBER: str = Field(..., pattern=r'^\d{11}$')
    BVN: str = Field("", pattern=r'^$|^\d{11}$')
    NIN: str = Field(..., pattern=r'^\d{11}$')
    AMT_INCOME_TOTAL: float
    AMT_CREDIT: float
    AMT_ANNUITY: float
    AMT_GOODS_PRICE: float
    INTEREST_RATE: float = 0
    EXT_SOURCE_1: float
    EXT_SOURCE_2: float
    EXT_SOURCE_3: float
    AGE_YEARS: int
    YEARS_EMPLOYED: float
    IS_UNEMPLOYED_OR_RETIRED: int
    CREDIT_INCOME_RATIO: float
    ANNUITY_INCOME_RATIO: float
    CREDIT_GOODS_RATIO: float
    INCOME_OUTLIER_FLAG: int
    CNT_CHILDREN: int
    FLAG_OWN_CAR: str
    FLAG_OWN_REALTY: str
    NAME_EDUCATION_TYPE: str
    NAME_FAMILY_STATUS: str
    NAME_INCOME_TYPE: str
    OCCUPATION_TYPE: str
    REGION_POPULATION_RELATIVE: float
    REGION_RATING_CLIENT: int
    REG_CITY_NOT_LIVE_CITY: int
    REG_REGION_NOT_LIVE_REGION: int
    ORGANIZATION_TYPE: str
    EXT_SOURCE_1_MISSING: int = 0
    EXT_SOURCE_2_MISSING: int = 0
    EXT_SOURCE_3_MISSING: int = 0


@app.get("/")
def root():
    return {"message": "Credit Decision Engine API. Visit /docs to test it, or /app for the web app."}


@app.get("/app")
def serve_frontend():
    return FileResponse("frontend/index.html")


class SignupRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str
    password: str = Field(..., min_length=8)


def send_password_reset_email(email: str, reset_url: str):
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    if not smtp_user or not smtp_password:
        raise RuntimeError("SMTP_USER and SMTP_PASSWORD must be configured")

    message = EmailMessage()
    message["Subject"] = "Reset your Credit Decision Engine password"
    message["From"] = os.getenv("SMTP_FROM", smtp_user)
    message["To"] = email
    message.set_content(
        "Use the link below to reset your password. It expires in 30 minutes and can only be used once.\n\n"
        f"{reset_url}\n\nIf you did not request this, you can ignore this email."
    )

    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=10) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(message)


def get_current_user(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]
    try:
        return decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session")


@app.post("/signup")
def signup(req: SignupRequest):
    if get_user_by_email(req.email):
        raise HTTPException(status_code=400, detail="Email already registered")
    user_id = create_user(req.email, hash_password(req.password))
    log_login(user_id, req.email)
    return {"token": create_token(req.email), "email": req.email}


@app.post("/login")
def login(req: LoginRequest):
    user = get_user_by_email(req.email)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    log_login(user["id"], req.email)
    return {"token": create_token(req.email), "email": req.email}


@app.post("/password-reset/request")
def request_password_reset(req: PasswordResetRequest):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    if create_password_reset_token(req.email, token_hash, expires_at):
        app_url = os.getenv("APP_URL", "http://127.0.0.1:8003").rstrip("/")
        reset_url = f"{app_url}/app?reset_token={token}"
        try:
            send_password_reset_email(req.email, reset_url)
        except Exception:
            delete_password_reset_token(token_hash)
            logger.exception("Failed to send password reset email")
    return {"message": "If an account exists for that email, check your inbox for reset instructions."}


@app.post("/password-reset/confirm")
def confirm_password_reset(req: PasswordResetConfirmRequest):
    token_hash = hashlib.sha256(req.token.encode()).hexdigest()
    if not consume_password_reset_token(token_hash, hash_password(req.password)):
        raise HTTPException(status_code=400, detail="Reset link is invalid or expired")
    return {"message": "Password reset successfully. You can now log in."}


@app.get("/me")
def me(current_user: str = Depends(get_current_user)):
    return {"email": current_user}


@app.get("/kyc-lookup")
def kyc_lookup(
    bvn: str = "",
    nin: str = "",
    current_user: str = Depends(get_current_user),
):
    if bvn and (not bvn.isdigit() or len(bvn) != 11):
        raise HTTPException(status_code=422, detail="BVN must be exactly 11 digits")
    if nin and (not nin.isdigit() or len(nin) != 11):
        raise HTTPException(status_code=422, detail="NIN must be exactly 11 digits")
    return lookup_identity(bvn=bvn, nin=nin)


@app.get("/bureau-lookup")
def bureau_lookup(bvn: str, nin: str, current_user: str = Depends(get_current_user)):
    identity = verify_identity(bvn, nin)
    if not identity["verified"]:
        return {"verified": False, "message": "Identity could not be verified."}
    scores = get_bureau_scores(bvn)
    return {"verified": True, **scores}


@app.post("/score")
def score(applicant: Applicant, current_user: str = Depends(get_current_user)):
    return score_applicant(applicant.dict())


def _save_temp(upload: UploadFile) -> str:
    suffix = "." + upload.filename.rsplit(".", 1)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(upload.file, tmp)
        return tmp.name


def _evaluate_decision(applicant, profile, statement_summary=None, credit_report_data=None):
    result = score_applicant(applicant.dict(), profile)

    if statement_summary:
        result = apply_bank_statement_overlay(result, statement_summary, applicant.dict())

    if credit_report_data:
        result = apply_credit_report_overlay(result, credit_report_data, applicant.dict())

    loan_rec = recommend_loan_amount(applicant.dict(), result, profile)
    result["loan_recommendation"] = loan_rec
    result["explanation"] = build_decision_explanation(result, loan_rec)
    return result


@app.post("/simulate")
def simulate(
    applicant_json: str = Form(...),
    profile_json: str = Form(None),
    bank_statement_json: str = Form(None),
    credit_report_json: str = Form(None),
    current_user: str = Depends(get_current_user),
):
    applicant = Applicant(**json.loads(applicant_json))
    profile = json.loads(profile_json) if profile_json else {}
    statement_summary = json.loads(bank_statement_json) if bank_statement_json else None
    credit_report_data = json.loads(credit_report_json) if credit_report_json else None
    return _evaluate_decision(applicant, profile, statement_summary, credit_report_data)


@app.post("/parse-statement")
async def parse_statement(
    file: UploadFile = File(...),
    password: str = Form(None),
    file2: UploadFile = File(None),
    password2: str = Form(None),
    current_user: str = Depends(get_current_user),
):
    tmp_path = _save_temp(file)
    try:
        result = parse_financial_document(tmp_path, file.filename, password=password)
    finally:
        os.remove(tmp_path)

    if file2 is not None:
        tmp_path2 = _save_temp(file2)
        try:
            result2 = parse_financial_document(tmp_path2, file2.filename, password=password2)
        finally:
            os.remove(tmp_path2)
        result = merge_reports(result, result2)

    return result


@app.post("/parse-credit-report")
async def parse_credit_report(
    file: UploadFile = File(...),
    password: str = Form(None),
    current_user: str = Depends(get_current_user),
):
    ext = file.filename.lower().rsplit(".", 1)[-1]
    tmp_path = _save_temp(file)
    decrypted_path = None
    try:
        target_path = tmp_path
        if ext == "pdf":
            try:
                decrypted_path = decrypt_pdf_if_needed(tmp_path, password, tmp_path + ".decrypted.pdf")
                target_path = decrypted_path
            except ValueError as e:
                return {"parsed_successfully": False, "error": str(e)}
        result = extract_credit_report(target_path, ext)
    finally:
        os.remove(tmp_path)
        if decrypted_path and decrypted_path != tmp_path and os.path.exists(decrypted_path):
            os.remove(decrypted_path)
    return result


@app.post("/score-full")
async def score_full(
    applicant_json: str = Form(...),
    profile_json: str = Form(None),
    bank_statement_json: str = Form(None),
    credit_report_json: str = Form(None),
    file: UploadFile = File(None),
    password: str = Form(None),
    current_user: str = Depends(get_current_user),
):
    applicant_data = json.loads(applicant_json)
    applicant = Applicant(**applicant_data)
    profile = json.loads(profile_json) if profile_json else {}
    statement_summary = None
    credit_report_data = None

    if bank_statement_json:
        statement_summary = json.loads(bank_statement_json)
    elif file is not None:
        suffix = "." + file.filename.rsplit(".", 1)[-1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
        try:
            statement_summary = parse_financial_document(tmp_path, file.filename, password=password)
        finally:
            os.remove(tmp_path)

    if credit_report_json:
        credit_report_data = json.loads(credit_report_json)

    result = _evaluate_decision(applicant, profile, statement_summary, credit_report_data)

    current_user_record = get_user_by_email(current_user)
    save_application(applicant.dict(), result, result["loan_recommendation"], profile, operator_id=current_user_record["id"] if current_user_record else None)

    return result


@app.get("/applications")
def list_applications(current_user: str = Depends(get_current_user)):
    return get_all_applications()