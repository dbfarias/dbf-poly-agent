"""Application configuration via Pydantic Settings."""

from enum import Enum
from pathlib import Path

from pydantic import Field
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
            "max_positions": 1,
            "max_per_position_pct": 1.0,
            "daily_loss_limit_pct": 0.10,
            "max_drawdown_pct": 0.25,
            "min_edge_pct": 0.05,
            "min_win_prob": 0.85,
            "max_per_category_pct": 1.0,
            "kelly_fraction": 0.25,
        },
        CapitalTier.TIER2: {
            "max_positions": 3,
            "max_per_position_pct": 0.50,
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

    # Trading
    trading_mode: TradingMode = TradingMode.PAPER
    initial_bankroll: float = 5.0
    scan_interval_seconds: int = 60
    snapshot_interval_seconds: int = 300

    # Risk
    max_daily_loss_pct: float = 0.10
    max_drawdown_pct: float = 0.25

    # Database
    database_url: str = "sqlite+aiosqlite:///data/polybot.db"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_secret_key: str = "change-me-in-production"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    # Paths
    data_dir: Path = Field(default=Path("data"))

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == TradingMode.PAPER

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


settings = Settings()
