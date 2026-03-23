"""Market type classification — determines lifecycle behavior for each market.

Single source of truth for market categorization. Every entry/exit decision
flows through the MarketPolicy associated with each MarketType.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

import structlog

logger = structlog.get_logger()


class MarketType(Enum):
    SHORT_TERM = "short_term"   # crypto 5-min, daily binary, hourly
    EVENT = "event"              # sports, eSports, soccer, MMA
    LONG_TERM = "long_term"     # politics, geopolitics, ceasefire, elections
    ECONOMIC = "economic"        # Fed rate, CPI, unemployment, GDP
    WEATHER = "weather"          # temperature, precipitation
    UNKNOWN = "unknown"          # fallback — treated as long-term (safe)


@dataclass(frozen=True)
class MarketPolicy:
    """Immutable policy governing a market type's full lifecycle."""

    allowed_strategies: frozenset[str]
    allow_early_exit: bool        # can any exit check fire before resolution?
    allow_bayesian_exit: bool     # can Bayesian updater close?
    allow_stop_loss: bool         # can universal stop loss fire?
    stop_loss_pct: float          # if allowed, at what % (0.0 = N/A)
    allow_rebalance: bool         # can rebalance close this?
    max_hold_hours: float         # 0 = wait for resolution
    description: str


# ---------------------------------------------------------------------------
# Policy definitions
# ---------------------------------------------------------------------------

_ALL_STRATEGIES = frozenset({
    "time_decay", "arbitrage", "value_betting", "price_divergence",
    "swing_trading", "market_making", "weather_trading",
    "crypto_short_term", "news_sniping", "copy_trading",
})

POLICIES: dict[MarketType, MarketPolicy] = {
    MarketType.SHORT_TERM: MarketPolicy(
        allowed_strategies=_ALL_STRATEGIES,
        allow_early_exit=True,
        allow_bayesian_exit=True,
        allow_stop_loss=True,
        stop_loss_pct=0.15,
        allow_rebalance=True,
        max_hold_hours=48.0,
        description="Crypto 5-min, daily binary, hourly markets",
    ),
    MarketType.EVENT: MarketPolicy(
        allowed_strategies=frozenset({"time_decay", "copy_trading"}),
        allow_early_exit=False,
        allow_bayesian_exit=False,
        allow_stop_loss=False,
        stop_loss_pct=0.0,
        allow_rebalance=False,
        max_hold_hours=0.0,
        description="Sports, eSports, soccer — wait for resolution",
    ),
    MarketType.LONG_TERM: MarketPolicy(
        allowed_strategies=frozenset({
            "time_decay", "copy_trading", "news_sniping",
        }),
        allow_early_exit=True,
        allow_bayesian_exit=False,
        allow_stop_loss=True,
        stop_loss_pct=0.35,
        allow_rebalance=False,
        max_hold_hours=336.0,  # 14 days
        description="Politics, geopolitics, elections — patient hold",
    ),
    MarketType.ECONOMIC: MarketPolicy(
        allowed_strategies=frozenset({"time_decay"}),
        allow_early_exit=False,
        allow_bayesian_exit=False,
        allow_stop_loss=False,
        stop_loss_pct=0.0,
        allow_rebalance=False,
        max_hold_hours=0.0,
        description="Fed rate, CPI, unemployment — wait for data release",
    ),
    MarketType.WEATHER: MarketPolicy(
        allowed_strategies=frozenset({"time_decay", "weather_trading"}),
        allow_early_exit=True,
        allow_bayesian_exit=False,
        allow_stop_loss=True,
        stop_loss_pct=0.25,
        allow_rebalance=False,
        max_hold_hours=168.0,  # 7 days
        description="Temperature, precipitation forecasts",
    ),
    MarketType.UNKNOWN: MarketPolicy(
        allowed_strategies=frozenset({
            "time_decay", "copy_trading", "news_sniping",
        }),
        allow_early_exit=True,
        allow_bayesian_exit=False,
        allow_stop_loss=True,
        stop_loss_pct=0.35,
        allow_rebalance=False,
        max_hold_hours=336.0,  # 14 days — same as LONG_TERM (safe)
        description="Unclassified — treated as long-term for safety",
    ),
}


def get_policy(market_type: MarketType) -> MarketPolicy:
    """Return the policy for a market type."""
    return POLICIES[market_type]


# ---------------------------------------------------------------------------
# Classification patterns
# ---------------------------------------------------------------------------

# Short-term: crypto up/down, 5-min, 15-min, hourly, daily binary
_SHORT_TERM_PATTERNS = re.compile(
    r"\b("
    r"up or down|5[\s-]?min|15[\s-]?min|hourly|opens up|opens down"
    r"|daily|end of day|close above|close below"
    r"|price at \d|by midnight|by end of"
    r")\b",
    re.IGNORECASE,
)

_SHORT_TERM_CRYPTO = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|dogecoin|doge"
    r"|litecoin|ltc|polygon|matic|avalanche|avax"
    r"|cardano|ada|polkadot|dot|chainlink|link"
    r"|uniswap|uni|aave)\b",
    re.IGNORECASE,
)

# Event: sports, eSports, soccer — reuse existing comprehensive patterns
# from market_analyzer._SPORTS_KEYWORDS for consistency
_EVENT_PATTERNS = re.compile(
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
    # College teams
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
    # Soccer-specific prefixes
    r"|fc |sc |afc |sfc |united |city |rovers|wanderers"
    r"|athletic|atletico|eredivisie"
    r")",
    re.IGNORECASE,
)

# Additional event heuristics
_EVENT_WIN_ON = re.compile(
    r"will .+ win on \d{4}-\d{2}-\d{2}", re.IGNORECASE,
)
_EVENT_VS = re.compile(
    r"(?:^|\:\s*).+\bvs\.?\s+.+", re.IGNORECASE,
)

# Long-term: politics, geopolitics, elections
_LONG_TERM_PATTERNS = re.compile(
    r"\b("
    r"president|prime minister|election|ceasefire|war\b|invade|invasion"
    r"|regime|treaty|sanctions|impeach|congress|senate|governor"
    r"|legislation|bill pass|executive order|veto|referendum"
    r"|geopolit|diplomat|nuclear|nato|un resolution"
    r"|democrat|republican|ballot|nomination|nominee|inaugurat"
    r"|midterm|electoral|swing state|cabinet|pardon"
    r")\b",
    re.IGNORECASE,
)

# Economic: macro data releases
_ECONOMIC_PATTERNS = re.compile(
    r"\b("
    r"fed\b|federal reserve|interest rate|rate cut|rate hike"
    r"|cpi\b|inflation|unemployment|gdp\b|treasury|fomc"
    r"|payroll|jobs report|retail sales|housing starts"
    r"|consumer price|producer price|ppi\b|pce\b"
    r"|quantitative|taper|yield curve|bond yield"
    r"|trade deficit|current account|fiscal"
    r")\b",
    re.IGNORECASE,
)

# Weather: temperature, precipitation
_WEATHER_PATTERNS = re.compile(
    r"\b("
    r"temperature|degrees\s*fahrenheit|degrees\s*celsius|weather forecast"
    r"|high(?:est)?\s+temp|low(?:est)?\s+temp|°F|°C|heat wave|cold snap"
    r"|snowfall|rainfall|precipitation|inches of rain|inches of snow"
    r")\b",
    re.IGNORECASE,
)


def classify_market(
    question: str,
    end_date: datetime | None = None,
) -> MarketType:
    """Classify a market by question text and optional end date.

    Priority order:
    1. EVENT (sports/eSports/soccer) — highest priority, distinctive keywords
    2. WEATHER — distinct domain
    3. ECONOMIC — distinct domain
    4. SHORT_TERM — crypto + short-term pattern, or end_date < 24h
    5. LONG_TERM — political/geopolitical keywords, or end_date > 7 days
    6. UNKNOWN — fallback
    """
    if not question:
        return MarketType.UNKNOWN

    # 1. EVENT detection (most distinctive — sports/eSports have unique keywords)
    if (
        _EVENT_PATTERNS.search(question)
        or _EVENT_WIN_ON.search(question)
        or _EVENT_VS.search(question)
    ):
        return MarketType.EVENT

    # 2. WEATHER detection (before other types — temperature markets are distinct)
    if _WEATHER_PATTERNS.search(question):
        return MarketType.WEATHER

    # 3. ECONOMIC detection
    if _ECONOMIC_PATTERNS.search(question):
        return MarketType.ECONOMIC

    # 4. SHORT_TERM: crypto + short-term pattern
    is_crypto = bool(_SHORT_TERM_CRYPTO.search(question))
    has_short_pattern = bool(_SHORT_TERM_PATTERNS.search(question))

    if is_crypto and has_short_pattern:
        return MarketType.SHORT_TERM

    # Time-based classification using end_date (if it's a real datetime)
    _valid_end = (
        isinstance(end_date, datetime)
        and end_date is not None
    )

    # Time-based short-term: end_date within 24 hours
    if _valid_end:
        try:
            now = datetime.now(timezone.utc)
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
            hours_left = (end_date - now).total_seconds() / 3600
            if hours_left <= 24 and hours_left > 0:
                return MarketType.SHORT_TERM
        except (TypeError, AttributeError):
            pass

    # 5. LONG_TERM: political/geopolitical keywords
    if _LONG_TERM_PATTERNS.search(question):
        return MarketType.LONG_TERM

    # Time-based long-term: end_date > 7 days
    if _valid_end:
        try:
            now = datetime.now(timezone.utc)
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
            days_left = (end_date - now).total_seconds() / 86400
            if days_left > 7:
                return MarketType.LONG_TERM
        except (TypeError, AttributeError):
            pass

    # 6. Crypto without short-term patterns (e.g. "Will BTC hit $100k?")
    # These are typically medium-term — treat as UNKNOWN (safe long-term defaults)
    if is_crypto:
        return MarketType.UNKNOWN

    return MarketType.UNKNOWN
