"""Persist and restore bot settings across restarts."""

import json

import structlog

from bot.config import RiskConfig, settings
from bot.data.database import async_session
from bot.data.repositories import SettingsRepository

logger = structlog.get_logger()

# ── Settings migrations ──
# Bump _SETTINGS_VERSION and add entries to _MIGRATIONS when code defaults change.
# On startup, any DB-persisted value listed in a new migration will be overwritten
# so that the new code default takes effect even if an old value was saved.
_SETTINGS_VERSION = 3

_MIGRATIONS: dict[int, dict[str, object]] = {
    # v2: Exit logic fix (2026-03-02) — faster capital rotation
    2: {
        "quality.max_position_age_hours": 72.0,
        "quality.take_profit_price": 0.95,
    },
    # v3: Signal quality (2026-03-03) — reduce swing noise
    3: {
        "strategy.swing_trading.MIN_MOMENTUM_TICKS": 3,
    },
}

# Maps quality_params API keys → (target_obj_name, attribute_name)
# target_obj_name is resolved on the engine: "analyzer", "learner", "closer"
_QUALITY_ATTR_MAP: dict[str, tuple[str, str]] = {
    # MarketAnalyzer params
    "max_spread": ("analyzer", "MAX_SPREAD"),
    "max_category_positions": ("analyzer", "MAX_CATEGORY_POSITIONS"),
    "min_bid_ratio": ("analyzer", "MIN_BID_RATIO"),
    "min_volume_24h": ("analyzer", "MIN_VOLUME_24H"),
    "stop_loss_pct": ("analyzer", "STOP_LOSS_PCT"),
    "near_worthless_price": ("analyzer", "NEAR_WORTHLESS_PRICE"),
    "default_exit_price": ("analyzer", "DEFAULT_EXIT_PRICE"),
    "max_position_age_hours": ("analyzer", "MAX_POSITION_AGE_HOURS"),
    "take_profit_price": ("analyzer", "TAKE_PROFIT_PRICE"),
    "take_profit_min_hold_hours": ("analyzer", "TAKE_PROFIT_MIN_HOLD_HOURS"),
    # Learner params
    "pause_lookback": ("learner", "PAUSE_LOOKBACK"),
    "pause_win_rate": ("learner", "PAUSE_WIN_RATE"),
    "pause_min_loss": ("learner", "PAUSE_MIN_LOSS"),
    "pause_cooldown_hours": ("learner", "PAUSE_COOLDOWN_HOURS"),
    "multiplier_min": ("learner", "MULTIPLIER_MIN"),
    "multiplier_max": ("learner", "MULTIPLIER_MAX"),
    "min_trades_for_adjustment": ("learner", "MIN_TRADES_FOR_ADJUSTMENT"),
    # PositionCloser params
    "min_rebalance_edge": ("closer", "min_rebalance_edge"),
    "min_hold_seconds": ("closer", "min_hold_seconds"),
    "rebalance_resolution_shield_hours": ("closer", "rebalance_resolution_shield_hours"),
    "rebalance_resolution_max_loss_pct": ("closer", "rebalance_resolution_max_loss_pct"),
    # Engine-level params (target resolved as engine itself, not engine.attr)
    "market_cooldown_hours": ("_engine", "market_cooldown_hours"),
    "min_balance_for_trades": ("_engine", "min_balance_for_trades"),
    "min_edge_for_debate": ("_engine", "min_edge_for_debate"),
    # Edge adjustment params
    "spread_penalty_factor": ("_engine", "spread_penalty_factor"),
    "cal_gap_weight": ("_engine", "cal_gap_weight"),
    # Learner advanced params
    "recompute_interval": ("learner", "RECOMPUTE_INTERVAL"),
    "unpause_grace_hours": ("learner", "UNPAUSE_GRACE_HOURS"),
}

# Global settings that are persisted
_GLOBAL_ATTRS = (
    "scan_interval_seconds",
    "max_daily_loss_pct",
    "max_drawdown_pct",
    "daily_target_pct",
    "use_llm_sentiment",
    "use_llm_debate",
    "use_llm_reviewer",
    "use_multi_round_debate",
    "use_llm_keywords",
    "use_llm_post_mortem",
    "llm_daily_budget",
    "use_auto_claim",
)


class SettingsStore:
    """Save dashboard settings to DB and restore them on startup."""

    @staticmethod
    async def save_from_update(update) -> int:
        """Persist non-None fields from a BotConfigUpdate to the DB.

        Returns the number of settings saved.
        """
        items: dict[str, str] = {}

        # Global settings
        for attr in _GLOBAL_ATTRS:
            value = getattr(update, attr, None)
            if value is not None:
                items[f"global.{attr}"] = json.dumps(value)

        # Trading mode
        if getattr(update, "trading_mode", None) is not None:
            items["global.trading_mode"] = json.dumps(update.trading_mode)

        # Risk config
        if update.risk_config:
            for param, value in update.risk_config.items():
                items[f"risk.{param}"] = json.dumps(value)

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

        # Blocked market types
        if getattr(update, "blocked_market_types", None) is not None:
            items["global.blocked_market_types"] = json.dumps(
                update.blocked_market_types
            )

        if not items:
            return 0

        async with async_session() as session:
            repo = SettingsRepository(session)
            await repo.set_many(items)

        logger.info("settings_persisted", count=len(items))
        return len(items)

    @staticmethod
    async def run_migrations() -> int:
        """Apply pending settings migrations.

        Compares DB's stored settings_version against _SETTINGS_VERSION.
        For each new version, overwrites stale DB values with new code defaults.
        Returns count of migrated settings.
        """
        async with async_session() as session:
            repo = SettingsRepository(session)
            raw = await repo.get("state.settings_version")

        current = 0
        if raw is not None:
            try:
                current = int(json.loads(raw))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        if current >= _SETTINGS_VERSION:
            return 0

        migrated = 0
        items: dict[str, str] = {}
        for version in range(current + 1, _SETTINGS_VERSION + 1):
            changes = _MIGRATIONS.get(version, {})
            for key, value in changes.items():
                items[key] = json.dumps(value)
                migrated += 1
            logger.info(
                "settings_migration",
                from_version=current,
                to_version=version,
                changes=list(changes.keys()),
            )

        items["state.settings_version"] = json.dumps(_SETTINGS_VERSION)

        if items:
            async with async_session() as session:
                repo = SettingsRepository(session)
                await repo.set_many(items)

        logger.info("settings_migrations_complete", version=_SETTINGS_VERSION, migrated=migrated)
        return migrated

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
                elif parts[1] == "blocked_market_types":
                    applied += _apply_blocked_market_types(engine, value)
                else:
                    applied += _apply_global(parts[1], value)

            elif prefix == "tier" and len(parts) == 3:
                # Backward compat: old "tier.tier1.param" keys
                applied += _apply_risk(parts[2], value)

            elif prefix == "risk" and len(parts) == 2:
                applied += _apply_risk(parts[1], value)

            elif prefix == "strategy" and len(parts) == 3:
                applied += _apply_strategy(engine, parts[1], parts[2], value)

            elif prefix == "quality" and len(parts) == 2:
                applied += _apply_quality(engine, parts[1], value)

        if applied > 0:
            logger.info("settings_restored_from_db", count=applied)

        return applied


_GLOBAL_RANGES: dict[str, tuple[type, float, float]] = {
    "scan_interval_seconds": (int, 5, 600),
    "max_daily_loss_pct": (float, 0.01, 1.0),
    "max_drawdown_pct": (float, 0.01, 1.0),
    "daily_target_pct": (float, 0.001, 0.5),
    "use_llm_sentiment": (bool, 0, 1),
    "use_llm_debate": (bool, 0, 1),
    "use_llm_reviewer": (bool, 0, 1),
    "use_multi_round_debate": (bool, 0, 1),
    "use_llm_keywords": (bool, 0, 1),
    "use_llm_post_mortem": (bool, 0, 1),
    "llm_daily_budget": (float, 0.5, 20.0),
    "use_auto_claim": (bool, 0, 1),
}


def _apply_global(attr: str, value) -> int:
    # Special case: trading_mode is not in _GLOBAL_ATTRS
    if attr == "trading_mode":
        from bot.config import TradingMode
        if value in ("paper", "live"):
            settings.trading_mode = TradingMode(value)
            return 1
        return 0

    if attr not in _GLOBAL_ATTRS or not hasattr(settings, attr):
        return 0

    spec = _GLOBAL_RANGES.get(attr)
    if spec is not None:
        typ, lo, hi = spec
        try:
            value = typ(value)
        except (TypeError, ValueError):
            logger.warning("settings_type_coerce_failed", attr=attr, value=value)
            return 0
        if not (lo <= value <= hi):
            logger.warning("settings_out_of_range", attr=attr, value=value)
            return 0

    setattr(settings, attr, value)
    return 1


def _apply_risk(param: str, value) -> int:
    valid_keys = set(RiskConfig._DEFAULTS.keys())
    if param not in valid_keys:
        return 0
    RiskConfig.update({param: value})
    return 1


def _apply_strategy(engine, strategy_name: str, param: str, value) -> int:
    for strategy in engine.analyzer.strategies:
        if strategy.name == strategy_name:
            return 1 if strategy.update_param(param, value) else 0
    return 0


def _apply_disabled_strategies(engine, value) -> int:
    if not isinstance(value, list):
        return 0
    engine.disabled_strategies = set(value)
    engine.analyzer.disabled_strategies = set(value)
    return 1


def _apply_blocked_market_types(engine, value) -> int:
    if not isinstance(value, list):
        return 0
    valid = {"sports", "crypto", "meme", "other"}
    engine.analyzer.blocked_market_types = set(value) & valid
    return 1


_QUALITY_RANGES: dict[str, tuple[type, float, float]] = {
    # MarketAnalyzer params
    "max_spread": (float, 0.0, 1.0),
    "max_category_positions": (int, 1, 20),
    "min_bid_ratio": (float, 0.0, 1.0),
    "min_volume_24h": (float, 0.0, 100000.0),
    "stop_loss_pct": (float, 0.0, 1.0),
    "near_worthless_price": (float, 0.0, 0.5),
    "default_exit_price": (float, 0.0, 1.0),
    "max_position_age_hours": (float, 1.0, 720.0),
    "take_profit_price": (float, 0.5, 1.0),
    "take_profit_min_hold_hours": (float, 0.0, 168.0),
    # Learner params
    "pause_lookback": (int, 2, 50),
    "pause_win_rate": (float, 0.0, 1.0),
    "pause_min_loss": (float, -100.0, 0.0),
    "pause_cooldown_hours": (float, 1.0, 168.0),
    "multiplier_min": (float, 0.1, 2.0),
    "multiplier_max": (float, 1.0, 5.0),
    "min_trades_for_adjustment": (int, 1, 50),
    # PositionCloser params
    "min_rebalance_edge": (float, 0.0, 0.5),
    "min_hold_seconds": (int, 0, 14400),
    "rebalance_resolution_shield_hours": (float, 0.0, 168.0),
    "rebalance_resolution_max_loss_pct": (float, 0.01, 0.5),
    # Engine-level params
    "market_cooldown_hours": (float, 0.25, 24.0),
    "min_balance_for_trades": (float, 0.0, 100.0),
    "min_edge_for_debate": (float, 0.0, 0.10),
    # Edge adjustment params
    "spread_penalty_factor": (float, 0.0, 2.0),
    "cal_gap_weight": (float, 0.0, 1.0),
    # Learner advanced params
    "recompute_interval": (int, 60, 3600),
    "unpause_grace_hours": (float, 0.5, 48.0),
}


def _apply_quality(engine, param: str, value) -> int:
    # Per-strategy cooldown overrides
    if param.startswith("cooldown_"):
        strategy_name = param[len("cooldown_"):]
        if strategy_name in engine._strategy_cooldown_hours:
            try:
                value = float(value)
                if 0.01 <= value <= 24.0:
                    engine._strategy_cooldown_hours[strategy_name] = value
                    return 1
            except (TypeError, ValueError):
                pass
        return 0

    # LLM debate cache TTLs (stored on settings object)
    if param == "llm_debate_cache_ttl_approved":
        try:
            value = float(value)
            if 300 <= value <= 86400:
                settings.llm_debate_cache_ttl_approved = value
                return 1
        except (TypeError, ValueError):
            pass
        return 0
    if param == "llm_debate_cache_ttl_rejected":
        try:
            value = float(value)
            if 60 <= value <= 14400:
                settings.llm_debate_cache_ttl_rejected = value
                return 1
        except (TypeError, ValueError):
            pass
        return 0

    mapping = _QUALITY_ATTR_MAP.get(param)
    if not mapping:
        return 0

    target_name, attr = mapping
    # "_engine" means the engine object itself (not an attribute of it)
    if target_name == "_engine":
        target = engine
    else:
        target = getattr(engine, target_name, None)
    if target is None or not hasattr(target, attr):
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

    setattr(target, attr, value)
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
    async def save_day_start_equity(equity: float, date: str) -> None:
        """Persist start-of-day equity so it survives restarts."""
        items = {
            "state.day_start_equity": json.dumps(equity),
            "state.day_start_equity_date": json.dumps(date),
        }
        async with async_session() as session:
            repo = SettingsRepository(session)
            await repo.set_many(items)

    @staticmethod
    async def load_day_start_equity() -> tuple[float, str]:
        """Load persisted start-of-day equity. Returns (equity, date_str)."""
        async with async_session() as session:
            repo = SettingsRepository(session)
            eq_raw = await repo.get("state.day_start_equity")
            date_raw = await repo.get("state.day_start_equity_date")

        if eq_raw is None or date_raw is None:
            return 0.0, ""

        try:
            return float(json.loads(eq_raw)), json.loads(date_raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return 0.0, ""

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
    async def save_paper_cash(cash: float, bankroll: float) -> None:
        """Persist paper mode cash and bankroll config so they survive restarts."""
        async with async_session() as session:
            repo = SettingsRepository(session)
            await repo.set_many({
                "state.paper_cash": json.dumps(cash),
                "state.paper_bankroll": json.dumps(bankroll),
            })

    @staticmethod
    async def load_paper_cash() -> tuple[float | None, float | None]:
        """Load persisted paper cash and bankroll. Returns (cash, bankroll)."""
        async with async_session() as session:
            repo = SettingsRepository(session)
            cash_raw = await repo.get("state.paper_cash")
            bankroll_raw = await repo.get("state.paper_bankroll")

        cash = None
        if cash_raw is not None:
            try:
                cash = float(json.loads(cash_raw))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        bankroll = None
        if bankroll_raw is not None:
            try:
                bankroll = float(json.loads(bankroll_raw))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        return cash, bankroll

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
    async def save_unpause_immunity(immunity: dict[str, str]) -> None:
        """Persist unpause immunity (strategy_name → ISO grant datetime)."""
        async with async_session() as session:
            repo = SettingsRepository(session)
            await repo.set_many(
                {"state.unpause_immunity": json.dumps(immunity)}
            )

    @staticmethod
    async def load_unpause_immunity() -> dict[str, str]:
        """Load persisted unpause immunity."""
        async with async_session() as session:
            repo = SettingsRepository(session)
            raw = await repo.get("state.unpause_immunity")

        if raw is None:
            return {}

        try:
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    async def save_trading_paused(paused: bool) -> None:
        """Persist global trading pause state."""
        async with async_session() as session:
            repo = SettingsRepository(session)
            await repo.set_many(
                {"state.trading_paused": json.dumps(paused)}
            )

    @staticmethod
    async def load_trading_paused() -> bool:
        """Load persisted global trading pause state."""
        async with async_session() as session:
            repo = SettingsRepository(session)
            raw = await repo.get("state.trading_paused")

        if raw is None:
            return False

        try:
            return bool(json.loads(raw))
        except (json.JSONDecodeError, TypeError, ValueError):
            return False

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
