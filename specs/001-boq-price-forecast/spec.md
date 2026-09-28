# Feature Specification: BoQ Price Forecast

**Feature Branch**: `001-boq-price-forecast`

**Created**: 2026-09-26

**Status**: Draft

**Input**: User description: AI-powered application for automated adjustment and forecasting of construction/electrical Bill of Quantities (BoQ) unit prices based on real-time market data.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Upload and Parse BoQ Excel (Priority: P1)

A cost estimator uploads an Excel file containing a multi-chapter BoQ (فهرست بها). The system parses the file, identifies chapters by the first three digits of item codes, and displays a preview of all items grouped by chapter.

**Why this priority**: This is the entry point - without parsing the BoQ, no further analysis can occur.

**Independent Test**: Can be fully tested by uploading a sample BoQ Excel file and verifying chapter/item extraction matches expected structure.

**Acceptance Scenarios**:
1. **Given** a valid BoQ Excel with multiple chapters, **When** uploaded, **Then** system extracts all items and groups them by chapter (first 3 digits of code)
2. **Given** an Excel with Persian text in descriptions, **When** parsed, **Then** RTL/Unicode text displays correctly
3. **Given** an Excel with missing or malformed data, **When** uploaded, **Then** system reports specific row/column errors

---

### User Story 2 - AI Weight Attribution per Item (Priority: P1)

For each BoQ item, the system determines the weight (percentage share) of each cost component (copper, steel, cement, polymers, energy, labor, overhead) using a hybrid LLM + ML approach.

**Why this priority**: Weight attribution is the core AI function that enables price recalculation.

**Independent Test**: Can be fully tested by selecting a single item and verifying weight breakdown sums to 100% with audit trail.

**Acceptance Scenarios**:
1. **Given** a BoQ item description (e.g., "MV copper cable 3x150mm²"), **When** analyzed, **Then** system outputs weights for copper, polymers, labor, etc. summing to 100%
2. **Given** historical price data exists for the item, **When** ML regression runs, **Then** empirical weights correlate with regression coefficients
3. **Given** LLM and ML weights differ, **When** fusion runs, **Then** final weights show reconciliation method (weighted average, Bayesian, or expert override)

---

### User Story 3 - Price Recalculation with Market Indices (Priority: P1)

The system recomputes updated unit prices for all items using current market data indices and the formula: P_new = P_base × Σ(W_i × Index_current / Index_base).

**Why this priority**: This delivers the primary value - updated prices reflecting current market conditions.

**Independent Test**: Can be fully tested by providing known base prices, weights, and indices, then verifying calculated output matches manual calculation.

**Acceptance Scenarios**:
1. **Given** base price, component weights, and current/base indices for each component, **When** recalculation runs, **Then** output matches formula exactly
2. **Given** copper price increased 28.5% (3.5M → 4.5M IRR/kg) with 70% weight, **Then** copper contribution = 1.285 × 0.70
3. **Given** missing market index for a component, **When** recalculation runs, **Then** system flags item and uses fallback or alerts user

---

### User Story 4 - Commercial Adjustments & Final Price (Priority: P2)

The system applies sequential commercial multipliers: risk buffer (×1.04), payment terms (×1.08), profit margin (×1.10) to produce the final tender price.

**Why this priority**: Commercial adjustments are standard practice in Iranian construction tenders.

**Independent Test**: Can be tested by applying multipliers to a known baseline and verifying sequential multiplication.

**Acceptance Scenarios**:
1. **Given** baseline updated price, **When** adjustments applied, **Then** final price = baseline × 1.04 × 1.08 × 1.10
2. **Given** user overrides a multiplier (e.g., profit 15% instead of 10%), **When** recalculated, **Then** new multiplier used in sequence
3. **Given** adjustments applied, **Then** audit trail shows each step with multiplier value and rationale

---

### User Story 5 - Price Forecast & Scenario Analysis (Priority: P2)

The system generates 1-12 month price forecasts using time-series models (ARIMA/Prophet/LSTM) with optimistic/base/pessimistic scenarios and confidence intervals.

**Why this priority**: Forecasting enables strategic bidding decisions for future tenders.

**Independent Test**: Can be tested by feeding historical data and verifying forecast outputs have scenarios with confidence bounds.

**Acceptance Scenarios**:
1. **Given** 24 months of copper price history, **When** forecast runs for 6 months, **Then** three scenarios produced with 80% confidence intervals
2. **Given** forecast generated, **When** user selects horizon, **Then** chart updates to show projected price path
3. **Given** scenario analysis, **Then** each scenario documents key assumptions (inflation, FX, demand)

---

### User Story 6 - Defense Document Generator (سند دفاعیه) (Priority: P2)

The system produces a formal justification document explaining every weight, index, multiplier, and calculation step for tender dispute defense.

**Why this priority**: Iranian tender regulations require defensible price justification.

**Independent Test**: Can be tested by generating document for a sample item and verifying all calculation steps are traceable.

**Acceptance Scenarios**:
1. **Given** a completed price analysis, **When** defense document generated, **Then** document includes: item description, weight table, index sources, formula, adjustments, final price
2. **Given** expert modified weights, **When** document generated, **Then** shows both AI-suggested and expert-final weights with change reason
3. **Given** document exported to PDF, **Then** formatted for official submission (Persian, RTL, headers/footers)

---

### User Story 7 - Dashboard & Export (Priority: P3)

A React/Next.js dashboard allows upload, per-item weight visualization (pie/bar charts), price comparison tables, and export to Excel/PDF.

**Why this priority**: Usable interface for daily workflow.

**Independent Test**: Can be tested by navigating through upload → analysis → visualization → export flow.

**Acceptance Scenarios**:
1. **Given** analysis complete, **When** user views item detail, **Then** weight breakdown shown as interactive pie chart
2. **Given** multiple items selected, **When** comparison view opened, **Then** table shows base vs updated vs forecast prices
3. **Given** analysis complete, **When** export clicked, **Then** Excel with all chapters/items/weights/prices downloaded

---

### Edge Cases

- What happens when market data feed is unavailable? → System uses last cached data with staleness warning
- How does system handle items with no historical price data? → LLM-only weights with confidence flag
- What if chapter mapping (first 3 digits) conflicts with description? → Description-based classification takes precedence, flagged for review
- How are unit conversions handled (kg vs m vs m²)? → Conversion factors stored per component, validated on import
- What if Persian/Farsi text has encoding issues? → UTF-8 enforced, fallback to transliteration if needed
- What happens when nightly ETL fails for a market data source? → System uses last successful ETL data, shows warning banner with source name and failure timestamp, allows manual re-trigger
- What if ETL succeeds but returns anomalous values (outliers)? → Statistical outlier detection flags values beyond 3 standard deviations for expert review before use
- What if forecast model fails to converge for a component? → Falls back to naive seasonal average with high-uncertainty flag, alerts data science team
- What if forecast horizon exceeds training data range? → Extrapolation warning shown; confidence intervals widen proportionally
- What if chapter has too few items for reliable aggregate? → Shows item count warning; aggregate marked as low-confidence

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept Excel files (.xlsx, .xls) containing BoQ data with multiple chapters
- **FR-002**: System MUST identify chapters by the first three digits of each item code
- **FR-003**: System MUST parse and display Persian (Farsi) text correctly (RTL, Unicode)
- **FR-004**: System MUST classify each BoQ item into a construction/electrical category using LLM semantic analysis via OpenRouter
- **FR-005**: System MUST identify constituent raw materials/components for each item (copper, steel, cement, polymers, energy, labor, overhead)
- **FR-006**: System MUST propose initial weight distribution (summing to 100%) per component via LLM
- **FR-026**: System MUST support configurable LLM provider (OpenRouter primary, with local Ollama/vLLM fallback) and model selection per environment
- **FR-027**: System MUST log LLM prompt, response, model used, and token counts for each weight attribution request
- **FR-007**: System MUST train ML models (Ridge Regression, Random Forest, XGBoost) on historical price data to derive empirical weights
- **FR-008**: System MUST reconcile LLM-derived and ML-derived weights using weighted average fusion (default 70% ML / 30% LLM, configurable per project)
- **FR-009**: System MUST allow human experts to override final fused weights with mandatory reason field and full audit trail
- **FR-028**: System MUST display both LLM and ML weight breakdowns side-by-side with fusion result for expert review
- **FR-029**: System MUST compute and show confidence score for fused weights based on ML model R² and LLM certainty
- **FR-010**: System MUST compute updated unit price using formula: P_new = P_base × Σ(W_i × Index_current / Index_base)
- **FR-011**: System MUST ingest market indices via nightly batch ETL from IME (copper, steel), Central Bank (FX), energy tariffs, and labor indices into local database
- **FR-012**: System MUST apply commercial adjustments sequentially: risk buffer, payment terms, profit margin
- **FR-013**: System MUST allow configuration of commercial adjustment multipliers per project
- **FR-014**: System MUST generate price forecasts per item at 1, 3, 6, 12 month horizons using ARIMA, Prophet, and/or LSTM models
- **FR-015**: System MUST produce scenario analysis: optimistic, base, pessimistic with 80% confidence intervals
- **FR-030**: System MUST generate chapter-level forecast aggregates (mean/median/total) from per-item forecasts
- **FR-031**: System MUST retrain forecast models weekly using latest market data ETL snapshot
- **FR-032**: System MUST allow on-demand forecast refresh for selected items/chapters
- **FR-016**: System MUST generate defense/justification document (سند دفاعیه) with full audit trail
- **FR-017**: System MUST export updated BoQ to Excel with per-item weights and prices
- **FR-018**: System MUST export defense document to PDF (Persian, RTL formatted)
- **FR-019**: System MUST provide dashboard with: upload, chapter preview, weight visualization, price comparison, export
- **FR-020**: System MUST support unit conversions (kg, m, m², m³) for materials
- **FR-021**: System MUST store all weights, indices, calculations, and user overrides for audit trail
- **FR-022**: System MUST provide market data freshness indicator showing last ETL run timestamp per source
- **FR-023**: System MUST enforce role-based access control (estimator/reviewer/manager) with SSO/LDAP integration
- **FR-024**: System MUST encrypt sensitive data at rest and in transit
- **FR-025**: System MUST log field-level audit trail for all weight, index, and price changes with user identity and timestamp

### Key Entities

- **BoQ Item**: Represents a line item in the Bill of Quantities. Attributes: code (chapter prefix + item), description (Persian), chapter, base price, unit (m, kg, m², m³), category, weights per component
- **Chapter**: Group of BoQ items sharing first 3 digits of code. Attributes: code (3 digits), name, items[]
- **Component**: Raw material or cost factor. Attributes: name (copper, steel, cement, polymer, energy, labor, overhead), category, unit, conversion factor
- **Weight**: Percentage share of a component in an item's price. Attributes: item_id, component_id, llm_weight, ml_weight, final_weight, source (llm/ml/fusion/expert), confidence, overridden_by, overridden_at
- **Market Index**: Time-series price/index for a component. Attributes: component_id, date, value, source (IME, Central Bank, etc.), frequency
- **Price Calculation**: Result of baseline update + commercial adjustments. Attributes: item_id, base_price, updated_price, final_price, adjustments_applied[], timestamp
- **Forecast**: Projected prices for an item. Attributes: item_id, horizon_months, scenario (optimistic/base/pessimistic), values[], confidence_interval, model_used, assumptions
- **Defense Document**: Audit trail for a price calculation. Attributes: item_id, calculation_id, full_trace (weights, indices, formulas, adjustments), generated_at, generated_by, format (PDF/HTML)
- **Market Data Source**: External price feed configuration. Attributes: name (IME copper, IME steel, Central Bank FX, energy tariff), api_endpoint, auth_method, schedule (cron), last_run_at, last_success_at, status (success/failed/stale)
- **Forecast Model**: Trained time-series model per component. Attributes: component_id, model_type (ARIMA/Prophet/LSTM), hyperparameters, trained_at, training_data_range, performance_metrics (MAE, RMSE, R²), version, status (active/archived)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can upload a 500-item BoQ Excel and see parsed chapters/items within 30 seconds
- **SC-002**: Weight attribution for a single item completes within 10 seconds (LLM + ML + fusion)
- **SC-003**: Full BoQ price recalculation (500 items) completes within 60 seconds
- **SC-004**: Forecast generation for 100 items with 3 scenarios completes within 120 seconds
- **SC-005**: Defense document generated for a chapter (50 items) within 15 seconds
- **SC-006**: Price calculation accuracy matches manual spreadsheet verification to within 0.1%
- **SC-007**: 90% of cost estimators can complete upload→analysis→export workflow without training
- **SC-008**: System handles concurrent analysis of 5 BoQ files without degradation
- **SC-009**: Market data freshness indicator shows last ETL run timestamp; data no older than 24 hours at calculation time
- **SC-010**: Audit trail captures 100% of weight changes, index sources, and adjustment decisions
- **SC-011**: Unauthorized access attempts blocked and logged within 1 second
- **SC-012**: Data encryption verified via penetration test before production deployment
- **SC-013**: LLM weight attribution latency < 5 seconds per item in production (GPT-4o/Claude tier)
- **SC-014**: LLM provider failover to local model completes within 30 seconds with degraded quality notice
- **SC-015**: Fused weights match expert manual selection in ≥85% of validated items (measured during UAT)
- **SC-016**: Weight fusion computation completes in <1 second per item
- **SC-017**: Per-item forecast (3 scenarios, 4 horizons) completes in <2 seconds
- **SC-018**: Weekly model retrain completes within 4-hour maintenance window
- **SC-019**: Forecast MAE (Mean Absolute Error) ≤ 15% vs actuals at 3-month horizon (measured quarterly)

## Assumptions

- Target users are cost estimators and tender managers in Iranian construction/electrical firms
- Users have stable internet connectivity for market data feeds and LLM API calls
- Historical BoQ price data exists for at least 20% of items to enable ML regression
- IME (Iran Mercantile Exchange) and Central Bank APIs are accessible for nightly batch ETL (directly or via proxy)
- Persian calendar (Solar Hijri) used for date display; Gregorian for internal storage
- All monetary values in IRR (ریال); no multi-currency support needed for v1
- On-premise deployment preferred for data sovereignty; cloud option for scalability
- Role-based access: estimator (analyze), reviewer (override weights), manager (approve/export)
- Existing authentication system (SSO/LDAP) will be integrated
- Mobile/responsive UI not required for v1 (desktop dashboard sufficient)
- OpenRouter API key available for production; free tier models sufficient for development
- Local Ollama/vLLM endpoint available for on-premise fallback (optional)
- Minimum 24 months historical market data available per component for forecast model training
- Forecast model retrain compute resources available during weekly maintenance window

## Clarifications

### Session 2026-09-26

- Q: What security model should protect sensitive pricing data for tenders? → A: Role-based access with SSO/LDAP integration, field-level audit logging for all weight/price changes, data encryption at rest and in transit
- Q: How should market data (IME, Central Bank, energy) be ingested and made available to calculations? → A: Batch ETL pipeline (nightly) loading indices into local DB; calculation engine reads only from local DB
- Q: Which LLM provider and model strategy should be used for semantic weight attribution? → A: OpenRouter with auto model selection (dev: free models like Nemotron/Qwen; prod: GPT-4o/Claude 3.5 Sonnet), supports local-compatible endpoints
- Q: How should LLM-derived and ML-derived weights be reconciled when they differ? → A: Weighted average fusion (default 70% ML empirical / 30% LLM semantic), expert can override final weight with mandatory reason, full audit trail of both sources
- Q: What forecast horizons, granularity, and retrain frequency are required? → A: Per-item forecasts at 1, 3, 6, 12 month horizons; chapter-level aggregates; models retrained weekly; 3 scenarios with 80% CI