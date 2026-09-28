# Tasks: BoQ Price Forecast

**Input**: Design documents from `/specs/001-boq-price-forecast/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Tests are MANDATORY (test-first TDD required per constitution). All scenarios from quickstart.md must be fully implemented and testable.

**Organization**: Tasks are grouped by user story in priority order (P1, P2, P3). Each user story must be independently complete and testable before moving to the next.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Web app**: `backend/src/` for backend services, `backend/tests/` for backend tests
- **Frontend**: `frontend/src/` for React/Next.js, `frontend/tests/` for frontend tests
- **Contracts**: `contracts/` for API specifications and schemas

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization for web application with backend API + frontend dashboard

- [x] T001 Create project structure per implementation plan
- [x] T002 Initialize Python 3.11+ backend project with FastAPI 2.0, SQLAlchemy 2.0, Alembic
- [x] T003 [P] Install all required dependencies (OpenRouter, scikit-learn, XGBoost, statsmodels, Prophet, pandas, etc.)
- [x] T004 [P] Configure linting and formatting tools (black, isort, ruff, mypy)
- [x] T005 [P] Setup Docker Compose for development environment (postgres, redis, nginx)
- [x] T006 [P] Initialize Git repository with proper .gitignore
- [x] T007 [P] Initialize frontend project with Next.js 14, React 18, Tailwind CSS
- [x] T008 [P] Create README.md and .env.example

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**?? CRITICAL**: No user story work can begin until this phase is complete

Examples of foundational tasks (adjust based on your project):

- [x] T009 Setup database schema and migrations framework (PostgreSQL 15+)
- [x] T010 [P] Implement authentication/authorization framework with SSO/LDAP integration
- [x] T011 [P] Setup API routing and middleware structure in FastAPI
- [x] T012 Create base models/entities that all stories depend on (SQLAlchemy models)
- [x] T013 Configure error handling and logging infrastructure
- [x] T014 Setup environment configuration management (python-dotenv)
- [x] T015 [P] Setup Redis 7+ for caching/Celery
- [x] T016 [P] Setup Celery for async task processing (ETL, forecasts, calculations)
- [x] T017 [P] Create alembic migration configuration
- [x] T018 [P] Seed reference data (components, default admin user)

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Upload and Parse BoQ Excel (Priority: P1) [US1]

**Goal**: Parse Excel uploads, extract chapters by item codes, display preview

**Independent Test**: All scenarios from quickstart.md Scenario 1 must pass

### Tests for User Story 1

- [x] T015 [P] [US1] Parse Excel file format validation in backend/tests/unit/test_excel_parser.py
- [x] T016 [P] [US1] Chapter extraction logic test in backend/tests/unit/test_chapter_extractor.py
- [x] T017 [P] [US1] Persian RTL text handling test in backend/tests/unit/test_persian_text.py
- [x] T018 [P] [US1] Error handling for malformed data test in backend/tests/unit/test_error_handling.py
- [x] T019 [P] [US1] Complete end-to-end upload->parse test (quickstart Scenario 1)

### Implementation for User Story 1

- [x] T020 [P] [US1] Create Excel parser service in backend/src/services/boq_parser.py
- [x] T021 [P] [US1] Implement chapter extraction from item codes (first 3 digits) in backend/src/services/chapter_extractor.py
- [x] T022 [US1] Create API endpoint for BoQ upload in backend/src/api/routes/boq.py
- [x] T023 [US1] Create preview response model in backend/src/schemas/boq.py
- [x] T024 [US1] Add validation for Persian text and item codes in backend/src/validators/boq.py
- [x] T025 [US1] Setup job queue for BoQ processing in backend/src/services/boq_processing.py
- [x] T026 [US1] Create audit logging for upload operations in backend/src/services/audit_logger.py
- [x] T027 [US1] Implement error reporting for malformed rows in backend/src/services/error_handler.py

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - AI Weight Attribution per Item (Priority: P1) [US2]

**Goal**: For each BoQ item, determine weight percentages for 7 cost components using hybrid LLM + ML approach

**Independent Test**: All scenarios from quickstart.md Scenario 2 must pass

### Tests for User Story 2

- [x] T028 [P] [US2] LLM semantic parsing unit test in backend/tests/unit/test_llm_parser.py
- [x] T029 [P] [US2] ML regression weight unit test in backend/tests/unit/test_ml_regressor.py
- [x] T030 [P] [US2] Fusion algorithm test in backend/tests/unit/test_weight_fusion.py
- [x] T031 [P] [US2] Expert override validation test in backend/tests/unit/test_expert_override.py
- [x] T032 [P] [US2] Complete end-to-end weight analysis test (quickstart Scenario 2)

### Implementation for User Story 2

- [x] T033 [P] [US2] Create LLM parser service in backend/src/services/weight_attribution/llm_parser.py
- [x] T034 [P] [US2] Create ML regressor service in backend/src/services/weight_attribution/ml_regressor.py
- [x] T035 [P] [US2] Implement weight fusion service in backend/src/services/weight_attribution/fusion.py
- [x] T036 [P] [US2] Create weight analysis API in backend/src/api/routes/weights.py
- [x] T037 [US2] Implement confidence scoring for fused weights in backend/src/services/confidence_scorer.py
- [x] T038 [US2] Setup OpenRouter SDK integration with model selection per environment in backend/src/integrations/openrouter.py
- [x] T039 [US2] Create weight breakdown response model in backend/src/schemas/weight.py
- [x] T040 [US2] Implement audit trail for weight attribution in backend/src/services/weight_audit.py

**Checkpoint**: At this point, User Story 2 should be fully functional and testable independently

---

## Phase 5: User Story 3 - Price Recalculation with Market Indices (Priority: P1) [US3]

**Goal**: Compute updated prices using current market indices and formula: P_new = P_base × Σ(W_i × Index_current / Index_base)

**Independent Test**: All scenarios from quickstart.md Scenario 3 must pass

### Tests for User Story 3

- [x] T041 [P] [US3] Price calculation formula unit test in backend/tests/unit/test_price_calculation.py
- [x] T042 [P] [US3] Market index application test in backend/tests/unit/test_market_indices.py
- [x] T043 [P] [US3] Commercial adjustments calculation test in backend/tests/unit/test_commercial_adjustments.py
- [x] T044 [P] [US3] Full price recalculation integration test in backend/tests/integration/test_full_recalculation.py
- [x] T045 [P] [US3] Complete end-to-end recalculation test (quickstart Scenario 3)

### Implementation for User Story 3

- [x] T046 [P] [US3] Create price calculator service in backend/src/services/price_calculator.py
- [x] T047 [P] [US3] Implement market index service in backend/src/services/market_index_service.py
- [x] T048 [P] [US3] Create commercial adjustments service in backend/src/services/commercial_adjustments.py
- [x] T049 [P] [US3] Create price recalculation API in backend/src/api/routes/prices.py
- [x] T050 [P] [US3] Implement complete formula P_new = P_base × Σ(W_i × Index_current / Index_base) in backend/src/services/price_recalculation.py
- [x] T051 [US3] Setup batch index updating service in backend/src/services/batch_updater.py
- [x] T052 [US3] Create price result model in backend/src/schemas/price.py
- [x] T053 [US3] Implement index validation and fallback logic in backend/src/services/index_validator.py
- [x] T054 [US3] Add field-level audit logging for price changes in backend/src/services/price_audit.py

**Checkpoint**: At this point, User Story 3 should be fully functional and testable independently

---

## Phase 6: User Story 4 - Commercial Adjustments & Final Price (Priority: P2) [US4]

**Goal**: Apply sequential commercial multipliers: risk buffer (×1.04), payment terms (×1.08), profit margin (×1.10) to produce final tender price

**Independent Test**: Apply multipliers to known baseline and verify sequential multiplication results

### Tests for User Story 4

- [x] T055 [P] [US4] Commercial multiplier calculation test in backend/tests/unit/test_commercial_multipliers.py
- [x] T056 [P] [US4] Adjustment override validation test in backend/tests/unit/test_adjustment_override.py
- [x] T057 [P] [US4] Sequential application test in backend/tests/unit/test_sequential_adjustments.py
- [x] T058 [P] [US4] Audit trail for adjustments test in backend/tests/unit/test_adjustment_audit.py

### Implementation for User Story 4

- [x] T059 [P] [US4] Create commercial adjustments service in backend/src/services/commercial_adjustments.py
- [x] T060 [P] [US4] Implement risk buffer calculation (×1.04) in backend/src/services/commercial_adjustments.py
- [x] T061 [P] [US4] Create payment terms service (×1.08) in backend/src/services/commercial_adjustments.py
- [x] T062 [P] [US4] Implement profit margin calculation (×1.10) in backend/src/services/commercial_adjustments.py
- [x] T063 [P] [US4] Create final price API endpoint in backend/src/api/routes/commercial.py
- [x] T064 [US4] Implement adjustment override feature with user reason field in backend/src/services/adjustment_override.py
- [x] T065 [US4] Create adjustment result model in backend/src/schemas/commercial.py
- [x] T066 [US4] Setup adjustment configuration management in backend/src/services/adjustment_config.py
- [x] T067 [US4] Implement field-level audit logging for all adjustment steps in backend/src/services/commercial_audit.py

**Checkpoint**: At this point, User Stories 1, 2, 3, AND 4 should be independently functional

---

## Phase 7: User Story 5 - Price Forecast & Scenario Analysis (Priority: P2) [US5]

**Goal**: Generate 1-12 month price forecasts using time-series models (ARIMA/Prophet/LSTM) with optimistic/base/pessimistic scenarios

**Independent Test**: All scenarios from quickstart.md Scenario 4 must pass

### Tests for User Story 5

- [x] T068 [P] [US5] Time-series forecasting model unit test in backend/tests/unit/test_forecast_models.py
- [x] T069 [P] [US5] Scenario generation test in backend/tests/unit/test_scenario_generator.py
- [x] T070 [P] [US5] Confidence interval calculation test in backend/tests/unit/test_confidence_intervals.py
- [x] T071 [P] [US5] Chapter-level aggregate test in backend/tests/unit/test_chapter_forecasts.py
- [x] T072 [P] [US5] Complete end-to-end forecast test (quickstart Scenario 4)

### Implementation for User Story 5

- [x] T073 [P] [US5] Create Prophet forecast service in backend/src/services/forecast_engine.py
- [x] T074 [P] [US5] Implement ARIMA model service in backend/src/services/arima_forecast.py
- [x] T075 [P] [US5] Create LSTM forecasting service in backend/src/services/lstm_forecast.py
- [x] T076 [P] [US5] Create forecast service in backend/src/services/forecast_service.py
- [x] T077 [P] [US5] Create forecast API in backend/src/api/routes/forecasts.py
- [x] T078 [P] [US5] Implement scenario analysis (optimistic/base/pessimistic) in backend/src/services/scenario_analyzer.py
- [x] T079 [US5] Create forecast result model in backend/src/schemas/forecast.py
- [x] T080 [US5] Implement model retraining service in backend/src/services/model_retrainer.py
- [x] T081 [US5] Setup weekly model retraining schedule in backend/src/services/retraining_scheduler.py
- [x] T082 [US5] Create confidence interval calculation for forecasts in backend/src/services/confidence_calculator.py

**Checkpoint**: At this point, User Stories 1-5 should be independently functional

---

## Phase 8: User Story 6 - Defense Document Generator (سند دفاعیه) (Priority: P2) [US6]

**Goal**: Produce formal justification document explaining every weight, index, multiplier, and calculation step for tender dispute defense

**Independent Test**: All scenarios from quickstart.md Scenario 5 must pass

### Tests for User Story 6

- [x] T083 [P] [US6] Defense document generation test in backend/tests/unit/test_defense_document.py
- [x] T084 [P] [US6] Persian RTL document formatting test in backend/tests/unit/test_persian_pdf.py
- [x] T085 [P] [US6] Complete traceability test in backend/tests/unit/test_document_traceability.py
- [x] T086 [P] [US6] PDF export test in backend/tests/unit/test_pdf_export.py

### Implementation for User Story 6

- [x] T087 [P] [US6] Create defense document generator in backend/src/services/defense_doc_generator.py
- [x] T088 [P] [US6] Implement WeasyPrint + Jinja2 HTML templates (RTL, Vazirmatn font) in backend/src/services/pdf_generator.py
- [x] T089 [P] [US6] Create defense document API in backend/src/api/routes/exports.py
- [x] T090 [P] [US6] Implement content structure for: item description, weight table, index sources, formula, adjustments, final price in backend/src/services/document_content.py
- [x] T091 [US6] Create both AI-suggested and expert-final weight display with change reasons in backend/src/services/weight_history.py
- [x] T092 [US6] Implement Persian calendar display with Solar Hijri dates in backend/src/services/calendar_converter.py
- [x] T093 [US6] Create defense document model in backend/src/schemas/defense_doc.py
- [x] T094 [US6] Setup export validation for official tender submission in backend/src/services/export_validator.py

**Checkpoint**: At this point, User Stories 1-6 should be independently functional

---

## Phase 9: User Story 7 - Dashboard & Export (Priority: P3) [US7]

**Goal**: React/Next.js dashboard allows upload, per-item weight visualization (pie/bar charts), price comparison tables, and export to Excel/PDF

**Independent Test**: All scenarios from quickstart.md Scenario 6 must pass

### Tests for User Story 7

- [x] T095 [P] [US7] Dashboard component unit tests in frontend/tests/unit/test_dashboard_components.tsx
- [x] T096 [P] [US7] Visualization chart unit tests in frontend/tests/unit/test_visualizations.tsx
- [x] T097 [P] [US7] Export functionality tests in frontend/tests/unit/test_export_functions.tsx
- [x] T098 [P] [US7] E2E dashboard flow test in frontend/tests/e2e/test_dashboard_flow.tsx

### Implementation for User Story 7

- [x] T099 [P] [US7] Create React frontend dashboard in frontend/src/app/(dashboard)/page.tsx
- [x] T100 [P] [US7] Implement upload interface component in frontend/src/components/ui/upload-interface.tsx
- [x] T101 [P] [US7] Create weight visualization components (pie/bar charts) in frontend/src/components/charts/weight-visualization.tsx
- [x] T102 [P] [US7] Implement price comparison table in frontend/src/components/tables/price-comparison.tsx
- [x] T103 [P] [US7] Create export buttons and modals in frontend/src/components/ui/export-modal.tsx
- [x] T104 [US7] Setup API route proxies in frontend/src/app/api/(dashboard)/upload/route.ts
- [x] T105 [US7] Implement real-time data updates via TanStack Query in frontend/src/lib/api/queries.ts
- [x] T106 [US7] Create Persian RTL support components in frontend/src/lib/persian.ts
- [x] T107 [US7] Setup responsive dashboard layout with shadcn/ui in frontend/src/components/ui/dashboard-layout.tsx

**Checkpoint**: All user stories should now be independently functional

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T108 [P] Documentation updates in docs/
- [ ] T109 Code cleanup and refactoring
- [ ] T110 [P] Performance optimization across all stories
- [ ] T111 [P] Additional unit tests (if requested) in backend/tests/unit/
- [X] T112 Security hardening
- [X] T113 Run quickstart.md validation
- [X] T114 [P] Integration test scenarios (upload→analysis→export flow) in backend/tests/integration/test_complete_flow.py
- [X] T115 [P] Contract tests for API via OpenAPI spec in backend/tests/contract/test_api_contracts.py
- [X] T116 Setup Docker Compose for development environment
- [X] T117 Setup Kubernetes configurations for production
- [X] T118 [P] Observability (Prometheus metrics, structured logging) across all stories
- [X] T119 [P] Environment configuration management with .env files
- [X] T120 [P] Error handling and retry logic for external API calls
- [X] T121 [P] Input validation and sanitization across all endpoints (upload validation)
- [X] T122 [P] CORS and security headers configuration
- [X] T123 [P] Rate limiting and API throttling
- [X] T124 [P] ETL failure recovery and retry logic
- [X] T125 [P] Weekly ETL and forecast model retrain scheduling
- [X] T126 [P] API monitoring and alerting

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-9)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 ? P2 ? P3 ? P4 ? P5 ? P6 ? P7)
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P2)**: Can start after Foundational (Phase 2) - May integrate with US1 for weight analysis but should be independently testable
- **User Story 3 (P3)**: Can start after Foundational (Phase 2) - May integrate with US1/US2 but should be independently testable
- **User Story 4 (P4)**: Can start after Foundational (Phase 2) - May integrate with US3 for commercial adjustments but should be independently testable
- **User Story 5 (P5)**: Can start after Foundational (Phase 2) - May integrate with US3 for forecasts but should be independently testable
- **User Story 6 (P6)**: Can start after Foundational (Phase 2) - May integrate with US3/US5 for defense content but should be independently testable
- **User Story 7 (P7)**: Can start after Foundational (Phase 2) - May integrate with all previous stories for dashboard display but should be independently testable

### Within Each User Story

- Tests (if included) MUST be written and FAIL before implementation (TDD mandatory)
- Models before services
- Services before endpoints
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel (within Phase 2)
- Once Foundational phase completes, all user stories can start in parallel (if team capacity allows)
- All tests for a user story marked [P] can run in parallel
- Models within a story marked [P] can run in parallel
- Different user stories can be worked on in parallel by different team members

---

## Parallel Example: User Story 1

```bash
# Launch all tests for User Story 1 together:
Task: "Parse Excel file format validation in backend/tests/unit/test_excel_parser.py"
Task: "Chapter extraction logic test in backend/tests/unit/test_chapter_extractor.py"
Task: "Persian RTL text handling test in backend/tests/unit/test_persian_text.py"
Task: "Error handling for malformed data test in backend/tests/unit/test_error_handling.py"
Task: "Complete end-to-end upload->parse test"

# Launch all models for User Story 1 together:
Task: "Create Excel parser service in backend/src/services/boq_parser.py"
Task: "Implement chapter extraction from item codes in backend/src/services/chapter_extractor.py"
Task: "Create API endpoint for BoQ upload in backend/src/api/routes/boq.py"
Task: "Create preview response model in backend/src/schemas/boq.py"
Task: "Add validation for Persian text and item codes in backend/src/validators/boq.py"
Task: "Setup job queue for BoQ processing in backend/src/services/boq_processing.py"
Task: "Create audit logging for upload operations in backend/src/services/audit_logger.py"
Task: "Implement error reporting for malformed rows in backend/src/services/error_handler.py"
```

---

## Implementation Strategy

### MVP First (User Stories 1-3 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1 (Upload & Parse)
4. **STOP and VALIDATE**: Test User Story 1 independently against quickstart.md Scenario 1
5. Complete Phase 4: User Story 2 (AI Weight Attribution)
6. **STOP and VALIDATE**: Test User Story 2 independently against quickstart.md Scenario 2
7. Complete Phase 5: User Story 3 (Price Recalculation)
8. **STOP and VALIDATE**: Test User Story 3 independently against quickstart.md Scenario 3
9. **MVP complete**: Core functionality works with basic Excel upload → parsing → analysis → price calculation

### Incremental Delivery

1. Complete Setup + Foundational ? Foundation ready
2. Add User Story 1 ? Test independently ? Deploy/Demo (Core functionality)
3. Add User Story 2 ? Test independently ? Deploy/Demo
4. Add User Story 3 ? Test independently ? Deploy/Demo
5. Add User Story 4 ? Test independently ? Deploy/Demo
6. Add User Story 5 ? Test independently ? Deploy/Demo
7. Add User Story 6 ? Test independently ? Deploy/Demo
8. Add User Story 7 ? Test independently ? Deploy/Demo
9. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1
   - Developer B: User Story 2
   - Developer C: User Story 3
   - Developer D: User Story 4
   - Developer E: User Story 5
   - Developer F: User Story 6
   - Developer G: User Story 7
3. Stories complete and integrate independently

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable per quickstart.md scenarios
- Verify tests fail before implementing (TDD mandatory)
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence

---

## Quickstart Validation

To validate this implementation:

1. Run the quickstart.md validation scenarios:
   - Scenario 1: Upload → Parse
   - Scenario 2: Weights
   - Scenario 3: Prices
   - Scenario 4: Forecast
   - Scenario 5: Defense Document
   - Scenario 6: Excel Export
   - Scenario 7: Expert Override
   - Scenario 8: Concurrent Load
   - Scenario 9: ETL Failure & Recovery
2. Verify all success criteria (SC-001 through SC-019) are met
3. Test concurrent analysis of 5 BoQ files
4. Validate Persian RTL support throughout
5. Verify audit trail captures all changes
6. Test SSO/LDAP integration
7. Validate encryption at rest and in transit
8. Test performance targets (<30s for 500-item BoQ upload, <60s for full recalculation, etc.)

---

*Generated by setup-tasks.ps1 from BoQ Price Forecast implementation plan*