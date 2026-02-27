"""Application configuration via Pydantic Settings."""

from enum import Enum
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class CapitalTier(str, Enum):
    TIER1 = "tier1"  # $5-$25
    TIER2 = "tier2"  # $25-$100
    TIER3 = "tier3"  # $100+

    @classmethod
    def from_bankroll(cls, bankroll: float) -> "CapitalTier":
        if bankroll >= 100:
            return cls.TIER3
        elif bankroll >= 25:
            return cls.TIER2
        return cls.TIER1


class TierConfig:
    """Risk parameters per capital tier (mutable at runtime via dashboard)."""

    _DEFAULTS = {
        CapitalTier.TIER1: {
            "max_positions": 3,             # Few focused positions (each ~$5 min)
            "max_per_position_pct": 0.55,   # Allow $5 trades on $10 bankroll
            "max_deployed_pct": 0.80,       # Keep 20% cash reserve
            "daily_loss_limit_pct": 0.10,
            "max_drawdown_pct": 0.25,
            "min_edge_pct": 0.01,
            "min_win_prob": 0.65,
            "max_per_category_pct": 0.55,   # Match position pct (1 trade = 1 category)
            "kelly_fraction": 0.20,         # Slightly more aggressive for learning
        },
        CapitalTier.TIER2: {
            "max_positions": 6,             # Enough slots for diversified short-term trades
            "max_per_position_pct": 0.20,   # ~$6 max per position
            "max_deployed_pct": 0.60,       # Keep 40% cash reserve
            "daily_loss_limit_pct": 0.08,
            "max_drawdown_pct": 0.15,
            "min_edge_pct": 0.02,
            "min_win_prob": 0.70,
            "max_per_category_pct": 0.30,   # ~$9 max per normalized category
            "kelly_fraction": 0.15,
        },
        CapitalTier.TIER3: {
            "max_positions": 15,
            "max_per_position_pct": 0.15,
            "max_deployed_pct": 0.85,
            "daily_loss_limit_pct": 0.06,
            "max_drawdown_pct": 0.12,
            "min_edge_pct": 0.02,
            "min_win_prob": 0.60,
            "max_per_category_pct": 0.30,
            "kelly_fraction": 0.20,
        },
    }

    # Runtime-mutable copies
    CONFIGS: dict[CapitalTier, dict] = {
        tier: dict(params) for tier, params in _DEFAULTS.items()
    }

    @classmethod
    def get(cls, tier: CapitalTier) -> dict:
        return cls.CONFIGS[tier]

    @classmethod
    def update(cls, tier: CapitalTier, updates: dict) -> None:
        """Update tier config at runtime. Only known keys are accepted."""
        valid_keys = set(cls._DEFAULTS[CapitalTier.TIER1].keys())
        for key, value in updates.items():
            if key in valid_keys:
                cls.CONFIGS[tier][key] = value

    @classmethod
    def reset(cls, tier: CapitalTier) -> None:
        """Reset a tier to default values."""
        cls.CONFIGS[tier] = dict(cls._DEFAULTS[tier])


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

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

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
        return self

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == TradingMode.PAPER

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


settings = Settings()
