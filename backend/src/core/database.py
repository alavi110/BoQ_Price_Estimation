"""
Database configuration and session management

The engine is created lazily so that importing this module (and therefore
collecting tests) never requires a reachable database or an installed async
driver.
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.core.config import settings


class Base(DeclarativeBase):
    pass


def _async_url(url: str) -> str:
    """
    Normalise a sync SQLAlchemy URL to its async driver.

    ``psycopg2`` -> ``asyncpg``; SQLite -> ``aiosqlite``. The asyncpg fallback
    is what production uses, so it is the documented default.
    """
    if url.startswith("postgresql+psycopg2"):
        return url.replace("postgresql+psycopg2", "postgresql+asyncpg", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite://") and "+aiosqlite" not in url:
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


_engine = None
_session_factory: Optional[async_sessionmaker] = None


def get_engine():
    """Create the async engine on first use."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _async_url(settings.DATABASE_URL),
            pool_size=settings.DATABASE_POOL_SIZE,
            max_overflow=settings.DATABASE_MAX_OVERFLOW,
            pool_pre_ping=True,
            echo=settings.DEBUG,
        )
    return _engine


def get_session_factory() -> async_sessionmaker:
    """Session factory bound to the lazily-created engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


def configure(url: str) -> None:
    """
    Point the engine at a different URL and drop the cached engine.

    Used by the test fixtures and by the CLI, which may target SQLite.
    """
    global _engine, _session_factory
    settings.DATABASE_URL = url
    _engine = None
    _session_factory = None


#: Eagerly-resolved handles kept for call-site compatibility. These are
#: ``None`` until :func:`get_engine` / :func:`get_session_factory` is called.
engine = None
AsyncSessionLocal = None


def __getattr__(name: str):
    """Resolve ``database.engine`` / ``database.AsyncSessionLocal`` on access."""
    if name == "engine":
        return get_engine()
    if name == "AsyncSessionLocal":
        return get_session_factory()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def init_db() -> None:
    """Initialize database tables"""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database connections"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI to get database session"""
    async with get_session_factory()() as session:
        try:
            yield session
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for database session outside of FastAPI"""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
