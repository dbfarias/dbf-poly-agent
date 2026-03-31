"""Trade Assistant API — parse free-text commands and execute trades."""

import json
import math
import re

import httpx
import structlog
from fastapi import APIRouter, Depends, Request

from api.dependencies import get_engine
from api.middleware import verify_api_key
from api.rate_limit import limiter
from api.schemas import AssistantRequest, AssistantResponse
from bot.polymarket.client import TICK_SIZE
from bot.polymarket.types import OrderSide

logger = structlog.get_logger()

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

GAMMA_API_URL = "https://gamma-api.polymarket.com"

# --- Parsing helpers (pure functions, no side effects) ---

_URL_RE = re.compile(r"https?://polymarket\.com/\S+")
_AMOUNT_RE = re.compile(r"\$\s?(\d+(?:\.\d+)?)")
_AMOUNT_BARE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:dollars?|usd)\b", re.IGNORECASE)

_NO_KEYWORDS = frozenset({
    # English
    "no", "not win", "lose", "loss", "against", "not", "won't", "wont",
    "defeat", "fail", "draw",
    # Portuguese
    "não ganha", "nao ganha", "não vence", "nao vence", "perde",
    "perder", "contra", "empate", "não", "nao",
})
_YES_KEYWORDS = frozenset({
    # English
    "yes", "win", "wins", "victory", "for",
    # Portuguese
    "ganha", "vence", "ganhar", "vencer", "vitória", "vitoria", "sim",
})
_SELL_KEYWORDS = frozenset({
    "sell", "exit", "close", "dump",
    "vender", "sair", "fechar",
})


def extract_url(message: str) -> str | None:
    """Extract the first Polymarket URL from a message."""
    match = _URL_RE.search(message)
    return match.group(0) if match else None


def extract_slug(url: str) -> str:
    """Extract the slug (last path segment) from a Polymarket URL.

    Handles URLs like:
      https://polymarket.com/sports/fifa-friendlies/fif-ita-nir-2026-03-26
      https://polymarket.com/event/some-event-slug
    Strips query params and trailing slashes.
    """
    path = url.split("?")[0].rstrip("/")
    return path.rsplit("/", maxsplit=1)[-1]


def parse_amount(message: str) -> float:
    """Extract dollar amount from message. Defaults to 5.0."""
    match = _AMOUNT_RE.search(message)
    if match:
        return float(match.group(1))
    match = _AMOUNT_BARE_RE.search(message)
    if match:
        return float(match.group(1))
    return 5.0


def _keyword_matches(keyword: str, text: str) -> bool:
    """Check if keyword appears in text.

    Single-word keywords use word-boundary matching to avoid false positives
    (e.g. "no" matching "northern"). Multi-word phrases use substring match.
    """
    if " " not in keyword:
        return bool(re.search(r"\b" + re.escape(keyword) + r"\b", text))
    return keyword in text


def parse_intent(message: str) -> tuple[str, str]:
    """Parse side (BUY/SELL) and outcome preference (Yes/No) from message.

    Returns (side, outcome_hint) where outcome_hint is "Yes", "No", or "".
    """
    lower = message.lower()

    side = "BUY"
    for kw in _SELL_KEYWORDS:
        if _keyword_matches(kw, lower):
            side = "SELL"
            break

    outcome_hint = ""
    # Check NO keywords first (more specific patterns like "not win")
    for kw in sorted(_NO_KEYWORDS, key=len, reverse=True):
        if _keyword_matches(kw, lower):
            outcome_hint = "No"
            break
    if not outcome_hint:
        for kw in _YES_KEYWORDS:
            if _keyword_matches(kw, lower):
                outcome_hint = "Yes"
                break

    return side, outcome_hint


def _find_best_market(
    markets: list[dict], message: str, outcome_hint: str,
) -> tuple[dict | None, str]:
    """Pick the best matching market and resolve the outcome token.

    For multi-market events (e.g. "Italy", "Northern Ireland", "Draw"),
    match the market whose question or outcomes contain a user keyword.

    Returns (market_dict, resolved_outcome).
    """
    lower = message.lower()

    # Single-market event — straightforward
    if len(markets) == 1:
        mkt = markets[0]
        outcomes = _parse_json_field(mkt.get("outcomes", "[]"))
        resolved = outcome_hint if outcome_hint else "Yes"
        # Validate outcome exists
        if resolved not in outcomes and outcomes:
            resolved = outcomes[0]
        return mkt, resolved

    # Multi-market event — find the market whose question matches user text
    best_market = None
    best_score = -1
    for mkt in markets:
        q = mkt.get("question", "").lower()
        group_title = mkt.get("groupItemTitle", "").lower()
        outcomes = _parse_json_field(mkt.get("outcomes", "[]"))

        score = 0
        # Check if any word from the user message appears in the market question
        for word in lower.split():
            if len(word) >= 3 and word in q:
                score += 2
            if len(word) >= 3 and word in group_title:
                score += 1
            for out in outcomes:
                if word in out.lower():
                    score += 3

        if score > best_score:
            best_score = score
            best_market = mkt

    if best_market is None:
        best_market = markets[0]

    outcomes = _parse_json_field(best_market.get("outcomes", "[]"))
    resolved = outcome_hint if outcome_hint and outcome_hint in outcomes else "Yes"
    if resolved not in outcomes and outcomes:
        resolved = outcomes[0]

    return best_market, resolved


def _parse_json_field(value) -> list:
    """Parse a field that may be a JSON string or already a list."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _get_token_and_price(
    market: dict, outcome: str,
) -> tuple[str | None, float | None]:
    """Get token_id and price for the chosen outcome."""
    outcomes = _parse_json_field(market.get("outcomes", "[]"))
    prices = _parse_json_field(market.get("outcomePrices", "[]"))
    token_ids = _parse_json_field(market.get("clobTokenIds", "[]"))

    if not outcomes or not token_ids:
        return None, None

    try:
        idx = outcomes.index(outcome)
    except ValueError:
        return None, None

    token_id = token_ids[idx] if idx < len(token_ids) else None
    price = float(prices[idx]) if idx < len(prices) else None
    return token_id, price


# --- API endpoint ---


@router.post("/execute", response_model=AssistantResponse)
@limiter.limit("5/minute")
async def execute_trade_assistant(
    request: Request,
    body: AssistantRequest,
    _: str = Depends(verify_api_key),
):
    """Parse a free-text trade command and execute it via CLOB."""
    log: list[str] = []
    message = body.message.strip()
    log.append(f"Received: {message}")

    # Step 1: Extract URL
    url = extract_url(message)
    if not url:
        return AssistantResponse(
            success=False,
            log=log + ["No Polymarket URL found in message."],
            error="No Polymarket URL found in message.",
        )
    log.append(f"URL: {url}")

    # Step 2: Extract slug
    slug = extract_slug(url)
    log.append(f"Slug: {slug}")

    # Step 3: Fetch event from Gamma API
    event = await _fetch_event(slug)
    if not event:
        return AssistantResponse(
            success=False,
            log=log + [f"Event not found for slug: {slug}"],
            error=f"Event not found for slug: {slug}",
        )

    event_title = event.get("title", slug)
    markets = event.get("markets", [])
    log.append(f"Event: {event_title} ({len(markets)} market(s))")

    if not markets:
        return AssistantResponse(
            success=False,
            log=log + ["No markets in this event."],
            error="No markets in this event.",
        )

    # Step 4: Parse intent
    side, outcome_hint = parse_intent(message)
    amount = parse_amount(message)
    log.append(f"Intent: {side} {outcome_hint or '(auto)'}, amount=${amount:.2f}")

    # Step 5: Find the best matching market
    market, resolved_outcome = _find_best_market(markets, message, outcome_hint)
    if market is None:
        return AssistantResponse(
            success=False,
            log=log + ["Could not match a market from the event."],
            error="Could not match a market from the event.",
        )

    market_question = market.get("question", "")
    log.append(f"Market: {market_question}")
    log.append(f"Outcome: {resolved_outcome}")

    # Step 6: Get token_id and price
    token_id, price = _get_token_and_price(market, resolved_outcome)
    if not token_id or not price or price <= 0:
        return AssistantResponse(
            success=False,
            log=log + [f"Could not resolve token/price for outcome: {resolved_outcome}"],
            error=f"Could not resolve token/price for outcome: {resolved_outcome}",
        )

    log.append(f"Token: {token_id[:20]}...")
    log.append(f"Price: ${price:.2f}")

    # Step 7: Calculate shares
    shares = round(amount / price, 2)
    if shares < 1.0:
        return AssistantResponse(
            success=False,
            log=log + [f"Order too small: {shares} shares (min 1.0)"],
            error=f"Order too small: {shares} shares at ${price:.2f}",
        )
    log.append(f"Shares: {shares}")

    # Step 8: Round price to tick size (BUY rounds up)
    order_side = OrderSide.BUY if side == "BUY" else OrderSide.SELL
    if order_side == OrderSide.BUY:
        order_price = round(math.ceil(price / TICK_SIZE) * TICK_SIZE, 2)
    else:
        order_price = round(math.floor(price / TICK_SIZE) * TICK_SIZE, 2)

    cost = round(shares * order_price, 2)
    log.append(f"Placing {side} order: {shares} shares @ ${order_price:.2f} = ${cost:.2f}")

    # Step 9: Place order
    try:
        engine = get_engine()
        result = await engine.clob_client.place_order(
            token_id=token_id,
            side=order_side,
            price=order_price,
            size=shares,
        )
    except Exception as exc:
        logger.error("assistant_order_failed", error=str(exc))
        return AssistantResponse(
            success=False,
            log=log + [f"Order failed: {exc}"],
            market_title=event_title,
            outcome=resolved_outcome,
            side=side,
            price=order_price,
            shares=shares,
            cost=cost,
            error=str(exc),
        )

    # Check for CLOB-level errors
    if result.get("error"):
        error_msg = result["error"]
        log.append(f"CLOB error: {error_msg}")
        return AssistantResponse(
            success=False,
            log=log,
            market_title=event_title,
            outcome=resolved_outcome,
            side=side,
            price=order_price,
            shares=shares,
            cost=cost,
            error=error_msg,
        )

    order_id = result.get("orderID", result.get("order_id", ""))
    log.append(f"Order placed! ID: {order_id}")

    return AssistantResponse(
        success=True,
        log=log,
        market_title=event_title,
        outcome=resolved_outcome,
        side=side,
        price=order_price,
        shares=shares,
        cost=cost,
        order_id=order_id,
    )


async def _fetch_event(slug: str) -> dict | None:
    """Fetch event from Gamma API by slug.

    Returns None on both "not found" and network errors, but logs
    differently to aid debugging.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{GAMMA_API_URL}/events",
                params={"slug": slug},
            )
            if resp.status_code == 404:
                logger.info("gamma_event_not_found", slug=slug)
                return None
            resp.raise_for_status()
            data = resp.json()
            if data and isinstance(data, list) and len(data) > 0:
                return data[0]
            # Empty response — event does not exist
            logger.info("gamma_event_empty_response", slug=slug)
            return None
    except httpx.HTTPStatusError as exc:
        logger.error(
            "gamma_event_http_error", slug=slug,
            status=exc.response.status_code, error=str(exc),
        )
        return None
    except Exception as exc:
        logger.error("gamma_event_network_error", slug=slug, error=str(exc))
        return None
