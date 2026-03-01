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


def _apply_global(attr: str, value) -> int:
    if attr in _GLOBAL_ATTRS and hasattr(settings, attr):
        setattr(settings, attr, value)
        return 1
    return 0


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
        if strategy.name == strategy_name and hasattr(strategy, param):
            setattr(strategy, param, value)
            return 1
    return 0


def _apply_disabled_strategies(engine, value) -> int:
    if not isinstance(value, list):
        return 0
    disabled = set(value)
    engine.disabled_strategies = disabled
    engine.analyzer.disabled_strategies = disabled
    return 1


def _apply_quality(engine, param: str, value) -> int:
    attr = _QUALITY_ATTR_MAP.get(param)
    if attr and hasattr(engine.analyzer, attr):
        setattr(engine.analyzer, attr, value)
        return 1
    return 0
