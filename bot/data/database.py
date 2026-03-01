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


def _set_wal_mode(dbapi_conn, _connection_record):
    """Enable WAL mode for better concurrent read/write performance."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


event.listen(engine.sync_engine, "connect", _set_wal_mode)


async def init_db() -> None:
    """Create all tables if they don't exist, then run lightweight migrations."""
    data_dir = Path(settings.database_url.split("///")[-1]).parent
    data_dir.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA journal_mode=WAL"))

    await _migrate(engine)
    logger.info("database_initialized", url=_sanitize_url(settings.database_url))


async def _migrate(eng) -> None:
    """Add missing columns to existing tables (lightweight SQLite migrations)."""
    migrations = [
        ("trades", "exit_reason", "TEXT NOT NULL DEFAULT ''"),
    ]

    async with eng.begin() as conn:
        for table, column, col_type in migrations:
            # Check if column already exists
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if column not in existing:
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                )
                logger.info(
                    "migration_applied",
                    table=table,
                    column=column,
                )
