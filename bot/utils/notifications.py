"""Telegram notification system for trade alerts and errors."""

import httpx
import structlog

from bot.config import settings

logger = structlog.get_logger()

TELEGRAM_API = "https://api.telegram.org"


async def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured Telegram chat."""
    if not settings.has_telegram:
        return False

    url = f"{TELEGRAM_API}/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error("telegram_send_failed", error=str(e))
        return False


async def notify_trade(
    action: str, strategy: str, question: str, side: str, price: float, size: float, pnl: float = 0
) -> None:
    """Send trade notification."""
    emoji = "🟢" if action == "opened" else ("🔴" if pnl < 0 else "✅")
    msg = (
        f"{emoji} <b>Trade {action.upper()}</b>\n"
        f"Strategy: {strategy}\n"
        f"Market: {question[:80]}\n"
        f"Side: {side} @ ${price:.4f}\n"
        f"Size: ${size:.2f}"
    )
    if action == "closed":
        msg += f"\nPnL: ${pnl:+.2f}"
    await send_telegram(msg)


async def notify_error(category: str, message: str) -> None:
    """Send error notification."""
    msg = f"⚠️ <b>ERROR: {category}</b>\n{message}"
    await send_telegram(msg)


async def notify_strategy_paused(strategy: str, reason: str) -> None:
    """Notify when a strategy is paused by the learner."""
    msg = (
        f"⏸️ <b>Strategy Paused</b>\n"
        f"Strategy: {strategy}\n"
        f"Reason: {reason}\n"
        f"Cooldown: 24 hours"
    )
    await send_telegram(msg)


async def notify_risk_limit(limit_type: str, current: float, threshold: float) -> None:
    """Notify when a risk limit is hit (daily loss, drawdown)."""
    msg = (
        f"🚨 <b>Risk Limit Hit</b>\n"
        f"Type: {limit_type}\n"
        f"Current: {current:.1%}\n"
        f"Threshold: {threshold:.1%}\n"
        f"Trading paused automatically."
    )
    await send_telegram(msg)


async def notify_daily_target(equity: float, daily_pnl: float, target_pct: float) -> None:
    """Notify when the daily profit target is achieved."""
    msg = (
        f"🎯 <b>Daily Target Reached!</b>\n"
        f"Equity: ${equity:.2f}\n"
        f"Daily PnL: ${daily_pnl:+.2f}\n"
        f"Target: {target_pct:.1%}\n"
        f"Strategy edges will tighten."
    )
    await send_telegram(msg)


async def notify_daily_summary(
    equity: float, daily_pnl: float, daily_return: float, trades: int, win_rate: float
) -> None:
    """Send daily performance summary."""
    emoji = "📈" if daily_pnl >= 0 else "📉"
    msg = (
        f"{emoji} <b>Daily Summary</b>\n"
        f"Equity: ${equity:.2f}\n"
        f"Daily PnL: ${daily_pnl:+.2f} ({daily_return:+.1%})\n"
        f"Trades: {trades}\n"
        f"Win Rate: {win_rate:.0%}"
    )
    await send_telegram(msg)
