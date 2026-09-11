"""
Step 5: expose the decision engine as a web API.
Run from the project root: uvicorn src.api:app --reload --port 8003
Then open: http://127.0.0.1:8003/app
"""
import shutil
import tempfile
import os
import json
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from src.decision_engine import score_applicant, apply_bank_statement_overlay, recommend_loan_amount, build_decision_explanation
from src.parse_statement import parse_financial_document
from src.db import init_db, save_application, get_all_applications

app = FastAPI(title="Credit Decision")


@app.on_event("startup")
def on_startup():
    init_db()


class Applicant(BaseModel):
    PHONE_NUMBER: str = Field(..., pattern=r'^\d{11}$')
    BVN: str = Field(..., pattern=r'^\d{11}$')
    NIN: str = Field(..., pattern=r'^\d{11}$')
    AMT_INCOME_TOTAL: float
    AMT_CREDIT: float
    AMT_ANNUITY: float
    AMT_GOODS_PRICE: float
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
    return {"message": "Credit Decision API. Visit /docs to test it, or /app for the web app."}


@app.get("/app")
def serve_frontend():
    return FileResponse("frontend/index.html")


@app.post("/score")
def score(applicant: Applicant):
    return score_applicant(applicant.dict())


@app.post("/parse-statement")
async def parse_statement(file: UploadFile = File(...), password: str = Form(None)):
    suffix = "." + file.filename.rsplit(".", 1)[-1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        result = parse_financial_document(tmp_path, file.filename, password=password)
    finally:
        os.remove(tmp_path)
    return result


@app.post("/score-full")
async def score_full(applicant_json: str = Form(...), profile_json: str = Form(None), file: UploadFile = File(None), password: str = Form(None)):
    applicant_data = json.loads(applicant_json)
    applicant = Applicant(**applicant_data)
    result = score_applicant(applicant.dict())

    if file is not None:
        suffix = "." + file.filename.rsplit(".", 1)[-1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
        try:
            statement_summary = parse_financial_document(tmp_path, file.filename, password=password)
        finally:
            os.remove(tmp_path)
        result = apply_bank_statement_overlay(result, statement_summary)

    loan_rec = recommend_loan_amount(applicant.dict(), result)
    result["loan_recommendation"] = loan_rec
    result["explanation"] = build_decision_explanation(result, loan_rec)

    profile = json.loads(profile_json) if profile_json else {}
    save_application(applicant.dict(), result, loan_rec, profile)

    return result


@app.get("/applications")
def list_applications():
    return get_all_applications()