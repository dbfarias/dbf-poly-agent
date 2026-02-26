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
# Edit .env with your settings (paper trading works without API keys)

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
# Edit .env with your settings
docker compose up --build
```

Dashboard at `http://localhost:80`, API at `http://localhost:8000`.

---

## ⚙ Configuration

All configuration is via environment variables (`.env` file):

| Variable | Default | Description |
|:---|:---|:---|
| `TRADING_MODE` | `paper` | `paper` or `live` — **paper is default** |
| `INITIAL_BANKROLL` | `5.0` | Starting capital in USD |
| `SCAN_INTERVAL_SECONDS` | `60` | Market scan frequency |
| `SNAPSHOT_INTERVAL_SECONDS` | `300` | Portfolio snapshot frequency |
| `MAX_DAILY_LOSS_PCT` | `0.10` | Daily loss limit (10%) |
| `MAX_DRAWDOWN_PCT` | `0.25` | Max drawdown before halt (25%) |
| `POLY_API_KEY` | — | Polymarket API key (live mode only) |
| `POLY_API_SECRET` | — | Polymarket API secret |
| `POLY_API_PASSPHRASE` | — | Polymarket API passphrase |
| `POLY_PRIVATE_KEY` | — | Wallet private key (live mode only) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token (optional) |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID (optional) |

> ⚠️ **Important:** The bot starts in **paper trading mode** by default. You must explicitly set `TRADING_MODE=live` to trade with real funds.

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

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=bot --cov-report=html

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
│   ├── schemas.py                    # Response models
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
├── tests/                            # pytest test suite
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

| Method | Endpoint | Description |
|:---:|:---|:---|
| `GET` | `/api/health` | Health check |
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
| `WS` | `/ws/live` | Real-time updates |

---

<div align="center">

**Built with precision.** Paper trade first. Manage risk always.

</div>
