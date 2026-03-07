<div align="center">

# PolyBot

### Autonomous Polymarket Trading Agent

An AI-powered trading bot that operates 24/7 on [Polymarket](https://polymarket.com), targeting conservative daily returns through micro-operations. Includes a full React dashboard for real-time monitoring.

[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![React](https://img.shields.io/badge/React-18-61dafb?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-Private-red?style=for-the-badge)]()

---

**$5 to $500** | **6 Strategies** | **AI Trade Debates** | **Tier-Based Risk** | **Active Rebalancing** | **Adaptive Learning** | **Real-Time Dashboard**

</div>

---

## Table of Contents

- [Overview](#overview)
- [Growth Model](#growth-model)
- [Architecture](#architecture)
- [Trading Strategies](#trading-strategies)
- [AI-Powered Analysis](#ai-powered-analysis)
- [Risk Management](#risk-management)
- [Adaptive Learning](#adaptive-learning)
- [Active Rebalancing](#active-rebalancing)
- [Dashboard](#dashboard)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Deployment](#deployment)
- [Security](#security)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [Tech Stack](#tech-stack)
- [API Reference](#api-reference)

---

## Overview

PolyBot is a fully autonomous prediction market trading agent designed to grow a small bankroll ($5) through conservative, high-probability trades. It runs as a single Python process (bot + API) with a React dashboard for monitoring.

### Key Features

| Feature | Description |
|:---|:---|
| **6 Trading Strategies** | Time Decay, Arbitrage, Value Betting, Price Divergence, Swing Trading, Market Making |
| **Adaptive Learning** | Self-tuning engine (PerformanceLearner) adjusts edge/confidence/urgency from trade history every 5 min |
| **Active Rebalancing** | Automatically closes weak losers to make room for higher-edge signals |
| **Market Quality Filter** | Order book depth, spread, liquidity checks + Gamma API discovery (500 markets/scan) |
| **Tier-Based Risk System** | 3 capital tiers with automatic position sizing and limit adaptation |
| **Quarter-Kelly Sizing** | Conservative position sizing to minimize risk of ruin |
| **Paper Trading Mode** | ON by default — test safely before going live |
| **AI Trade Debates** | Claude Haiku-powered Proposer vs Challenger debate gate — every trade is debated before execution |
| **AI Position Reviewer** | LLM reviews open positions every ~30min: HOLD, EXIT, REDUCE, or INCREASE recommendations |
| **LLM Sentiment** | Optional Claude-powered sentiment analysis replacing VADER for deeper market understanding |
| **Secure Dashboard** | JWT-authenticated React app with 11 pages including AI Debates viewer and Research |
| **WebSocket Updates** | Live portfolio and trade updates pushed to dashboard |
| **Activity Log** | Every bot decision logged with reasoning — visible in dashboard |
| **Telegram Alerts** | Trade notifications, error alerts, daily performance summaries |
| **Docker Deploy** | One-command deployment on AWS Lightsail (~$5/month) with HTTPS |
| **CI/CD Pipeline** | GitHub Actions 3-job pipeline: test, build, deploy on every push to main |

---

## Growth Model

Compound growth targeting **1% daily return** via high-probability short-term trades:

```
                        Compound Growth Projection
    $500 |                                                    .--
         |                                                 .--'
         |                                              .--'
         |                                           .--'
    $250 |                                        .--'
         |                                    .---'
         |                                .---'
         |                           .----'
    $100 |                      .----'
         |                 .----'
         |            .----'
     $25 |       .---'
         |   .---'
      $5 |---'
         +----------------------------------------------------
          0          100         200         300         400  days
```

| Daily Return | Days to $500 | Timeframe |
|:---:|:---:|:---:|
| 0.5% | 922 days | ~2.5 years |
| **1.0%** | **462 days** | **~1.3 years** |
| 1.5% | 309 days | ~10 months |
| 2.0% | 232 days | ~7.7 months |

### Capital Tiers

The bot automatically adjusts its behavior as the bankroll grows:

```
 TIER 1                    TIER 2                    TIER 3
 $5 -- $25                 $25 -- $100               $100+
 +-------------------+     +-------------------+     +-------------------+
 | 6 positions max   |     | 6 positions max   |     | 15 positions max  |
 | 40% per trade     | --> | 20% per trade     | --> | 15% per trade     |
 | 85% max deployed  |     | 80% max deployed  |     | 85% max deployed  |
 | Min edge: 1%      |     | Min edge: 2%      |     | Min edge: 2%      |
 | Min prob: 55%     |     | Min prob: 70%     |     | Min prob: 60%     |
 | Kelly: 25%        |     | Kelly: 15%        |     | Kelly: 20%        |
 |                   |     |                   |     |                   |
 | Strategies:       |     | + Swing Trading   |     | + Market Making   |
 | Arbitrage         |     |                   |     |                   |
 | Time Decay        |     |                   |     |                   |
 | Value Betting     |     |                   |     |                   |
 +-------------------+     +-------------------+     +-------------------+
```

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
|  11 Pages:          |     |  +------------------+   |
|  - Login            |<--->|  |   FastAPI App     |   |
|  - Dashboard        | API |  |   /api/* + /ws/*  |   |
|  - Trades           |     |  +--------+---------+   |
|  - Strategies       |     |           |              |
|  - Markets          |     |  +--------v---------+   |
|  - Risk             |     |  |  Trading Engine   |   |
|  - Research         |     |  |  (asyncio task)   |   |
|  - Learner          |     |  |                   |   |
|  - AI Debates       |     |  |  - 6 Strategies   |   |
|  - Activity         |     |  |  - Risk Manager   |   |
|  - Settings         |     |  |  - Portfolio      |   |
+---------------------+     |  |  - Learner        |   |
                             |  |  - Order Manager  |   |
                             |  |  - Rebalancer     |   |
                             |  |  - Research Engine|   |
                             |  |  - LLM Debate Gate|   |
                             |  +--------+---------+   |
                             |           |              |
                             |  +--------v---------+   |
                             |  |  SQLite (WAL)     |   |
                             |  +------------------+   |
                             +------------------------+

                             +------------------------+
                             |   Polymarket APIs       |
                             |  - CLOB API (orders)    |
                             |  - Gamma API (markets)  |
                             |  - Data API (balance)   |
                             |  - WebSocket (prices)   |
                             +------------------------+
```

**Key Design Decision:** Bot and API run in the **same Python process** — the trading engine runs as an `asyncio` background task inside FastAPI. This saves ~50% RAM on the 1GB Lightsail server.

---

## Trading Strategies

### 1. Time Decay (Tier 1+) -- Primary Strategy

> Buy near-certain outcomes close to market resolution

Targets markets resolving within 72 hours where one outcome has high implied probability. Dynamic MAX_PRICE adjusts based on time remaining.

```
Example:
  Market: "Will BTC be above $50K on March 1?"
  BTC Price: $95,000 on Feb 28
  YES Token: $0.97, resolves in 12h

  -> Buy YES at $0.97
  -> Market resolves YES -> Collect $1.00
  -> Profit: +3.1% ($0.03/share)
```

| Parameter | Value |
|:---|:---|
| Price range | $0.60 - $0.99 (dynamic by time) |
| Min edge | 1.5% |
| Max horizon | 72h (dynamic, expands with urgency) |
| Exit: take-profit | +3% after 12h hold |
| Exit: price drop | Below $0.70 |
| Win rate | ~90-95% |
| Hold time | < 72 hours |

### 2. Arbitrage (Tier 1+)

> Exploit pricing inconsistencies for risk-free profit

Detects when YES + NO prices sum to less than $1.00, guaranteeing profit regardless of outcome.

```
Example:
  YES: $0.52 + NO: $0.46 = $0.98
  Buy both -> Guaranteed $1.00 payout
  -> Risk-free profit: $0.02 (2%)
```

### 3. Value Betting (Tier 1+)

> Detect mispriced markets using order book analysis

Analyzes order book imbalance and volume momentum to identify markets where the true probability differs from the market price. Dynamic horizon (urgency-based, up to 168h).

| Parameter | Value |
|:---|:---|
| Min edge | 2%+ |
| Exit: take-profit | +3% after 6h hold |
| Exit: stop-loss | -10% from entry |
| Exit: floor | Below $0.40 |

### 4. Price Divergence (Tier 1+)

> Detect crypto/sentiment price divergence using external signals

Tracks price movements and detects divergence between market price and expected value. Separate hold times for crypto (24h) and non-crypto (4h) markets.

### 5. Swing Trading (Tier 2+)

> Buy markets with confirmed upward momentum

Detects 3+ consecutive rising price ticks, enters position, and exits quickly via take-profit (1.5%), stop-loss (1.5%), or time limit (4h).

```
Exit rules:
  +1.5%  -> Take Profit
  -1.5%  -> Stop Loss
  4h     -> Time exit
  3 down -> Reversal exit
```

### 6. Market Making (Tier 3+)

> Provide liquidity on both sides of the spread

Places limit orders below mid-price to capture the spread. Only enabled with $100+ capital to manage inventory risk.

### Strategy Comparison

| | Arbitrage | Time Decay | Value Betting | Price Divergence | Swing | Market Making |
|---|---|---|---|---|---|---|
| **Tier** | 1+ | 1+ | 1+ | 1+ | 2+ | 3+ |
| **Min Edge** | 1%+ | 1.5%+ | 2%+ | 2%+ | 0.5%+ | spread |
| **Horizon** | resolution | < 72h | < 168h | 4-24h | < 4h | 1-2h |
| **Win Rate** | ~95% | ~90% | ~65% | ~60% | ~60% | ~55% |
| **Risk** | Zero | Low | Medium | Medium | Medium | High |

---

## AI-Powered Analysis

Three independent LLM features using **Claude Haiku 4.5** — each toggleable via dashboard with a shared daily budget cap.

### 1. Trade Debate Gate (Proposer vs Challenger)

Every trade signal goes through a two-agent debate before execution:

```
Signal Found --> PROPOSER Agent --> BUY or PASS?
                                        |
                              BUY       |       PASS
                               |        |        |
                               v        |     SKIP (save cost)
                        CHALLENGER Agent |
                     APPROVE or REJECT?  |
                          |              |
                    APPROVE     REJECT   |
                       |           |     |
                    EXECUTE     SKIP   SKIP
```

- **Proposer** evaluates market data, edge, sentiment, timing — votes BUY or PASS
- **Challenger** critiques the proposal — looks for flawed assumptions, missed risks, market efficiency
- Trade only executes if Proposer says BUY **and** Challenger doesn't REJECT
- If Proposer says PASS, Challenger is skipped (saves API cost)
- All debates are logged to the Activity table and visible on the AI Debates dashboard page

### 2. Position Reviewer (4-Action)

Every ~30 minutes, the AI reviews each open position (>2h old) and recommends one of 4 actions:

| Action | When | Engine Behavior |
|:---|:---|:---|
| **HOLD** | Thesis intact, price stable | No action |
| **EXIT** | Thesis broken, risk too high | Full close (HIGH urgency only) |
| **REDUCE** | Take partial profits, cut exposure | Sell half the position (MEDIUM+ urgency) |
| **INCREASE** | Thesis strengthened, price improved | Log recommendation (manual review) |

Reviews are staggered across cycles (position hash modulo) to spread API costs evenly.

### 3. LLM Sentiment Analysis

Optional replacement for VADER (lexicon-based) sentiment:

- Sends market question + news headlines to Claude Haiku
- Returns sentiment score (-1.0 to +1.0) with contextual understanding
- Understands sarcasm, market implications, and nuance that VADER misses
- Cached via ResearchCache (1h TTL) to minimize API calls

### Cost Control

| Feature | Est. Cost/Day | Toggle |
|:---|:---:|:---|
| Sentiment | ~$0.38 | `use_llm_sentiment` |
| Debate Gate | ~$1.10 | `use_llm_debate` |
| Position Reviewer | ~$0.20 | `use_llm_reviewer` |
| **Total** | **~$1.68** | Daily budget cap (default $3.00) |

All LLM calls share a global `LlmCostTracker`. When the daily budget is exhausted, all LLM features gracefully fall back to non-LLM behavior (VADER sentiment, no debate gate, no reviews).

---

## Risk Management

Multi-layered risk system with **10 cascading checks** — every trade must pass all gates:

```
Signal --> Paused? --> Duplicate? --> AI Debate --> Daily Loss --> Drawdown
            |            |              |              |             |
            x STOP       x STOP     x REJECT          x STOP       x STOP
                                                                     |
   EXECUTE <-- Win Prob <-- Min Edge <-- Category <-- Deployed <-- Max Pos
      |           |           |            |            |            |
      OK          x SKIP      x SKIP       x SKIP      x SKIP     x SKIP
```

### Risk Limits by Tier

| Rule | Tier 1 ($5-$25) | Tier 2 ($25-$100) | Tier 3 ($100+) |
|:---|:---:|:---:|:---:|
| Max Positions | 6 | 6 | 15 |
| Max Per Position | 40% | 20% | 15% |
| Max Deployed | 85% | 80% | 85% |
| Daily Loss Limit | 10% | 8% | 6% |
| Max Drawdown | 25% | 15% | 12% |
| Min Edge Required | 1% | 2% | 2% |
| Min Win Probability | 55% | 70% | 60% |
| Max Per Category | 40% | 30% | 30% |
| Kelly Fraction | 25% | 15% | 20% |

### Position Sizing

Uses **Fractional Kelly Criterion** for conservative sizing:

```
f* = kelly_fraction x (p - c) / (1 - c)

where:
  p = estimated real probability
  c = market price (cost)
  kelly_fraction = 0.15-0.25 per tier

Minimum: 5 shares (Polymarket CLOB requirement)
Positions < 5 shares CANNOT be sold -- must wait for resolution
```

### Time-Adjusted Edge

Near-resolution markets need less edge because uncertainty is lower:

| Hours to Resolution | Edge Multiplier | Effective Min (Tier 2) |
|:---:|:---:|:---:|
| <= 12h | 0.3x | ~0.6% |
| <= 24h | 0.4x | ~0.8% |
| <= 48h | 0.5x | ~1.0% |
| <= 96h | 0.7x | ~1.4% |
| > 96h | 1.0x | 2.0% |

---

## Adaptive Learning

The bot includes a **PerformanceLearner** that continuously analyzes trade history and adjusts strategy parameters in real time.

### How It Works

```
SCAN -> VALIDATE -> TRADE -> TRACK -> LEARN -> (adjust) -> SCAN
```

Every 5 minutes, the learner recomputes statistics from the last 30 days of trades (up to 500):

| Adjustment | Description |
|:---|:---|
| **Edge Multiplier** | Per-strategy modifier (0.5-2.0x). Winning strategies get relaxed thresholds; losing ones require higher edge |
| **Category Confidence** | Per-category modifier (0.5-1.5x). Boosts exposure to proven categories, penalizes underperforming ones |
| **Confidence Calibration** | Compares predicted probability vs actual win rate. If 95% confidence only wins 60%, edge requirements increase |
| **Urgency Multiplier** | Daily target progress: behind target = more aggressive (expand horizons, lower edge); ahead = conservative |
| **Strategy Auto-Pause** | If last 5 trades have <30% win rate and PnL < -$0.05, the strategy is paused for 12 hours. Manual unpause via API with 6h grace period |

### Edge Multiplier Logic

| Win Rate | Multiplier | Effect |
|:---:|:---:|:---|
| > 60% | 0.8x | Relaxed — allow lower edge |
| 40-60% | 1.0x | Neutral — default thresholds |
| < 40% | 1.5x | Strict — require 50% more edge |
| No data | 1.2x | Cautious — slightly stricter |

### Urgency System

The daily target is 1% (configurable). Progress is tracked in real-time:

| Progress | Urgency | Effect |
|:---:|:---:|:---|
| > 100% | 0.7x | Conservative — raise edge requirements, narrow horizons |
| 50-100% | 1.0x | Normal — default parameters |
| 0-50% | 1.3x | Aggressive — lower edge requirements, expand time horizons |
| Negative | 1.5x+ | Very aggressive — maximum opportunity seeking |

---

## Active Rebalancing

When all position slots are full or max deployed capital is reached, the bot can find 9+ signals per cycle but blocks them all. Active rebalancing solves this by closing the weakest loser to make room.

### How It Works

When a signal is rejected due to "Max positions" or "Max deployed capital":

```
Signal rejected ("Max positions" or "Max deployed capital")
     |
     v
Edge >= min_rebalance_edge?  ---NO---> Skip (low-quality signal)
     |
    YES
     |
     v
Find losing positions (unrealized PnL <= 0)
     |
     v
Filter: >= 5 shares, held >= min_hold_seconds, not winning
     |
     v
Pick worst (lowest PnL%) -- if can't sell, try next candidate
     |
     v
Close position --> Record PnL --> Log rebalance
     |
     v
Re-evaluate signal with freed slot
     |
     v
Approved? --> Execute trade
```

### Rebalance Conditions (ALL must be true)

1. Signal rejected due to "Max positions" **or** "Max deployed capital"
2. New signal edge >= **min_rebalance_edge** (default 1.5%, tunable via admin)
3. Worst position has **unrealized PnL <= 0** (never close winners)
4. Worst position has **>= 5 shares** (can actually sell on Polymarket CLOB)
5. Worst position held for at least **min_hold_seconds** (default 120s, tunable via admin)
6. Max **1 rebalance per cycle** (prevent churning)
7. If sell fails (e.g. insufficient balance), tries next candidate instead of giving up

---

## Dashboard

JWT-authenticated React dashboard with **11 pages** for full visibility into the bot's operations:

| Page | Description |
|:---|:---|
| **Login** | Secure JWT login (username/password), 24h token expiry, auto-logout on 401 |
| **Dashboard** | Equity curve (equity + cash), daily PnL bar chart, daily target hit/miss tracker, PnL cards, daily progress vs target, active positions |
| **Trades** | Expandable trade history — click any trade to see reasoning, edge, confidence, estimated probability, price, cost, paper/live |
| **Strategies** | Per-strategy performance: win rate, PnL, Sharpe ratio (real-time from trade data) |
| **Markets** | Live market scanner with opportunities and signals |
| **Risk** | Drawdown chart, category exposure (pie), risk limits by tier |
| **Research** | News sentiment analysis: per-market headlines, sentiment scores, research multipliers |
| **Learner** | Adaptive learning dashboard: edge multipliers per strategy+category, category confidence cards, probability calibration chart, strategy pause status and cooldown timers |
| **AI Debates** | Trade debate history (Proposer vs Challenger verdicts + reasoning) and position review log (HOLD/EXIT/REDUCE/INCREASE). Tabbed view with approval rates and cost tracking |
| **Activity** | Bot decision log — every signal found, rejected, approved, with reasoning and metadata. Filterable by event type |
| **Settings** | AI feature toggles (sentiment/debate/reviewer + daily budget), strategy toggles, risk parameters, strategy parameters, quality filters, system info. All persisted across restarts |

Features: real-time WebSocket updates (JWT-authenticated), auto-refreshing queries, global refresh button, auto-logout on token expiry.

---

## Getting Started

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

## Configuration

All configuration is via environment variables (`.env` file):

| Variable | Default | Description |
|:---|:---|:---|
| `API_SECRET_KEY` | -- | **Required.** Min 16 chars. Used for API auth, JWT signing, and WS token |
| `DASHBOARD_USER` | `admin` | Dashboard login username |
| `DASHBOARD_PASSWORD` | -- | **Required for dashboard.** Dashboard login password |
| `ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Comma-separated CORS origins |
| `TRADING_MODE` | `paper` | `paper` or `live` — **paper is default** |
| `INITIAL_BANKROLL` | `5.0` | Starting capital in USD |
| `SCAN_INTERVAL_SECONDS` | `30` | Market scan frequency (5-3600) |
| `SNAPSHOT_INTERVAL_SECONDS` | `300` | Portfolio snapshot frequency |
| `DAILY_TARGET_PCT` | `0.01` | Daily profit target (1%) |
| `MAX_DAILY_LOSS_PCT` | `0.10` | Daily loss limit (0-50%) |
| `MAX_DRAWDOWN_PCT` | `0.25` | Max drawdown before halt (0-50%) |
| `POLY_API_KEY` | -- | Polymarket API key (required for live mode) |
| `POLY_API_SECRET` | -- | Polymarket API secret |
| `POLY_API_PASSPHRASE` | -- | Polymarket API passphrase |
| `POLY_PRIVATE_KEY` | -- | Wallet private key (required for live mode) |
| `FORCE_HTTPS_COOKIES` | `false` | Set `true` behind HTTPS reverse proxy |
| `DATABASE_URL` | `sqlite+aiosqlite:///data/polybot.db` | Database connection URL |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING) |
| `LOG_FORMAT` | `json` | Log format (json or console) |
| `ANTHROPIC_API_KEY` | -- | Anthropic API key for Claude Haiku (required for AI features) |
| `TELEGRAM_BOT_TOKEN` | -- | Telegram bot token (optional) |
| `TELEGRAM_CHAT_ID` | -- | Telegram chat ID (optional) |

> **Important:** The bot starts in **paper trading mode** by default. You must explicitly set `TRADING_MODE=live` to trade with real funds. Live mode requires `POLY_PRIVATE_KEY` (API creds are auto-derived).

### Runtime Settings (via Dashboard)

These parameters can be changed at runtime via the Settings page and are **persisted across restarts**:

- Scan interval, snapshot interval
- Risk parameters per tier (max positions, deployed %, edge, etc.)
- Strategy parameters (MAX_HOURS, quality filter thresholds, take-profit %)
- Learner parameters (pause lookback, win rate threshold, cooldown hours)
- Rebalance parameters (min_rebalance_edge, min_hold_seconds)
- Quality gate parameters (max spread, min volume, stop loss, take profit price)
- AI features (LLM sentiment, debate gate, position reviewer toggles + daily budget)
- Blocked market types (sports, crypto, other)
- Pause/resume trading, force-unpause strategies

---

## Deployment

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
docker compose -f docker-compose.prod.yml up -d
```

### HTTPS

HTTPS is enabled via Let's Encrypt + DuckDNS dynamic DNS. Nginx handles SSL termination.

### CI/CD

Push to `main` triggers GitHub Actions (3-job pipeline):
1. **Test** — pytest (1300+ tests, ~30s) + ruff lint + frontend build
2. **Build & Push** — Docker image to GitHub Container Registry (GHCR)
3. **Deploy** — SSH to server, pull image, restart containers

> **Note:** After deploy, `docker compose -f docker-compose.prod.yml up -d --force-recreate app` is needed to reload env changes.

### Monthly Cost

| Item | Cost |
|:---|:---:|
| Lightsail 1GB | $5.00 |
| S3 Backups | ~$0.05 |
| Monitoring | Free |
| CI/CD | Free |
| **Total** | **~$5.05** |

---

## Security

The bot handles real money — security is enforced at every layer.

### API Authentication

Dual authentication system:

| Method | Use Case | Header/Param |
|:---|:---|:---|
| **API Key** | Programmatic access, scripts | `X-API-Key: <API_SECRET_KEY>` |
| **JWT Bearer** | Dashboard login | `Authorization: Bearer <token>` |

WebSocket connections accept either API key or JWT via `?token=` query parameter.

```bash
# Login to get JWT token
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your-password"}'

# Authenticated request (API key)
curl -H "X-API-Key: $API_SECRET_KEY" http://localhost:8000/api/config/

# Authenticated request (JWT)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/config/
```

### Infrastructure Hardening

| Layer | Protection |
|:---|:---|
| **CORS** | Restricted to configured origins (no wildcard) |
| **Nginx** | Rate limiting (30 req/min), security headers (X-Frame-Options, CSP, etc.) |
| **Docker** | Non-root container user, no exposed ports (Nginx-only access) |
| **WebSocket** | Token auth + max 10 concurrent connections |
| **Math** | Bounds checks on Kelly criterion and position sizing inputs |
| **Auth** | Timing-safe password comparison, login rate limiting |
| **HTTPS** | Let's Encrypt TLS, auto-renew via certbot |
| **Secrets** | Build-time Docker secrets (no ARG/ENV), min 16-char API key |
| **Input** | Pydantic validators with bounded ranges on all config updates |

---

## Testing

**1350+ tests** across **35+ test files** covering bot logic, API endpoints, strategies, and adaptive learning.

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ --cov=bot --cov=api --cov-report=term-missing

# Lint
uv run ruff check bot/ api/ tests/

# Type check frontend
cd frontend && npx vite build
```

### Test Modules

| Module | Description |
|:---|:---|
| `test_risk_manager.py` | 73 tests — all 9 cascading risk checks, tier configs |
| `test_learner.py` | 84 tests — edge multipliers, calibration, pauses, urgency |
| `test_time_decay_strategy.py` | Time decay probability, confidence, dynamic MAX_PRICE |
| `test_swing_trading.py` | Momentum detection, exit rules, position scoring |
| `test_engine.py` | Engine init, shutdown, liquidity checks, fill callbacks |
| `test_rebalance.py` | 12 tests — all rebalance conditions and edge cases |
| `test_order_manager.py` | Order execution, monitoring, cancellation, min shares |
| `test_portfolio.py` | Position tracking, PnL, sync, settlement prices |
| `test_api_auth.py` | JWT creation, decoding, expiry, password verification |
| `test_settings_store.py` | Persist/restore settings across restarts |
| `test_market_analyzer.py` | Market scanning, quality filtering, deduplication |
| `test_llm_debate.py` | 18 tests — debate parsers, cost tracker, full debate flow, position review |
| `test_llm_sentiment.py` | 10 tests — Haiku sentiment, clamping, errors, routing |
| `test_config.py` | Tier config, settings validation, capital tiers |
| `test_math_utils.py` | Kelly criterion, Sharpe ratio, drawdown |
| + 12 more | API routers, types, cache, strategies, price rounding |

---

## Project Structure

```
dbf-poly-agent/
|-- bot/                              # Trading bot (Python asyncio)
|   |-- config.py                     # Pydantic Settings + 3-tier config
|   |-- agent/
|   |   |-- engine.py                 # Main trading loop (60s cycle) + rebalancing
|   |   |-- portfolio.py              # Portfolio state tracker + Polymarket sync
|   |   |-- market_analyzer.py        # Gamma API scanner + quality filter
|   |   |-- order_manager.py          # Order lifecycle (place, monitor, cancel)
|   |   |-- risk_manager.py           # 9 cascading risk checks
|   |   |-- learner.py                # Adaptive learning engine
|   |   |-- position_closer.py        # Exit logic + rebalancing
|   |   +-- strategies/
|   |       |-- base.py               # Abstract strategy interface
|   |       |-- time_decay.py         # Near-resolution strategy (primary)
|   |       |-- arbitrage.py          # YES+NO arbitrage
|   |       |-- value_betting.py      # Order book analysis
|   |       |-- price_divergence.py   # Crypto/sentiment divergence
|   |       |-- swing_trading.py      # Momentum-based short-term
|   |       +-- market_making.py      # Spread capture
|   |-- polymarket/
|   |   |-- client.py                 # CLOB API wrapper (async)
|   |   |-- gamma.py                  # Market discovery API (500 mkts/scan)
|   |   |-- data_api.py              # Positions & balance API
|   |   |-- websocket_manager.py      # Real-time price feed
|   |   |-- heartbeat.py              # API session keepalive
|   |   +-- types.py                  # Pydantic models
|   |-- data/
|   |   |-- database.py               # SQLite async + WAL + migrations
|   |   |-- models.py                 # ORM models (Trade, Position, Snapshot, etc.)
|   |   |-- repositories.py           # CRUD operations
|   |   |-- activity.py               # Bot activity logger (decision log)
|   |   |-- settings_store.py         # Persistent settings (survives restarts)
|   |   +-- market_cache.py           # In-memory TTL cache
|   |-- research/
|   |   |-- engine.py                 # Background market research engine
|   |   |-- llm_sentiment.py          # Claude Haiku sentiment analysis
|   |   |-- llm_debate.py             # Proposer vs Challenger debate gate + Position reviewer
|   |   |-- sentiment.py              # VADER lexicon-based sentiment (fallback)
|   |   +-- cache.py                  # Research result cache (1h TTL)
|   +-- utils/
|       |-- logging_config.py         # structlog JSON logging
|       |-- math_utils.py             # Kelly, Sharpe, drawdown
|       |-- retry.py                  # Exponential backoff
|       +-- notifications.py          # Telegram alerts
|-- api/                              # FastAPI (same process as bot)
|   |-- main.py                       # App + bot as background task
|   |-- auth.py                       # JWT login + /me + /logout
|   |-- middleware.py                  # Dual auth (API key + JWT Bearer)
|   |-- schemas.py                    # Response models (with validation)
|   |-- dependencies.py               # DB session, engine access
|   +-- routers/
|       |-- portfolio.py              # GET /api/portfolio/* + daily-pnl + POST force-close
|       |-- trades.py                 # GET /api/trades/*
|       |-- strategies.py             # GET /api/strategies/*
|       |-- markets.py                # GET /api/markets/*
|       |-- risk.py                   # GET /api/risk/*
|       |-- config.py                 # GET/PUT /api/config/ + pause/resume/reset
|       |-- activity.py               # GET /api/activity/ + event types
|       |-- learner.py                # GET/POST /api/learner/* (multipliers, calibration, pauses, unpause)
|       +-- websocket.py              # WS /ws/live
|-- frontend/                         # React 18 + TypeScript + Vite
|   +-- src/
|       |-- pages/                    # 11 pages (Login + 10 dashboard pages)
|       |-- components/               # Reusable UI (TradeTable, Layout, charts)
|       |-- api/client.ts             # API client + types + 401 interceptor
|       +-- hooks/
|           |-- useAuth.ts            # JWT auth state (login/logout, session storage)
|           +-- useWebSocket.ts       # Real-time WS hook (JWT-authenticated)
|-- deploy/
|   |-- nginx/                        # Reverse proxy + SSL config
|   |-- lightsail/setup.sh            # Server provisioning
|   +-- scripts/                      # Backup + health check
|-- docs/
|   +-- STRATEGY_GUIDE.md             # Detailed strategy & decision documentation
|-- tests/                            # 1350+ pytest tests (35+ files)
|-- docker-compose.yml                # Dev stack
|-- docker-compose.prod.yml           # Production stack
|-- Dockerfile.bot                    # Python (bot + API)
|-- Dockerfile.frontend               # React -> Nginx
+-- pyproject.toml                    # Python dependencies (uv)
```

---

## Tech Stack

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
- **PyJWT** — JWT authentication
- **anthropic** — Claude Haiku AI (debate + sentiment)
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
- **Nginx** — reverse proxy + static + SSL
- **AWS Lightsail** — $5/mo hosting (Mumbai)
- **GitHub Actions** — CI/CD (3-job pipeline)
- **Let's Encrypt** — HTTPS certificates
- **DuckDNS** — Dynamic DNS

</td>
<td valign="top">

### Tooling
- **uv** — Python package manager
- **ruff** — Python linter
- **pytest** — 1350+ tests
- **pytest-asyncio** — async test support
- **Telegram Bot** — trade alerts
- **Health endpoint** — `/api/health`

</td>
</tr>
</table>

---

## API Reference

All endpoints except `/api/health` and `/api/auth/login` require authentication (API key or JWT). WebSocket requires `?token=` query param.

| Method | Endpoint | Description |
|:---:|:---|:---|
| `GET` | `/api/health` | Health check (no auth) |
| `POST` | `/api/auth/login` | JWT login (returns token) |
| `GET` | `/api/auth/me` | Current user info |
| `POST` | `/api/auth/logout` | Logout (client-side) |
| `GET` | `/api/status` | Full engine status |
| `GET` | `/api/portfolio/overview` | Portfolio summary + daily progress |
| `GET` | `/api/portfolio/positions` | Open positions |
| `GET` | `/api/portfolio/equity-curve` | Equity history |
| `GET` | `/api/portfolio/allocation` | Category allocation |
| `GET` | `/api/portfolio/daily-pnl` | Daily PnL history (aggregated by day) |
| `POST` | `/api/portfolio/positions/close` | Force-close a position |
| `GET` | `/api/trades/history` | Trade history (filterable) |
| `GET` | `/api/trades/stats` | Trade statistics |
| `GET` | `/api/strategies/performance` | Strategy metrics |
| `GET` | `/api/markets/scanner` | Live market scanner |
| `GET` | `/api/markets/opportunities` | Cached market data |
| `GET` | `/api/risk/metrics` | Current risk state |
| `GET` | `/api/risk/limits` | Risk limits for current tier |
| `GET` | `/api/activity/` | Activity log (filterable by type) |
| `GET` | `/api/activity/event-types` | Available event types |
| `GET` | `/api/learner/multipliers` | Edge multipliers + category confidences |
| `GET` | `/api/learner/calibration` | Probability calibration per bucket |
| `GET` | `/api/learner/pauses` | Strategy pause status + cooldowns |
| `POST` | `/api/learner/unpause` | Force-unpause a strategy (6h grace period) |
| `GET` | `/api/config/` | Bot configuration |
| `PUT` | `/api/config/` | Update configuration (persisted) |
| `POST` | `/api/trading/pause` | Pause trading |
| `POST` | `/api/trading/resume` | Resume trading |
| `POST` | `/api/config/risk/reset` | Reset risk state (peak equity, daily PnL) |
| `WS` | `/ws/live?token=KEY` | Real-time updates (token auth) |

---

<div align="center">

**Built with precision.** Paper trade first. Manage risk always.

</div>
