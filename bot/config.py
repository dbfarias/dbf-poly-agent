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
    """Risk parameters per capital tier."""

    CONFIGS = {
        CapitalTier.TIER1: {
            "max_positions": 3,
            "max_per_position_pct": 0.80,
            "max_deployed_pct": 0.80,
            "daily_loss_limit_pct": 0.15,
            "max_drawdown_pct": 0.30,
            "min_edge_pct": 0.01,
            "min_win_prob": 0.75,
            "max_per_category_pct": 0.60,
            "kelly_fraction": 0.50,
        },
        CapitalTier.TIER2: {
            "max_positions": 3,
            "max_per_position_pct": 0.50,
            "max_deployed_pct": 0.70,
            "daily_loss_limit_pct": 0.10,
            "max_drawdown_pct": 0.20,
            "min_edge_pct": 0.03,
            "min_win_prob": 0.70,
            "max_per_category_pct": 0.60,
            "kelly_fraction": 0.25,
        },
        CapitalTier.TIER3: {
            "max_positions": 10,
            "max_per_position_pct": 0.20,
            "max_deployed_pct": 0.80,
            "daily_loss_limit_pct": 0.08,
            "max_drawdown_pct": 0.15,
            "min_edge_pct": 0.02,
            "min_win_prob": 0.55,
            "max_per_category_pct": 0.40,
            "kelly_fraction": 0.25,
        },
    }

    @classmethod
    def get(cls, tier: CapitalTier) -> dict:
        return cls.CONFIGS[tier]


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

    # Database
    database_url: str = "sqlite+aiosqlite:///data/polybot.db"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_secret_key: str = ""
    allowed_origins: str = "http://localhost:3000,http://localhost:5173"

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
