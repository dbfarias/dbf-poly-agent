"""Web Push notification system for trade alerts and events."""

import json
import re

import structlog

from bot.config import settings
from bot.data.database import async_session
from bot.data.repositories import SettingsRepository

logger = structlog.get_logger()

_DB_KEY = "push.subscriptions"
_MAX_ERROR_LEN = 200
_REDACT_RE = re.compile(r"0x[0-9a-fA-F]{20,}")


def _has_vapid() -> bool:
    """Check if VAPID keys are configured."""
    return bool(
        settings.vapid_public_key
        and settings.vapid_private_key
        and settings.vapid_email
    )


async def _get_subscriptions() -> list[dict]:
    """Load push subscriptions from the BotSetting table."""
    async with async_session() as session:
        repo = SettingsRepository(session)
        raw = await repo.get(_DB_KEY)

    if raw is None:
        return []

    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def _save_subscriptions(subs: list[dict]) -> None:
    """Persist push subscriptions to the BotSetting table."""
    async with async_session() as session:
        repo = SettingsRepository(session)
        await repo.set_many({_DB_KEY: json.dumps(subs)})


async def add_subscription(sub: dict) -> None:
    """Add a push subscription, deduplicating by endpoint."""
    subs = await _get_subscriptions()
    endpoint = sub.get("endpoint", "")
    # Remove any existing subscription with the same endpoint
    subs = [s for s in subs if s.get("endpoint") != endpoint]
    subs.append(sub)
    await _save_subscriptions(subs)
    logger.info("push_subscription_added", endpoint=endpoint[:60])


async def remove_subscription(endpoint: str) -> bool:
    """Remove a push subscription by endpoint. Returns True if found."""
    subs = await _get_subscriptions()
    before = len(subs)
    subs = [s for s in subs if s.get("endpoint") != endpoint]
    if len(subs) < before:
        await _save_subscriptions(subs)
        logger.info("push_subscription_removed", endpoint=endpoint[:60])
        return True
    return False


def _safe_error_msg(error: str) -> str:
    """Sanitize error messages — truncate and redact secrets."""
    msg = error[:_MAX_ERROR_LEN]
    return _REDACT_RE.sub("0x[REDACTED]", msg)


async def _send_to_all(payload: dict) -> int:
    """Send a push notification to all subscribers.

    Returns the number of successful deliveries.
    Auto-removes expired subscriptions (410/404).
    """
    if not _has_vapid():
        return 0

    subs = await _get_subscriptions()
    if not subs:
        return 0

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning("pywebpush_not_installed")
        return 0

    vapid_claims = {
        "sub": f"mailto:{settings.vapid_email}",
    }
    data = json.dumps(payload)
    sent = 0
    expired_endpoints: list[str] = []

    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=data,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims=vapid_claims,
            )
            sent += 1
        except WebPushException as e:
            status_code = getattr(e, "response", None)
            if status_code is not None:
                status_code = getattr(status_code, "status_code", None)
            if status_code in (404, 410):
                expired_endpoints.append(sub.get("endpoint", ""))
                logger.info("push_subscription_expired", endpoint=sub.get("endpoint", "")[:60])
            else:
                logger.warning("push_send_failed", error=str(e)[:200])
        except Exception as e:
            logger.warning("push_send_error", error=str(e)[:200])

    # Auto-remove expired subscriptions
    if expired_endpoints:
        subs = [s for s in subs if s.get("endpoint") not in expired_endpoints]
        await _save_subscriptions(subs)

    return sent


async def push_notify_trade(
    action: str, strategy: str, question: str, side: str, price: float, size: float, pnl: float = 0
) -> None:
    """Send trade push notification."""
    title = f"{'BUY' if side.upper() == 'BUY' else 'SELL'} {action.upper()}"
    body = f"{strategy}: {question[:80]} — {size:.0f} shares @ ${price:.4f}"
    if action == "closed":
        body += f" | PnL: ${pnl:+.2f}"

    await _send_to_all({
        "title": title,
        "body": body,
        "tag": f"trade-{strategy}-{action}",
        "data": {"url": "/trades"},
    })


async def push_notify_error(category: str, message: str) -> None:
    """Send error push notification."""
    await _send_to_all({
        "title": f"Error: {category}",
        "body": _safe_error_msg(message),
        "tag": f"error-{category}",
        "data": {"url": "/"},
    })


async def push_notify_strategy_paused(strategy: str, reason: str) -> None:
    """Notify when a strategy is paused by the learner."""
    await _send_to_all({
        "title": "Strategy Paused",
        "body": f"{strategy}: {reason}",
        "tag": f"pause-{strategy}",
        "data": {"url": "/learner"},
    })


async def push_notify_risk_limit(limit_type: str, current: float, threshold: float) -> None:
    """Notify when a risk limit is hit."""
    await _send_to_all({
        "title": "Risk Limit Hit",
        "body": f"{limit_type}: {current:.1%} (threshold: {threshold:.1%})",
        "tag": f"risk-{limit_type}",
        "data": {"url": "/risk"},
    })


async def push_notify_position_alert(
    question: str, outcome: str, current_price: float, unrealized_pnl: float, reason: str
) -> None:
    """Send EXIT alert when LLM recommends closing a position."""
    pnl_str = f"${unrealized_pnl:+.2f}"
    await _send_to_all({
        "title": f"⚠️ EXIT Recommended: {outcome}",
        "body": f"{question[:70]} @ ${current_price:.3f} | PnL {pnl_str} — {reason}",
        "tag": f"exit-alert-{question[:30]}",
        "data": {"url": "/"},
    })


async def push_notify_daily_summary(
    equity: float, daily_pnl: float, daily_return: float, trades: int, win_rate: float
) -> None:
    """Send daily performance summary."""
    emoji = "up" if daily_pnl >= 0 else "down"
    await _send_to_all({
        "title": f"Daily Summary ({emoji})",
        "body": (
            f"Equity: ${equity:.2f} | PnL: ${daily_pnl:+.2f} ({daily_return:+.1%}) | "
            f"Trades: {trades} | WR: {win_rate:.0%}"
        ),
        "tag": "daily-summary",
        "data": {"url": "/"},
    })
