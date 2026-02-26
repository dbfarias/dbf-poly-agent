"""FastAPI dependencies: DB sessions, engine access."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from bot.data.database import async_session


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


def get_engine():
    """Get the global trading engine instance."""
    from bot.main import engine

    if engine is None:
        raise RuntimeError("Trading engine not initialized")
    return engine
