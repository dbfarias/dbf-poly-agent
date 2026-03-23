<div align="center">

# PolyBot

### Autonomous Polymarket Trading Agent

[![Tests](https://img.shields.io/badge/Tests-2300%2B_passing-brightgreen?style=for-the-badge)]()
[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-Welcome-brightgreen?style=for-the-badge)](CONTRIBUTING.md)

---

**10 Strategies** | **AI Trade Debates** | **Multi-Source Research** | **Quantitative Risk Gates** | **Adaptive Learning** | **Real-Time Dashboard**

</div>

---

PolyBot is a fully autonomous prediction market trading agent for [Polymarket](https://polymarket.com). It runs 24/7 as a single Python process (FastAPI + asyncio), scanning hundreds of markets every 60 seconds, generating signals across 10 parallel strategies, and filtering each signal through a 14-stage risk pipeline before execution. A React dashboard provides real-time monitoring and configuration. The bot starts in **paper trading mode** by default -- no real funds are needed to get started.

---

## Key Features

- **10 trading strategies** -- time decay, arbitrage, value betting, price divergence, swing trading, market making, weather trading, crypto short-term, news sniping, and copy trading
- **14-stage risk pipeline** -- VaR (95%), VPIN toxic flow, AI debate, drawdown checks, event-aware exit protection (sports, eSports, soccer), rate limiting, and more
- **AI-powered trade filtering** -- two Claude Haiku agents debate every trade (Proposer vs Challenger) before execution
- **Multi-source research engine** -- Tavily real-time search, Google News, Twitter/X, Reddit, CoinGecko, The Odds API (sports + eSports), NOAA + Open-Meteo (weather), Manifold Markets (cross-platform), FRED (economics), Fear & Greed Index, whale detection, volume anomaly tracking
- **Technical indicators** -- RSI, MACD, VWAP, CVD for crypto markets via Coinbase WebSocket
- **Cross-platform convergence scoring** -- aggregates signals across sources, boosts edge when multiple signals agree
- **Deep research mode** -- high-edge trades (>10%) get enriched context with all available data for better LLM analysis
- **Bayesian position updating** -- re-evaluates open positions with fresh research every cycle, exits when fundamentals shift
- **Adaptive learning** -- PerformanceLearner adjusts edge multipliers, category confidence, and urgency every 5 minutes
- **Real-time dashboard** -- 12-page React UI with equity curves, trade history, strategy performance, risk metrics, and AI debate logs
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
|  12 Pages:          |     |  +------------------+   |
|  - Dashboard        |<--->|  |   FastAPI App     |   |
|  - Trades           | API |  |   /api/* + /ws/*  |   |
|  - Strategies       |     |  +--------+---------+   |
|  - Markets          |     |           |              |
|  - Risk             |     |  +--------v---------+   |
|  - Research         |     |  |  Trading Engine   |   |
|  - Learner          |     |  |  (asyncio task)   |   |
|  - AI Debates       |     |  |                   |   |
|  - Activity         |     |  |  - 10 Strategies  |   |
|  - Settings         |     |  |  - Risk Manager   |   |
+---------------------+     |  |  - Portfolio      |   |
                             |  |  - Learner        |   |
                             |  |  - Research Engine|   |
                             |  |  - LLM Debate Gate|   |
                             |  +--------+---------+   |
                             |           |              |
                             |  +--------v---------+   |
                             |  |  SQLite (WAL)     |   |
                             |  +------------------+   |
                             +------------------------+
```

**Key design decision:** Bot and API run in the **same Python process** -- the trading engine runs as an `asyncio` background task inside FastAPI. This saves RAM and simplifies deployment (a single container handles everything).

### Trade Pipeline

Every 60 seconds, a signal must pass **all 14 stages** to become a trade:

```
Market Scan (~600 markets) -> Strategy Evaluation (10 strategies)
    -> Signal -> Risk Pipeline:
       1. Learner pause check        8. Debate cooldown
       2. Market cooldown (3h)       9. LLM Debate (Proposer + Challenger)
       3. Duplicate position         10. Z-Score gate (|Z| >= 1.5)
       4. Correlation check          11. Daily loss limit
       5. VaR pre-check (95%)        12. Drawdown check
       6. Debate cooldown            13. Position limits
       7. VPIN toxic flow filter     14. Category exposure cap
    -> Position Sizing (Kelly Criterion)
    -> Order Execution (CLOB API)
```

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

The React dashboard provides 12 pages for full visibility into the bot's operations:

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
| **Market Report** | Daily summary: portfolio, sentiment, top opportunities, alerts |
| **Activity** | Bot decision log with filtering by event type |
| **Settings** | All runtime parameters, AI toggles, strategy controls |

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
# 1. Provision a server (any cloud provider)
# 2. Install Docker and Docker Compose
# 3. Clone the repo and configure .env
# 4. Launch with docker compose
docker compose -f docker-compose.prod.yml up -d
```

### HTTPS

HTTPS setup is automated via Let's Encrypt + DuckDNS dynamic DNS:

```bash
bash deploy/setup-https.sh <duckdns-subdomain> <duckdns-token> <your-email>
```

### CI/CD

The included GitHub Actions workflow (`.github/workflows/deploy.yml`) runs a 3-job pipeline on push to `main`:

1. **Test** -- pytest (2300+ tests) + ruff lint + frontend build
2. **Build** -- Docker images pushed to GitHub Container Registry
3. **Deploy** -- SSH to server, pull images, restart with healthcheck + auto-rollback

Configure these GitHub secrets for CI/CD: `SERVER_HOST`, `SERVER_USER`, `SERVER_SSH_KEY`, `API_SECRET_KEY`.

---

## Testing

**2300+ tests** across 50+ test files covering bot logic, API endpoints, strategies, research engine, and adaptive learning.

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
