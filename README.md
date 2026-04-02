<div align="center">

# PolyBot

### Autonomous Polymarket Trading Agent

[![Tests](https://img.shields.io/badge/Tests-2687_passing-brightgreen?style=for-the-badge)]()
[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-Welcome-brightgreen?style=for-the-badge)](CONTRIBUTING.md)

---

**12 Strategies** | **Trade Watchers** | **AI Trade Debates** | **Multi-Source Research** | **Quantitative Risk Gates** | **Adaptive Learning** | **Real-Time Dashboard**

</div>

---

PolyBot is a fully autonomous prediction market trading agent for [Polymarket](https://polymarket.com). It runs 24/7 as a single Python process (FastAPI + asyncio), scanning hundreds of markets every 60 seconds, generating signals across 12 parallel strategies, and filtering each signal through a 15-stage risk pipeline before execution. A React dashboard provides real-time monitoring, manual trade execution, and configuration. The bot starts in **paper trading mode** by default -- no real funds are needed to get started.

---

## Key Features

- **12 trading strategies** -- time decay, arbitrage, value betting, price divergence, swing trading, market making, weather trading, crypto short-term, news sniping, copy trading, flash crash mean-reversion, and sports favorite
- **15-stage risk pipeline** -- VaR (95%), VPIN toxic flow, AI debate, drawdown checks, event-aware exit protection (sports, eSports, soccer), rate limiting, configurable spread-crossing for aggressive fills, and more
- **AI-powered trade filtering** -- two Claude Haiku agents debate every trade (Proposer vs Challenger) before execution
- **Trade Assistant** -- free-text trade execution from the dashboard. Type a message with a Polymarket URL (e.g., "Buy No on Uruguay $5") and the bot parses intent, fetches market data, and executes. Supports English and Portuguese
- **One-click sell** -- sell any open position directly from the dashboard with best-bid pricing and confirmation dialog
- **Backtesting framework** -- lightweight backtesting engine with historical data from Polymarket, accurate non-linear fee model, and metrics including Sharpe ratio, max drawdown, win rate, and ROI
- **Multi-source research engine** -- Tavily real-time search, Google News, Twitter/X, Reddit, CoinGecko, The Odds API (sports + eSports), NOAA + Open-Meteo (weather), Manifold Markets (cross-platform), FRED (economics), Fear & Greed Index, whale detection, volume anomaly tracking
- **Technical indicators** -- RSI, MACD, VWAP, CVD for crypto markets via Coinbase WebSocket
- **Cross-platform convergence scoring** -- aggregates signals across sources, boosts edge when multiple signals agree
- **Deep research mode** -- high-edge trades (>10%) get enriched context with all available data for better LLM analysis
- **Trade Watcher agents** -- live, temporary agents that monitor scalable positions in real-time. Automatically created on qualifying trades (or manually via dashboard). Each watcher tracks price momentum, volume spikes, and Google News RSS, aggregates multi-signal verdicts (requiring 2+ confirming signals), and autonomously scales up or exits positions. Guardrails: max 5 concurrent watchers, 50% equity cap, trailing stop, max 3 scale-ups per watcher, risk manager approval on every order
- **Bayesian position updating** -- re-evaluates open positions with fresh research every cycle, exits when fundamentals shift
- **Adaptive learning** -- PerformanceLearner adjusts edge multipliers, category confidence, and urgency every 5 minutes
- **Real-time dashboard** -- 15-page React UI with equity curves, trade history, strategy performance, risk metrics, AI debate logs, trade assistant, and backtesting
- **Real-time WebSocket orderbook tracking** -- live orderbook snapshots for spread analysis and flash crash detection
- **Flash crash detection** -- mean-reversion strategy that buys when price drops 30%+ within 30 seconds
- **On-chain position verification** -- phantom position sync detects and reconciles mismatches between local state and on-chain data
- **Thread-safe HTTP sessions** -- all external API calls use isolated sessions to prevent concurrency issues
- **Configurable spread-crossing** -- aggressive limit order pricing with adjustable offset for faster fills
- **Paper trading mode** -- test everything risk-free before going live
- **PWA push notifications** -- trade fills, errors, and daily summaries on mobile/desktop

---

## Quick Start

Get PolyBot running in paper trading mode in under 5 minutes.

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- Node.js 20+ (for the dashboard)

### 1. Clone and Install

```bash
git clone https://github.com/YOUR_USERNAME/polybot.git
cd polybot

# Install Python dependencies
uv sync --all-extras

# Install frontend dependencies
cd frontend && npm install && cd ..
```

Or with Make:

```bash
make install
```

### 2. Configure

```bash
cp .env.example .env

# Generate and set the required API secret key:
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Paste the output into .env as API_SECRET_KEY=<your-key>

# Set a dashboard password:
# Edit .env and set DASHBOARD_PASSWORD=<your-password>
```

The bot runs in paper trading mode by default. No Polymarket API keys or wallet are needed for paper trading.

### 3. Run

```bash
# Terminal 1: Start the backend (bot + API)
make dev

# Terminal 2: Start the dashboard
make frontend
```

- Dashboard: `http://localhost:5173`
- API: `http://localhost:8000`
- Health check: `http://localhost:8000/api/health`

The bot will immediately start scanning markets and generating paper trades. Open the dashboard to watch it work.

---

## Architecture

```
+------------------------------------------------------------------+
|                        NGINX (Port 80/443)                        |
|               Reverse Proxy + SSL (Let's Encrypt)                 |
+----------+----------------------------+--------------------------+
           |                            |
           v                            v
+---------------------+     +------------------------+
|   React Dashboard   |     |   FastAPI + Bot         |
|   (Static Nginx)    |     |   (Single Process)      |
|                     |     |                          |
|  15 Pages:          |     |  +------------------+   |
|  - Dashboard        |<--->|  |   FastAPI App     |   |
|  - Trades           | API |  |   /api/* + /ws/*  |   |
|  - Strategies       |     |  +--------+---------+   |
|  - Markets          |     |           |              |
|  - Risk             |     |  +--------v---------+   |
|  - Research         |     |  |  Trading Engine   |   |
|  - Learner          |     |  |  (asyncio task)   |   |
|  - AI Debates       |     |  |                   |   |
|  - Trade Assistant  |     |  |  - 12 Strategies  |   |
|  - Backtesting      |     |  |  - Risk Manager   |   |
|  - Activity         |     |  |  - Portfolio      |   |
|  - Settings         |     |  |  - Learner        |   |
+---------------------+     |  |  - Research Engine|   |
                             |  |  - Market Classif.|   |
                             |  |  - LLM Debate Gate|   |
                             |  |  - OrderbookTracker|  |
                             |  |  - Backtest Engine|   |
                             |  +--------+---------+   |
                             |           |              |
                             |  +--------v---------+   |
                             |  |  SQLite (WAL)     |   |
                             |  +------------------+   |
                             +------------------------+
```

**Key design decision:** Bot and API run in the **same Python process** -- the trading engine runs as an `asyncio` background task inside FastAPI. This saves RAM and simplifies deployment (a single container handles everything).

### Trade Pipeline

Every 60 seconds, a signal must pass **all 15 stages** to become a trade:

```
Market Scan (~600 markets) -> Strategy Evaluation (12 strategies)
    -> Signal -> Risk Pipeline:
       1. Market type policy check   9. Debate cooldown
       2. Learner pause check        10. LLM Debate (Proposer + Challenger)
       3. Market cooldown (3h)       11. Z-Score gate (|Z| >= 1.5)
       4. Duplicate position         12. Daily loss limit
       5. Correlation check          13. Drawdown check
       6. VaR pre-check (95%)        14. Position limits
       7. Debate cooldown            15. Category exposure cap
       8. VPIN toxic flow filter
    -> Position Sizing (Kelly Criterion)
    -> Order Execution (CLOB API)
```

### Market Classification

Every market is classified into one of 6 types, and a frozen `MarketPolicy` determines its full lifecycle behavior (entry strategies, exit rules, stop-loss thresholds). This is the single source of truth -- no more scattered `is_event_market()` checks.

| Type | Examples | Allowed Strategies | Stop Loss | Early Exit | Bayesian | Rebalance |
|:---|:---|:---|:---:|:---:|:---:|:---:|
| **SHORT_TERM** | Crypto 5-min, daily binary, hourly | All 12 | 15% | Yes | Yes | Yes |
| **EVENT** | Sports, eSports, soccer, MMA | time_decay, copy_trading | No | No | No | No |
| **LONG_TERM** | Politics, elections, ceasefire, treaties | time_decay, copy_trading, news_sniping | 35% | Yes | No | No |
| **ECONOMIC** | Fed rate, CPI, GDP, unemployment | time_decay | No | No | No | No |
| **WEATHER** | Temperature, precipitation, snowfall | time_decay, weather_trading | 25% | Yes | No | No |
| **UNKNOWN** | Unclassified (safe fallback) | time_decay, copy_trading, news_sniping | 35% | Yes | No | No |

Classification uses regex keyword matching + end_date heuristics. See `bot/research/market_classifier.py`.

---

## Strategies

| # | Strategy | Description | Risk Level |
|:---:|:---|:---|:---:|
| 1 | **Time Decay** | Buy near-certain outcomes close to market resolution | Low |
| 2 | **Arbitrage** | Exploit YES+NO pricing inconsistencies (sum < $1.00) | Zero |
| 3 | **Value Betting** | Detect mispriced markets via order book analysis + VPIN | Medium |
| 4 | **Price Divergence** | Detect crypto/sentiment price divergence using external signals | Medium |
| 5 | **Swing Trading** | Buy markets with confirmed upward momentum (3+ rising ticks) | Medium |
| 6 | **Market Making** | Provide liquidity on both sides of the spread | High |
| 7 | **Weather Trading** | Trade weather markets using NOAA forecast data | Medium |
| 8 | **Crypto Short-Term** | Trade 5-minute crypto markets using real-time spot prices | Medium |
| 9 | **News Sniping** | Trade on breaking news via RSS polling + sentiment analysis | Medium |
| 10 | **Copy Trading** | Follow top Polymarket traders via leaderboard + wallet tracking | Medium |
| 11 | **Flash Crash** | Mean-reversion on sudden probability drops. Buys when price drops 30%+ within 30 seconds | Very High |
| 12 | **Sports Favorite** | Buys "No" on weak teams in football matches, winning on both draw and loss. Targets "Will X win?" markets where No price is $0.70-$0.90, entering 1-12h before kickoff | Medium |

Strategies are modular -- each extends `BaseStrategy` and implements `scan()` and `should_exit()`. You can enable/disable any strategy at runtime via the dashboard or API. See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add your own.

---

## Configuration

All configuration is via environment variables (`.env` file). See [`.env.example`](.env.example) for the full list.

### Key Variables

| Variable | Default | Description |
|:---|:---|:---|
| `API_SECRET_KEY` | -- | **Required.** Min 16 chars. Used for API auth and JWT signing |
| `DASHBOARD_USER` | `admin` | Dashboard login username |
| `DASHBOARD_PASSWORD` | -- | **Required for dashboard.** Login password |
| `TRADING_MODE` | `paper` | `paper` or `live` -- paper is the default |
| `INITIAL_BANKROLL` | `5.0` | Starting capital in USD |
| `ANTHROPIC_API_KEY` | -- | Anthropic API key (required for AI features: debate, sentiment, post-mortem) |
| `POLY_API_KEY` | -- | Polymarket API key (required for live trading only) |
| `POLY_API_SECRET` | -- | Polymarket API secret (required for live trading only) |
| `POLY_PRIVATE_KEY` | -- | Wallet private key (required for live trading only) |
| `TAVILY_API_KEY` | -- | Tavily API key for Twitter/X research (optional) |
| `TELEGRAM_BOT_TOKEN` | -- | Telegram bot token for alerts (optional) |

### Runtime Risk Settings

| Parameter | Default | Range | Description |
|:---|:---|:---|:---|
| `spread_cross_offset` | `0.0` | 0.0 -- 0.05 | Aggressive pricing offset to cross the spread for faster limit order fills. 0 = disabled |

### Runtime Settings

Many parameters can be adjusted at runtime via the dashboard Settings page without restarting:

- Risk parameters (max positions, deployed capital %, Kelly fraction, loss limits)
- Strategy parameters (time horizons, edge thresholds, quality filters)
- AI feature toggles (debate, sentiment, post-mortem) with daily budget cap
- Blocked market types (sports, crypto, etc.)
- Scan interval, snapshot interval

All runtime settings are persisted to the database and restored on restart.

---

## Dashboard

The React dashboard provides 15 pages for full visibility into the bot's operations:

| Page | Description |
|:---|:---|
| **Dashboard** | Equity curve, daily PnL chart, daily target tracker, active positions |
| **Trades** | Expandable trade history with reasoning, edge, confidence, and pricing |
| **Strategies** | Per-strategy performance: win rate, PnL, Sharpe ratio |
| **Markets** | Live market scanner with opportunities and signals |
| **Risk** | Drawdown chart, category exposure, VaR/Sharpe/Profit Factor metrics |
| **Research** | News sentiment, volume anomalies, whale activity, market categories |
| **Learner** | Adaptive learning: edge multipliers, Brier scores, strategy pauses |
| **AI Debates** | Trade debate history, position reviews, post-mortem analysis |
| **Watchers** | Trade Watcher agents: create, monitor, kill. Live status, P&L, scale count, signals |
| **Market Report** | Daily summary: portfolio, sentiment, top opportunities, alerts |
| **Trade Assistant** | Free-text trade execution -- type a message with a Polymarket URL to buy or sell |
| **Backtesting** | Run strategy backtests with historical data, view Sharpe ratio, drawdown, and ROI |
| **Activity** | Bot decision log with filtering by event type |
| **Settings** | All runtime parameters, AI toggles, strategy controls |

Active positions include a **Sell** button for one-click exit at best bid with a confirmation dialog.

Features: real-time WebSocket updates, auto-refreshing queries, PWA push notifications, JWT authentication.

<!-- To add a screenshot, place it in docs/ and uncomment:
![Dashboard](docs/dashboard-screenshot.png)
-->

---

## Deployment

### Docker (Recommended)

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your API keys and passwords

# 2. Local development (builds images locally)
docker compose up --build

# 3. Production (pulls pre-built images from GHCR)
export GITHUB_OWNER=yourusername
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

### Cloud Server

PolyBot is designed to run on a minimal VPS (1GB RAM, 2 vCPU is sufficient):

```bash
# 1. Provision a server in an eligible region (see below)
# 2. Install Docker and Docker Compose
# 3. Clone the repo and configure .env
# 4. Launch with docker compose
docker compose -f docker-compose.prod.yml up -d
```

> **Server Location Matters.** Polymarket blocks access from certain countries (including the United States). Your server must be in an **eligible region** where Polymarket is accessible. Recommended regions:
>
> | Provider | Region | Example |
> |----------|--------|---------|
> | AWS Lightsail | Mumbai (ap-south-1) | $5/mo, 1GB RAM |
> | AWS Lightsail | Singapore (ap-southeast-1) | $5/mo, 1GB RAM |
> | DigitalOcean | Singapore (sgp1) | $6/mo, 1GB RAM |
> | DigitalOcean | Bangalore (blr1) | $6/mo, 1GB RAM |
> | Hetzner | Singapore | EUR 4.50/mo |
> | Vultr | Mumbai / Tokyo / Singapore | $6/mo |
>
> **Blocked regions include:** United States, France, Cuba, Iran, North Korea, Syria, and other OFAC-sanctioned countries. Check [Polymarket's Terms of Service](https://polymarket.com/tos) for the full list. If your server is in a blocked region, API calls to Polymarket will fail with 403 errors.

### HTTPS

HTTPS setup is automated via Let's Encrypt + DuckDNS dynamic DNS:

```bash
bash deploy/setup-https.sh <duckdns-subdomain> <duckdns-token> <your-email>
```

### CI/CD

The included GitHub Actions workflow (`.github/workflows/deploy.yml`) runs a 3-job pipeline on push to `main`:

1. **Test** -- pytest (2687+ tests) + ruff lint + frontend build
2. **Build** -- Docker images pushed to GitHub Container Registry
3. **Deploy** -- SSH to server, pull images, restart with healthcheck + auto-rollback

Configure these GitHub secrets for CI/CD: `SERVER_HOST`, `SERVER_USER`, `SERVER_SSH_KEY`, `API_SECRET_KEY`.

---

## Testing

**2687+ tests** across 50+ test files covering bot logic, API endpoints, strategies, research engine, and adaptive learning.

```bash
# Run all tests
make test

# Run with coverage
uv run pytest tests/ --cov=bot --cov=api --cov-report=term-missing

# Lint
make lint

# Frontend type check
cd frontend && npx vite build
```

---

## AI Features

Five LLM features using **Claude Haiku** -- each independently toggleable with a shared daily budget cap:

| Feature | Description | Est. Cost/Day |
|:---|:---|:---:|
| **Trade Debate** | Two-agent Proposer vs Challenger debate before each trade | ~$0.10-0.50 |
| **Position Reviewer** | AI reviews open positions every ~30min (HOLD/EXIT/REDUCE/INCREASE) | ~$0.05 |
| **Sentiment Analysis** | LLM sentiment on news headlines (hybrid VADER + LLM) | ~$0.30 |
| **Keyword Extraction** | LLM-powered keyword extraction for research queries | ~$0.02 |
| **Post-Mortem** | Analyzes resolved trades for strategy fit feedback | ~$0.05 |

All LLM calls share a global cost tracker. When the daily budget is exhausted (~$3/day default), all features gracefully fall back to non-LLM behavior.

---

## Backtesting

PolyBot includes a lightweight backtesting framework for strategy validation using historical data from the Polymarket Data API.

```bash
# Run a backtest via API
curl -X POST http://localhost:8000/api/backtest \
  -H "Authorization: Bearer <token>" \
  -d '{"strategy": "time_decay", "days": 30}'
```

**Features:**
- Historical price data fetched directly from Polymarket
- Accurate non-linear fee model with separate exponents for crypto and sports markets
- Metrics: Sharpe ratio, max drawdown, win rate, ROI, total PnL
- No heavy dependencies -- pure Python, no NautilusTrader or Rust toolchains required
- Accessible from the dashboard Backtesting page or via the REST API

Backtesting results help validate parameter changes and new strategies before deploying to live trading.

---

## Trade Watcher Agents

Trade Watchers are live, temporary agents that monitor and manage scalable positions autonomously. Unlike strategies (which find new opportunities), watchers manage existing positions -- scaling up when momentum confirms or exiting when conditions deteriorate.

### When They Activate

- **Automatically** after a qualifying trade fill (eligible strategies: time_decay, copy_trading, news_sniping, arbitrage, value_betting, swing_trading)
- **Manually** via the Watchers dashboard page or the REST API
- Only markets classified as LONG_TERM, ECONOMIC, or UNKNOWN qualify (sports, weather, and short-term markets do not)

### What They Monitor

Each watcher runs its own 15-minute check cycle, gathering three independent signals:

| Signal | Source | Bullish Trigger | Bearish Trigger |
|:---|:---|:---|:---|
| **Price Momentum** | In-memory price tracker (1h, 4h, 24h windows) | 1h > +1%, 4h > +2% | 1h < -1%, 4h < -2% |
| **Volume** | 24h volume vs rolling average | > 2x average (spike) | -- |
| **News** | Google News RSS for extracted keywords | 3+ headlines, sentiment > +0.3 | 3+ headlines, sentiment < -0.3 |

### How They Decide

Verdicts are computed via multi-signal aggregation with a minimum of **2 confirming signals** required:

- **Scale Up**: 2+ bullish signals (momentum + volume spike, or momentum + positive news)
- **Exit**: stop loss hit, or 2+ bearish signals (momentum + negative news)
- **Hold**: mixed or insufficient signals (default)

### Guardrails

| Limit | Value | Description |
|:---|:---|:---|
| Max concurrent watchers | 5 | Hard cap on active watchers |
| Max equity deployed | 50% | Aggregate watcher exposure cap |
| Max scale-ups per watcher | 3 | Prevents overconcentration |
| Max exposure per watcher | $20 | Default, configurable per watcher |
| Trailing stop | Configurable (default 25%) | Triggers exit from highest observed price |
| Max age | 7 days (default) | Auto-terminates stale watchers |
| Market end date | 48h | Auto-exits when market resolution approaches |

### Dashboard Management

The Watchers page shows all watchers (active, completed, killed) with status badges, entry/current price, P&L, exposure, scale count, and last signal. Active watchers can be killed with one click. New watchers can be created manually with a market ID, thesis, and risk parameters.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, code style, and how to add new strategies.

---

## Disclaimer

**This software is provided for educational and research purposes only.** It is not financial advice.

- Trading prediction markets involves risk of loss. You may lose some or all of your capital.
- Past performance (including backtests and paper trading results) does not guarantee future results.
- The authors and contributors are not responsible for any financial losses incurred through the use of this software.
- You are solely responsible for your own trading decisions and should do your own research.
- Use at your own risk. Start with paper trading mode to understand the system before committing real funds.

---

## License

This project is licensed under the MIT License -- see [LICENSE](LICENSE) for details.
