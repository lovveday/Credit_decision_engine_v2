# Credit Decision — Data Collection Dictionary (Nigeria)

**Purpose.** A field-by-field reference for what to collect, verify, and derive before making a lending decision — organized as an 8-layer architecture. Designed for the Nigerian market (BVN/NIN, NGN, informal-sector income, digital-lending behaviour) and mapped to *your* existing engine so you know what changes a decision **today** versus what to **collect now and use after a model retrain**.

> **How this relates to OPay.** OPay/OKash do **not** publish their exact application fields or scoring formula, so nothing here is presented as "OPay collects field X." What *is* public (from their privacy disclosures and how licensed Nigerian digital lenders operate) is the **categories** of data used: information supplied by the user, plus data obtained from NIBSS, NIMC, licensed credit bureaus and KYC providers, plus transaction, device and geolocation data used for risk/fraud. This dictionary builds a cleaner, more complete schema around those principles rather than cloning any one lender.

> **Sourcing note (read this).** External web access was unavailable when this was written, so regulatory specifics are grounded in what was well-established through early 2025 and should be **verified with a compliance professional** before launch. In particular, confirm the current instrument names/versions yourself — I have intentionally omitted one unverifiable citation that appeared in the source research.

---

## How to read this

Every field carries these tags:

- **Req** — **M** = Mandatory · **C** = Conditional (mandatory only when a condition holds, e.g. "if salaried") · **O** = Optional
- **MVP** — ✔ = build in v1 · — = phase 2
- **Use · Risk** — where the field goes in *your* engine, and how predictive it is:
  - **Model** = can feed your current Home-Credit-trained model (see mapping in Appendix B)
  - **Rules** = used by the rules / affordability engine right now (no retrain needed)
  - **Future** = worth collecting now, but only usable once you retrain on Nigerian data
  - **KYC/Ops** = identity or operational, not a risk feature
  - **Fraud** = feeds the fraud/identity check, *not* ability-to-repay
  - Risk relevance: V.High / High / Med / Low / —

> **The one big caveat.** Your model is trained on the **Home Credit** dataset, so it only understands features like `EXT_SOURCE_1/2/3`, `ORGANIZATION_TYPE`, and an `OCCUPATION_TYPE` whose valid values are *"Laborers/Core staff/Managers…"* — **not** Nigerian occupations. So most of the rich fields below power your **rules engine** today; they become **model features** only after you retrain (Task: *Improve model AUC*). Appendix B spells out exactly which fields your current model can consume.

---

## Start here — the MVP core (~30 fields)

If you build nothing else first, build these. They give a defensible decision with identity + affordability + existing debt + a bureau signal + the loan ask.

**Identity/KYC:** `first_name`, `last_name`, `date_of_birth`, `phone_number` (OTP-verified), `bvn` (NIBSS-verified), `nin`, `state_of_residence`, `id_verification_status`, `kyc_level`
**Employment/Income:** `employment_status`, `occupation`, `declared_monthly_income`, `income_frequency`, `primary_income_source`, `employment_tenure_months` (if salaried)
**Affordability/Debt:** `monthly_debt_obligations`, `total_outstanding_debt`, `active_loans_count`, `debt_to_income_ratio` (derived)
**Credit bureau:** `credit_check_consent`, `bureau_credit_score`, `bureau_enquiries_6m`, `defaults_count`
**Bank statement (you already parse these):** `verified_monthly_inflow`, `bounced_count`
**Loan request:** `requested_amount`, `loan_purpose`, `tenor_months`, `repayment_frequency`, `repayment_account`
**Consent:** `data_processing_consent`, `loan_terms_ack`

---

## Layer 1 — Identity / KYC  *(Who is this person?)*

| Field (`id`) | Type | Req | MVP | Options / validation | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Customer ID (`customer_id`) | UUID | M | ✔ | system-generated | System | KYC/Ops · — |
| First name (`first_name`) | Text | M | ✔ | letters, must match NIN/BVN | Customer → NIMC/NIBSS | KYC/Ops · Low |
| Middle name (`middle_name`) | Text | O | — | — | Customer | KYC/Ops · — |
| Last name (`last_name`) | Text | M | ✔ | letters, must match NIN/BVN | Customer → NIMC/NIBSS | KYC/Ops · Low |
| Date of birth (`date_of_birth`) | Date | M | ✔ | age ≥ 18; must match NIN/BVN DOB | Customer → NIMC/NIBSS | Model (→`age`) · High |
| Gender (`gender`) | Enum | M | ✔ | Male / Female | Customer / NIN | Future · Low |
| Phone number (`phone_number`) | Phone | M | ✔ | 11 digits (NG); verify by OTP | Customer | KYC/Ops · Med *(SIM/phone age is a fraud signal)* |
| Email (`email`) | Email | O | — | RFC-valid | Customer | KYC/Ops · — |
| Residential address (`residential_address`) | Text | M | ✔ | free text + structured | Customer | Rules · Low |
| State of residence (`state_of_residence`) | Enum | M | ✔ | 36 states + FCT | Customer | Future · Low |
| LGA (`lga`) | Enum | M | ✔ | valid LGA within state | Customer | Future · Low |
| Nationality (`nationality`) | Enum | M | — | default Nigerian | Customer | KYC/Ops · — |
| BVN (`bvn`) | ID(11) | M | ✔ | 11 digits; **verify via NIBSS**; name/DOB must match | Customer → NIBSS | Model-link · **V.High** *(gateway to bureau + `EXT_SOURCE`)* |
| NIN (`nin`) | ID(11) | M | ✔ | 11 digits; **verify via NIMC** | Customer → NIMC | KYC/Ops · Med |
| Government ID type (`gov_id_type`) | Enum | C | — | Voter's card / Passport / Driver's licence — if KYC tier requires | Customer | KYC/Ops · — |
| Government ID number (`gov_id_number`) | Text | C | — | format per ID type | Customer | KYC/Ops · — |
| ID verification status (`id_verification_status`) | Enum | M | ✔ | Verified / Pending / Failed | System | Rules · **High** *(fail → decline/refer)* |
| Selfie / liveness (`liveness_status`) | Enum | C | — | Pass / Fail — required at higher tiers/amounts | Device/KYC | Fraud · High |
| KYC tier (`kyc_level`) | Enum | M | ✔ | Tier 1 / 2 / 3 (CBN 3-tier KYC) | System | Rules · **High** *(caps max loan size)* |
| KYC verified at (`kyc_verified_at`) | Datetime | M | — | ISO-8601 | System | KYC/Ops · — |
| Identity match score (`identity_match_score`) | Decimal | C | — | 0–1 across NIN/BVN/bank-account name | System | Fraud · High |

**Notes.** CBN's tiered KYC ties account/loan limits to how much identity you've verified — Tier 1 (minimal) through Tier 3 (full), so `kyc_level` should hard-cap the offer. Verify BVN/NIN through NIBSS/NIMC (typically via a licensed aggregator, see Task: *Credit bureau / BVN*), never trust the raw number.

---

## Layer 2 — Demographics  *(What is the customer's profile?)*

| Field (`id`) | Type | Req | MVP | Options / validation | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Age (`age`) | Int | M (derived) | ✔ | from DOB; policy 18–70 | System | Model (`AGE_YEARS`) · High |
| Marital status (`marital_status`) | Enum | O | — | Single / Married / Divorced / Widowed / Separated | Customer | Model (`NAME_FAMILY_STATUS`) · Low |
| Dependents (`dependents_count`) | Int | O | ✔ | ≥ 0 | Customer | Model (`CNT_CHILDREN`) · Med |
| Education level (`education_level`) | Enum | O | — | see Appendix A | Customer | Model (`NAME_EDUCATION_TYPE`) · Low |
| Housing status (`housing_status`) | Enum | C | — | Owned / Rented / Living with family / Employer-provided | Customer | Rules/Future · Med |
| Years at address (`years_at_address`) | Decimal | O | — | ≥ 0 | Customer | Future · Med *(stability)* |
| Owns car (`owns_car`) | Bool | O | — | Y / N | Customer | Model (`FLAG_OWN_CAR`) · Low |
| Owns real estate (`owns_realty`) | Bool | O | — | Y / N | Customer | Model (`FLAG_OWN_REALTY`) · Low |
| Region risk rating (`region_rating`) | Enum | O (derived) | — | internal 1 (best) – 3 (worst) | System | Model (`REGION_RATING_CLIENT`) · Low |

---

## Layer 3 — Employment / Occupation  *(How does this person earn money?)*

This is the layer you asked about most, and where the product gets meaningfully better than "one occupation box." Model it in **three top-level fields**, then branch into a **conditional block** that depends on employment status.

### 3a. Top-level (always asked)

| Field (`id`) | Type | Req | MVP | Options / validation | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Employment status (`employment_status`) | Enum | M | ✔ | see Appendix A.1 | Customer | Model (→`NAME_INCOME_TYPE` / `IS_UNEMPLOYED_OR_RETIRED`) · High |
| Occupation category (`occupation_category`) | Enum | M | ✔ | see Appendix A.2 | Customer | Future *(model needs retrain to use NG occupations)* · Med |
| Specific occupation (`occupation`) | Enum | M | ✔ | see Appendix A.2 | Customer | Future · Med |

### 3b. Conditional — if **Salaried** (`employment_status` ∈ salaried types)

| Field (`id`) | Type | Req | MVP | Options / validation | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Employer name (`employer_name`) | Text | C-M | ✔ | required if salaried | Customer | Rules/Future · Med |
| Employer industry (`employer_industry`) | Enum | C-M | — | see Appendix A | Customer | Model (→`ORGANIZATION_TYPE`) · Med |
| Job title (`job_title`) | Text | C-M | ✔ | — | Customer | Future · Low |
| Employment type (`employment_type`) | Enum | C-M | — | Permanent / Contract / Casual / Probation | Customer | Rules · Med |
| Employment start date (`employment_start_date`) | Date | C-M | ✔ | ≤ today | Customer | derives tenure |
| Employment tenure (`employment_tenure_months`) | Int | M (derived) | ✔ | ≥ 0 | System | Model (→`YEARS_EMPLOYED`) · **High** |
| Monthly gross income (`monthly_gross_income`) | Decimal | C-M | ✔ | > 0 | Customer | Rules · High |
| Monthly net income (`monthly_net_income`) | Decimal | C-M | ✔ | > 0, ≤ gross | Customer | Rules · **V.High** *(affordability)* |
| Salary frequency (`salary_frequency`) | Enum | C-M | — | Monthly / Bi-weekly / Weekly | Customer | Rules · Med |
| Salary payment method (`salary_payment_method`) | Enum | O | — | Bank / Cash / Wallet | Customer | Rules · Med |
| Salary bank (`salary_bank`) | Enum | O | — | NG banks | Customer | Rules · Low |
| Next salary date (`next_salary_date`) | Date | O | — | — | Customer | Rules · Med *(repayment timing)* |
| Work verification (`work_verification`) | Enum | O | — | Work email / ID card / paystub | Customer | Fraud · Med |

### 3c. Conditional — if **Self-employed / Business owner**

| Field (`id`) | Type | Req | MVP | Options / validation | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Business name (`business_name`) | Text | C-M | — | — | Customer | Rules · Low |
| Business type (`business_type`) | Enum | C-M | — | Sole prop / Partnership / Ltd / Informal | Customer | Rules · Med |
| Business industry (`business_industry`) | Enum | C-M | — | see Appendix A | Customer | Future · Med |
| Registration status (`business_registration_status`) | Enum | C | — | Registered / Unregistered | Customer | Rules · Med |
| CAC number (`cac_number`) | Text | C | — | required if "Registered" | Customer → CAC | Fraud/Rules · Med |
| Years in business (`years_in_business`) | Decimal | C-M | — | ≥ 0 | Customer | Rules · **High** |
| Number of employees (`employee_count`) | Int | O | — | ≥ 0 | Customer | Future · Low |
| Avg monthly revenue (`avg_monthly_revenue`) | Decimal | C-M | — | > 0 | Customer / Bank | Rules · High |
| Avg monthly expenses (`avg_monthly_expenses`) | Decimal | C-M | — | ≥ 0 | Customer / Bank | Rules · High |
| Avg monthly profit (`avg_monthly_profit`) | Decimal | M (derived) | — | revenue − expenses | System | Rules · **V.High** |
| Business bank account (`business_bank_account`) | Bool | O | — | Y / N | Customer | Rules · Med |
| Avg monthly POS inflow (`avg_monthly_pos_inflow`) | Decimal | O | — | ≥ 0 | Customer / Bank | Rules · Med |
| Cash sales % (`cash_sales_pct`) | Decimal | O | — | 0–100 | Customer | Rules · Med *(cash-heavy = harder to verify)* |
| Premises ownership (`premises_ownership`) | Enum | O | — | Owned / Rented | Customer | Future · Low |
| Seasonality (`is_seasonal`) | Bool | O | — | Y / N | Customer | Rules · Med |

### 3d. Conditional — if **Trader / informal**

| Field (`id`) | Type | Req | MVP | Options / validation | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Goods/trade type (`trade_goods_type`) | Text | C-M | — | — | Customer | Future · Med |
| Market / location (`trade_location`) | Text | C | — | — | Customer | Rules · Low |
| Years trading (`years_trading`) | Decimal | C-M | — | ≥ 0 | Customer | Rules · High |
| Avg monthly sales (`avg_monthly_sales`) | Decimal | C-M | — | > 0 | Customer / Bank | Rules · High |
| Estimated monthly profit (`est_monthly_profit`) | Decimal | C-M | — | ≥ 0 | Customer | Rules · High |
| POS usage (`uses_pos`) | Bool | O | — | Y / N | Customer | Rules · Med |
| Bank/mobile-money usage (`uses_bank_or_momo`) | Bool | O | — | Y / N | Customer | Rules · Med |
| Seasonal (`is_seasonal`) | Bool | O | — | Y / N | Customer | Rules · Med |

### 3e. Conditional — if **Farmer**

| Field (`id`) | Type | Req | MVP | Options / validation | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Crop/livestock type (`agri_type`) | Enum | C-M | — | — | Customer | Future · Med |
| Farm location (`farm_location`) | Text | C | — | — | Customer | Rules · Low |
| Farm size (`farm_size_hectares`) | Decimal | C | — | ≥ 0 | Customer | Rules · Med |
| Years farming (`years_farming`) | Decimal | C-M | — | ≥ 0 | Customer | Rules · Med |
| Harvest cycle (`harvest_cycle`) | Enum | C | — | Annual / Biannual / Continuous | Customer | Rules · **High** *(repayment timing)* |
| Avg annual revenue (`avg_annual_revenue`) | Decimal | C-M | — | > 0 | Customer | Rules · High |
| Input costs (`input_costs`) | Decimal | O | — | ≥ 0 | Customer | Rules · Med |
| Land ownership (`land_ownership`) | Enum | O | — | Owned / Leased / Communal | Customer | Future · Low |

### 3f. Conditional — if **Freelancer / gig**

| Field (`id`) | Type | Req | MVP | Options / validation | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Profession (`freelance_profession`) | Text | C-M | — | — | Customer | Future · Med |
| Years freelancing (`years_freelancing`) | Decimal | C-M | — | ≥ 0 | Customer | Rules · Med |
| Avg monthly income (`freelance_monthly_income`) | Decimal | C-M | — | > 0 | Customer / Bank | Rules · High |
| Active clients (`active_clients_count`) | Int | O | — | ≥ 0 | Customer | Rules · Med |
| Largest client % (`largest_client_pct`) | Decimal | O | — | 0–100 | Customer | Rules · Med *(concentration risk)* |
| Payment method (`freelance_payment_method`) | Enum | O | — | Bank / Wallet / Foreign / Crypto | Customer | Rules · Med |

### 3g. Conditional — if **Student / Unemployed / Retired**

| Field (`id`) | Type | Req | MVP | Options / validation | Source | Use · Risk |
|---|---|---|---|---|---|---|
| School / institution (`institution`) | Text | C | — | if student | Customer | Future · Low |
| Study level (`study_level`) | Enum | C | — | if student | Customer | Future · Low |
| Funding source (`funding_source`) | Enum | C-M | — | Self / Sponsor / Scholarship / Loan | Customer | Rules · High |
| Sponsor monthly income (`sponsor_income`) | Decimal | C | — | if sponsor-funded | Customer | Rules · Med |
| Retirement/pension income (`pension_income`) | Decimal | C | — | if retired | Customer | Rules · High |

> **Design point:** *employment status* ≠ *occupation*. "Self-employed · Trader · 8 years" should not score like "Self-employed · Trader · 3 months." Keep status, category, specific occupation, and tenure as separate fields so the rules engine (and a future model) can weigh them independently.

---

## Layer 4 — Income & Affordability  *(Can they afford this loan?)*

The key idea: never score on **declared** income alone. Collect the declared figure, then build **verified** and **derived** income variables from the bank statement.

| Field (`id`) | Type | Req | MVP | Options / validation | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Declared monthly income (`declared_monthly_income`) | Decimal | M | ✔ | > 0 | Customer | Model (`AMT_INCOME_TOTAL`) · High |
| Primary income source (`primary_income_source`) | Enum | M | ✔ | Salary / Business / Trade / Freelance / Pension / Allowance / Other | Customer | Rules · Med |
| Income frequency (`income_frequency`) | Enum | M | ✔ | Monthly / Weekly / Irregular | Customer | Rules · Med |
| Income currency (`income_currency`) | Enum | O | — | default NGN | Customer | Rules · Low |
| Other income source (`other_income_source`) | Enum | O | — | — | Customer | Rules · Low |
| Other income amount (`other_income_amount`) | Decimal | O | — | ≥ 0 | Customer | Rules · Med |
| Verified monthly inflow (`verified_monthly_inflow`) | Decimal | C (derived) | ✔ | from statement | Bank/Statement | Rules · **V.High** |
| Median monthly inflow (`median_monthly_inflow`) | Decimal | O (derived) | — | from statement | System | Rules · High |
| Income volatility (`income_volatility`) | Decimal | O (derived) | — | stdev/mean of monthly inflow | System | Rules/Future · High |
| Salary consistency (`salary_consistency`) | Enum | O (derived) | — | High / Med / Low | System | Rules · High |
| Income confidence (`income_confidence`) | Decimal | O (derived) | — | 0–1: declared vs verified agreement | System | Rules · **High** |
| Monthly expenses (`monthly_expenses`) | Decimal | C | — | ≥ 0 | Customer / Statement | Rules · High |
| Disposable income (`disposable_income`) | Decimal | M (derived) | ✔ | income − expenses − debt | System | Rules · **V.High** |
| Debt-to-income ratio (`debt_to_income_ratio`) | Decimal | M (derived) | ✔ | (existing + new repayment) / income | System | Rules · **V.High** |

---

## Layer 5 — Existing Credit & Debt  *(How much do they already owe?)*

Split into **self-declared** and **bureau-sourced**. The bureau is the source of truth; the self-declared version catches honesty gaps.

| Field (`id`) | Type | Req | MVP | Options / validation | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Active loans count (`active_loans_count`) | Int | M | ✔ | ≥ 0 | Customer / Bureau | Rules · High |
| Total outstanding debt (`total_outstanding_debt`) | Decimal | M | ✔ | ≥ 0 | Customer / Bureau | Rules · **V.High** |
| Monthly debt obligations (`monthly_debt_obligations`) | Decimal | M | ✔ | ≥ 0 | Customer / Bureau | Rules · **V.High** *(drives DTI)* |
| Existing loans (array) (`existing_loans[]`) | Object[] | C | — | lender, type, original, outstanding, monthly repayment, rate, start, maturity, status | Customer / Bureau | Rules · High |
| Previous loans count (`previous_loans_count`) | Int | O | — | ≥ 0 | Bureau | Future · Med |
| Successfully repaid (`loans_repaid_count`) | Int | O | — | ≥ 0 | Bureau | Rules · High |
| Late repayments (`late_repayments_count`) | Int | O | ✔ | ≥ 0 | Bureau | Rules · **High** |
| Defaults (`defaults_count`) | Int | M | ✔ | ≥ 0 | Bureau | Rules · **V.High** *(hard rule)* |
| Max days past due (`max_dpd`) | Int | O | — | ≥ 0 | Bureau | Rules · High |
| Current delinquency (`is_currently_delinquent`) | Bool | M | ✔ | Y / N | Bureau | Rules · **V.High** *(auto-decline)* |
| Write-offs (`writeoffs_count`) | Int | O | — | ≥ 0 | Bureau | Rules · High |
| Restructured loans (`restructured_count`) | Int | O | — | ≥ 0 | Bureau | Future · Med |

### 5b. Credit bureau block

| Field (`id`) | Type | Req | MVP | Options / validation | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Credit-check consent (`credit_check_consent`) | Bool | M | ✔ | must be TRUE to query bureau | Customer | Compliance · — |
| Bureau credit score (`bureau_credit_score`) | Int/Decimal | C | ✔ | provider scale | Bureau | **Model (→normalized `EXT_SOURCE`)** · V.High |
| Bureau score band (`bureau_score_band`) | Enum | O | — | provider bands | Bureau | Rules · High |
| Enquiries last 6m (`bureau_enquiries_6m`) | Int | O | ✔ | ≥ 0 | Bureau | Rules · **High** *(loan-hunting/velocity)* |
| Active facilities (`bureau_active_facilities`) | Int | O | — | ≥ 0 | Bureau | Rules · High |
| Total exposure (`bureau_total_exposure`) | Decimal | O | — | ≥ 0 | Bureau | Rules · High |
| Worst current DPD (`bureau_worst_dpd`) | Int | O | — | ≥ 0 | Bureau | Rules · High |
| Report date (`bureau_report_date`) | Date | C | — | freshness check | Bureau | Ops · — |

> **Integration reality (ties to your bureau task).** Direct feeds from Nigeria's licensed bureaus (CRC, FirstCentral, CreditRegistry) generally require being a licensed/subscribed institution. The practical route for a startup is an **aggregator API** (e.g. Mono, Okra, Indicina, Dojah, Prembly) that resells bureau + BVN/NIN lookups. Your **BVN is the join key** for a bureau pull. The bureau score is also the cleanest thing to normalize into your model's `EXT_SOURCE` slot — that's the single highest-value mapping in this whole document.

---

## Layer 6 — Financial Behaviour  *(How do they actually handle money?)*

Derived from the uploaded bank statement / open-banking pull. **Several of these your engine already produces** — marked ⚙️ *(implemented in `parse_statement.py` / `llm_extractor.py`)*.

| Field (`id`) | Type | Req | MVP | Notes | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Total monthly inflow (`total_monthly_inflow`) | Decimal | C | ✔ | ⚙️ `avg_monthly_inflow` | Statement | Rules · V.High |
| Salary inflow (`salary_inflow`) | Decimal | O | — | detected salary block | Statement | Rules · High |
| Business inflow (`business_inflow`) | Decimal | O | — | — | Statement | Rules · High |
| Transfers received / cash deposits | Decimal | O | — | — | Statement | Rules · Med |
| Total monthly outflow (`total_monthly_outflow`) | Decimal | O | — | — | Statement | Rules · High |
| Loan-repayment outflow (`loan_repayment_outflow`) | Decimal | O | — | digital-lender repayments | Statement | Rules · **High** |
| Airtime/data/bills/rent/fees | Decimal | O | — | expense breakdown | Statement | Rules · Med |
| Number of transactions (`num_transactions`) | Int | O | ✔ | ⚙️ implemented | Statement | Rules · Low |
| Bounced/returned count (`bounced_count`) | Int | M | ✔ | ⚙️ implemented — already a rule trigger | Statement | Rules · **V.High** |
| Avg end-of-day balance (`avg_eod_balance`) | Decimal | O | — | ⚙️ in behavioral profile | Statement | Rules · High |
| Minimum balance (`min_balance`) | Decimal | O | — | ⚙️ | Statement | Rules · High |
| Days thin buffer (`days_thin_buffer`) | Int | O | — | ⚙️ (<₦5k days) | Statement | Rules · High |
| Inflow/outflow ratio (`inflow_outflow_ratio`) | Decimal | O (derived) | — | > 1 = net saver | System | Rules · High |
| Sweeper behaviour (`sweeper_behavior_detected`) | Bool | O | — | ⚙️ funds leave <48h | LLM | Rules(advisory) · Med |
| Gambling involvement (`gambling_involvement_level`) | Enum | O | — | ⚙️ None/Low/High | LLM | Rules(advisory) · Med |
| Loan stacking (`loan_stacking_detected`) | Bool | O | — | ⚙️ borrow-to-repay | LLM | Rules(advisory) · **High** |
| Active digital loans (`active_digital_loans[]`) | Object[] | O | — | ⚙️ lender/type/amount | LLM | Rules · High |
| Fintech savings (`detected_fintech_savings[]`) | Object[] | O | — | ⚙️ PiggyVest/Cowrywise… | LLM | Rules(positive) · Med |
| Sustainability verdict (`sustainability_verdict`) | Text | O | — | ⚙️ plain-language | LLM | Human review · — |

> These LLM-derived flags are **advisory** — they should push a case to *Refer* (manual review), never auto-*Decline*, because an unexplainable label isn't a defensible basis for an automated denial. Your `decision_engine.py` already enforces exactly this; keep it that way for compliance.

---

## Layer 7 — Fraud / Device / Risk signals  *(Is this application genuine?)*

Keep this a **separate fraud engine** with its own output. Critical principle: device/geolocation data is for **fraud & identity**, *not* ability-to-repay — and **do not harvest contact lists** (a specific focus of Nigerian regulatory action against abusive lenders).

| Field (`id`) | Type | Req | MVP | Notes | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Device ID (`device_id`) | Text | O | — | — | Device | Fraud · Med |
| Device type / OS / app version | Text | O | — | — | Device | Fraud · Low |
| Number of devices (`device_count`) | Int | O | — | many devices = risk | Device | Fraud · Med |
| SIM/phone-number age (`phone_number_age_days`) | Int | O | — | new SIM = risk | Telco/aggregator | Fraud · Med |
| IP address / IP risk (`ip_risk`) | Enum | O | — | VPN/proxy/geo mismatch | Device | Fraud · Med |
| Location consistency (`location_consistency`) | Enum | O | — | GPS vs stated state | Device | Fraud · Med |
| Failed logins / ATO indicators | Int/Bool | O | — | — | System | Fraud · Med |
| Application velocity (`application_velocity`) | Int | O | ✔ | apps per identity per window | System | Fraud · **High** |
| Duplicate identity/account (`duplicate_identity_flag`) | Bool | O | ✔ | same BVN/NIN, different profile | System | Fraud · **V.High** |
| Identity mismatch (`identity_mismatch_flag`) | Bool | C | ✔ | name/DOB across NIN/BVN/bank disagree | System | Fraud · **V.High** |

---

## Layer 8 — Loan Request  *(What are they asking us to finance?)*

| Field (`id`) | Type | Req | MVP | Options / validation | Source | Use · Risk |
|---|---|---|---|---|---|---|
| Requested amount (`requested_amount`) | Decimal | M | ✔ | > 0; ≤ product max & KYC-tier cap | Customer | Model (`AMT_CREDIT`) · High |
| Loan purpose (`loan_purpose`) | Enum | M | ✔ | see Appendix A.3 | Customer | Rules/Future · Med |
| Tenor (`tenor_months`) | Int | M | ✔ | product range | Customer | Rules · High |
| Repayment frequency (`repayment_frequency`) | Enum | M | ✔ | Monthly / Weekly / Bullet | Customer | Rules · Med |
| Requested repayment (`requested_repayment`) | Decimal | C (derived) | ✔ | annuity from amount/tenor/rate | System | Model (`AMT_ANNUITY`) · High |
| Repayment account (`repayment_account`) | Text | M | ✔ | valid NUBAN; enables GSI mandate | Customer | Ops · High |
| Loan product (`loan_product`) | Enum | M | — | Payday / Salary / SME / BNPL / Asset | System | Rules · Med |
| Borrower type (`borrower_type`) | Enum | M (derived) | ✔ | First-time / Repeat | System | Rules · High |
| Secured? (`is_secured`) | Bool | C | — | — | Customer | Rules · Med |
| Collateral details (`collateral_*`) | Object | C | — | if secured | Customer | Rules · Med |
| Asset price (`asset_price`) | Decimal | C | — | asset-purchase loans | Customer | Model (`AMT_GOODS_PRICE`) · Med |

> Purpose is a useful feature but must **inform** underwriting, not become discrimination. Don't treat a stated purpose as proof of creditworthiness.

---

## Cross-cutting — Consent & Compliance  *(a product feature, not fine print)*

| Field (`id`) | Type | Req | MVP | Notes |
|---|---|---|---|---|
| Credit-check consent (`credit_check_consent`) | Bool | M | ✔ | required before any bureau/NIBSS pull |
| Data-processing consent (`data_processing_consent`) | Bool | M | ✔ | NDPA 2023 lawful basis |
| Loan-terms & APR acknowledgement (`loan_terms_ack`) | Bool | M | ✔ | full cost of credit disclosed (interest, fees, schedule) |
| GSI mandate consent (`gsi_consent`) | Bool | C | — | for CBN Global Standing Instruction recovery |
| Marketing consent (`marketing_consent`) | Bool | O | — | must be separate & optional |
| Consent version / timestamp / IP (`consent_meta`) | Object | M | — | audit trail |
| Decision reason codes stored (`decision_reasons`) | Text[] | M | ✔ | supports right-to-explanation / adverse-action notice |

> **Regulatory anchors to verify** (I could not confirm current versions live): Nigeria Data Protection Act (NDPA) 2023 + NDPC guidance; FCCPC digital-lending registration framework/guidelines and its rules against abusive recovery practices; Credit Reporting Act 2017 (bureaus); CBN 3-tier KYC, Credit Risk Management System (CRMS), and Global Standing Instruction (GSI). Treat privacy, consent, explainability and auditability as build requirements, not afterthoughts.

---

## Decision output — don't just return Approve/Reject

Your engine already returns `decision`, `risk_band`, `probability`, `rule_triggered`, and `loan_recommendation`. Extend the contract toward this so it's explainable and auditable:

```
{
  "eligibility":   { "passed": true, "failed_rules": [] },
  "risk":          { "probability": 0.31, "band": "Medium", "score": 640 },
  "affordability": { "max_monthly_repayment": 60000, "dti": 0.38, "dti_cap": 0.40 },
  "offer":         { "approved_amount": 250000, "tenor_months": 6,
                     "repayment": 46000, "indicative_apr": 0.0 },
  "decision":      "Approve | Refer | Decline",
  "reason_codes":  ["LOW_DTI", "CLEAN_BUREAU", "STATEMENT_VERIFIED"],
  "adverse_action":["HIGH_DTI"],          // populated on Decline/Refer
  "model_version": "lr-v1", "policy_version": "rules-v3",
  "decided_at":    "..."
}
```

Reason/adverse-action codes are what make the decision defensible to a regulator and explainable to the customer.

---

## Appendix A — Enumerations (dropdown option lists)

**A.1 Employment status**
`Salaried — private` · `Salaried — public/government` · `Salaried — NGO/multinational` · `Contract/casual` · `Self-employed — business owner` · `Self-employed — professional` · `Trader` · `Artisan` · `Farmer` · `Transport operator` · `Freelancer/gig` · `Online/e-commerce seller` · `Student` · `Retired/pensioner` · `Unemployed` · `Homemaker` · `Apprentice`

**A.2 Occupation category → specific occupation (examples)**
- **Professional:** Doctor, Nurse, Pharmacist, Engineer, Accountant, Lawyer, Teacher/Lecturer, Software/IT, Data professional, Architect
- **Skilled/artisan:** Mechanic, Tailor/fashion designer, Hairdresser/barber, Electrician, Plumber, Welder, Carpenter, Builder
- **Trade/commerce:** Market trader, Shop owner, Wholesaler, Distributor, Online seller, POS agent
- **Transport:** Taxi/ride-hailing driver, Okada/bike rider, Bus/danfo driver, Truck/haulage, Dispatch rider
- **Agriculture:** Crop farmer, Livestock farmer, Poultry, Fishery, Agro-processor
- **Services:** Consultant, Event/catering, Cleaning, Security, Real-estate agent
- **Public/formal:** Civil servant, Military/paramilitary, Banker, Corporate staff
- **Other:** Student, Retired, Unemployed, Homemaker, Apprentice, Clergy, NGO worker

**A.3 Loan purpose**
`Emergency` · `Medical` · `Education/school fees` · `Rent` · `Household/personal` · `Debt consolidation` · `Business — working capital` · `Business — inventory` · `Business — equipment` · `Agriculture — inputs` · `Transportation/vehicle` · `Asset purchase (BNPL)` · `Other`

**A.4 Education** `No formal` · `Primary` · `Secondary` · `Vocational/ND/NCE` · `Higher (HND/BSc)` · `Postgraduate`
**A.5 Marital status** `Single` · `Married` · `Divorced` · `Separated` · `Widowed`
**A.6 Housing** `Owned` · `Rented` · `Living with family` · `Employer-provided`
**A.7 Income frequency** `Monthly` · `Bi-weekly` · `Weekly` · `Daily` · `Irregular/seasonal`
**A.8 Repayment frequency** `Monthly` · `Bi-weekly` · `Weekly` · `Bullet (end of term)`

---

## Appendix B — Mapping to your **current** model (Home Credit)

What your existing model can consume **today** vs. what stays in the rules engine until you retrain.

| Dictionary field | Current model input | Notes |
|---|---|---|
| `age` | `AGE_YEARS` | direct |
| `employment_tenure_months` | `YEARS_EMPLOYED` | ÷12 |
| `employment_status` (unemployed/retired) | `IS_UNEMPLOYED_OR_RETIRED` | boolean map |
| `declared_monthly_income` | `AMT_INCOME_TOTAL` | direct |
| `requested_amount` | `AMT_CREDIT` | direct |
| `requested_repayment` | `AMT_ANNUITY` | direct |
| `asset_price` | `AMT_GOODS_PRICE` | asset loans only |
| **`bureau_credit_score`** | **`EXT_SOURCE_1/2/3`** | **normalize to 0–1; highest-value mapping** |
| `dependents_count` | `CNT_CHILDREN` | direct |
| `owns_car` / `owns_realty` | `FLAG_OWN_CAR` / `FLAG_OWN_REALTY` | Y/N |
| `education_level` | `NAME_EDUCATION_TYPE` | remap to Home Credit categories |
| `marital_status` | `NAME_FAMILY_STATUS` | remap |
| `employment_status` | `NAME_INCOME_TYPE` | approximate remap |
| `occupation` | `OCCUPATION_TYPE` | ⚠️ Home Credit categories only — NG occupations don't map cleanly |
| `employer_industry` | `ORGANIZATION_TYPE` | ⚠️ Home Credit categories |
| — | `REGION_*` | Home-Credit-specific; hard to populate in NG — send neutral defaults |
| **No model home (rules only until retrain):** | | `debt_to_income_ratio`, `monthly_debt_obligations`, `defaults_count`, `is_currently_delinquent`, `bureau_enquiries_6m`, `bounced_count`, `verified_monthly_inflow`, all Layer-6 behaviour, all Layer-7 fraud, `loan_purpose`, business/trader/farmer blocks |

**Takeaway:** the richest, most Nigeria-relevant signals (real DTI, bureau delinquency, statement behaviour, fraud) drive your **rules layer** now and become **model features** only after a retrain on locally-labelled data. That's the honest path to a genuinely strong engine — the model and the rules improve on separate tracks.

---

*End of reference. Companion tasks: bureau/BVN integration (Layer 5b), model retrain (Appendix B), and the frontend should render fields conditionally per Layer 3.*

