"""Trade Assistant API — parse free-text commands and execute trades.

Supports three modes:
- EXECUTE: URL + trade intent -> place order via CLOB
- ANALYZE: URL without trade intent -> fetch market data + Claude analysis
- SEARCH: no URL -> search Gamma API + Claude analysis
"""

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

# Trade intent keywords — presence of these + URL means EXECUTE mode
_TRADE_KEYWORDS = frozenset({
    "buy", "sell", "exit", "close", "dump", "trade", "execute", "place",
    "comprar", "vender", "sair", "fechar", "apostar", "entrar", "executar",
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


def has_trade_intent(message: str) -> bool:
    """Check if message contains explicit trade keywords (buy/sell/etc)."""
    lower = message.lower()
    # Dollar amount is strong trade intent signal
    if _AMOUNT_RE.search(message) or _AMOUNT_BARE_RE.search(lower):
        return True
    return any(_keyword_matches(kw, lower) for kw in _TRADE_KEYWORDS)


def detect_mode(message: str) -> str:
    """Detect assistant mode from message content.

    Returns "execute", "analyze", or "search".
    """
    url = extract_url(message)
    if url and has_trade_intent(message):
        return "execute"
    if url:
        return "analyze"
    return "search"


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


def _format_market_summary(market: dict) -> str:
    """Format a single market's data for Claude analysis."""
    question = market.get("question", "Unknown")
    outcomes = _parse_json_field(market.get("outcomes", "[]"))
    prices = _parse_json_field(market.get("outcomePrices", "[]"))
    volume = market.get("volume", 0)
    end_date = market.get("endDate", "Unknown")

    parts = [f"  Q: {question}"]
    for i, outcome in enumerate(outcomes):
        price = float(prices[i]) if i < len(prices) else 0.0
        pct = price * 100
        parts.append(f"    {outcome}: {pct:.0f}c (${price:.2f})")
    if volume:
        try:
            parts.append(f"    Volume: ${float(volume):,.0f}")
        except (ValueError, TypeError):
            pass
    if end_date and end_date != "Unknown":
        parts.append(f"    Ends: {end_date}")
    return "\n".join(parts)


def _format_event_summary(event: dict) -> str:
    """Format an event from search results for Claude analysis."""
    title = event.get("title", "Unknown")
    volume = event.get("volume", 0)
    end_date = event.get("endDate", "Unknown")
    markets = event.get("markets", [])

    parts = [f"- {title}"]
    if volume:
        try:
            parts[0] += f" (Volume: ${float(volume):,.0f})"
        except (ValueError, TypeError):
            pass
    if end_date and end_date != "Unknown":
        parts.append(f"  Ends: {end_date}")

    for mkt in markets[:5]:  # Limit markets per event
        outcomes = _parse_json_field(mkt.get("outcomes", "[]"))
        prices = _parse_json_field(mkt.get("outcomePrices", "[]"))
        q = mkt.get("question", "")
        if q:
            price_parts = []
            for i, out in enumerate(outcomes):
                p = float(prices[i]) if i < len(prices) else 0.0
                price_parts.append(f"{out}:{p * 100:.0f}c")
            prices_str = ", ".join(price_parts) if price_parts else ""
            parts.append(f"  - {q} [{prices_str}]")

    return "\n".join(parts)


# --- Claude LLM helper ---

_ANALYZE_SYSTEM = (
    "You are a Polymarket trading analyst. Analyze this market and recommend "
    "a trade. Be concise (3-5 sentences). Include: which side to buy, at what "
    "price, and why. Consider: probability edge, liquidity, time to resolution, "
    "fees (~2%). Respond in the same language as the user's message."
)

_SEARCH_SYSTEM = (
    "You are a Polymarket trading analyst. The user is looking for trading "
    "opportunities. Analyze these markets and suggest the best 1-3 "
    "opportunities. Be concise. Consider probability edge, volume, and timing. "
    "Respond in the same language as the user's message."
)


async def _ask_claude(system_prompt: str, user_message: str) -> str:
    """Call Claude Haiku for analysis. Returns empty string on failure."""
    from bot.config import settings

    if not settings.anthropic_api_key:
        return "(LLM analysis unavailable -- no API key)"
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text
    except Exception as e:
        logger.error("assistant_llm_failed", error=str(e))
        return f"(Analysis error: {e})"


# --- API endpoints ---


@router.post("/execute", response_model=AssistantResponse)
@limiter.limit("5/minute")
async def execute_trade_assistant(
    request: Request,
    body: AssistantRequest,
    _: str = Depends(verify_api_key),
):
    """Parse a free-text trade command and execute it via CLOB."""
    return await _execute_trade_logic(body)


async def _execute_trade_logic(body: AssistantRequest) -> AssistantResponse:
    """Core trade execution logic, shared by /execute and /analyze endpoints."""
    log: list[str] = []
    message = body.message.strip()
    log.append(f"Received: {message}")

    # Step 1: Extract URL
    url = extract_url(message)
    if not url:
        return AssistantResponse(
            success=False,
            mode="execute",
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
            mode="execute",
            log=log + [f"Event not found for slug: {slug}"],
            error=f"Event not found for slug: {slug}",
        )

    event_title = event.get("title", slug)
    markets = event.get("markets", [])
    log.append(f"Event: {event_title} ({len(markets)} market(s))")

    if not markets:
        return AssistantResponse(
            success=False,
            mode="execute",
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
            mode="execute",
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
            mode="execute",
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
            mode="execute",
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
            mode="execute",
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
            mode="execute",
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
        mode="execute",
        log=log,
        market_title=event_title,
        outcome=resolved_outcome,
        side=side,
        price=order_price,
        shares=shares,
        cost=cost,
        order_id=order_id,
    )


@router.post("/analyze", response_model=AssistantResponse)
@limiter.limit("10/minute")
async def analyze_assistant(
    request: Request,
    body: AssistantRequest,
    _: str = Depends(verify_api_key),
):
    """Smart assistant: auto-detects mode (execute/analyze/search)."""
    message = body.message.strip()
    mode = detect_mode(message)

    if mode == "execute":
        return await _execute_trade_logic(body)

    if mode == "analyze":
        return await _handle_analyze(message)

    return await _handle_search(message)


async def _handle_analyze(message: str) -> AssistantResponse:
    """Fetch market data from URL and provide Claude analysis."""
    log: list[str] = []
    log.append(f"Received: {message}")
    log.append("Mode: ANALYZE")

    url = extract_url(message)
    if not url:
        return AssistantResponse(
            success=False, mode="analyze",
            log=log + ["No URL found."], error="No URL found.",
        )

    slug = extract_slug(url)
    log.append(f"Slug: {slug}")

    event = await _fetch_event(slug)
    if not event:
        return AssistantResponse(
            success=False, mode="analyze",
            log=log + [f"Event not found: {slug}"],
            error=f"Event not found: {slug}",
        )

    event_title = event.get("title", slug)
    markets = event.get("markets", [])
    log.append(f"Event: {event_title} ({len(markets)} market(s))")

    if not markets:
        return AssistantResponse(
            success=False, mode="analyze",
            log=log + ["No markets in this event."],
            error="No markets in this event.",
        )

    # Build market summary for Claude
    market_lines = []
    for mkt in markets:
        market_lines.append(_format_market_summary(mkt))
    market_text = "\n".join(market_lines)

    log.append("--- Market Data ---")
    for mkt in markets:
        question = mkt.get("question", "Unknown")
        outcomes = _parse_json_field(mkt.get("outcomes", "[]"))
        prices = _parse_json_field(mkt.get("outcomePrices", "[]"))
        price_parts = []
        for i, out in enumerate(outcomes):
            p = float(prices[i]) if i < len(prices) else 0.0
            price_parts.append(f"{out}: {p * 100:.0f}c")
        log.append(f"{question} -- {', '.join(price_parts)}")

    # Ask Claude for analysis
    user_msg = (
        f"Event: {event_title}\n"
        f"Markets:\n{market_text}\n\n"
        f"User message: {message}"
    )
    analysis = await _ask_claude(_ANALYZE_SYSTEM, user_msg)

    log.append("--- Analysis ---")
    log.append(analysis)

    return AssistantResponse(
        success=True, mode="analyze", log=log,
        market_title=event_title,
    )


async def _handle_search(message: str) -> AssistantResponse:
    """Search Gamma API for markets matching the query."""
    log: list[str] = []
    log.append(f"Received: {message}")
    log.append("Mode: SEARCH")

    events = await _search_events(message)
    if not events:
        return AssistantResponse(
            success=False, mode="search",
            log=log + ["No markets found for this query."],
            error="No markets found for this query.",
        )

    log.append(f"Found {len(events)} event(s)")

    # Build summary for Claude
    event_lines = []
    for ev in events:
        event_lines.append(_format_event_summary(ev))
        # Also add to log for the user
        title = ev.get("title", "Unknown")
        markets = ev.get("markets", [])
        log.append(f"--- {title} ({len(markets)} market(s)) ---")
        for mkt in markets[:5]:
            question = mkt.get("question", "")
            outcomes = _parse_json_field(mkt.get("outcomes", "[]"))
            prices = _parse_json_field(mkt.get("outcomePrices", "[]"))
            price_parts = []
            for i, out in enumerate(outcomes):
                p = float(prices[i]) if i < len(prices) else 0.0
                price_parts.append(f"{out}: {p * 100:.0f}c")
            if question:
                log.append(f"  {question} -- {', '.join(price_parts)}")

    events_text = "\n".join(event_lines)

    # Ask Claude for analysis
    user_msg = (
        f"Markets found:\n{events_text}\n\n"
        f"User query: {message}"
    )
    analysis = await _ask_claude(_SEARCH_SYSTEM, user_msg)

    log.append("--- Analysis ---")
    log.append(analysis)

    return AssistantResponse(success=True, mode="search", log=log)


# --- Gamma API helpers ---


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


async def _search_events(query: str) -> list[dict]:
    """Search Gamma API for events matching a query.

    Returns up to 5 open events, or empty list on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{GAMMA_API_URL}/events",
                params={"_q": query, "closed": "false", "_limit": 5},
            )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return []
    except httpx.HTTPStatusError as exc:
        logger.error(
            "gamma_search_http_error", query=query,
            status=exc.response.status_code, error=str(exc),
        )
        return []
    except Exception as exc:
        logger.error("gamma_search_network_error", query=query, error=str(exc))
        return []
