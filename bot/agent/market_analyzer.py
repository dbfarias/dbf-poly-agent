"""Market scanner and analyzer for identifying trading opportunities."""

import re
from datetime import datetime, timezone

import structlog

from bot.config import CapitalTier
from bot.data.database import async_session
from bot.data.market_cache import MarketCache
from bot.data.models import MarketScan
from bot.data.repositories import MarketScanRepository, PositionRepository
from bot.polymarket.client import PolymarketClient
from bot.polymarket.gamma import GammaClient
from bot.polymarket.types import GammaMarket, TradeSignal

from .strategies.base import BaseStrategy

logger = structlog.get_logger()

# Sports/esports keywords for question-text-based filtering.
# Gamma API category field is unreliable (groupItemTitle is not a category),
# so we detect market type by matching keywords in the question text.
_SPORTS_KEYWORDS = re.compile(
    r"\b("
    # Leagues and governing bodies
    r"nba|nfl|nhl|mlb|mls|ufc|afl|epl|serie a|la liga|bundesliga|ligue 1"
    r"|premier league|champions league|europa league|copa libertadores"
    r"|world cup|super bowl|stanley cup"
    # College sports
    r"|march madness|final four|ncaa|big east|big ten|big 12|sec championship"
    r"|acc tournament|pac-12|college basketball|college football"
    # Tennis
    r"|antalya|roland garros|wimbledon|us open tennis|australian open tennis"
    r"|atp |wta |grand slam"
    # Sports betting terms
    r"|spread[:\s]|o/u\s|over/under|moneyline|handicap|point spread"
    r"|total points|total goals|first half|second half|first quarter"
    # Game actions
    r"|touchdown|field goal|three-pointer|home run|penalty kick"
    r"|slam dunk|free throw|rushing yards|passing yards|quarterback|wide receiver"
    # General sports patterns
    r"|championship|playoff|semifinals|quarterfinals|round of 16|group stage"
    # NBA teams
    r"|raptors|nuggets|pelicans|panthers|islanders|lightning|jets|kings"
    r"|lakers|celtics|warriors|bucks|heat|knicks|nets|bulls|suns|76ers"
    r"|cavaliers|mavericks|rockets|pacers|hawks|pistons|spurs|grizzlies"
    r"|timberwolves|clippers|blazers|wizards|hornets|magic"
    # NFL teams
    r"|chiefs|eagles|cowboys|49ers|ravens|bills|lions|bengals|dolphins"
    r"|steelers|texans|vikings|packers|broncos|chargers|rams|seahawks"
    r"|commanders|bears|saints|falcons|cardinals|colts|jaguars|titans"
    r"|patriots|giants|raiders|browns|buccaneers"
    # MLB teams
    r"|yankees|dodgers|mets|braves|astros|padres|phillies|orioles"
    r"|red sox|cubs|brewers|guardians|royals|rangers|twins|tigers|marlins"
    # NHL teams
    r"|maple leafs|bruins|oilers|hurricanes|avalanche|capitals"
    r"|penguins|blue jackets|predators|wild|sabres|red wings|senators"
    r"|canucks|flames|blackhawks|kraken|sharks|ducks|coyotes|flyers"
    # European soccer
    r"|real madrid|barcelona|bayern|juventus|psg|manchester"
    r"|chelsea|arsenal|liverpool|tottenham|atletico"
    # Liga MX / MLS
    r"|pumas|unam|santos laguna|necaxa|toluca|leon|puebla|queretaro|mazatlan"
    r"|tigres|monterrey|america|chivas|cruz azul"
    r"|inter miami|la galaxy|atlanta united|seattle sounders|portland timbers"
    r"|nashville sc|orlando city|charlotte fc|st\. louis city|austin fc"
    r"|fc cincinnati|columbus crew|new york red bulls|new york city fc"
    r"|sporting kc|minnesota united|vancouver whitecaps|cf montreal"
    r"|dc united|chicago fire"
    # College teams (commonly traded)
    r"|uconn|gonzaga|duke|kentucky|north carolina|villanova|kansas|baylor"
    # Esports — games
    r"|valorant|counter-strike|dota|league of legends|overwatch"
    r"|esports|e-sports|csgo|cs2|cs:go|fortnite|pubg|call of duty"
    r"|rocket league|rainbow six|apex legends|starcraft|hearthstone"
    r"|smash bros|tekken|street fighter|mortal kombat"
    # Esports — match formats
    r"|bo1|bo3|bo5|best of 3|best of 5|best of 7"
    r"|game [1-9]|map [1-9]|set [1-9]|round [1-9]"
    # Esports — in-game events
    r"|first blood|first kill|first tower|first dragon|first baron"
    r"|first roshan|ace |clutch |mvp |pentakill|quadrakill|triple kill"
    r"|pistol round|knife round|overtime"
    # Esports — orgs and tournaments
    r"|fnatic|cloud9|team liquid|g2 esports|navi|faze clan|100 thieves"
    r"|t1 |gen\.?g|drx |nrg |sentinels|loud |mibr|furia"
    r"|worlds 202|msi 202|vct |pgl |esl |iem |blast premier|dreamhack"
    r"|lck |lpl |lec |lcs |cblol"
    r")",
    re.IGNORECASE,
)

# Simple heuristic: "Will X win on YYYY-MM-DD?" is almost always a sports match
_SPORTS_WIN_ON_PATTERN = re.compile(
    r"will .+ win on \d{4}-\d{2}-\d{2}", re.IGNORECASE,
)

# Second-pass heuristic: "X vs. Y" or "X vs Y" patterns are almost always sports
# Catches formats like "Antalya 2: Player A vs Player B"
_SPORTS_VS_PATTERN = re.compile(
    r"(?:^|\:\s*).+\bvs\.?\s+.+", re.IGNORECASE,
)


def classify_market_type(question: str) -> str:
    """Classify market type by question text. Returns 'sports', 'crypto', or 'other'."""
    if (
        _SPORTS_KEYWORDS.search(question)
        or _SPORTS_WIN_ON_PATTERN.search(question)
        or _SPORTS_VS_PATTERN.search(question)
    ):
        return "sports"
    # Crypto detection (from price_divergence)
    crypto_pattern = re.compile(
        r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|cardano|ada"
        r"|dogecoin|doge|polkadot|dot|chainlink|link|avalanche|avax"
        r"|polygon|matic|litecoin|ltc|uniswap|uni|aave|crypto)\b",
        re.IGNORECASE,
    )
    if crypto_pattern.search(question):
        return "crypto"
    return "other"


# Category normalization: group related categories together
# All political categories map to "Politics" for concentration checks
_CATEGORY_GROUPS = {
    "politics": "Politics",
    "republican primary": "Politics",
    "democratic primary": "Politics",
    "u.s. elections": "Politics",
    "us elections": "Politics",
    "elections": "Politics",
    "midterms": "Politics",
    "senate": "Politics",
    "congress": "Politics",
    "governor": "Politics",
    "presidential": "Politics",
}


def normalize_category(category: str) -> str:
    """Normalize market category for concentration checks.

    Groups related categories (e.g. all political categories → "Politics")
    to prevent over-concentration in a single domain.
    """
    if not category:
        return "Other"
    key = category.lower().strip()
    return _CATEGORY_GROUPS.get(key, category)


class MarketAnalyzer:
    """Scans markets and runs strategies to find opportunities."""

    # Quality filter thresholds
    MAX_SPREAD = 0.04  # 4 cents max spread
    MAX_CATEGORY_POSITIONS = 3  # Max pending+open per normalized category
    MIN_BID_RATIO = 0.50  # Best bid must be >= 50% of fair price
    MIN_VOLUME_24H = 150.0  # Minimum 24h volume to avoid dead/thin markets

    def __init__(
        self,
        gamma_client: GammaClient,
        cache: MarketCache,
        strategies: list[BaseStrategy],
        clob_client: PolymarketClient | None = None,
        price_tracker=None,
        correlation_detector=None,
    ):
        self.gamma = gamma_client
        self.cache = cache
        self.strategies = strategies
        self.clob = clob_client
        self._price_tracker = price_tracker
        self._correlation_detector = correlation_detector
        self.disabled_strategies: set[str] = set()
        # Market type blocklist: skip markets classified as these types
        # Types: "sports", "crypto", "other" (from classify_market_type)
        self.blocked_market_types: set[str] = set()

    async def scan_markets(self, tier: CapitalTier) -> list[TradeSignal]:
        """Scan all markets and return ranked signals from all enabled strategies."""
        # Fetch active markets + short-term markets, merge and deduplicate
        try:
            markets = await self.gamma.get_active_markets(limit=500)
            self.cache.set_markets_bulk(markets, ttl=120)
            logger.info("markets_fetched", count=len(markets))
        except Exception as e:
            logger.error("market_fetch_failed", error=str(e))
            markets = self.cache.get_all_markets()
            if not markets:
                return []

        # Shared dedup set for merging all supplementary sources
        existing_ids = {m.id for m in markets}

        # Merge short-term markets (resolving within 48h) for more signals
        try:
            short_term = await self.gamma.get_short_term_markets(
                max_hours=48, min_volume_24h=30,
            )
            added = self._merge_markets(markets, existing_ids, short_term)
            if added:
                logger.info("short_term_markets_merged", new=added, total=len(markets))
        except Exception as e:
            logger.warning("short_term_fetch_failed", error=str(e))

        # Merge new markets (recently created, may not be in top-500 yet)
        try:
            new_markets = await self.gamma.get_new_markets(limit=100, min_volume=10.0)
            added = self._merge_markets(markets, existing_ids, new_markets)
            if added:
                logger.info("new_markets_merged", new=added, total=len(markets))
        except Exception as e:
            logger.warning("new_markets_fetch_failed", error=str(e))

        # Merge trending markets (highest 24h volume)
        try:
            trending = await self.gamma.get_trending_markets(
                limit=100, min_volume_24h=100.0,
            )
            added = self._merge_markets(markets, existing_ids, trending)
            if added:
                logger.info("trending_markets_merged", new=added, total=len(markets))
        except Exception as e:
            logger.warning("trending_markets_fetch_failed", error=str(e))

        # Merge breaking markets (new + high activity = breaking news)
        try:
            breaking = await self.gamma.get_breaking_markets(
                limit=50, max_age_hours=24.0, min_volume_24h=50.0,
            )
            added = self._merge_markets(markets, existing_ids, breaking)
            if added:
                logger.info("breaking_markets_merged", new=added, total=len(markets))
        except Exception as e:
            logger.warning("breaking_markets_fetch_failed", error=str(e))

        # Apply quality filter before strategy evaluation
        markets = await self._filter_quality(markets)

        # Feed prices into shared tracker (after quality filter)
        if self._price_tracker:
            prices = {
                m.id: m.best_bid_price
                for m in markets
                if m.id and m.best_bid_price
            }
            self._price_tracker.record_batch(prices)
            self._price_tracker.evict_stale(set(prices.keys()))

        # Run enabled strategies
        all_signals: list[TradeSignal] = []
        for strategy in self.strategies:
            if strategy.name in self.disabled_strategies:
                continue
            if not strategy.is_enabled_for_tier(tier):
                continue

            try:
                signals = await strategy.scan(markets)
                all_signals.extend(signals)
                logger.info(
                    "strategy_scan_complete",
                    strategy=strategy.name,
                    signals=len(signals),
                )
            except Exception as e:
                logger.error(
                    "strategy_scan_failed",
                    strategy=strategy.name,
                    error=str(e),
                )

        # Record scan results
        await self._record_scans(markets, all_signals)

        # Deduplicate correlated markets — keep only the best signal per group
        all_signals = self._deduplicate_correlated(all_signals)

        # Rank signals by edge * confidence
        all_signals.sort(key=lambda s: s.edge * s.confidence, reverse=True)
        return all_signals

    # Universal stop-loss thresholds
    STOP_LOSS_PCT = 0.15  # Exit if lost 15%+ of entry price (was 40%)
    NEAR_WORTHLESS_PRICE = 0.10  # Always exit below 10 cents
    DEFAULT_EXIT_PRICE = 0.70  # Fallback exit for unmatched strategies
    MAX_POSITION_AGE_HOURS = 72.0  # Auto-close after 3 days (capital efficiency)
    TAKE_PROFIT_PRICE = 0.95  # Lock in profit near certainty
    TAKE_PROFIT_MIN_HOLD_HOURS = 6.0  # Faster TP lock-in (was 12h)

    async def check_exits(
        self, positions: list, tier: CapitalTier
    ) -> list[tuple[str, str]]:
        """Check if any open positions should be exited.

        Returns list of (market_id, exit_reason) tuples so callers can
        propagate the reason through to the trade DB and learner.
        """
        exits: list[tuple[str, str]] = []
        exited_ids: set[str] = set()
        for position in positions:
            # 1. Strategy-specific exit check
            strategy_matched = False
            for strategy in self.strategies:
                if strategy.name == position.strategy:
                    strategy_matched = True
                    try:
                        should_exit = await strategy.should_exit(
                            position.market_id,
                            position.current_price,
                            avg_price=position.avg_price,
                            created_at=position.created_at,
                            question=position.question,
                        )
                        if should_exit:
                            reason = (
                                should_exit
                                if isinstance(should_exit, str)
                                else f"{strategy.name}_exit"
                            )
                            exits.append((position.market_id, reason))
                            exited_ids.add(position.market_id)
                            logger.info(
                                "exit_signal",
                                strategy=strategy.name,
                                market_id=position.market_id,
                                reason=reason,
                            )
                    except Exception as e:
                        logger.error(
                            "exit_check_failed",
                            strategy=strategy.name,
                            market_id=position.market_id,
                            error=str(e),
                        )
                    break

            # 2. Universal stop-loss for ALL positions (including unmatched strategies)
            if position.market_id not in exited_ids:
                exit_reason = self._check_stop_loss(position, strategy_matched)
                if exit_reason:
                    exits.append((position.market_id, exit_reason))
                    exited_ids.add(position.market_id)
                    logger.info(
                        "stop_loss_exit",
                        market_id=position.market_id,
                        strategy=position.strategy,
                        reason=exit_reason,
                        avg_price=round(position.avg_price, 4),
                        current_price=round(position.current_price, 4),
                    )
        return exits

    def _check_stop_loss(self, position, strategy_matched: bool) -> str | None:
        """Universal stop-loss check. Returns exit reason or None."""
        now = datetime.now(timezone.utc)

        # Normalize created_at once for reuse in age/take-profit checks
        created = getattr(position, "created_at", None)
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_hours = (
            (now - created).total_seconds() / 3600
            if created is not None
            else None
        )

        # Near-worthless: always exit
        if position.current_price < self.NEAR_WORTHLESS_PRICE:
            return f"near_worthless (price={position.current_price:.4f})"

        # Loss exceeds stop-loss threshold
        if position.avg_price > 0:
            loss_pct = (
                (position.avg_price - position.current_price) / position.avg_price
            )
            if loss_pct >= self.STOP_LOSS_PCT:
                return f"stop_loss ({loss_pct:.0%} loss)"

        # Max position age: free up capital tied in stale positions
        if age_hours is not None and age_hours > self.MAX_POSITION_AGE_HOURS:
            return f"max_age ({age_hours:.0f}h > {self.MAX_POSITION_AGE_HOURS:.0f}h)"

        # Take profit: lock in gains when price near certainty
        if position.current_price >= self.TAKE_PROFIT_PRICE:
            if age_hours is not None and age_hours >= self.TAKE_PROFIT_MIN_HOLD_HOURS:
                profit_pct = (
                    (position.current_price - position.avg_price) / position.avg_price
                    if position.avg_price > 0 else 0.0
                )
                return (
                    f"take_profit (price={position.current_price:.4f},"
                    f" +{profit_pct:.1%} after {age_hours:.0f}h)"
                )

        # Unmatched strategy: apply default exit threshold
        if not strategy_matched and position.current_price < self.DEFAULT_EXIT_PRICE:
            return f"unmatched_strategy_exit (price={position.current_price:.4f})"

        return None

    async def _filter_quality(
        self, markets: list[GammaMarket]
    ) -> list[GammaMarket]:
        """Filter markets by quality before passing to strategies.

        Checks: binary-only, token IDs present, neg_risk exclusion,
        exit liquidity (bid near fair price), order book spread,
        24h volume, and category diversification.
        """
        # Get current open positions per NORMALIZED category for diversification check
        category_counts: dict[str, int] = {}
        try:
            async with async_session() as session:
                pos_repo = PositionRepository(session)
                open_positions = await pos_repo.get_open()
                for pos in open_positions:
                    cat = normalize_category(pos.category or "Other")
                    category_counts[cat] = category_counts.get(cat, 0) + 1
        except Exception as e:
            logger.warning("quality_filter_position_fetch_failed", error=str(e))

        quality: list[GammaMarket] = []
        filtered_reasons: dict[str, int] = {}

        for market in markets:
            # Binary markets only (2 outcomes)
            if len(market.outcomes) != 2:
                filtered_reasons["not_binary"] = (
                    filtered_reasons.get("not_binary", 0) + 1
                )
                continue

            # Market type filter (keyword-based, since Gamma API has no category)
            if self.blocked_market_types:
                mtype = classify_market_type(market.question)
                if mtype in self.blocked_market_types:
                    filtered_reasons["blocked_type"] = (
                        filtered_reasons.get("blocked_type", 0) + 1
                    )
                    continue

            # Must have token IDs for both outcomes
            if not market.token_ids or len(market.token_ids) < 2:
                filtered_reasons["no_token_ids"] = (
                    filtered_reasons.get("no_token_ids", 0) + 1
                )
                continue

            # Log neg_risk markets but don't filter them — they include
            # high-volume primaries that pass spread/liquidity checks.
            # Individual strategies apply their own criteria.
            if market.neg_risk:
                filtered_reasons["neg_risk_count"] = (
                    filtered_reasons.get("neg_risk_count", 0) + 1
                )

            # 24h volume check (from Gamma API data)
            if market.volume_24h > 0 and market.volume_24h < self.MIN_VOLUME_24H:
                filtered_reasons["low_volume"] = (
                    filtered_reasons.get("low_volume", 0) + 1
                )
                continue

            # Exit liquidity check using Gamma API bid/ask data
            if market.best_bid_price is not None and market.best_ask_price is not None:
                gamma_spread = market.best_ask_price - market.best_bid_price
                if gamma_spread > self.MAX_SPREAD:
                    filtered_reasons["wide_spread_gamma"] = (
                        filtered_reasons.get("wide_spread_gamma", 0) + 1
                    )
                    continue

                # Ensure best bid is reasonable relative to the YES ask price.
                # bestBid/bestAsk from Gamma are for the YES token only,
                # so use best_ask_price as fair price (NOT outcome_price_list
                # which includes both YES and NO sides).
                fair_price = market.best_ask_price
                if fair_price > 0.10 and market.best_bid_price < fair_price * self.MIN_BID_RATIO:
                    filtered_reasons["no_exit_liquidity"] = (
                        filtered_reasons.get("no_exit_liquidity", 0) + 1
                    )
                    continue

            # Category diversification check (using normalized categories)
            cat = normalize_category(market.category or "Other")
            if category_counts.get(cat, 0) >= self.MAX_CATEGORY_POSITIONS:
                filtered_reasons["category_limit"] = (
                    filtered_reasons.get("category_limit", 0) + 1
                )
                continue

            # Order book quality check via CLOB (if Gamma data not available)
            if (
                self.clob
                and market.best_bid_price is None
                and market.best_ask_price is None
            ):
                try:
                    book = self.cache.get_order_book(market.token_ids[0])
                    if book is None:
                        book = await self.clob.get_order_book(market.token_ids[0])
                        self.cache.set_order_book(market.token_ids[0], book, ttl=10)
                    if not book.bids or not book.asks:
                        filtered_reasons["no_liquidity"] = (
                            filtered_reasons.get("no_liquidity", 0) + 1
                        )
                        continue
                    if book.spread is not None and book.spread > self.MAX_SPREAD:
                        filtered_reasons["wide_spread"] = (
                            filtered_reasons.get("wide_spread", 0) + 1
                        )
                        continue
                    # Check bid is near fair price (not $0.001)
                    if book.best_bid is not None and book.best_ask is not None:
                        fair = max(book.best_bid, book.best_ask)
                        if fair > 0.10 and book.best_bid < fair * self.MIN_BID_RATIO:
                            filtered_reasons["no_exit_liquidity"] = (
                                filtered_reasons.get("no_exit_liquidity", 0) + 1
                            )
                            continue
                except Exception:
                    # If order book fetch fails, still allow — don't block on API errors
                    pass

            quality.append(market)

        if filtered_reasons:
            logger.info(
                "quality_filter_applied",
                original=len(markets),
                passed=len(quality),
                reasons=filtered_reasons,
            )

        return quality

    async def _record_scans(
        self, markets: list[GammaMarket], signals: list[TradeSignal]
    ) -> None:
        """Record scan results to database."""
        signal_map = {s.market_id: s for s in signals}
        now = datetime.now(timezone.utc)
        scans = []
        for market in markets[:50]:  # Limit to top 50 markets
            signal = signal_map.get(market.id)

            hours_to_resolution = None
            end = market.end_date
            if end is not None:
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                hours_left = (end - now).total_seconds() / 3600
                if hours_left > 0:
                    hours_to_resolution = round(hours_left, 1)

            scan = MarketScan(
                scanned_at=datetime.now(timezone.utc),
                market_id=market.id,
                question=market.question[:200],
                category=market.category or "",
                yes_price=market.yes_price or 0.0,
                no_price=market.no_price or 0.0,
                volume=market.volume,
                liquidity=market.liquidity,
                end_date=market.end_date,
                hours_to_resolution=hours_to_resolution,
                signal_strategy=signal.strategy if signal else "",
                signal_edge=signal.edge if signal else 0.0,
                signal_confidence=signal.confidence if signal else 0.0,
            )
            scans.append(scan)

        try:
            async with async_session() as session:
                repo = MarketScanRepository(session)
                await repo.create_batch(scans)
        except Exception as e:
            logger.error("scan_record_failed", error=str(e))

    @staticmethod
    def _merge_markets(
        markets: list[GammaMarket],
        existing_ids: set[str],
        new_markets: list[GammaMarket],
    ) -> int:
        """Merge new markets into the list, skipping duplicates. Returns count added."""
        added = 0
        for m in new_markets:
            if m.id not in existing_ids:
                existing_ids.add(m.id)
                markets.append(m)
                added += 1
        return added

    @staticmethod
    def _question_group_key(question: str) -> str:
        """Extract a group key from a question to detect mutually exclusive markets.

        E.g. "Will Albert Littell be the Democratic nominee for Senate in Mississippi?"
        and  "Will Scott Colom be the Democratic nominee for Senate in Mississippi?"
        both map to "be the democratic nominee for senate in mississippi".
        """
        q = question.lower().strip().rstrip("?")
        # Remove "will <name>" prefix — name is 1-4 words before a common verb/preposition
        q = re.sub(r"^will\s+[\w\s]{1,60}?\s+(be\s+)", r"\1", q)
        # Remove leading articles
        q = re.sub(r"^(the|a|an)\s+", "", q)
        return q.strip()

    def _deduplicate_correlated(self, signals: list[TradeSignal]) -> list[TradeSignal]:
        """Keep only the best signal per strategy per group of mutually exclusive markets.

        Dedup is scoped per strategy so that different strategies evaluating
        the same market can each pass through independently — the risk manager
        decides which is viable.  Within a single strategy, only the best
        signal per correlated group is kept (e.g. two candidates for the
        same race).

        Also uses CorrelationDetector (Jaccard word-overlap) when available
        for broader cross-question dedup beyond the question_group_key heuristic.
        """
        if not signals:
            return signals

        # Key = (strategy, question_group) so cross-strategy signals survive
        groups: dict[tuple[str, str], TradeSignal] = {}
        for signal in signals:
            question_key = self._question_group_key(signal.question)

            # Also check correlation detector for broader grouping
            if getattr(self, "_correlation_detector", None) is not None:
                corr_group = self._correlation_detector.get_group(signal.market_id)
                if corr_group is not None:
                    # Use correlation group as the dedup key when available
                    question_key = f"corr:{corr_group}"

            key = (signal.strategy, question_key)
            existing = groups.get(key)
            if existing is None or (signal.edge * signal.confidence) > (
                existing.edge * existing.confidence
            ):
                if existing is not None:
                    logger.info(
                        "correlated_market_filtered",
                        strategy=signal.strategy,
                        kept=signal.question[:60],
                        dropped=existing.question[:60],
                        group_key=question_key[:40],
                    )
                groups[key] = signal

        filtered = list(groups.values())
        dropped = len(signals) - len(filtered)
        if dropped > 0:
            logger.info("correlated_dedup_complete", original=len(signals), kept=len(filtered))
        return filtered
