<div align="center">

# 🤖 PolyBot

### Autonomous Polymarket Trading Agent

An AI-powered trading bot that operates 24/7 on [Polymarket](https://polymarket.com), targeting conservative daily returns through micro-operations. Includes a full React dashboard for real-time monitoring.

[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![React](https://img.shields.io/badge/React-18-61dafb?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-Private-red?style=for-the-badge)]()

---

**$5 → $500** · **4 Strategies** · **Tier-Based Risk** · **Paper Trading** · **Real-Time Dashboard**

</div>

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Growth Model](#-growth-model)
- [Architecture](#-architecture)
- [Trading Strategies](#-trading-strategies)
- [Risk Management](#-risk-management)
- [Dashboard](#-dashboard)
- [Getting Started](#-getting-started)
- [Configuration](#-configuration)
- [Deployment](#-deployment)
- [Security](#-security)
- [Testing](#-testing)
- [Project Structure](#-project-structure)
- [Tech Stack](#-tech-stack)

---

## 🎯 Overview

PolyBot is a fully autonomous prediction market trading agent designed to grow a small bankroll ($5) through conservative, high-probability trades. It runs as a single Python process (bot + API) with a React dashboard for monitoring.

### Key Features

| Feature | Description |
|:---|:---|
| **4 Trading Strategies** | Time Decay, Arbitrage, Value Betting, Market Making |
| **Tier-Based Risk System** | Automatically adapts position sizing and limits as capital grows |
| **Quarter-Kelly Sizing** | Conservative position sizing to minimize risk of ruin |
| **Paper Trading Mode** | ON by default — test safely before going live |
| **Real-Time Dashboard** | 6-page React app with equity curves, trade history, risk metrics |
| **WebSocket Updates** | Live portfolio and trade updates pushed to dashboard |
| **Telegram Alerts** | Trade notifications, error alerts, daily performance summaries |
| **Docker Deploy** | One-command deployment on AWS Lightsail (~$5/month) |

---

## 📈 Growth Model

Compound growth targeting **0.5%–2% daily returns** via high-probability trades:

```
                        Compound Growth Projection
    $500 ┤                                                    ╭──
         │                                                 ╭──╯
         │                                              ╭──╯
         │                                           ╭──╯
    $250 ┤                                        ╭──╯
         │                                    ╭───╯
         │                                ╭───╯
         │                           ╭────╯
    $100 ┤                      ╭────╯
         │                 ╭────╯
         │            ╭────╯
      $25 ┤       ╭───╯
         │   ╭───╯
      $5 ┤───╯
         └────────────────────────────────────────────────────
          0          100         200         300         400  days
```

| Daily Return | Days to $500 | Timeframe |
|:---:|:---:|:---:|
| 0.5% | 922 days | ~2.5 years |
| 1.0% | 462 days | ~1.3 years |
| 1.5% | 309 days | ~10 months |
| **2.0%** | **232 days** | **~7.7 months** |

### Capital Tiers

The bot automatically adjusts its behavior as the bankroll grows:

```
 TIER 1                    TIER 2                    TIER 3
 $5 — $25                  $25 — $100                $100+
 ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
 │ 1 position max  │       │ 3 positions max │       │ 10 positions max│
 │ 100% per trade  │  ──►  │ 50% per trade   │  ──►  │ 20% per trade   │
 │ High cert. only │       │ + Value Betting │       │ + Market Making │
 │ Min prob: 85%   │       │ Min prob: 70%   │       │ Min prob: 55%   │
 └─────────────────┘       └─────────────────┘       └─────────────────┘
```

---

## 🏗 Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        NGINX (Port 80/443)                       │
│                    Reverse Proxy + SSL Termination                │
└──────────┬────────────────────────────┬──────────────────────────┘
           │                            │
           ▼                            ▼
┌─────────────────────┐     ┌──────────────────────┐
│   React Dashboard   │     │   FastAPI + Bot       │
│   (Static Nginx)    │     │   (Single Process)    │
│                     │     │                       │
│  • Dashboard        │     │  ┌─────────────────┐  │
│  • Trades           │◄───►│  │   FastAPI App    │  │
│  • Strategies       │ API │  │   /api/* + /ws/* │  │
│  • Markets          │     │  └────────┬────────┘  │
│  • Risk             │     │           │            │
│  • Settings         │     │  ┌────────▼────────┐  │
│                     │     │  │  Trading Engine  │  │
└─────────────────────┘     │  │  (asyncio task)  │  │
                            │  │                  │  │
                            │  │  ┌────────────┐  │  │
                            │  │  │ Strategies │  │  │
                            │  │  │ Risk Mgr   │  │  │
                            │  │  │ Portfolio   │  │  │
                            │  │  │ Orders     │  │  │
                            │  │  └────────────┘  │  │
                            │  └────────┬────────┘  │
                            │           │            │
                            │  ┌────────▼────────┐  │
                            │  │  SQLite (WAL)   │  │
                            │  └─────────────────┘  │
                            └──────────────────────┘

                            ┌──────────────────────┐
                            │   Polymarket APIs     │
                            │  • CLOB API (orders)  │
                            │  • Gamma API (markets) │
                            │  • Data API (balance)  │
                            │  • WebSocket (prices)  │
                            └──────────────────────┘
```

**Key Design Decision:** Bot and API run in the **same Python process** — the trading engine runs as an `asyncio` background task inside FastAPI. This saves ~50% RAM on the 1GB Lightsail server.

---

## 🎲 Trading Strategies

### 1. ⏱ Time Decay (Tier 1+)

> Buy near-certain outcomes close to market resolution

The primary strategy. Targets markets resolving within 48 hours where one outcome has >85% implied probability.

```
Example:
  Market: "Will BTC be above $50K on March 1?"
  BTC Price: $95,000 on Feb 28
  YES Token: $0.97

  → Buy YES at $0.97
  → Market resolves YES → Collect $1.00
  → Profit: +3.1% ($0.03/share)
```

- **Win Rate:** 90–95%
- **Avg Profit:** $0.03–$0.10 per share
- **Hold Time:** <48 hours

### 2. 🔄 Arbitrage (Tier 1+)

> Exploit pricing inconsistencies for risk-free profit

Detects when YES + NO prices sum to less than $1.00, guaranteeing profit regardless of outcome.

```
Example:
  YES: $0.52 + NO: $0.46 = $0.98
  Buy both → Guaranteed $1.00 payout
  → Risk-free profit: $0.02 (2%)
```

### 3. 📊 Value Betting (Tier 2+)

> Detect mispriced markets using order book analysis

Analyzes order book imbalance and volume momentum to identify markets where the true probability differs from the market price.

### 4. 💹 Market Making (Tier 3+)

> Provide liquidity on both sides of the spread

Places limit orders on bid and ask to capture the spread and earn maker rebates. Only enabled with sufficient capital ($100+) to manage inventory risk.

---

## 🛡 Risk Management

Multi-layered risk system with **cascading checks** — every trade must pass all gates:

```
Signal ──► Daily Loss ──► Drawdown ──► Positions ──► Category ──► Edge ──► Win Prob ──► ✅ Execute
              │              │             │            │           │          │
              ▼              ▼             ▼            ▼           ▼          ▼
           ❌ Stop        ❌ Stop       ❌ Skip      ❌ Skip    ❌ Skip    ❌ Skip
```

### Risk Limits by Tier

| Rule | Tier 1 ($5-$25) | Tier 2 ($25-$100) | Tier 3 ($100+) |
|:---|:---:|:---:|:---:|
| Max Positions | 1 | 3 | 10 |
| Max Per Position | 100% | 50% | 20% |
| Daily Loss Limit | 10% | 10% | 8% |
| Max Drawdown | 25% | 20% | 15% |
| Min Edge Required | 5% | 3% | 2% |
| Min Win Probability | 85% | 70% | 55% |
| Max Per Category | 100% | 60% | 40% |

### Position Sizing

Uses **Quarter-Kelly Criterion** for conservative sizing:

```
f* = 0.25 × (p - c) / (1 - c)

where:
  p = estimated real probability
  c = market price (cost)
  0.25 = quarter-Kelly multiplier (reduces risk of ruin)
```

---

## 📊 Dashboard

Six-page React dashboard for full visibility into the bot's operations:

| Page | Description |
|:---|:---|
| **Dashboard** | Equity curve, PnL cards, active positions, recent trades |
| **Trades** | Paginated trade history with filters, CSV export |
| **Strategies** | Per-strategy performance: win rate, PnL, Sharpe ratio |
| **Markets** | Live market scanner with opportunities and signals |
| **Risk** | Drawdown chart, category exposure (pie), risk limits |
| **Settings** | Pause/resume trading, risk parameters, system info |

Features real-time WebSocket updates and auto-refreshing queries.

---

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- Node.js 20+
- Docker & Docker Compose (for production)

### Local Development

```bash
# 1. Clone the repo
git clone https://github.com/dbfarias/dbf-poly-agent.git
cd dbf-poly-agent

# 2. Setup Python environment
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"

# 3. Configure environment
cp .env.example .env
# Generate an API secret key (required):
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Paste it into .env as API_SECRET_KEY=<generated-key>

# 4. Start the backend (bot + API)
uvicorn api.main:app --reload

# 5. Start the frontend (separate terminal)
cd frontend
npm install
npm run dev
```

The dashboard will be at `http://localhost:5173` and the API at `http://localhost:8000`.

### Docker (Production-like)

```bash
cp .env.example .env
# Set API_SECRET_KEY (required, min 16 chars):
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Edit .env with your key and other settings
docker compose up --build
```

Dashboard at `http://localhost:80`. API and frontend ports are internal-only (Nginx proxies all traffic).

---

## ⚙ Configuration

All configuration is via environment variables (`.env` file):

| Variable | Default | Description |
|:---|:---|:---|
| `API_SECRET_KEY` | — | **Required.** Min 16 chars. Used for API auth and WS token |
| `ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Comma-separated CORS origins |
| `TRADING_MODE` | `paper` | `paper` or `live` — **paper is default** |
| `INITIAL_BANKROLL` | `5.0` | Starting capital in USD |
| `SCAN_INTERVAL_SECONDS` | `60` | Market scan frequency (5–3600) |
| `SNAPSHOT_INTERVAL_SECONDS` | `300` | Portfolio snapshot frequency |
| `MAX_DAILY_LOSS_PCT` | `0.10` | Daily loss limit (0–50%) |
| `MAX_DRAWDOWN_PCT` | `0.25` | Max drawdown before halt (0–50%) |
| `POLY_API_KEY` | — | Polymarket API key (required for live mode) |
| `POLY_API_SECRET` | — | Polymarket API secret |
| `POLY_API_PASSPHRASE` | — | Polymarket API passphrase |
| `POLY_PRIVATE_KEY` | — | Wallet private key (required for live mode) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token (optional) |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID (optional) |

> **Important:** The bot starts in **paper trading mode** by default. You must explicitly set `TRADING_MODE=live` to trade with real funds. Live mode requires all four `POLY_*` credentials to be set.

---

## 🌐 Deployment

### AWS Lightsail ($5/month)

```bash
# 1. Create a Lightsail instance (Ubuntu, 1GB RAM, 2 vCPU)

# 2. SSH into the server and run setup
scp deploy/lightsail/setup.sh user@server:~/
ssh user@server 'bash ~/setup.sh'

# 3. Clone repo and configure
ssh user@server
cd ~/polybot
git clone https://github.com/dbfarias/dbf-poly-agent.git .
cp .env.example .env
nano .env  # Add your API keys

# 4. Launch
docker compose up -d
```

### CI/CD

Push to `main` triggers GitHub Actions:
1. **Test** — pytest + ruff lint
2. **Deploy** — SSH to server, pull, rebuild, restart

### Monthly Cost

| Item | Cost |
|:---|:---:|
| Lightsail 1GB | $5.00 |
| S3 Backups | ~$0.05 |
| Monitoring | Free |
| CI/CD | Free |
| **Total** | **~$5.05** |

---

## 🔒 Security

The bot handles real money — security is enforced at every layer.

### API Authentication

All API endpoints (except `/api/health`) require an `X-API-Key` header matching the configured `API_SECRET_KEY`. WebSocket connections require a `?token=` query parameter with the same key.

```bash
# Authenticated request
curl -H "X-API-Key: $API_SECRET_KEY" http://localhost:8000/api/config/

# Unauthenticated → 401
curl http://localhost:8000/api/config/
```

### Input Validation

All configuration update fields are bounded by Pydantic validators:
- `scan_interval_seconds`: 5–3600
- `max_daily_loss_pct`: 0–50%
- `max_drawdown_pct`: 0–50%

### Secrets Management

- `API_SECRET_KEY` must be at least 16 characters (app refuses to start otherwise)
- Live mode requires all four Polymarket credentials to be set
- No hardcoded defaults for secrets — `.env.example` ships with empty values
- Database URLs are sanitized before logging (credentials stripped)

### Infrastructure Hardening

| Layer | Protection |
|:---|:---|
| **CORS** | Restricted to configured origins (no wildcard) |
| **Nginx** | Rate limiting (30 req/min), security headers (X-Frame-Options, CSP, etc.) |
| **Docker** | Non-root container user, no exposed ports (Nginx-only access) |
| **WebSocket** | Token auth + max 10 concurrent connections |
| **Math** | Bounds checks on Kelly criterion and position sizing inputs |

---

## 🧪 Testing

**135 tests** across 12 test files covering bot logic and API endpoints.

| Module | Tests | Coverage |
|--------|-------|----------|
| RiskManager | 34 | 99% |
| Strategies (base, time_decay, arbitrage) | 33 | 80–100% |
| API routers (config, portfolio, trades, risk, markets, strategies) | 28 | 94–100% |
| Config, math_utils, types, market_cache | 34 | 84–100% |

```bash
# Run all tests (API_SECRET_KEY is set automatically in test fixtures)
API_SECRET_KEY=test-key-32chars-long-enough-xx pytest tests/ -v

# Run with coverage
API_SECRET_KEY=test-key-32chars-long-enough-xx pytest tests/ --cov=bot --cov=api --cov-report=term-missing

# Lint
ruff check bot/ api/

# Type check frontend
cd frontend && npx tsc --noEmit
```

### Validation Checklist

- [ ] `docker compose up` → dashboard loads at `localhost:80`
- [ ] Paper trading 48h → bot finds opportunities and simulates trades
- [ ] Live with $5 → first real trade visible in dashboard
- [ ] Telegram alerts working for trades and errors
- [ ] Health check endpoint returns 200

---

## 📁 Project Structure

```
dbf-poly-agent/
├── bot/                              # Trading bot (Python asyncio)
│   ├── main.py                       # Entry point
│   ├── config.py                     # Pydantic Settings + tier config
│   ├── agent/
│   │   ├── engine.py                 # Main trading loop
│   │   ├── portfolio.py              # Portfolio state tracker
│   │   ├── market_analyzer.py        # Market scanner
│   │   ├── order_manager.py          # Order lifecycle
│   │   ├── risk_manager.py           # Tier-based risk checks
│   │   └── strategies/
│   │       ├── base.py               # Abstract strategy interface
│   │       ├── time_decay.py         # Near-resolution strategy
│   │       ├── arbitrage.py          # YES+NO arbitrage
│   │       ├── value_betting.py      # Order book analysis
│   │       └── market_making.py      # Spread capture
│   ├── polymarket/
│   │   ├── client.py                 # CLOB API wrapper (async)
│   │   ├── gamma.py                  # Market discovery API
│   │   ├── data_api.py              # Positions & balance API
│   │   ├── websocket_manager.py      # Real-time price feed
│   │   ├── heartbeat.py              # API session keepalive
│   │   └── types.py                  # Pydantic models
│   ├── data/
│   │   ├── database.py               # SQLite async + WAL
│   │   ├── models.py                 # 6 ORM models
│   │   ├── repositories.py           # CRUD operations
│   │   └── market_cache.py           # In-memory TTL cache
│   └── utils/
│       ├── logging_config.py         # structlog JSON logging
│       ├── math_utils.py             # Kelly, Sharpe, drawdown
│       ├── retry.py                  # Exponential backoff
│       └── notifications.py          # Telegram alerts
├── api/                              # FastAPI (same process as bot)
│   ├── main.py                       # App + bot as background task
│   ├── middleware.py                  # API key authentication
│   ├── schemas.py                    # Response models (with validation)
│   ├── dependencies.py               # DB session, engine access
│   └── routers/
│       ├── portfolio.py              # GET /api/portfolio/*
│       ├── trades.py                 # GET /api/trades/*
│       ├── strategies.py             # GET /api/strategies/*
│       ├── markets.py                # GET /api/markets/*
│       ├── risk.py                   # GET /api/risk/*
│       ├── config.py                 # GET/PUT /api/config/*
│       └── websocket.py              # WS /ws/live
├── frontend/                         # React 18 + TypeScript + Vite
│   └── src/
│       ├── pages/                    # 6 dashboard pages
│       ├── components/               # Reusable UI components
│       ├── api/client.ts             # API client + types
│       └── hooks/useWebSocket.ts     # Real-time WS hook
├── deploy/
│   ├── nginx/                        # Reverse proxy config
│   ├── lightsail/setup.sh            # Server provisioning
│   └── scripts/                      # Backup + health check
├── tests/                            # 135 pytest tests (12 files)
│   ├── conftest.py                   # Shared fixtures (async DB, mock engine, HTTP client)
│   ├── test_risk_manager.py          # 34 tests — cascading risk checks
│   ├── test_base_strategy.py         # 6 tests — tier gating
│   ├── test_time_decay_strategy.py   # 16 tests — probability & confidence
│   ├── test_arbitrage_strategy.py    # 11 tests — arb detection
│   ├── test_api_config.py            # 8 tests — config endpoints
│   ├── test_api_portfolio.py         # 10 tests — portfolio endpoints
│   └── test_api_trades_risk_markets.py # 10 tests — trades/risk/markets
├── docker-compose.yml                # Full stack orchestration
├── Dockerfile.bot                    # Python (bot + API)
├── Dockerfile.frontend               # React → Nginx
└── pyproject.toml                    # Python dependencies
```

---

## 🔧 Tech Stack

<table>
<tr>
<td valign="top" width="50%">

### Backend
- **Python 3.11** — asyncio runtime
- **FastAPI** — REST API + WebSocket
- **SQLAlchemy 2.0** — async ORM
- **SQLite** — WAL mode database
- **py-clob-client** — Polymarket CLOB
- **httpx** — async HTTP client
- **structlog** — JSON logging
- **Pydantic v2** — validation & settings
- **tenacity** — retry logic

</td>
<td valign="top" width="50%">

### Frontend
- **React 18** — UI framework
- **TypeScript** — type safety
- **Vite** — build tool
- **TanStack Query** — data fetching
- **Recharts** — charts & graphs
- **Tailwind CSS** — styling
- **Lucide React** — icons

</td>
</tr>
<tr>
<td valign="top">

### Infrastructure
- **Docker Compose** — orchestration
- **Nginx** — reverse proxy + static
- **AWS Lightsail** — $5/mo hosting
- **GitHub Actions** — CI/CD
- **AWS S3** — database backups

</td>
<td valign="top">

### Monitoring
- **Telegram Bot** — trade alerts
- **Health endpoint** — `/api/health`
- **Cron jobs** — backup + health check
- **CloudWatch** — instance monitoring

</td>
</tr>
</table>

---

<div align="center">

### API Endpoints

</div>

All endpoints except `/api/health` require `X-API-Key` header. WebSocket requires `?token=` query param.

| Method | Endpoint | Description |
|:---:|:---|:---|
| `GET` | `/api/health` | Health check (no auth) |
| `GET` | `/api/status` | Full engine status |
| `GET` | `/api/portfolio/overview` | Portfolio summary |
| `GET` | `/api/portfolio/positions` | Open positions |
| `GET` | `/api/portfolio/equity-curve` | Equity history |
| `GET` | `/api/portfolio/allocation` | Category allocation |
| `GET` | `/api/trades/history` | Trade history (filterable) |
| `GET` | `/api/trades/stats` | Trade statistics |
| `GET` | `/api/strategies/performance` | Strategy metrics |
| `GET` | `/api/markets/scanner` | Market opportunities |
| `GET` | `/api/markets/opportunities` | Cached market data |
| `GET` | `/api/risk/metrics` | Current risk state |
| `GET` | `/api/risk/limits` | Risk limits for current tier |
| `GET` | `/api/config/` | Bot configuration |
| `PUT` | `/api/config/` | Update configuration |
| `POST` | `/api/trading/pause` | Pause trading |
| `POST` | `/api/trading/resume` | Resume trading |
| `WS` | `/ws/live?token=KEY` | Real-time updates (token auth) |

---

<div align="center">

**Built with precision.** Paper trade first. Manage risk always.

</div>
