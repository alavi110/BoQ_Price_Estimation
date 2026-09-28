"""
Pytest configuration and fixtures
"""
import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.core.database import Base


@pytest.fixture(scope="session")
def event_loop_policy():
    """Session-wide event loop policy for the async test suite."""
    return asyncio.get_event_loop_policy()


@pytest_asyncio.fixture
async def test_engine():
    """
    In-memory SQLite engine with the full schema created.

    Function-scoped, not session-scoped, and that matters: components are a
    global catalogue (``components.code`` is unique) and several tests rewrite
    their market-index readings to simulate bad data. With a shared database
    those committed rows survive the per-test rollback and leak into the next
    test, so a suite that passes test-by-test fails as a whole - and any future
    reordering makes it fail differently. One database per test is the only
    isolation that is actually isolation. The cost is ~1.7s for 36 tests.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Transaction-scoped session; rolled back after every test."""
    factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest.fixture
def override_get_db(db_session):
    """Swap the FastAPI ``get_db`` dependency for the test session."""
    from src.core.database import get_db
    from src.main import app

    async def _get_db():
        yield db_session

    app.dependency_overrides[get_db] = _get_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def reset_audit_stores():
    """
    Clear the process-wide in-memory audit trails between tests.

    These services stand in for tables, so without this each test would see
    entries recorded by the ones before it.
    """
    from src.services.commercial_audit import CommercialAuditService

    CommercialAuditService.reset()
    yield
    CommercialAuditService.reset()


@pytest.fixture
def sample_boq_item():
    """Sample BoQ item data for tests"""
    return {
        "code": "101001",
        "description_fa": "بتنی ساده آرمه",
        "description_en": "Plain Concrete",
        "unit": "متر مکعب",
        "base_price": 5000000,
        "quantity": 10,
        "chapter_code": "101",
    }


@pytest.fixture
def sample_components():
    """Sample component data"""
    return [
        {"code": "copper", "name_fa": "مسی", "name_en": "Copper", "category": "material", "unit": "kg"},
        {"code": "steel", "name_fa": "فولاد", "name_en": "Steel", "category": "material", "unit": "kg"},
        {"code": "cement", "name_fa": "سیمان", "name_en": "Cement", "category": "material", "unit": "kg"},
        {"code": "polymer", "name_fa": "پلیمر", "name_en": "Polymer", "category": "material", "unit": "kg"},
        {"code": "energy", "name_fa": "انرژی", "name_en": "Energy", "category": "energy", "unit": "kwh"},
        {"code": "labor", "name_fa": "کار", "name_en": "Labor", "category": "labor", "unit": "man_day"},
        {"code": "overhead", "name_fa": "مصارف عمومی", "name_en": "Overhead", "category": "overhead", "unit": "percent"},
    ]


# --------------------------------------------------------------------------- #
# Defense document (US6) fixtures
# --------------------------------------------------------------------------- #
#: Weights that sum to exactly 1.0, one per component in ``COMPONENT_ORDER``
DEFENSE_WEIGHTS = {
    "copper": 0.30,
    "steel": 0.25,
    "cement": 0.10,
    "polymer": 0.10,
    "energy": 0.10,
    "labor": 0.10,
    "overhead": 0.05,
}
#: Base / current index values per component
DEFENSE_INDEXES = {
    "copper": (100.0, 110.0),
    "steel": (200.0, 190.0),
    "cement": (300.0, 330.0),
    "polymer": (400.0, 420.0),
    "energy": (500.0, 540.0),
    "labor": (600.0, 640.0),
    "overhead": (700.0, 700.0),
}
#: The three commercial multipliers, in application order
DEFENSE_MULTIPLIERS = {"risk_buffer": 1.04, "payment_terms": 1.08, "profit_margin": 1.10}


def make_defense_weight_rows(override_copper: bool = False) -> list:
    """
    Build a full seven-component weight table for one item.

    With ``override_copper`` the expert lowers copper from 0.30 to 0.20 and
    raises overhead from 0.05 to 0.15, so the table still sums to 1.0 - which is
    what the export validator insists on.
    """
    rows = []
    for code, weight in DEFENSE_WEIGHTS.items():
        base_index, current_index = DEFENSE_INDEXES[code]
        overridden = override_copper and code == "copper"
        if overridden:
            weight = 0.20
        elif override_copper and code == "overhead":
            weight = 0.15
        rows.append(
            {
                "component_code": code,
                "final_weight": weight,
                # Pre-override machine suggestion. On an override this is a
                # fused value that differs from what the expert settled on, so
                # the document has something real to show in the "AI
                # suggested" column.
                "llm_weight": 0.26 if overridden else weight,
                "ml_weight": 0.22 if overridden else weight,
                "source": "expert" if overridden else "fusion",
                "confidence": 0.9,
                "overridden": overridden,
                "override_reason": (
                    "قیمت مس در بازار جهانی افزایش یافت" if overridden else None
                ),
                "overridden_by": "کارشناس ارشد" if overridden else None,
                "index_name": f"{code}_index",
                "index_base": base_index,
                "index_current": current_index,
                "index_source_url": f"https://example.org/{code}",
                "index_base_date": "2026-03-01",
                "index_current_date": "2026-09-01",
            }
        )
    return rows


def make_defense_item_row(index: int, chapter: str = "1", override: bool = False) -> dict:
    """Build one complete item row: weights, adjustments and forecasts."""
    base_price = 1_000_000.0
    weights = make_defense_weight_rows(override_copper=override)
    row = {
        "item_id": f"item-{index}",
        "code": f"{chapter}-{index:03d}",
        "description_fa": f"ردیف نمونه شماره {index}",
        "description_en": f"Sample item {index}",
        "unit": "kg",
        "quantity": 10.0,
        "chapter_code": chapter,
        "chapter_name_fa": "فصل اول",
        "base_price": base_price,
        "weights": weights,
        "adjustments": [],
        "forecasts": [],
    }
    return recompute_defense_prices(row)


def recompute_defense_prices(row: dict) -> dict:
    """
    Re-derive an item row's index-adjusted price, adjustment ladder and total.

    This mirrors what the price pipeline does once the weights or indexes move:
    the index adjustment comes from the published formula and the ladder is
    produced by the real :class:`CommercialAdjustmentsService`. Tests that move
    an input call this so the row stays internally consistent, exactly as a live
    recalculation would.
    """
    from decimal import Decimal

    from src.services.commercial_adjustments import CommercialAdjustmentsService

    ratio_sum = sum(
        w["final_weight"] * (w["index_current"] / w["index_base"])
        for w in row["weights"]
    )
    adjusted = row["base_price"] * ratio_sum
    row["index_adjusted_price"] = adjusted

    service = CommercialAdjustmentsService(
        risk_buffer=Decimal(str(DEFENSE_MULTIPLIERS["risk_buffer"])),
        payment_terms=Decimal(str(DEFENSE_MULTIPLIERS["payment_terms"])),
        profit_margin=Decimal(str(DEFENSE_MULTIPLIERS["profit_margin"])),
    )
    result = service.apply_adjustments(Decimal(str(adjusted)))

    row["adjustments"] = [
        {
            "step": step.step,
            "multiplier": float(step.multiplier),
            "input_price": float(step.input_price),
            "output_price": float(step.output_price),
            "overridden": step.overridden,
            "override_reason": step.override_reason,
        }
        for step in result.adjustment_steps
    ]
    row["final_price"] = float(result.final_price)
    row["total_price"] = float(result.final_price) * row["quantity"]

    base = row["final_price"]
    row["forecasts"] = [
        {
            "horizon_months": 3,
            "scenario": "base",
            "predicted_price": base * 1.05,
            "lower_bound": base * 0.99,
            "upper_bound": base * 1.11,
            "change_pct": 5.0,
            "model_type": "prophet",
        },
        {
            "horizon_months": 3,
            "scenario": "optimistic",
            "predicted_price": base * 0.97,
            "model_type": "prophet",
        },
        {
            "horizon_months": 6,
            "scenario": "pessimistic",
            "predicted_price": base * 1.18,
            "model_type": "prophet",
        },
    ]
    return row


@pytest.fixture
def defense_project():
    """Project metadata for the defense document cover page."""
    from datetime import date, datetime

    return {
        "project_name": "پروژه نمونه ساختمانی",
        "project_code": "PRJ-2026-01",
        "tender_reference": "مناقصه ۱۴۰۵/۷/۲۲۱۴",
        "contractor_name": "پیمانکار نمونه",
        "document_date": date(2026, 9, 27),
        "generated_at": datetime(2026, 9, 27, 11, 30),
    }


@pytest.fixture
def defense_item_rows():
    """Two items in one chapter, the second carrying an expert override."""
    return [
        make_defense_item_row(1, chapter="1", override=False),
        make_defense_item_row(2, chapter="1", override=True),
    ]
