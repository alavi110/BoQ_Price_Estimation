# Implementation Plan: BoQ Price Forecast

**Branch**: `001-boq-price-forecast` | **Date**: 2026-09-26 | **Spec**: specs/001-boq-price-forecast/spec.md

**Input**: Feature specification from `/specs/001-boq-price-forecast/spec.md`

## Summary

AI-powered full-stack application for automated adjustment and forecasting of construction/electrical Bill of Quantities (BoQ) unit prices based on real-time market data. The system implements a two-pillar AI architecture: (1) Weight Attribution via hybrid LLM semantic parsing + ML regression fusion, and (2) Price Prediction via baseline index recalculation, commercial adjustments, and time-series forecasting. Delivers updated BoQ Excel, weight breakdowns, forecast scenarios, and defensible audit documents (سند دفاعیه) for Iranian tender compliance.

## Technical Context

**Language/Version**: Python 3.11+ (backend), TypeScript 5+ (frontend)

**Primary Dependencies**:
- Backend: FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2, Celery + Redis
- AI/ML: OpenRouter SDK, scikit-learn, XGBoost, statsmodels, Prophet, PyTorch (LSTM), pandas, openpyxl
- Database: PostgreSQL 15+ (primary), Redis 7+ (caching, Celery broker)
- Frontend: Next.js 14 (App Router), React 18, Tailwind CSS, Recharts, React Hook Form, TanStack Query
- Infrastructure: Docker Compose (dev), Kubernetes (prod), Nginx, Prometheus/Grafana

**Storage**: PostgreSQL (relational data: BoQ items, weights, calculations, forecasts, audit trails), Redis (market data cache, Celery broker, session), File system (Excel/PDF exports, model artifacts)

**Testing**: pytest (backend unit/integration), Vitest (frontend unit), Playwright (E2E), Locust (load)

**Target Platform**: Linux server (on-premise primary, cloud optional), Docker containers, Kubernetes orchestration

**Project Type**: Web application (backend API + frontend dashboard)

**Performance Goals**:
- BoQ upload/parse (500 items): <30s
- Weight attribution/item (LLM+ML+fusion): <10s
- Full BoQ recalculation (500 items): <60s
- Forecast generation (100 items, 3 scenarios): <120s
- Defense doc generation (chapter/50 items): <15s
- Concurrent BoQ analyses: 5 without degradation
- Weekly model retrain: <4h maintenance window

**Constraints**:
- Persian (Farsi) RTL text support throughout
- All monetary values in IRR (ریال)
- Solar Hijri calendar display, Gregorian storage
- Data sovereignty: on-premise deployment preferred
- SSO/LDAP integration mandatory
- Encryption at rest (AES-256) and in transit (TLS 1.3)
- Field-level audit trail for all weight/price changes
- Nightly batch ETL for market data (IME, Central Bank, energy)
- 24-month minimum historical data for forecast training

**Scale/Scope**:
- 500 items per BoQ typical, up to 2000
- 7 core components (copper, steel, cement, polymers, energy, labor, overhead)
- 10-50 concurrent users per deployment
- 4 market data sources (IME copper, IME steel, Central Bank FX, energy tariffs)
- 3 forecast scenarios × 4 horizons × per-item + chapter aggregates

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| Library-First | ✅ Pass | Core calculation engine extracted as `boq_core` library (services/weight_attribution/, services/price_calculator.py, services/forecast_engine.py); API and UI consume via well-defined interfaces |
| CLI Interface | ✅ Pass | Backend exposes CLI for ETL (`etl_cli.py`), model training (`train_cli.py`), batch calculations (`calc_cli.py`) |
| Test-First (NON-NEGOTIABLE) | ✅ Pass | All phases require tests first; TDD enforced in implementation; contracts defined before implementation |
| Integration Testing | ✅ Pass | E2E tests for upload→analysis→export flow; contract tests for API via OpenAPI spec |
| Observability/Simplicity | ✅ Pass | Structured logging, Prometheus metrics, health endpoints; YAGNI for v1 |

**Gates**: All pass. No violations requiring justification.

**Post-Phase 1 Re-evaluation**: ✅ All principles satisfied. Library extraction clarified, CLI interfaces specified, test contracts defined, integration test scenarios documented, observability built into service structure.

## Project Structure

### Documentation (this feature)

```text
specs/001-boq-price-forecast/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── api-openapi.yaml
│   ├── etl-config-schema.json
│   └── model-config-schema.json
└── tasks.md             # Phase 2 output (NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
# Option 2: Web application (backend + frontend)
backend/
├── src/
│   ├── models/              # SQLAlchemy models
│   ├── schemas/             # Pydantic request/response
│   ├── services/            # Business logic
│   │   ├── boq_parser.py
│   │   ├── weight_attribution/
│   │   │   ├── llm_parser.py
│   │   │   ├── ml_regressor.py
│   │   │   └── fusion.py
│   │   ├── price_calculator.py
│   │   ├── forecast_engine.py
│   │   ├── defense_doc_generator.py
│   │   └── etl/
│   │       ├── ime_connector.py
│   │       ├── central_bank_connector.py
│   │       └── energy_connector.py
│   ├── api/
│   │   ├── routes/
│   │   │   ├── boq.py
│   │   │   ├── weights.py
│   │   │   ├── prices.py
│   │   │   ├── forecasts.py
│   │   │   ├── exports.py
│   │   │   └── admin.py
│   │   └── deps.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   ├── database.py
│   │   └── celery_app.py
│   ├── tasks/               # Celery tasks
│   │   ├── etl_tasks.py
│   │   ├── forecast_tasks.py
│   │   └── calc_tasks.py
│   └── cli/
│       ├── etl_cli.py
│       ├── train_cli.py
│       └── calc_cli.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── fixtures/
├── alembic/
├── Dockerfile
├── pyproject.toml
└── requirements.txt

frontend/
├── src/
│   ├── app/                 # Next.js App Router pages
│   │   ├── (dashboard)/
│   │   │   ├── upload/
│   │   │   ├── analysis/
│   │   │   ├── weights/
│   │   │   ├── prices/
│   │   │   ├── forecasts/
│   │   │   └── exports/
│   │   └── api/             # API route proxies
│   ├── components/
│   │   ├── ui/              # Shadcn/UI components
│   │   ├── charts/          # Recharts wrappers
│   │   ├── forms/
│   │   └── tables/
│   ├── lib/
│   │   ├── api.ts           # TanStack Query hooks
│   │   ├── utils.ts
│   │   └── persian.ts       # RTL, Jalali helpers
│   └── styles/
├── tests/
│   ├── unit/
│   └── e2e/
├── Dockerfile
├── package.json
└── tailwind.config.ts

infrastructure/
├── docker-compose.yml
├── kubernetes/
│   ├── base/
│   └── overlays/
│       ├── dev/
│       └── prod/
└── monitoring/
    ├── prometheus.yml
    └── grafana-dashboards/
```

**Structure Decision**: Option 2 (Web application) — backend API (FastAPI) + frontend dashboard (Next.js) with shared infrastructure. This matches the spec's requirement for a React/Next.js dashboard with upload, visualization, and export capabilities.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| N/A | All gates pass | No violations |

---

## Phase 0: Research ✅ COMPLETE

**Output**: `specs/001-boq-price-forecast/research.md`

All 10 unknowns resolved with decisions, rationale, alternatives, and references:

| # | Unknown | Decision |
|---|---------|----------|
| 1 | OpenRouter API integration | OpenRouter SDK with auto model routing (dev: free models; prod: GPT-4o/Claude) |
| 2 | Persian BoQ classification | Few-shot prompting (15-20 examples/category) with structured JSON output |
| 3 | ML regression features | Ridge (primary) + XGBoost; lagged indices, YoY%, rolling stats, Solar Hijri calendar |
| 4 | Weight fusion | Configurable weighted average (default 70% ML / 30% LLM), expert override with audit |
| 5 | Time-series forecasting | Prophet primary (Iranian holidays), ARIMA baseline, LSTM for volatile series |
| 6 | Nightly ETL | Celery beat 02:00 Tehran; per-source connectors; idempotent upserts; alerting |
| 7 | Persian PDF generation | WeasyPrint + Jinja2 HTML templates (RTL, Vazirmatn font, Jalali dates) |
| 8 | Excel parsing | openpyxl (preserves formatting) + pandas; regex chapter detection; Persian keywords |
| 9 | SSO/LDAP integration | Authlib OAuth2/OIDC + ldap3 for direct LDAP; JWT validation; role mapping |
| 10 | Field-level audit trail | SQLAlchemy event listeners → immutable AuditLog table (entity, field, old/new, user, timestamp) |

---

## Phase 1: Design & Contracts ✅ COMPLETE

### Data Model → `specs/001-boq-price-forecast/data-model.md`
- 14 tables with full DDL, constraints, indexes, relationships
- ERD diagram, validation rules, state transitions

### Contracts → `specs/001-boq-price-forecast/contracts/`
1. **api-openapi.yaml** — Complete OpenAPI 3.1 spec (40+ endpoints)
2. **etl-config-schema.json** — JSON Schema for 4 market data sources
3. **model-config-schema.json** — JSON Schema for Prophet/ARIMA/LSTM hyperparameters

### Quickstart → `specs/001-boq-price-forecast/quickstart.md`
- 9 runnable validation scenarios (upload → weights → prices → forecast → defense doc → export → override → load → ETL failure)
- Prerequisites, steps, validation criteria, troubleshooting