# Data Model: BoQ Price Forecast

## Entity Relationship Diagram (ERD)

```
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│    Project      │──────<│     BoQItem     │──────<│     Weight      │
└─────────────────┘       └─────────────────┘       └────────┬────────┘
                                                             │
                                                             │
                    ┌─────────────────┐       ┌──────────────┴──────────┐
                    │   Component     │       │                         │
                    └────────┬────────┘       │                         │
                             │                ▼                         ▼
                    ┌────────┴────────┐ ┌───────────┐           ┌───────────────┐
                    │   MarketIndex   │ │PriceCalc  │           │  WeightAudit  │
                    └─────────────────┘ └─────┬─────┘           └───────────────┘
                                             │
                                             ▼
                                    ┌───────────────┐
                                    │   Forecast    │
                                    └───────┬───────┘
                                            │
                                            ▼
                                    ┌───────────────┐
                                    │ ForecastModel │
                                    └───────────────┘

┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│  MarketDataSrc  │──────<│   ETLRunLog     │       │   AuditLog      │
└─────────────────┘       └─────────────────┘       └─────────────────┘
         │                                               ▲
         │                                               │
         ▼                                               │
┌─────────────────┐                                     │
│  User (SSO)     │─────────────────────────────────────┘
└─────────────────┘
```

---

## Table Definitions

### 1. projects
```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    client_name VARCHAR(255),
    tender_date DATE,
    base_date DATE NOT NULL,              -- Index base date for calculations
    risk_buffer NUMERIC(5,4) DEFAULT 1.04,
    payment_terms NUMERIC(5,4) DEFAULT 1.08,
    profit_margin NUMERIC(5,4) DEFAULT 1.10,
    fusion_alpha NUMERIC(3,2) DEFAULT 0.70,  -- ML weight in fusion
    status VARCHAR(20) DEFAULT 'draft',    -- draft, active, archived
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_projects_status ON projects(status);
CREATE INDEX idx_projects_tender_date ON projects(tender_date);
```

### 2. users (SSO-synced)
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sso_id VARCHAR(255) UNIQUE NOT NULL,    -- Keycloak/Azure AD subject
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255),
    role VARCHAR(20) NOT NULL,              -- estimator, reviewer, manager, admin
    department VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_users_sso_id ON users(sso_id);
CREATE INDEX idx_users_role ON users(role);
```

### 3. chapters
```sql
CREATE TABLE chapters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    code CHAR(3) NOT NULL,                  -- First 3 digits of item code
    name VARCHAR(255) NOT NULL,             -- Persian name
    description TEXT,
    sort_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (project_id, code)
);

-- Indexes
CREATE INDEX idx_chapters_project ON chapters(project_id);
```

### 4. components (Reference data - seeded)
```sql
CREATE TABLE components (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(50) UNIQUE NOT NULL,       -- copper, steel, cement, polymer, energy, labor, overhead
    name_fa VARCHAR(255) NOT NULL,          -- Persian name
    name_en VARCHAR(255) NOT NULL,
    category VARCHAR(50) NOT NULL,          -- material, energy, labor, overhead
    unit VARCHAR(20) NOT NULL,              -- kg, m, m2, m3, kwh, man_day
    conversion_factor NUMERIC(15,6) DEFAULT 1.0,  -- To base unit
    base_unit VARCHAR(20) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    display_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed data (7 core components)
INSERT INTO components (code, name_fa, name_en, category, unit, base_unit) VALUES
('copper', 'مسی', 'Copper', 'material', 'kg', 'kg'),
('steel', 'فولاد', 'Steel', 'material', 'kg', 'kg'),
('cement', 'سیمان', 'Cement', 'material', 'kg', 'kg'),
('polymer', 'پلیمر', 'Polymer', 'material', 'kg', 'kg'),
('energy', 'انرژی', 'Energy', 'energy', 'kwh', 'kwh'),
('labor', 'کار', 'Labor', 'labor', 'man_day', 'man_day'),
('overhead', 'مصارف عمومی', 'Overhead', 'overhead', 'percent', 'percent');
```

### 5. boq_items
```sql
CREATE TABLE boq_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_id UUID NOT NULL REFERENCES chapters(id) ON DELETE RESTRICT,
    code VARCHAR(50) NOT NULL,              -- Full item code (e.g., 101001)
    description_fa TEXT NOT NULL,           -- Persian description
    description_en TEXT,                    -- Optional English
    unit VARCHAR(20) NOT NULL,              -- m, kg, m2, m3, each
    base_price NUMERIC(20,2) NOT NULL,      -- IRR per unit
    quantity NUMERIC(15,4) DEFAULT 1,
    category VARCHAR(100),                  -- LLM-classified category
    llm_confidence NUMERIC(3,2),            -- LLM classification confidence
    status VARCHAR(20) DEFAULT 'pending',   -- pending, analyzed, priced, forecasted, exported
    row_number INT,                         -- Original Excel row
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (project_id, code)
);

-- Indexes
CREATE INDEX idx_boq_items_project ON boq_items(project_id);
CREATE INDEX idx_boq_items_chapter ON boq_items(chapter_id);
CREATE INDEX idx_boq_items_status ON boq_items(status);
```

### 6. weights
```sql
CREATE TABLE weights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    boq_item_id UUID NOT NULL REFERENCES boq_items(id) ON DELETE CASCADE,
    component_id UUID NOT NULL REFERENCES components(id) ON DELETE RESTRICT,
    llm_weight NUMERIC(5,4),                -- LLM-suggested weight (0-1)
    ml_weight NUMERIC(5,4),                 -- ML-derived weight (0-1)
    final_weight NUMERIC(5,4) NOT NULL,     -- Fused/overridden weight (0-1)
    source VARCHAR(20) NOT NULL,            -- llm, ml, fusion, expert
    confidence NUMERIC(3,2),                -- Fusion confidence score
    ml_r2 NUMERIC(3,4),                     -- ML model R² for this item
    llm_certainty NUMERIC(3,2),             -- LLM self-reported certainty
    overridden_by UUID REFERENCES users(id),
    overridden_at TIMESTAMPTZ,
    override_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (boq_item_id, component_id)
);

-- Constraints
ALTER TABLE weights ADD CONSTRAINT chk_weights_sum
    -- Enforced at application level: SUM(final_weight) = 1.0 per boq_item_id
    -- Checked via trigger or application validation
    CHECK (final_weight >= 0 AND final_weight <= 1);

-- Indexes
CREATE INDEX idx_weights_boq_item ON weights(boq_item_id);
CREATE INDEX idx_weights_source ON weights(source);
```

### 7. market_indices
```sql
CREATE TABLE market_indices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component_id UUID NOT NULL REFERENCES components(id) ON DELETE RESTRICT,
    source_id UUID NOT NULL REFERENCES market_data_sources(id) ON DELETE RESTRICT,
    date DATE NOT NULL,                     -- Gregorian date
    value NUMERIC(20,6) NOT NULL,           -- Index value or price
    frequency VARCHAR(10) NOT NULL,         -- daily, monthly, quarterly
    is_interpolated BOOLEAN DEFAULT FALSE,  -- For missing days
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (component_id, source_id, date)
);

-- Indexes
CREATE INDEX idx_market_indices_component_date ON market_indices(component_id, date DESC);
CREATE INDEX idx_market_indices_source_date ON market_indices(source_id, date DESC);
```

### 8. market_data_sources
```sql
CREATE TABLE market_data_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,             -- IME Copper, IME Steel, Central Bank FX, Energy Tariff
    component_id UUID NOT NULL REFERENCES components(id) ON DELETE RESTRICT,
    api_endpoint VARCHAR(500),
    auth_method VARCHAR(50),                -- api_key, bearer, basic, none, portal
    auth_config JSONB,                      -- Encrypted credentials reference
    schedule_cron VARCHAR(100) DEFAULT '0 2 * * *',  -- 02:00 Tehran
    timezone VARCHAR(50) DEFAULT 'Asia/Tehran',
    last_run_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'unknown',   -- success, failed, stale, unknown
    error_message TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_market_data_sources_component ON market_data_sources(component_id);
CREATE INDEX idx_market_data_sources_status ON market_data_sources(status);
```

### 9. etl_run_logs
```sql
CREATE TABLE etl_run_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES market_data_sources(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL,            -- running, success, failed, partial
    records_processed INT DEFAULT 0,
    records_inserted INT DEFAULT 0,
    records_updated INT DEFAULT 0,
    records_failed INT DEFAULT 0,
    error_details JSONB,
    triggered_by VARCHAR(20) DEFAULT 'schedule',  -- schedule, manual, retry
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_etl_run_logs_source ON etl_run_logs(source_id, started_at DESC);
```

### 10. price_calculations
```sql
CREATE TABLE price_calculations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    boq_item_id UUID NOT NULL REFERENCES boq_items(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    base_price NUMERIC(20,2) NOT NULL,
    updated_price NUMERIC(20,2) NOT NULL,   -- After index adjustment
    final_price NUMERIC(20,2) NOT NULL,     -- After commercial adjustments
    risk_buffer_applied NUMERIC(5,4),
    payment_terms_applied NUMERIC(5,4),
    profit_margin_applied NUMERIC(5,4),
    adjustments_json JSONB NOT NULL,        -- Full breakdown for audit
    index_snapshot JSONB NOT NULL,          -- Indices used at calculation time
    calculated_by UUID REFERENCES users(id),
    calculated_at TIMESTAMPTZ DEFAULT NOW(),
    is_current BOOLEAN DEFAULT TRUE         -- For versioning
);

-- Indexes
CREATE INDEX idx_price_calculations_boq_item ON price_calculations(boq_item_id);
CREATE INDEX idx_price_calculations_project ON price_calculations(project_id);
CREATE INDEX idx_price_calculations_current ON price_calculations(is_current) WHERE is_current = TRUE;
```

### 11. forecast_models
```sql
CREATE TABLE forecast_models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    component_id UUID NOT NULL REFERENCES components(id) ON DELETE RESTRICT,
    model_type VARCHAR(20) NOT NULL,        -- prophet, arima, lstm
    hyperparameters JSONB NOT NULL,         -- Model-specific config
    training_start_date DATE NOT NULL,
    training_end_date DATE NOT NULL,
    mae NUMERIC(10,4),
    rmse NUMERIC(10,4),
    r2 NUMERIC(5,4),
    mape NUMERIC(6,2),                      -- Mean Absolute Percentage Error
    version INT DEFAULT 1,
    status VARCHAR(20) DEFAULT 'training',  -- training, active, archived, failed
    trained_at TIMESTAMPTZ,
    trained_by UUID REFERENCES users(id),
    model_artifact_path VARCHAR(500),       -- Path to pickled model
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_forecast_models_component ON forecast_models(component_id, status);
CREATE INDEX idx_forecast_models_status ON forecast_models(status);
```

### 12. forecasts
```sql
CREATE TABLE forecasts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    boq_item_id UUID NOT NULL REFERENCES boq_items(id) ON DELETE CASCADE,
    component_id UUID NOT NULL REFERENCES components(id) ON DELETE RESTRICT,
    model_id UUID NOT NULL REFERENCES forecast_models(id) ON DELETE RESTRICT,
    horizon_months INT NOT NULL,            -- 1, 3, 6, 12
    scenario VARCHAR(20) NOT NULL,          -- optimistic, base, pessimistic
    forecast_date DATE NOT NULL,            -- Target date
    predicted_price NUMERIC(20,2) NOT NULL, -- IRR per unit
    lower_bound NUMERIC(20,2),              -- 80% CI lower
    upper_bound NUMERIC(20,2),              -- 80% CI upper
    assumptions_json JSONB,                 -- Key assumptions for this forecast
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (boq_item_id, component_id, horizon_months, scenario, forecast_date)
);

-- Indexes
CREATE INDEX idx_forecasts_boq_item ON forecasts(boq_item_id);
CREATE INDEX idx_forecasts_horizon_scenario ON forecasts(horizon_months, scenario);
```

### 13. defense_documents
```sql
CREATE TABLE defense_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    boq_item_id UUID REFERENCES boq_items(id) ON DELETE SET NULL,  -- Null = chapter-level
    chapter_id UUID REFERENCES chapters(id) ON DELETE SET NULL,
    title VARCHAR(255) NOT NULL,
    content_json JSONB NOT NULL,            -- Structured content for PDF generation
    pdf_path VARCHAR(500),                  -- Generated PDF path
    generated_by UUID REFERENCES users(id),
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    version INT DEFAULT 1
);

-- Indexes
CREATE INDEX idx_defense_documents_project ON defense_documents(project_id);
CREATE INDEX idx_defense_documents_item ON defense_documents(boq_item_id);
```

### 14. audit_logs (Immutable)
```sql
CREATE TABLE audit_logs (
    id BIGSERIAL PRIMARY KEY,
    entity_type VARCHAR(50) NOT NULL,       -- boq_item, weight, price_calculation, forecast, etc.
    entity_id UUID NOT NULL,
    field_name VARCHAR(100) NOT NULL,
    old_value JSONB,
    new_value JSONB,
    action VARCHAR(20) NOT NULL,            -- insert, update, delete, override
    user_id UUID REFERENCES users(id),
    ip_address INET,
    user_agent TEXT,
    request_id UUID,                        -- Correlation ID
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_audit_logs_entity ON audit_logs(entity_type, entity_id, created_at DESC);
CREATE INDEX idx_audit_logs_user ON audit_logs(user_id, created_at DESC);
CREATE INDEX idx_audit_logs_request ON audit_logs(request_id);

-- No DELETE, No UPDATE policy enforced by RLS or application
```

---

## Validation Rules (from Functional Requirements)

| Entity | Rule | Implementation |
|--------|------|----------------|
| BoQItem | `code` unique per project | DB unique constraint |
| BoQItem | `base_price` > 0 | Check constraint + Pydantic |
| Weight | `final_weight` ∈ [0,1] | Check constraint |
| Weight | Σ `final_weight` = 1.0 per BoQItem | Application validation + trigger |
| Weight | `source` ∈ {llm, ml, fusion, expert} | Check constraint |
| MarketIndex | `date` not future | Application validation |
| MarketIndex | `value` > 0 | Check constraint |
| PriceCalculation | `final_price` = `updated_price` × adjustments | Computed column or trigger |
| Forecast | `lower_bound` ≤ `predicted_price` ≤ `upper_bound` | Check constraint |
| ForecastModel | `status` ∈ {training, active, archived, failed} | Check constraint |
| Project | `fusion_alpha` ∈ [0,1] | Check constraint |
| User | `role` ∈ {estimator, reviewer, manager, admin} | Check constraint |

---

## State Transitions

### BoQItem.status
```
pending → analyzed → priced → forecasted → exported
                ↘ (re-analyze)     ↘ (re-price)     ↘ (re-export)
```

### Weight.source
```
llm → fusion → expert
ml  ↗
```
Expert override: any → expert (with reason, user, timestamp)

### ForecastModel.status
```
training → active → archived
     ↘ failed
```

### MarketDataSource.status
```
unknown → success → stale (if >24h since last_success)
     ↘ failed
```

### MarketIndex.is_interpolated
```
FALSE (actual) / TRUE (interpolated for missing trading days)
```

---

## Relationships Summary

| Parent | Child | Cardinality | Cascade |
|--------|-------|-------------|---------|
| Project | BoQItem | 1:N | CASCADE |
| Project | Chapter | 1:N | CASCADE |
| Project | PriceCalculation | 1:N | CASCADE |
| Project | DefenseDocument | 1:N | CASCADE |
| Chapter | BoQItem | 1:N | RESTRICT |
| BoQItem | Weight | 1:N | CASCADE |
| BoQItem | PriceCalculation | 1:N | CASCADE |
| BoQItem | Forecast | 1:N | CASCADE |
| BoQItem | DefenseDocument | 1:N | SET NULL |
| Component | Weight | 1:N | RESTRICT |
| Component | MarketIndex | 1:N | RESTRICT |
| Component | ForecastModel | 1:N | RESTRICT |
| Component | Forecast | 1:N | RESTRICT |
| MarketDataSource | MarketIndex | 1:N | RESTRICT |
| MarketDataSource | ETLRunLog | 1:N | CASCADE |
| ForecastModel | Forecast | 1:N | RESTRICT |
| User | (all audit fields) | 1:N | SET NULL |