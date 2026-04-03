"""Application configuration via Pydantic Settings."""

from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"






class RiskConfig:
    """Risk parameters (flat config, no tiers). Mutable at runtime via dashboard."""

    _VALIDATION: dict[str, dict] = {
        "max_positions": {"type": int, "min": 1, "max": 50},
        "max_per_position_pct": {"type": float, "min": 0.01, "max": 1.0},
        "kelly_fraction": {"type": float, "min": 0.05, "max": 1.0},
        "min_edge_pct": {"type": float, "min": 0.0, "max": 0.5},
        "max_drawdown_pct": {"type": float, "min": 0.01, "max": 1.0},
        "daily_loss_limit_pct": {"type": float, "min": 0.01, "max": 1.0},
        "max_per_category_pct": {"type": float, "min": 0.01, "max": 1.0},
        "max_deployed_pct": {"type": float, "min": 0.1, "max": 1.0},
        "min_win_prob": {"type": float, "min": 0.0, "max": 1.0},
        "spread_cross_offset": {"type": float, "min": 0.0, "max": 0.05},
    }

    _DEFAULTS: dict[str, object] = {
        "max_positions": 8,            # Was 6: weather+crypto need room
        "max_per_position_pct": 0.10,  # 10%: $60 equity → max $6/trade
        "max_deployed_pct": 0.65,      # Was 0.60: slightly more capital at work
        "daily_loss_limit_pct": 0.08,  # 8%: enough headroom to trade without blocking too early
        "max_drawdown_pct": 0.12,
        "min_edge_pct": 0.005,         # 0.5% base — learner multiplies to ~0.85% effective (1.7x)
        "min_win_prob": 0.55,
        "max_per_category_pct": 0.35,  # Was 0.40: less concentration per category
        "kelly_fraction": 0.25,        # Quarter-Kelly for conservative sizing
        "spread_cross_offset": 0.0,    # Aggressive pricing offset (0 = disabled)
    }

    _CONFIG: dict[str, object] = dict(_DEFAULTS)

    @classmethod
    def get(cls) -> dict:
        return dict(cls._CONFIG)

    @classmethod
    def update(cls, updates: dict) -> None:
        """Update risk config at runtime. Only known keys are accepted."""
        valid_keys = set(cls._DEFAULTS.keys())
        known_updates = {k: v for k, v in updates.items() if k in valid_keys}

        for key, value in known_updates.items():
            rule = cls._VALIDATION.get(key)
            if rule is None:
                continue
            expected_type = rule["type"]
            if expected_type is int:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(f"{key}: expected int, got {type(value).__name__} ({value!r})")
            elif expected_type is float:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(
                        f"{key}: expected number, got {type(value).__name__} ({value!r})"
                    )
            if value < rule["min"] or value > rule["max"]:
                raise ValueError(f"{key}: {value} out of range [{rule['min']}, {rule['max']}]")

        for key, value in known_updates.items():
            cls._CONFIG[key] = value

    @classmethod
    def reset(cls) -> None:
        """Reset to default values."""
        cls._CONFIG = dict(cls._DEFAULTS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Polymarket API
    poly_api_key: str = ""
    poly_api_secret: str = ""
    poly_api_passphrase: str = ""
    poly_private_key: str = ""
    poly_chain_id: int = 137
    # 0=EOA (MetaMask), 1=POLY_PROXY (Magic Link/email users), 2=GNOSIS_SAFE
    poly_signature_type: int = 1

    # Trading
    trading_mode: TradingMode = TradingMode.PAPER
    initial_bankroll: float = 5.0
    scan_interval_seconds: int = 30
    snapshot_interval_seconds: int = 300

    # Risk
    max_daily_loss_pct: float = 0.10
    max_drawdown_pct: float = 0.25
    daily_target_pct: float = 0.01  # 1% daily profit target
    min_balance_for_trades: float = 1.0  # Skip new trades when cash below this

    # Timezone offset for daily boundaries (e.g., -3 for BRT)
    timezone_offset_hours: int = -3

    # Database
    database_url: str = "sqlite+aiosqlite:///data/polybot.db"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_secret_key: str = ""
    allowed_origins: str = "http://localhost:3000,http://localhost:5173"

    # Dashboard auth
    dashboard_user: str = "admin"
    dashboard_password: str = ""
    force_https_cookies: bool = False

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Web Push (VAPID)
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_email: str = ""

    # LLM features
    use_llm_sentiment: bool = False
    use_llm_debate: bool = False
    use_llm_reviewer: bool = False
    use_multi_round_debate: bool = False
    use_llm_keywords: bool = False
    use_llm_post_mortem: bool = False
    use_llm_consensus: bool = False
    llm_daily_budget: float = 3.0
    anthropic_api_key: str = ""

    # Twitter/X research (via Tavily)
    tavily_api_key: str = ""
    use_twitter_fetcher: bool = True
    twitter_daily_budget: int = 10  # Tavily: 10 twitter + 10 news = 20/day (1000/mo)

    # Auto-claim
    use_auto_claim: bool = False
    polygon_rpc_url: str = "https://polygon-rpc.com"

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    # Paths
    data_dir: Path = Field(default=Path("data"))

    @model_validator(mode="after")
    def _validate_secrets(self) -> "Settings":
        if not self.api_secret_key or len(self.api_secret_key) < 16:
            raise ValueError(
                "API_SECRET_KEY must be set and at least 16 characters. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        if self.trading_mode == TradingMode.LIVE:
            if not self.poly_private_key:
                raise ValueError(
                    "LIVE mode requires POLY_PRIVATE_KEY. "
                    "API creds (key/secret/passphrase) will be auto-derived."
                )
            if not self.dashboard_password:
                raise ValueError(
                    "LIVE mode requires DASHBOARD_PASSWORD to be set."
                )
        return self

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == TradingMode.PAPER

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


settings = Settings()


def trading_day() -> str:
    """Return the current 'trading day' date string adjusted for local timezone.

    Uses TIMEZONE_OFFSET_HOURS so the day boundary matches the user's local
    midnight instead of UTC midnight.  E.g., offset=-3 (BRT) rolls the day
    at 03:00 UTC.
    """
    offset = timedelta(hours=settings.timezone_offset_hours)
    local_now = datetime.now(timezone.utc) + offset
    return local_now.strftime("%Y-%m-%d")
