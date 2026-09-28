# Research Findings: BoQ Price Forecast

## 1. OpenRouter API Integration for Persian Content

**Decision**: Use OpenRouter Python SDK with structured prompts, model routing via `model: "openrouter/auto"` for dev, explicit model IDs for prod (GPT-4o, Claude-3.5-Sonnet). Implement retry with exponential backoff, token budgeting per request.

**Rationale**: OpenRouter provides unified access to 100+ models, automatic fallovers, usage tracking. Free tier models (Nemotron, Qwen) sufficient for development. Production models offer superior Persian reasoning. SDK handles auth, streaming, tool calling.

**Alternatives Considered**:
- Direct OpenAI/Anthropic APIs: Vendor lock-in, no free tier, manual failover
- Local-only (Ollama/vLLM): Lower Persian quality, GPU cost, maintenance burden
- HuggingFace Inference: Higher latency, less model variety

**Key References**:
- OpenRouter docs: https://openrouter.ai/docs
- Persian LLM benchmarks: https://huggingface.co/spaces/benchmarks/persian-llm-leaderboard

---

## 2. Persian BoQ Item Classification with LLMs

**Decision**: Few-shot prompting with 15-20 curated examples per category. Structured output via Pydantic/JSON Schema. Categories: Concrete, Steel Structure, Electrical Cable, Piping, HVAC, Finishing, Site Work. Prompt includes: item description, code, chapter, unit → output: category, components[], initial_weights{}.

**Rationale**: Few-shot outperforms zero-shot for domain-specific Persian classification. Structured output enables reliable parsing. 15-20 examples covers variation without token bloat. Categories align with Iranian construction standard chapters (فصل‌ها).

**Alternatives Considered**:
- Fine-tuned Persian BERT: Requires labeled data, maintenance, less flexible
- Zero-shot with long prompt: Lower accuracy, inconsistent JSON
- Embedding + k-NN: Needs training data, less interpretable

**Key References**:
- "Persian Construction Text Classification" (ACL 2023)
- Few-shot prompting best practices: OpenAI Cookbook

---

## 3. ML Regression Feature Engineering for BoQ Weights

**Decision**: Ridge Regression (L2) as primary, XGBoost for non-linear. Features: lagged market indices (1,3,6,12 month), YoY change %, rolling stats (mean, std), calendar features (Solar Hijri month, Nowruz proximity). Target: log price change of BoQ item. Minimum 24 months data per item. Regularization via cross-validation.

**Rationale**: Ridge handles multicollinearity among market indices (copper↔steel↔FX). XGBoost captures non-linear interactions. Log-transform stabilizes variance. Iranian market has strong seasonality (Nowruz, budget year). 24-month minimum ensures statistical validity.

**Alternatives Considered**:
- OLS: Fails with correlated predictors
- Random Forest: Less interpretable coefficients, overfits small data
- Neural nets: Overkill, needs more data, black box

**Key References**:
- "Forecasting Construction Costs with Machine Learning" (J. Constr. Eng. Manage. 2022)
- scikit-learn RidgeCV, XGBoost docs

---

## 4. Weight Fusion Strategy (70/30 Weighted Average)

**Decision**: Configurable weighted average: `final_weight = α × ml_weight + (1-α) × llm_weight`, default α=0.7. Expert override replaces final_weight with mandatory reason. Confidence score = `ml_r² × 0.6 + llm_certainty × 0.4`. Side-by-side UI for review.

**Rationale**: Weighted average is transparent, auditable, explainable to tender committees. 70/30 favors empirical data when available. Bayesian fusion requires prior specification, harder to justify. Stacking needs meta-learner training data. Simple average (50/50) undervalues historical evidence.

**Alternatives Considered**:
- Bayesian model averaging: Statistically rigorous but complex to explain
- Stacking ensemble: Needs holdout data, less interpretable
- Simple average: Ignores data quality differences
- ML-only when R²>0.7: Discontinuous, loses LLM domain knowledge

**Key References**:
- "Combining Expert and Model Predictions" (Management Science 2020)
- Iranian tender audit requirements (سازمان مدیریت و برنامه‌ریزی)

---

## 5. Time-Series Forecasting for Iranian Market Data

**Decision**: Prophet as primary (handles Iranian holidays, seasonality, missing data), ARIMA as baseline, LSTM for copper/FX with sufficient data. Per-component models. Weekly retrain via Celery beat. 3 scenarios via parameter perturbation: optimistic (-1σ), base, pessimistic (+1σ). 80% CI from Prophet's uncertainty intervals.

**Rationale**: Prophet natively supports custom holidays (Solar Hijri: Nowruz, Ramadan, etc.), missing data, changepoints. ARIMA good for stationary series. LSTM captures complex non-linear dynamics for volatile series (copper, FX). Weekly retrain balances freshness vs compute. 3 scenarios via σ-perturbation is standard practice.

**Alternatives Considered**:
- Pure LSTM: Needs more data, harder to debug, no native holiday support
- SARIMAX: Complex exogenous vars, manual holiday encoding
- NeuralProphet: Less mature, similar to Prophet
- DeepAR: Requires large dataset, overkill

**Key References**:
- Prophet paper (Taylor & Letham, 2018) + Iranian holiday calendar
- "Copper Price Forecasting with Prophet" (Resources Policy 2021)
- Central Bank of Iran FX regime analysis

---

## 6. Nightly ETL for Iranian Market Data Sources

**Decision**: Celery beat scheduled tasks (02:00 Tehran time). Per-source connectors with: auth (API key/header), rate limit handling (token bucket), schema validation (Pydantic), upsert to MarketIndex table. Idempotent by (component_id, date). Alerting on failure via Slack/email. Manual re-trigger CLI.

**Rationale**: Nightly batch matches market data publication schedules (IME daily close, Central Bank daily rates). Celery provides retry, monitoring, scaling. Idempotent upserts handle re-runs. Token bucket respects rate limits. Separate connectors isolate source-specific logic.

**Source Details**:
| Source | Frequency | Auth | Key Fields |
|--------|-----------|------|------------|
| IME Copper/Steel | Daily (trading days) | API Key | date, close_price, volume |
| Central Bank FX | Daily | None (public) | date, USD/IRR, EUR/IRR |
| Energy Tariffs | Monthly | Portal login | effective_date, industrial_rate |
| Labor Index | Quarterly | Ministry portal | period, index_value |

**Alternatives Considered**:
- Airflow: Heavier, overkill for 4 sources
- Custom cron + scripts: No retry/monitoring, harder to manage
- Event-driven (webhook): Sources don't support push

**Key References**:
- IME API docs (requires registration)
- Central Bank of Iran API: https://cbi.ir
- Celery beat docs

---

## 7. Persian PDF Generation (RTL, Jalali) for سند دفاعیه

**Decision**: WeasyPrint + Jinja2 HTML templates. WeasyPrint supports RTL via CSS `direction: rtl`, Persian fonts (Vazirmatn, IRANSans), Jalali dates via template filter. Alternative: Playwright (headless Chrome) for complex layouts.

**Rationale**: WeasyPrint is pure Python, no browser dependency, fast, excellent CSS/RTL support. Jinja2 templates allow designers to edit HTML/CSS. Persian font embedding via `@font-face`. Fallback to Playwright if layout issues.

**Alternatives Considered**:
- ReportLab: Low-level, painful RTL, manual layout
- FPDF2: Limited RTL support
- Playwright only: Heavier, slower, but pixel-perfect
- LaTeX + xelatex: Powerful but steep learning curve, font issues

**Key References**:
- WeasyPrint docs: https://weasyprint.org
- Vazirmatn font: https://github.com/rastikerdar/vazirmatn
- Jalali date Python: `khayyam` or `jdatetime`

---

## 8. Excel Parsing with Persian Headers

**Decision**: openpyxl for parsing (preserves formatting, merged cells), pandas for data manipulation. Chapter detection: regex `^\d{3}` on first column. Header row detection: scan for Persian keywords (شرح، واحد، مبلغ، فصل). Skip empty rows. Validate required columns. Return structured Pydantic models.

**Rationale**: openpyxl handles .xlsx/.xls, merged cells, Persian text in cells. Pandas excels at vectorized ops. Regex on 3-digit prefix matches spec. Keyword scanning handles variable header positions. Pydantic validates output.

**Alternatives Considered**:
- pandas only: Loses merged cell info, slower for complex sheets
- xlrd: Deprecated, no .xlsx support
- Custom CSV export: Requires user action, error-prone

**Key References**:
- openpyxl docs: https://openpyxl.readthedocs.io
- Persian regex: `[\u0600-\u06FF]`

---

## 9. SSO/LDAP Integration with FastAPI

**Decision**: Authlib for OAuth2/OIDC (Keycloak, Azure AD, generic LDAP via ldap3). FastAPI dependency `get_current_user` with JWT validation. Role mapping: LDAP groups → estimator/reviewer/manager. Token refresh via refresh_token grant. Fallback: local users for dev.

**Rationale**: Authlib is standard for FastAPI OAuth2. Supports OIDC discovery, JWKS rotation. LDAP via ldap3 for direct bind. Role mapping decouples external groups from internal RBAC. JWT stateless, scales.

**Alternatives Considered**:
- FastAPI Users: Good but less flexible for LDAP
- python-jose + manual OIDC: Reinventing wheel
- Keycloak only: Vendor-specific, not all clients use it

**Key References**:
- Authlib FastAPI integration: https://docs.authlib.org/en/latest/client/fastapi.html
- ldap3 docs: https://ldap3.readthedocs.io

---

## 10. Field-Level Audit Trail Implementation

**Decision**: SQLAlchemy event listeners (`before_insert`, `before_update`, `after_update`) on Weight, PriceCalculation, Forecast, MarketIndex models. Immutable `AuditLog` table: entity_type, entity_id, field_name, old_value, new_value, user_id, timestamp, action. Indexed by (entity_type, entity_id, timestamp). No delete — only insert.

**Rationale**: Event listeners capture all ORM changes automatically, including bulk updates. Immutable table ensures tamper-evidence. Composite index enables fast per-entity history. No delete = complete trail. Structured JSON storage for complex diffs.

**Alternatives Considered**:
- Manual logging in services: Error-prone, misses bulk ops
- Temporal tables (PostgreSQL): Vendor-specific, harder to query
- pgaudit: PostgreSQL extension, captures all DML but verbose
- Event sourcing: Overkill, changes architecture

**Key References**:
- SQLAlchemy events: https://docs.sqlalchemy.org/en/20/orm/events.html
- Audit log patterns: Martin Fowler, "Audit Log"