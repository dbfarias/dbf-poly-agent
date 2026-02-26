"""Bot entry point — can run standalone or as a background task in FastAPI."""

import asyncio

import structlog

from bot.agent.engine import TradingEngine
from bot.data.database import init_db
from bot.utils.logging_config import setup_logging

logger = structlog.get_logger()

# Global engine instance (shared with FastAPI)
engine: TradingEngine | None = None


async def create_engine() -> TradingEngine:
    """Create and initialize the trading engine."""
    global engine
    setup_logging()
    await init_db()

    engine = TradingEngine()
    await engine.initialize()
    return engine


async def run_bot() -> None:
    """Run the bot as a standalone process."""
    eng = await create_engine()
    try:
        await eng.run()
    except KeyboardInterrupt:
        logger.info("bot_interrupted")
    finally:
        await eng.shutdown()


def main() -> None:
    """CLI entry point."""
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
