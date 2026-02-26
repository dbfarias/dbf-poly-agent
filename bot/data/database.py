"""Async SQLite database setup with WAL mode."""

import re
from pathlib import Path

import structlog
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import settings
from bot.data.models import Base

logger = structlog.get_logger()


def _sanitize_url(url: str) -> str:
    """Strip credentials from a database URL for safe logging."""
    return re.sub(r"://[^@]+@", "://***@", url)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _set_wal_mode(dbapi_conn, connection_record):
    """Enable WAL mode for better concurrent read/write performance."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


event.listen(engine.sync_engine, "connect", _set_wal_mode)


async def init_db() -> None:
    """Create all tables if they don't exist."""
    data_dir = Path(settings.database_url.split("///")[-1]).parent
    data_dir.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA journal_mode=WAL"))

    logger.info("database_initialized", url=_sanitize_url(settings.database_url))


async def get_session() -> AsyncSession:
    """Get a database session."""
    async with async_session() as session:
        yield session
