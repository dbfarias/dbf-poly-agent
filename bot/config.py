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

    # Validation rules: type, min, max for each known key
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
    }

    _DEFAULTS = {
        CapitalTier.TIER1: {
            "max_positions": 6,             # More slots for diverse short-term trades
            "max_per_position_pct": 0.40,   # ~$7 max per position on $17 bankroll
            "max_deployed_pct": 0.85,       # Keep 15% cash reserve
            "daily_loss_limit_pct": 0.10,
            "max_drawdown_pct": 0.25,
            "min_edge_pct": 0.01,
            "min_win_prob": 0.55,           # Allow more signals (strategies do own filtering)
            "max_per_category_pct": 0.40,   # Better diversification across categories
            "kelly_fraction": 0.25,         # Higher for small bankroll (need 5-share minimums)
        },
        CapitalTier.TIER2: {
            "max_positions": 6,             # Enough slots for diversified short-term trades
            "max_per_position_pct": 0.20,   # ~$6 max per position
            "max_deployed_pct": 0.80,       # Keep 20% cash reserve (6 positions × 20% = full use)
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
        return dict(cls.CONFIGS[tier])

    @classmethod
    def update(cls, tier: CapitalTier, updates: dict) -> None:
        """Update tier config at runtime. Only known keys are accepted.

        Validates all values before applying (atomic — rollback if any fail).
        Raises ValueError on invalid values.
        """
        valid_keys = set(cls._DEFAULTS[CapitalTier.TIER1].keys())

        # Filter to known keys only (unknown keys silently ignored)
        known_updates = {k: v for k, v in updates.items() if k in valid_keys}

        # Validate ALL values before applying any
        for key, value in known_updates.items():
            rule = cls._VALIDATION.get(key)
            if rule is None:
                continue

            expected_type = rule["type"]
            # For int fields, reject floats (3.5 is not a valid int)
            if expected_type is int:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(
                        f"{key}: expected int, got {type(value).__name__} ({value!r})"
                    )
            elif expected_type is float:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(
                        f"{key}: expected number, got {type(value).__name__} ({value!r})"
                    )

            if value < rule["min"] or value > rule["max"]:
                raise ValueError(
                    f"{key}: {value} out of range [{rule['min']}, {rule['max']}]"
                )

        # All valid — apply atomically
        for key, value in known_updates.items():
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
    force_https_cookies: bool = False

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
