"""Persist and restore bot settings across restarts."""

import json

import structlog

from bot.config import CapitalTier, TierConfig, settings
from bot.data.database import async_session
from bot.data.repositories import SettingsRepository

logger = structlog.get_logger()

# Maps quality_params API keys → MarketAnalyzer attribute names
_QUALITY_ATTR_MAP = {
    "max_spread": "MAX_SPREAD",
    "max_category_positions": "MAX_CATEGORY_POSITIONS",
    "min_bid_ratio": "MIN_BID_RATIO",
    "min_volume_24h": "MIN_VOLUME_24H",
    "stop_loss_pct": "STOP_LOSS_PCT",
    "near_worthless_price": "NEAR_WORTHLESS_PRICE",
    "default_exit_price": "DEFAULT_EXIT_PRICE",
}

# Global settings that are persisted
_GLOBAL_ATTRS = (
    "scan_interval_seconds",
    "max_daily_loss_pct",
    "max_drawdown_pct",
    "daily_target_pct",
)


class SettingsStore:
    """Save dashboard settings to DB and restore them on startup."""

    @staticmethod
    async def save_from_update(update, tier: CapitalTier) -> int:
        """Persist non-None fields from a BotConfigUpdate to the DB.

        Returns the number of settings saved.
        """
        items: dict[str, str] = {}

        # Global settings
        for attr in _GLOBAL_ATTRS:
            value = getattr(update, attr, None)
            if value is not None:
                items[f"global.{attr}"] = json.dumps(value)

        # Tier config
        if update.tier_config:
            for param, value in update.tier_config.items():
                items[f"tier.{tier.value}.{param}"] = json.dumps(value)

        # Strategy params
        if update.strategy_params:
            for strategy_name, params in update.strategy_params.items():
                for param, value in params.items():
                    items[f"strategy.{strategy_name}.{param}"] = json.dumps(value)

        # Quality params
        if update.quality_params:
            for param, value in update.quality_params.items():
                items[f"quality.{param}"] = json.dumps(value)

        # Disabled strategies
        if getattr(update, "disabled_strategies", None) is not None:
            items["global.disabled_strategies"] = json.dumps(
                update.disabled_strategies
            )

        if not items:
            return 0

        async with async_session() as session:
            repo = SettingsRepository(session)
            await repo.set_many(items)

        logger.info("settings_persisted", count=len(items))
        return len(items)

    @staticmethod
    async def load_and_apply(engine) -> int:
        """Load persisted settings from DB and apply over defaults.

        Returns the number of settings applied.
        """
        async with async_session() as session:
            repo = SettingsRepository(session)
            all_settings = await repo.get_all()

        if not all_settings:
            return 0

        applied = 0

        for key, raw_value in all_settings.items():
            try:
                value = json.loads(raw_value)
            except (json.JSONDecodeError, TypeError):
                logger.warning("settings_invalid_json", key=key)
                continue

            parts = key.split(".", maxsplit=2)
            if len(parts) < 2:
                continue

            prefix = parts[0]

            if prefix == "global" and len(parts) == 2:
                if parts[1] == "disabled_strategies":
                    applied += _apply_disabled_strategies(engine, value)
                else:
                    applied += _apply_global(parts[1], value)

            elif prefix == "tier" and len(parts) == 3:
                applied += _apply_tier(parts[1], parts[2], value)

            elif prefix == "strategy" and len(parts) == 3:
                applied += _apply_strategy(engine, parts[1], parts[2], value)

            elif prefix == "quality" and len(parts) == 2:
                applied += _apply_quality(engine, parts[1], value)

        if applied > 0:
            logger.info("settings_restored_from_db", count=applied)

        return applied


_GLOBAL_TYPES: dict[str, type] = {
    "scan_interval_seconds": int,
    "max_daily_loss_pct": float,
    "max_drawdown_pct": float,
    "daily_target_pct": float,
}


def _apply_global(attr: str, value) -> int:
    if attr not in _GLOBAL_ATTRS or not hasattr(settings, attr):
        return 0

    expected = _GLOBAL_TYPES.get(attr)
    if expected is not None:
        try:
            value = expected(value)
        except (TypeError, ValueError):
            logger.warning("settings_type_coerce_failed", attr=attr, value=value)
            return 0

    setattr(settings, attr, value)
    return 1


def _apply_tier(tier_str: str, param: str, value) -> int:
    try:
        tier = CapitalTier(tier_str)
    except ValueError:
        logger.warning("settings_unknown_tier", tier=tier_str)
        return 0

    valid_keys = set(TierConfig._DEFAULTS[CapitalTier.TIER1].keys())
    if param not in valid_keys:
        return 0

    TierConfig.update(tier, {param: value})
    return 1


def _apply_strategy(engine, strategy_name: str, param: str, value) -> int:
    for strategy in engine.analyzer.strategies:
        if strategy.name == strategy_name:
            return 1 if strategy.update_param(param, value) else 0
    return 0


def _apply_disabled_strategies(engine, value) -> int:
    if not isinstance(value, list):
        return 0
    disabled = set(value)
    engine.disabled_strategies = disabled
    engine.analyzer.disabled_strategies = disabled
    return 1


_QUALITY_RANGES: dict[str, tuple[type, float, float]] = {
    "max_spread": (float, 0.0, 1.0),
    "max_category_positions": (int, 1, 20),
    "min_bid_ratio": (float, 0.0, 1.0),
    "min_volume_24h": (float, 0.0, 100000.0),
    "stop_loss_pct": (float, 0.0, 1.0),
    "near_worthless_price": (float, 0.0, 0.5),
    "default_exit_price": (float, 0.0, 1.0),
}


def _apply_quality(engine, param: str, value) -> int:
    attr = _QUALITY_ATTR_MAP.get(param)
    if not attr or not hasattr(engine.analyzer, attr):
        return 0

    spec = _QUALITY_RANGES.get(param)
    if spec is not None:
        typ, lo, hi = spec
        try:
            value = typ(value)
        except (TypeError, ValueError):
            logger.warning("quality_type_coerce_failed", param=param, value=value)
            return 0
        if not (lo <= value <= hi):
            logger.warning("quality_out_of_range", param=param, value=value)
            return 0

    setattr(engine.analyzer, attr, value)
    return 1


class StateStore:
    """Persist and restore ephemeral bot state (daily PnL, cooldowns, pauses).

    Uses the same settings DB table but with 'state.' key prefix.
    State is volatile — it's saved frequently and restored on restart
    to avoid losing in-memory progress.
    """

    @staticmethod
    async def save_daily_pnl(daily_pnl: float, daily_pnl_date: str) -> None:
        """Persist daily PnL and its date."""
        items = {
            "state.daily_pnl": json.dumps(daily_pnl),
            "state.daily_pnl_date": json.dumps(daily_pnl_date),
        }
        async with async_session() as session:
            repo = SettingsRepository(session)
            await repo.set_many(items)

    @staticmethod
    async def load_daily_pnl() -> tuple[float, str]:
        """Load persisted daily PnL. Returns (pnl, date_str)."""
        async with async_session() as session:
            repo = SettingsRepository(session)
            pnl_raw = await repo.get("state.daily_pnl")
            date_raw = await repo.get("state.daily_pnl_date")

        if pnl_raw is None or date_raw is None:
            return 0.0, ""

        try:
            return float(json.loads(pnl_raw)), json.loads(date_raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return 0.0, ""

    @staticmethod
    async def save_market_cooldowns(cooldowns: dict[str, str]) -> None:
        """Persist market cooldowns as JSON (market_id → ISO datetime)."""
        async with async_session() as session:
            repo = SettingsRepository(session)
            await repo.set_many(
                {"state.market_cooldowns": json.dumps(cooldowns)}
            )

    @staticmethod
    async def load_market_cooldowns() -> dict[str, str]:
        """Load persisted market cooldowns."""
        async with async_session() as session:
            repo = SettingsRepository(session)
            raw = await repo.get("state.market_cooldowns")

        if raw is None:
            return {}

        try:
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    async def save_paused_strategies(paused: dict[str, str]) -> None:
        """Persist paused strategies (strategy_name → ISO pause datetime)."""
        async with async_session() as session:
            repo = SettingsRepository(session)
            await repo.set_many(
                {"state.paused_strategies": json.dumps(paused)}
            )

    @staticmethod
    async def load_paused_strategies() -> dict[str, str]:
        """Load persisted paused strategies."""
        async with async_session() as session:
            repo = SettingsRepository(session)
            raw = await repo.get("state.paused_strategies")

        if raw is None:
            return {}

        try:
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
