"""
Seed reference data for BoQ Price Forecast
"""
import asyncio
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from src.core.config import settings
from src.models import Base
from src.models.component import Component
from src.models.user import User
from src.core.security import get_password_hash


async def seed_components(db: AsyncSession):
    """Seed the 7 core components"""
    components = [
        Component(
            id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            code="copper",
            name_fa="مسی",
            name_en="Copper",
            category="material",
            unit="kg",
            conversion_factor=1.0,
            base_unit="kg",
            display_order=1,
        ),
        Component(
            id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            code="steel",
            name_fa="فولاد",
            name_en="Steel",
            category="material",
            unit="kg",
            conversion_factor=1.0,
            base_unit="kg",
            display_order=2,
        ),
        Component(
            id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
            code="cement",
            name_fa="سیمان",
            name_en="Cement",
            category="material",
            unit="kg",
            conversion_factor=1.0,
            base_unit="kg",
            display_order=3,
        ),
        Component(
            id=uuid.UUID("44444444-4444-4444-4444-444444444444"),
            code="polymer",
            name_fa="پلیمر",
            name_en="Polymer",
            category="material",
            unit="kg",
            conversion_factor=1.0,
            base_unit="kg",
            display_order=4,
        ),
        Component(
            id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
            code="energy",
            name_fa="انرژی",
            name_en="Energy",
            category="energy",
            unit="kwh",
            conversion_factor=1.0,
            base_unit="kwh",
            display_order=5,
        ),
        Component(
            id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
            code="labor",
            name_fa="کار",
            name_en="Labor",
            category="labor",
            unit="man_day",
            conversion_factor=1.0,
            base_unit="man_day",
            display_order=6,
        ),
        Component(
            id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
            code="overhead",
            name_fa="مصارف عمومی",
            name_en="Overhead",
            category="overhead",
            unit="percent",
            conversion_factor=1.0,
            base_unit="percent",
            display_order=7,
        ),
    ]
    
    for comp in components:
        existing = await db.get(Component, comp.id)
        if not existing:
            db.add(comp)
    
    await db.commit()
    print("✓ Components seeded")


async def seed_admin_user(db: AsyncSession):
    """Seed default admin user"""
    admin = User(
        id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        sso_id="admin",
        email="admin@boqprice.local",
        full_name="System Administrator",
        role="admin",
        department="IT",
        is_active=True,
    )
    
    existing = await db.get(User, admin.id)
    if not existing:
        db.add(admin)
        await db.commit()
        print("✓ Admin user seeded")


async def main():
    """Main seeding function"""
    engine = create_async_engine(
        settings.DATABASE_URL.replace("postgresql+psycopg2", "postgresql+asyncpg"),
        echo=True,
    )
    
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with async_session() as db:
        await seed_components(db)
        await seed_admin_user(db)
    
    await engine.dispose()
    print("✓ All seed data completed")


if __name__ == "__main__":
    asyncio.run(main())