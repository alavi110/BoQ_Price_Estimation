# Quickstart Guide: BoQ Price Forecast

Validate the BoQ Price Forecast feature end-to-end using the following scenarios.

## Prerequisites

### System Requirements
- Docker 24+ and Docker Compose 2+
- 8GB RAM minimum (16GB recommended for ML training)
- 20GB free disk space
- Ports 8000 (backend), 3000 (frontend), 5432 (PostgreSQL), 6379 (Redis) available

### Environment Setup
```bash
# Clone repository
git clone <repo-url>
cd BidPrice_Estimation

# Copy environment template
cp .env.example .env

# Edit .env with your keys
# Required: OPENROUTER_API_KEY, DATABASE_URL, REDIS_URL, JWT_SECRET
# Optional: IME_API_KEY, CENTRAL_BANK_API_KEY, ENERGY_PORTAL_CREDENTIALS

# Start infrastructure
docker-compose up -d postgres redis

# Run migrations
docker-compose run --rm backend alembic upgrade head

# Seed reference data (components, default project)
docker-compose run --rm backend python -m scripts.seed_data

# Start all services
docker-compose up -d
```

### Verify Services
```bash
# Health checks
curl http://localhost:8000/health
curl http://localhost:3000/api/health

# Expected: {"status": "healthy", ...}
```

---

## Scenario 1: Upload and Parse BoQ Excel

### Prerequisites
- Sample BoQ Excel file: `tests/fixtures/sample_boq.xlsx`
  - 3 chapters (101, 201, 301)
  - 50 items total
  - Persian descriptions with RTL text
  - Columns: Code, Description, Unit, Base Price, Quantity

### Steps
```bash
# 1. Upload BoQ (creates new project)
curl -X POST http://localhost:8000/boq/upload \
  -H "Authorization: Bearer <TOKEN>" \
  -F "file=@tests/fixtures/sample_boq.xlsx" \
  -F "create_new_project=true" \
  -F "project_name=Sample Tender 2026"

# Response: {"job_id": "uuid", "status": "completed", "preview": {...}}

# 2. Get preview
JOB_ID=<job_id_from_step_1>
curl -X GET http://localhost:8000/boq/$JOB_ID/preview \
  -H "Authorization: Bearer <TOKEN>"

# Expected: 3 chapters, 50 items, RTL text displayed correctly
```

### Validation
- [ ] Job status = "completed"
- [ ] 3 chapters with codes 101, 201, 301
- [ ] 50 items total across chapters
- [ ] Persian descriptions render correctly (RTL)
- [ ] Base prices in IRR
- [ ] No parsing errors in warnings array

---

## Scenario 2: AI Weight Attribution

### Prerequisites
- Completed Scenario 1 (have JOB_ID)
- OpenRouter API key configured
- At least 10 items with historical price data for ML regression

### Steps
```bash
# 1. Trigger weight analysis
curl -X POST http://localhost:8000/weights/analyze \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"job_id\": \"$JOB_ID\", \"force_reanalyze\": false}"

# Response: {"job_id": "uuid", "status": "running", ...}

# 2. Poll for completion (max 60s for 50 items)
WEIGHT_JOB_ID=<job_id_from_step_1>
while true; do
  STATUS=$(curl -s http://localhost:8000/weights/$WEIGHT_JOB_ID -H "Authorization: Bearer <TOKEN>" | jq -r .status)
  echo "Status: $STATUS"
  [ "$STATUS" = "completed" ] && break
  [ "$STATUS" = "failed" ] && echo "FAILED" && exit 1
  sleep 5
done

# 3. Get weight breakdown
curl -X GET http://localhost:8000/weights/$JOB_ID \
  -H "Authorization: Bearer <TOKEN>"
```

### Validation
- [ ] All 50 items have weight breakdowns
- [ ] Each item: weights sum to 1.0 (±0.001)
- [ ] 7 components per item (copper, steel, cement, polymer, energy, labor, overhead)
- [ ] Source ∈ {llm, ml, fusion, expert}
- [ ] LLM weights + ML weights + fusion weights present
- [ ] Confidence scores in [0, 1]
- [ ] Items with history show ML weights; others show LLM-only with low confidence
- [ ] Analysis completes < 10s/item (50 items < 500s total)

---

## Scenario 3: Price Recalculation with Market Indices

### Prerequisites
- Completed Scenario 2 (weights available)
- Market data ETL run successfully (check `/market-data/freshness`)

### Steps
```bash
# 1. Check market data freshness
curl -X GET http://localhost:8000/market-data/freshness \
  -H "Authorization: Bearer <TOKEN>"

# Expected: all 4 sources status="success", last_success_at < 24h ago

# 2. Trigger price calculation
curl -X POST http://localhost:8000/prices/recalculate \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"job_id\": \"$JOB_ID\"}"

# 3. Get calculated prices
curl -X GET http://localhost:8000/prices/$JOB_ID \
  -H "Authorization: Bearer <TOKEN>"
```

### Validation
- [ ] All 50 items have updated_price and final_price
- [ ] Formula verified: `final_price = base_price × Σ(W_i × Index_current/Index_base) × 1.04 × 1.08 × 1.10`
- [ ] Copper example: base 2,500,000 → final ~3,872,118 IRR/m (per spec example)
- [ ] Adjustments breakdown present in each item
- [ ] Index snapshot captured for audit
- [ ] Calculation completes < 60s for 50 items

---

## Scenario 4: Price Forecast & Scenario Analysis

### Prerequisites
- Completed Scenario 3
- Minimum 24 months market data per component
- Forecast models trained (run `/admin/models/retrain` if needed)

### Steps
```bash
# 1. Ensure models are trained
curl -X POST http://localhost:8000/admin/models/retrain \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"full_retrain": true}'

# Wait for completion (check /admin/models for status="active")

# 2. Generate forecasts
curl -X POST http://localhost:8000/forecasts/generate \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"job_id\": \"$JOB_ID\", \"horizons\": [1, 3, 6, 12], \"scenarios\": [\"optimistic\", \"base\", \"pessimistic\"]}"

# 3. Get forecast results
curl -X GET http://localhost:8000/forecasts/$JOB_ID \
  -H "Authorization: Bearer <TOKEN>"
```

### Validation
- [ ] 4 horizons × 3 scenarios = 12 forecasts per item
- [ ] Per-item results + chapter aggregates
- [ ] 80% confidence intervals (lower_bound ≤ predicted ≤ upper_bound)
- [ ] Scenarios ordered: optimistic < base < pessimistic (for price increases)
- [ ] Model type indicated (prophet/arima/lstm)
- [ ] Assumptions documented per scenario
- [ ] Chart data endpoint returns Recharts-compatible format
- [ ] Generation completes < 120s for 100 items

---

## Scenario 5: Defense Document Generation (سند دفاعیه)

### Prerequisites
- Completed Scenarios 1-4 (full analysis chain)
- Persian font installed (Vazirmatn) in container

### Steps
```bash
# 1. Generate defense document for full project
curl -X POST http://localhost:8000/exports/defense-doc \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"job_id\": \"$JOB_ID\", \"format\": \"pdf\"}"

# 2. Download PDF
DOWNLOAD_URL=<from_response>
curl -X GET "$DOWNLOAD_URL" -H "Authorization: Bearer <TOKEN>" -o defense_doc.pdf

# 3. Verify PDF content
# Open defense_doc.pdf and verify:
```

### Validation
- [ ] PDF opens correctly, RTL layout
- [ ] Cover page: project name, date, tender reference
- [ ] Per-item: description, weight table (7 components), index sources, formula, adjustments, final price
- [ ] Expert overrides shown with both AI and final weights + reason
- [ ] Chapter summary tables
- [ ] Forecast scenarios appendix
- [ ] Page headers/footers in Persian
- [ ] Generation completes < 15s for 50-item chapter

---

## Scenario 6: Excel Export

### Prerequisites
- Completed Scenarios 1-4

### Steps
```bash
# 1. Export full BoQ with weights and forecasts
curl -X POST http://localhost:8000/exports/excel \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"job_id\": \"$JOB_ID\", \"include_weights\": true, \"include_forecasts\": true, \"include_comparison\": true}"

# 2. Download Excel
DOWNLOAD_URL=<from_response>
curl -X GET "$DOWNLOAD_URL" -H "Authorization: Bearer <TOKEN>" -o updated_boq.xlsx

# 3. Open and verify
```

### Validation
- [ ] Excel opens without corruption
- [ ] Sheet 1: Updated BoQ (code, description, unit, base, updated, final, qty, total)
- [ ] Sheet 2: Weight breakdown (item × component matrix)
- [ ] Sheet 3: Price comparison (base vs updated vs final)
- [ ] Sheet 4: Forecast scenarios (per item × horizon × scenario)
- [ ] Sheet 5: Chapter summaries
- [ ] Persian text in RTL columns
- [ ] IRR formatting with digit grouping

---

## Scenario 7: Expert Weight Override

### Prerequisites
- Completed Scenario 2
- User with `reviewer` or `manager` role

### Steps
```bash
# 1. Override copper weight for item (e.g., increase from 0.70 to 0.75)
ITEM_ID=<item_id_from_weights>
COMPONENT_ID=<copper_component_id>
curl -X POST http://localhost:8000/weights/override \
  -H "Authorization: Bearer <REVIEWER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"item_id\": \"$ITEM_ID\", \"component_id\": \"$COMPONENT_ID\", \"new_weight\": 0.75, \"reason\": \"Higher copper content per manufacturer spec sheet\"}"

# 2. Verify override recorded
curl -X GET http://localhost:8000/weights/$JOB_ID \
  -H "Authorization: Bearer <TOKEN>" | jq '.items[] | select(.item_id=="'$ITEM_ID'") | .weights[] | select(.component_code=="copper")'

# Expected: source="expert", final_weight=0.75, overridden_by=<reviewer_id>, override_reason present

# 3. Recalculate prices (should use new weight)
curl -X POST http://localhost:8000/prices/recalculate \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"job_id\": \"$JOB_ID\"}"

# 4. Verify defense doc shows both weights
curl -X POST http://localhost:8000/exports/defense-doc \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d "{\"job_id\": \"$JOB_ID\", \"format\": \"pdf\"}"
```

### Validation
- [ ] Override accepted with mandatory reason (>10 chars)
- [ ] Weight source = "expert"
- [ ] Audit trail: old_weight, new_weight, user, timestamp, reason
- [ ] Recalculation uses overridden weight
- [ ] Defense doc shows "AI suggested: 70% → Expert final: 75% (reason: ...)"

---

## Scenario 8: Concurrent Load Test

### Prerequisites
- Locust installed: `pip install locust`
- 5 sample BoQ files

### Steps
```bash
# Run load test
locust -f tests/load/locustfile.py --host=http://localhost:8000 --users=5 --spawn-rate=1 --run-time=2m
```

### Validation
- [ ] 5 concurrent BoQ analyses complete without degradation
- [ ] All SC-008 metrics met
- [ ] No 5xx errors
- [ ] P95 latency within limits

---

## Scenario 9: ETL Failure & Recovery

### Prerequisites
- Running system with market data sources configured

### Steps
```bash
# 1. Simulate IME API failure (block network or invalid key)
# 2. Trigger ETL
curl -X POST http://localhost:8000/market-data/etl/trigger \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"source_ids": ["<ime_copper_source_id>"]}'

# 3. Check status
curl -X GET http://localhost:8000/market-data/freshness \
  -H "Authorization: Bearer <TOKEN>"

# Expected: IME Copper status="failed", error_message present, other sources OK

# 4. Fix issue, re-trigger
curl -X POST http://localhost:8000/market-data/etl/trigger \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"source_ids": ["<ime_copper_source_id>"], "force": true}'

# 5. Verify recovery
curl -X GET http://localhost:8000/market-data/freshness \
  -H "Authorization: Bearer <TOKEN>"

# Expected: status="success", last_success_at updated
```

### Validation
- [ ] Failed source doesn't block others
- [ ] Warning banner in UI shows failed source
- [ ] Last successful data used for calculations
- [ ] Manual re-trigger works
- [ ] Audit log captures failure and recovery

---

## Expected Outcomes Summary

| Scenario | Key Metric | Pass Criteria |
|----------|------------|---------------|
| 1. Upload | Parse time | < 30s for 500 items |
| 2. Weights | Attribution time | < 10s/item |
| 3. Prices | Recalculation time | < 60s for 500 items |
| 4. Forecast | Generation time | < 120s for 100 items |
| 5. Defense Doc | Generation time | < 15s for 50 items |
| 6. Export | File integrity | Opens without errors |
| 7. Override | Audit completeness | 100% fields captured |
| 8. Load | Concurrency | 5 BoQs no degradation |
| 9. ETL Failure | Isolation | Other sources unaffected |

---

## Troubleshooting

| Issue | Resolution |
|-------|------------|
| OpenRouter 429 | Check rate limits, increase backoff, use free models for dev |
| Persian text garbled | Ensure DB `client_encoding = 'UTF8'`, font installed |
| ML regression fails | Check min 24 months data, reduce features, increase regularization |
| Prophet install fails | Install system deps: `pystan`, `cmdstanpy` |
| PDF generation fails | Verify WeasyPrint deps: `pango`, `cairo`, `gdk-pixbuf` |
| ETL timeout | Increase Celery task timeout, check API rate limits |

---

## Next Steps

After validation:
1. Run `/speckit.tasks` to generate implementation tasks
2. Begin Phase 2: Implementation (TDD)
3. Set up CI/CD with test gates
4. Configure production Kubernetes manifests
5. Conduct security review (penetration test per SC-012)