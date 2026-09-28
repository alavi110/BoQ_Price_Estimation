# BoQ Price Forecast

AI-powered application for automated adjustment and forecasting of construction/electrical Bill of Quantities (BoQ) unit prices based on real-time market data.

## Features

- **BoQ Upload & Parsing**: Parse Excel files with Persian text support, automatic chapter detection
- **AI Weight Attribution**: Hybrid LLM + ML approach for cost component weight analysis
- **Price Recalculation**: Real-time price updates using market indices
- **Commercial Adjustments**: Risk buffer, payment terms, profit margin calculations
- **Price Forecasting**: Time-series forecasting with Prophet, ARIMA, LSTM models
- **Defense Document Generation**: Formal justification documents (سند دفاعیه) for tender compliance
- **Dashboard & Export**: React/Next.js dashboard with visualizations and Excel/PDF export

## Architecture

```
backend/          # FastAPI backend with Python 3.11+
├── src/
│   ├── models/       # SQLAlchemy models
│   ├── schemas/      # Pydantic schemas
│   ├── services/     # Business logic services
│   ├── api/          # API routes
│   ├── core/         # Core configuration
│   ├── tasks/        # Celery tasks
│   └── cli/          # CLI commands
├── tests/            # Test suite
└── alembic/          # Database migrations

frontend/         # Next.js 14 frontend
├── src/
│   ├── app/          # App Router pages
│   ├── components/   # React components
│   └── lib/          # Utilities

infrastructure/   # Docker, Kubernetes, monitoring
```

## Quick Start

### Prerequisites

- Docker 24+ and Docker Compose 2+
- 8GB RAM minimum (16GB recommended)
- 20GB free disk space
- OpenRouter API key

### Development Setup

```bash
# Clone repository
git clone <repo-url>
cd BoQ_Price_Estimation

# Copy environment template
cp .env.example .env

# Edit .env with your keys
# Required: OPENROUTER_API_KEY, DATABASE_URL, REDIS_URL, JWT_SECRET

# Start infrastructure
docker-compose -f infrastructure/docker-compose.yml up -d postgres redis

# Run migrations
docker-compose -f infrastructure/docker-compose.yml run --rm backend alembic upgrade head

# Seed reference data
docker-compose -f infrastructure/docker-compose.yml run --rm backend python -m scripts.seed_data

# Start all services
docker-compose -f infrastructure/docker-compose.yml up -d
```

### Access Points

- **Backend API**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs
- **Frontend Dashboard**: http://localhost:3000
- **Health Check**: http://localhost:8000/health

## Technology Stack

### Backend
- **Framework**: FastAPI 0.109+
- **Database**: PostgreSQL 15+ with SQLAlchemy 2.0
- **Cache/Queue**: Redis 7+ with Celery
- **ML/AI**: scikit-learn, XGBoost, Prophet, PyTorch, OpenRouter SDK
- **Auth**: JWT with SSO/LDAP integration
- **Documentation**: Auto-generated OpenAPI 3.1

### Frontend
- **Framework**: Next.js 14 (App Router) with React 18
- **Styling**: Tailwind CSS + Shadcn/UI
- **Charts**: Recharts
- **State**: TanStack Query
- **Forms**: React Hook Form

### Infrastructure
- **Development**: Docker Compose
- **Production**: Kubernetes
- **Monitoring**: Prometheus + Grafana
- **Reverse Proxy**: Nginx

## API Endpoints

### BoQ Management
- `POST /boq/upload` - Upload and parse BoQ Excel
- `GET /boq/{job_id}/preview` - Get parsed BoQ preview
- `GET /boq/{job_id}/items` - List BoQ items

### Weight Attribution
- `POST /weights/analyze` - Trigger AI weight analysis
- `GET /weights/{job_id}` - Get weight breakdown
- `POST /weights/override` - Expert weight override

### Price Calculation
- `POST /prices/recalculate` - Trigger price recalculation
- `GET /prices/{job_id}` - Get calculated prices
- `GET /prices/{job_id}/comparison` - Get price comparison

### Forecasting
- `POST /forecasts/generate` - Generate forecasts
- `GET /forecasts/{job_id}` - Get forecast results
- `GET /forecasts/{job_id}/chart-data` - Get chart data

### Exports
- `POST /exports/excel` - Export to Excel
- `POST /exports/defense-doc` - Generate defense document

### Market Data
- `GET /market-data/freshness` - Get market data freshness
- `POST /market-data/etl/trigger` - Trigger ETL

### Administration
- `POST /admin/models/retrain` - Retrain forecast models
- `GET /admin/projects` - List projects
- `POST /admin/projects` - Create project

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+psycopg2://boq_user:boq_password@localhost:5432/boq_price_forecast` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `OPENROUTER_API_KEY` | OpenRouter API key | Required |
| `JWT_SECRET_KEY` | JWT signing secret | Required |
| `ENVIRONMENT` | Environment (development/production) | `development` |
| `LOG_LEVEL` | Logging level | `DEBUG` |

### Commercial Adjustments (per project)
- Risk Buffer: 1.04 (4%)
- Payment Terms: 1.08 (8%)
- Profit Margin: 1.10 (10%)

### Weight Fusion
- Default ML weight (alpha): 0.70 (70% ML / 30% LLM)

## Testing

```bash
# Run backend tests
docker-compose -f infrastructure/docker-compose.yml run --rm backend pytest

# Run frontend tests
docker-compose -f infrastructure/docker-compose.yml run --rm frontend npm test

# Run integration tests
docker-compose -f infrastructure/docker-compose.yml run --rm backend pytest tests/integration/

# Run with coverage
docker-compose -f infrastructure/docker-compose.yml run --rm backend pytest --cov=src --cov-report=html
```

## Deployment

### Production (Kubernetes)

```bash
# Apply Kubernetes manifests
kubectl apply -f infrastructure/kubernetes/overlays/prod/
```

### Environment-Specific Configs

- `infrastructure/kubernetes/overlays/dev/` - Development
- `infrastructure/kubernetes/overlays/prod/` - Production

## Security

- JWT-based authentication with SSO/LDAP integration
- Role-based access control (estimator, reviewer, manager, admin)
- Field-level audit trail for all changes
- Encryption at rest (AES-256) and in transit (TLS 1.3)
- Rate limiting and API throttling

## Localization

- Persian (Farsi) RTL text support throughout
- Solar Hijri calendar display (Jalali dates)
- Iranian market data sources (IME, Central Bank, Energy)
- Vazirmatn font for Persian rendering

## License

Proprietary - All rights reserved.

## Support

For issues and feature requests, please contact the development team.