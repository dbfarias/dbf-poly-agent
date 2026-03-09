<div align="center">

# PolyBot

### Autonomous Polymarket Trading Agent

[![Python](https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![React](https://img.shields.io/badge/React-18-61dafb?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-Private-red?style=for-the-badge)]()

---

**6 Strategies** | **AI Trade Debates** | **Multi-Source Research** | **Quantitative Risk Gates** | **Adaptive Learning** | **Real-Time Dashboard**

</div>

---

## Executive Summary

PolyBot is a fully autonomous prediction market trading agent for [Polymarket](https://polymarket.com). It operates 24/7 as a single Python process (FastAPI + asyncio trading engine), with a React dashboard for real-time monitoring and configuration.

**How it works:** Every 60 seconds, the bot scans ~600 markets via the Gamma API, runs 6 parallel trading strategies to generate signals, then passes each signal through a **14-stage risk pipeline** before execution. The pipeline includes quantitative gates (Value-at-Risk, Z-Score, VPIN toxic flow detection), an AI debate between two Claude Haiku agents (Proposer vs Challenger), and traditional risk checks (drawdown, position limits, deployed capital). Only signals that survive all 14 stages are executed.

**Key differentiators:**
- **Quantitative risk management** — Parametric VaR (95% confidence), mispricing Z-Score normalization, VPIN toxic flow filter, profit factor tracking, rolling Sharpe ratio
- **AI-powered trade filtering** — Every trade is debated by two LLM agents before execution; post-mortem analysis feeds back into the learning engine
- **Multi-source research** — Google News, Reddit, Twitter/X (via Tavily), CoinGecko, order book whale detection, volume anomaly tracking, cross-market correlation
- **Self-tuning** — PerformanceLearner adjusts edge multipliers, category confidence, and urgency every 5 minutes based on trade history
- **Market type filtering** — Sports/esports markets (coin flips for the bot) are automatically blocked via keyword classification

**Architecture:** Bot + API run in the same process on a $5/month AWS Lightsail instance. SQLite (WAL mode) for persistence. Docker Compose for deployment. GitHub Actions CI/CD with 1350+ tests.

---

## Table of Contents

- [Trade Pipeline](#trade-pipeline)
- [Growth Model](#growth-model)
- [Architecture](#architecture)
- [Trading Strategies](#trading-strategies)
- [AI-Powered Analysis](#ai-powered-analysis)
- [Quantitative Risk Gates](#quantitative-risk-gates)
- [Risk Management](#risk-management)
- [Adaptive Learning](#adaptive-learning)
- [Research Engine](#research-engine)
- [Price Momentum Tracking](#price-momentum-tracking)
- [Post-Mortem Feedback Loop](#post-mortem-feedback-loop)
- [Auto-Claim Resolved Positions](#auto-claim-resolved-positions)
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

## Trade Pipeline

Every 60 seconds, the engine runs the complete signal-to-execution pipeline. A signal must pass **all 14 stages** to become a trade:

```
                         POLYMARKET GAMMA API (~600 markets)
                                     |
                                     v
                    +--------------------------------+
                    |  1. MARKET SCAN & QUALITY      |
                    |     - Order book depth          |
                    |     - Spread < 15%              |
                    |     - Volume > $500             |
                    |     - Sports/esports blocked    |
                    +----------------+---------------+
                                     |
                                     v
                    +--------------------------------+
                    |  2. STRATEGY EVALUATION         |
                    |     6 parallel strategies:      |
                    |     - Time Decay                |
                    |     - Arbitrage                 |
                    |     - Value Betting (+VPIN)     |
                    |     - Price Divergence          |
                    |     - Swing Trading             |
                    |     - Market Making             |
                    +----------------+---------------+
                                     |
                              Signal generated
                                     |
        +----------------------------v----------------------------+
        |                    RISK PIPELINE (14 gates)             |
        |                                                          |
        |  3. Learner pause check   — strategy auto-paused?       |
        |  4. Market cooldown       — traded recently? (3h)       |
        |  5. Duplicate position    — already holding this market?|
        |  6. Correlation check     — correlated with open pos?   |
        |  7. VaR pre-check         — daily VaR too high?         |
        |  8. Debate cooldown       — debated & rejected < 1h?    |
        |  9. LLM Debate            — Proposer + Challenger vote  |
        | 10. Z-Score gate          — |Z| >= 1.5 required         |
        | 11. Daily loss limit      — within daily loss budget?   |
        | 12. Drawdown check        — within max drawdown?        |
        | 13. Position limits       — slots + deployed capital    |
        | 14. Category exposure     — per-category cap            |
        |                                                          |
        +----------------------------+----------------------------+
                                     |
                              All gates passed
                                     |
                                     v
                    +--------------------------------+
                    |  POSITION SIZING (Kelly)        |
                    |  f* = kelly_frac * (p - c)/(1-c)|
                    |  Min: 5 shares, $1.00 notional  |
                    +----------------+---------------+
                                     |
                                     v
                    +--------------------------------+
                    |  ORDER EXECUTION (CLOB API)     |
                    |  BUY timeout: 5 min             |
                    |  SELL timeout: 10 min            |
                    +--------------------------------+
```

### Gate Details

| # | Gate | What it checks | On failure |
|:---:|:---|:---|:---|
| 3 | Learner pause | Strategy win rate < 30% over last 5 trades | Signal skipped |
| 4 | Market cooldown | Same market traded within cooldown period (default 3h) | Signal skipped |
| 5 | Duplicate position | Already holding a position in this market | Signal skipped |
| 6 | Correlation | Jaccard similarity > 0.5 with any open position | Signal skipped |
| 7 | VaR pre-check | 95% daily VaR exceeds bankroll-scaled limit | Signal skipped (saves LLM cost) |
| 8 | Debate cooldown | Market was debated and rejected within 1h | Signal skipped (saves LLM cost) |
| 9 | LLM Debate | Proposer votes PASS, or Challenger votes REJECT | Signal rejected |
| 10 | Z-Score | \|Z\| = \|(p_model - p_market) / sigma\| < 1.5 | Signal rejected (edge too weak) |
| 11 | Daily loss | Daily PnL exceeds loss limit (6% Tier 1) | All trading halted |
| 12 | Drawdown | Current drawdown exceeds max (12% Tier 1) | All trading halted |
| 13 | Position limits | Max positions or max deployed capital reached | Triggers rebalance |
| 14 | Category exposure | Single category > 40% of deployed capital | Signal skipped |

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
 | 60% max deployed  |     | 50% max deployed  |     | 45% max deployed  |
 | Min edge: 1%      |     | Min edge: 2%      |     | Min edge: 2%      |
 | Min prob: 55%     |     | Min prob: 70%     |     | Min prob: 60%     |
 | Kelly: 35%        |     | Kelly: 25%        |     | Kelly: 20%        |
 | Drawdown: 12%     |     | Drawdown: 10%     |     | Drawdown: 8%      |
 | Daily loss: 6%    |     | Daily loss: 5%    |     | Daily loss: 4%    |
 | VaR limit: -35%   |     | VaR limit: -20%   |     | VaR limit: -5%    |
 |                   |     |                   |     |                   |
 | Strategies:       |     | + Swing Trading   |     | + Market Making   |
 | Arbitrage         |     |                   |     |                   |
 | Time Decay        |     |                   |     |                   |
 | Value Betting     |     |                   |     |                   |
 +-------------------+     +-------------------+     +-------------------+
```

**Design rationale:** Small bankrolls (Tier 1) get more aggressive Kelly (0.35) and relaxed VaR (-35%) because the signals that pass all 14 gates (VaR, Z-Score, VPIN, LLM Debate) are high-confidence. Tighter deployed capital (60%) and drawdown (12%) protect against downside. As capital grows, limits tighten to preserve gains.

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
|  - Login            |<--->|  |   FastAPI App     |   |
|  - Dashboard        | API |  |   /api/* + /ws/*  |   |
|  - Trades           |     |  +--------+---------+   |
|  - Strategies       |     |           |              |
|  - Markets          |     |  +--------v---------+   |
|  - Risk             |     |  |  Trading Engine   |   |
|  - Research         |     |  |  (asyncio task)   |   |
|  - Learner          |     |  |                   |   |
|  - AI Debates       |     |  |  - 6 Strategies   |   |
|  - Market Report    |     |  |  - Risk Manager   |   |
|  - Activity         |     |  |  - Portfolio      |   |
|  - Settings         |     |  |  - Learner        |   |
+---------------------+     |  |  - Order Manager  |   |
                             |  |  - Rebalancer     |   |
                             |  |  - Research Engine|   |
                             |  |  - LLM Debate Gate|   |
                             |  |  - Returns Tracker|   |
                             |  +--------+---------+   |
                             |           |              |
                             |  +--------v---------+   |
                             |  |  SQLite (WAL)     |   |
                             |  +------------------+   |
                             +------------------------+

                             +------------------------+
                             |   External APIs         |
                             |  - Polymarket CLOB      |
                             |  - Gamma API (markets)  |
                             |  - Data API (balance)   |
                             |  - WebSocket (prices)   |
                             |  - Anthropic (Claude)   |
                             |  - Tavily (Twitter/X)   |
                             |  - Google News RSS      |
                             |  - Reddit JSON API      |
                             |  - CoinGecko API        |
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

> Detect mispriced markets using order book analysis + VPIN

Analyzes order book imbalance and volume momentum to identify markets where the true probability differs from the market price. Includes VPIN (Volume-Synchronized Probability of Informed Trading) to skip toxic order flow.

| Parameter | Value |
|:---|:---|
| Min edge | 2%+ |
| VPIN threshold | 0.7 (skip toxic flow) |
| Exit: take-profit | +3% after 6h hold |
| Exit: stop-loss | -10% from entry |
| Exit: floor | Below $0.40 |

### 4. Price Divergence (Tier 1+)

> Detect crypto/sentiment price divergence using external signals

Tracks price movements and detects divergence between market price and expected value. Separate hold times for crypto (24h) and non-crypto (4h) markets.

### 5. Swing Trading (Tier 2+)

> Buy markets with confirmed upward momentum

Detects 3+ consecutive rising price ticks, enters position, and exits quickly via take-profit (1.5%), stop-loss (1.5%), or time limit (4h).

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

Five LLM features using **Claude Haiku 4.5** — each toggleable via dashboard with a shared daily budget cap.

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
- **Debate cooldown:** When a market is rejected by debate, it enters a 1h cooldown — no re-debating the same market (saves ~$15/day)
- **VaR pre-check:** VaR is checked before debate — if VaR would block, debate is skipped entirely (saves API cost)
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

### 4. Keyword Extraction

LLM-powered keyword extraction from market questions for research queries. Regex fast-path for common patterns, LLM fallback for ambiguous questions.

### 5. Post-Mortem Analysis

After trades resolve, LLM analyzes what went right or wrong:
- Evaluates `strategy_fit` (GOOD_FIT, POOR_FIT, NEUTRAL)
- Results feed back into the learner's edge multiplier computation
- Visible on the AI Debates dashboard page

### Cost Control

| Feature | Est. Cost/Day | Toggle |
|:---|:---:|:---|
| Debate Gate | ~$0.10-0.50 | `use_llm_debate` |
| Post-Mortem | ~$0.05 | `use_post_mortem` |
| Position Reviewer | ~$0.05 | `use_llm_reviewer` |
| Keyword Extraction | ~$0.02 | `use_llm_keywords` |
| Sentiment | ~$0.30 | `use_llm_sentiment` |
| **Total** | **~$0.50-0.90** | Daily budget cap (default $3.00) |

All LLM calls share a global `LlmCostTracker`. When the daily budget is exhausted, all LLM features gracefully fall back to non-LLM behavior.

**Cost optimizations:**
1. VaR pre-check before debate — skip LLM if VaR would block anyway
2. Debate cooldown per market (1h) — no re-debating rejected markets
3. Proposer PASS skips Challenger — save 50% on weak signals

---

## Quantitative Risk Gates

Three mathematical gates from quantitative finance, computed in real-time from trade history and order book data.

### Value-at-Risk (VaR 95%)

Parametric VaR estimates the worst-case daily loss at 95% confidence:

```
VaR = mu - z * sigma

where:
  mu    = mean of rolling 30-day returns
  sigma = standard deviation of returns
  z     = 1.645 (95% confidence)
```

VaR limits **scale with bankroll size** to prevent small accounts from being permanently frozen by early losses:

| Bankroll | VaR Limit | Rationale |
|:---:|:---:|:---|
| < $25 | -35% | Small account, allow recovery |
| $25 - $50 | -20% | Growing, moderate protection |
| $50 - $100 | -10% | Approaching Tier 2, tighten |
| $100+ | -5% | Full protection |

VaR is checked **twice** in the pipeline: once as a pre-check before LLM debate (to save API costs), and once in the full risk evaluation.

### Mispricing Z-Score

Normalizes the edge (model probability - market price) by the order book's price standard deviation:

```
Z = (p_model - p_market) / sigma_price

where:
  p_model  = bot's estimated probability
  p_market = current market price
  sigma    = standard deviation of bid/ask prices in order book
```

**Threshold: |Z| >= 1.5** — signals with Z-score below 1.5 are rejected as "not statistically significant enough." This prevents trading on noise.

### VPIN (Volume-Synchronized Probability of Informed Trading)

Detects toxic order flow in the order book before entering a market:

```
VPIN = |V_buy - V_sell| / (V_buy + V_sell)

where:
  V_buy  = total bid volume (size * price)
  V_sell = total ask volume (size * price)
```

**Threshold: VPIN > 0.7** — markets with VPIN above 0.7 are skipped as potentially manipulated or informed-trader-dominated. Computed in the Value Betting strategy from live order book data.

### Returns Tracker

Rolling 30-day returns tracker that computes VaR, Sharpe ratio, and Profit Factor from equity snapshots:

| Metric | Formula | Healthy Value |
|:---|:---|:---|
| Daily VaR 95% | mu - 1.645 * sigma | > -5% |
| Rolling Sharpe | mean(returns) / std(returns) * sqrt(365) | > 1.0 |
| Profit Factor | gross_profit / gross_loss | > 1.5 |

All three metrics are exposed via `GET /api/risk/metrics` and visible on the Risk dashboard.

---

## Risk Management

Multi-layered risk system with **14 cascading checks** — every trade must pass all gates:

### Risk Limits by Tier

| Rule | Tier 1 ($5-$25) | Tier 2 ($25-$100) | Tier 3 ($100+) |
|:---|:---:|:---:|:---:|
| Max Positions | 6 | 6 | 15 |
| Max Per Position | 40% | 20% | 15% |
| Max Deployed | 60% | 50% | 45% |
| Daily Loss Limit | 6% | 5% | 4% |
| Max Drawdown | 12% | 10% | 8% |
| Min Edge Required | 1% | 2% | 2% |
| Min Win Probability | 55% | 70% | 60% |
| Max Per Category | 40% | 30% | 30% |
| Kelly Fraction | 35% | 25% | 20% |
| VaR Limit (daily) | -35% | -20% | -5% |
| Z-Score Min | 1.5 | 1.5 | 1.5 |

### Position Sizing

Uses **Fractional Kelly Criterion** for conservative sizing:

```
f* = kelly_fraction x (p - c) / (1 - c)

where:
  p = estimated real probability
  c = market price (cost)
  kelly_fraction = 0.20-0.35 per tier

Minimum: 5 shares (Polymarket CLOB requirement)
Positions < 5 shares CANNOT be sold -- must wait for resolution
Minimum notional: $1.00 (shares * price)
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
| **Dynamic Category Min Edge** | Auto-computed min_edge per category based on win rate (>60%: 0.015, 40-60%: 0.025, <40%: 0.04) |
| **Confidence Calibration** | Compares predicted probability vs actual win rate. If 95% confidence only wins 60%, edge requirements increase |
| **Urgency Multiplier** | Daily target progress: behind target = more aggressive (expand horizons, lower edge); ahead = conservative |
| **Strategy Auto-Pause** | If last 5 trades have <30% win rate and PnL < -$0.05, the strategy is paused for 12 hours. Manual unpause via API with 6h grace period |
| **Profit Factor Feedback** | If profit factor < 1.0 over last 30 trades, edge multipliers increase by 30% (require stronger signals) |

### Edge Multiplier Logic

| Win Rate | Multiplier | Effect |
|:---:|:---:|:---|
| > 60% | 0.8x | Relaxed -- allow lower edge |
| 40-60% | 1.0x | Neutral -- default thresholds |
| < 40% | 1.5x | Strict -- require 50% more edge |
| No data | 1.2x | Cautious -- slightly stricter |

### Urgency System

The daily target is 1% (configurable). Progress is tracked in real-time:

| Progress | Urgency | Effect |
|:---:|:---:|:---|
| > 100% | 0.7x | Conservative -- raise edge requirements, narrow horizons |
| 50-100% | 1.0x | Normal -- default parameters |
| 0-50% | 1.3x | Aggressive -- lower edge requirements, expand time horizons |
| Negative | 1.5x+ | Very aggressive -- maximum opportunity seeking |

### Post-Mortem Feedback Loop

LLM post-mortem results (`strategy_fit`) feed back into the learner's edge multiplier computation:

| Condition | Adjustment | Effect |
|:---|:---:|:---|
| >50% POOR_FIT (3+ post-mortems) | x1.15 | Tighten -- require higher edge |
| >50% GOOD_FIT (3+ post-mortems) | x0.90 | Relax -- allow lower edge |
| <3 post-mortems or mixed | x1.0 | No change |

Visible via `GET /api/learner/multipliers` -> `post_mortem_influence` field.

---

## Research Engine

Background engine that scans markets every 15 minutes, aggregating news, sentiment, and market intelligence from multiple sources.

### Data Sources

| Source | Data | Refresh |
|:---|:---|:---:|
| **Google News** | Headlines + VADER/LLM sentiment | 15 min |
| **Twitter/X** | Posts via Tavily search (scoped to twitter.com/x.com) | 15 min |
| **Reddit** | Posts from category-specific subreddits (crypto, politics, economics, general) | 15 min |
| **CoinGecko** | BTC/ETH prices + market sentiment | 15 min |
| **Polymarket CLOB** | Order book whale detection (>$500 orders) | Real-time (WebSocket) |
| **Market Descriptions** | Resolution criteria (regex + LLM parsing) | On first scan |

### Research Cross-Validation

Research results are cross-validated across sources. The research multiplier combines:
- News sentiment strength and consistency
- Twitter/X signal alignment
- Volume anomaly detection (3x spike = flag)
- Whale activity (large CLOB orders)
- Historical base rate from similar resolved trades

### Volume Anomaly Detection

Tracks rolling 24h volume and price history per market (96 samples at 15-min intervals):

- **Volume spike:** Current `volume_24h > 3x mean(last 10 samples)` -- flags sudden interest
- **Price move:** `abs(current - mean(last 5)) / mean(last 5) > 10%` -- flags sharp price changes
- Anomaly markets are prioritized in research scans and flagged in trading signals

### Cross-Market Correlation

Prevents overexposure to the same underlying event across different markets:

- **Jaccard similarity** on tokenized questions (stop words removed, 3+ char words)
- **Union-Find** for transitive grouping: if A~B and B~C, then A, B, C are all correlated
- **Threshold:** Jaccard > 0.5 triggers grouping
- **Engine integration:** Correlated signals are deduplicated; correlated open positions block new entries

### LLM Category Classification

Classifies markets into categories (crypto, politics, economics, sports, etc.):

- **Regex fast-path:** Pattern matching on question keywords (zero cost)
- **LLM fallback:** Claude Haiku classifies ambiguous markets (~$0.00003/call)
- **Indefinite cache:** Each market classified once, cached forever
- **Sports/esports blocking:** Markets classified as sports are blocked before strategy evaluation

### Historical Pattern Matching

Estimates base rates from similar resolved trades in the database:

- Extracts pattern type from question (price_target, win_outcome, binary_event, etc.)
- Finds similar past trades via Jaccard similarity on question tokens
- Returns win rate as `historical_base_rate` in research results

### Probability Calibration (Brier Scores)

Tracks prediction accuracy per strategy using Brier scores:

- **5-bin calibration model:** Compares estimated probability vs actual win rate across probability buckets
- **Per-strategy Brier scores:** 0 = perfect predictions, 0.25 = random. Visible on Learner dashboard
- **Calibration integration:** `calibrator.calibrate(estimated_prob)` adjusts probabilities before trading

### Daily Market Report

Automated Telegram report generated at 23:00 UTC daily:

- Portfolio summary, daily PnL, open positions, capital deployed
- Top 5 markets with keywords and multipliers
- Strategy health: win rates, total PnL, active/paused status
- Risk alerts: volume anomalies, whale activity, high drawdown warnings

---

## Price Momentum Tracking

Shared `PriceTracker` (`bot/data/price_tracker.py`) provides cross-strategy momentum detection:

```
Engine Cycle
     |
     v
MarketAnalyzer.scan_markets()
     |
     +-- record_batch(500 markets x best_bid_price)
     +-- evict_stale(active market IDs)
     |
     v
Strategies read momentum:
  - ValueBetting: confidence +/-5% based on 1h momentum alignment
  - SwingTrading: shared tracker + internal history (backward compat)
```

| Property | Value |
|:---|:---|
| Max tracked markets | 500 |
| History depth | 360 ticks/market (~6h at 1-min cycles) |
| Memory footprint | ~2.8 MB max |
| Stale eviction | Markets not seen in 15+ minutes |
| `momentum(market_id, 60)` | % change over 60-min window |
| `trend(market_id)` | "rising" / "falling" / "flat" (+/-0.5% threshold) |

---

## Auto-Claim Resolved Positions

Optional web3.py integration to automatically redeem winning tokens on Polygon after market resolution.

```
Market resolves -> _close_if_resolved() -> _try_redeem()
     |                                        |
     v                                        v
Close position in DB              PositionRedeemer.redeem(condition_id)
Record PnL                              |
                                         v
                                    CTF.redeemPositions(USDC, 0x0, conditionId, [1, 2])
                                         |
                                    Fire-and-forget (never crashes cycle)
```

| Config | Default | Description |
|:---|:---:|:---|
| `use_auto_claim` | `false` | Toggle via Settings dashboard |
| `polygon_rpc_url` | `https://polygon-rpc.com` | Polygon RPC endpoint |

**Requirements:** `POLY_PRIVATE_KEY` env var, web3.py dependency, live trading mode. Disabled in paper mode.

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
2. New signal edge >= **min_rebalance_edge** (default 6%, tunable via admin)
3. Worst position has **unrealized PnL <= 0** (never close winners)
4. Worst position has **>= 5 shares** (can actually sell on Polymarket CLOB)
5. Worst position held for at least **min_hold_seconds** (default 7200s/2h, tunable via admin)
6. Max **1 rebalance per cycle** (prevent churning)
7. If sell fails (e.g. insufficient balance), tries next candidate instead of giving up

---

## Dashboard

JWT-authenticated React dashboard with **12 pages** for full visibility into the bot's operations:

| Page | Description |
|:---|:---|
| **Login** | Secure JWT login (username/password), 24h token expiry, auto-logout on 401 |
| **Dashboard** | Equity curve (equity + cash), daily PnL bar chart, daily target hit/miss tracker, PnL cards, daily progress vs target, active positions |
| **Trades** | Expandable trade history -- click any trade to see reasoning, edge, confidence, estimated probability, price, cost, paper/live |
| **Strategies** | Per-strategy performance: win rate, PnL, Sharpe ratio (real-time from trade data) |
| **Markets** | Live market scanner with opportunities and signals |
| **Risk** | Drawdown chart, category exposure (pie), risk limits by tier, VaR/Sharpe/Profit Factor metrics |
| **Research** | News sentiment analysis: per-market headlines, sentiment scores, research multipliers, volume anomaly/whale activity badges, market categories, resolution criteria, historical base rates |
| **Learner** | Adaptive learning dashboard: edge multipliers per strategy+category, category confidence cards, Brier scores per strategy, probability calibration chart, strategy pause status and cooldown timers |
| **AI Debates** | Trade debate history with decision filters per tab -- Reviews (HOLD/EXIT/REDUCE/INCREASE), Risk Debates (override/upheld), Post-Mortems (GOOD/BAD/NEUTRAL). Tabbed view with approval rates and cost tracking |
| **Market Report** | Daily report page: portfolio summary, market sentiment, top opportunities, strategy health, risk alerts |
| **Activity** | Bot decision log -- every signal found, rejected, approved, with reasoning and metadata. Filterable by event type |
| **Settings** | Push notification toggle, AI feature toggles (sentiment/debate/reviewer + daily budget), strategy toggles, risk parameters, strategy parameters, quality filters, system info. All persisted across restarts |

**PWA Push Notifications:** Install as a PWA (Add to Home Screen) to receive push notifications on mobile/desktop even when the browser is closed. Notifies on trade fills, errors, strategy pauses, risk limits, and daily summaries. Uses Web Push (VAPID).

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
| `TRADING_MODE` | `paper` | `paper` or `live` -- **paper is default** |
| `INITIAL_BANKROLL` | `5.0` | Starting capital in USD |
| `SCAN_INTERVAL_SECONDS` | `30` | Market scan frequency (5-3600) |
| `SNAPSHOT_INTERVAL_SECONDS` | `300` | Portfolio snapshot frequency |
| `DAILY_TARGET_PCT` | `0.01` | Daily profit target (1%) |
| `MAX_DAILY_LOSS_PCT` | `0.06` | Daily loss limit (6% Tier 1) |
| `MAX_DRAWDOWN_PCT` | `0.12` | Max drawdown before halt (12% Tier 1) |
| `POLY_API_KEY` | -- | Polymarket API key (required for live mode) |
| `POLY_API_SECRET` | -- | Polymarket API secret |
| `POLY_API_PASSPHRASE` | -- | Polymarket API passphrase |
| `POLY_PRIVATE_KEY` | -- | Wallet private key (required for live mode) |
| `POLYGON_RPC_URL` | `https://polygon-rpc.com` | Polygon RPC for auto-claim |
| `FORCE_HTTPS_COOKIES` | `false` | Set `true` behind HTTPS reverse proxy |
| `DATABASE_URL` | `sqlite+aiosqlite:///data/polybot.db` | Database connection URL |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING) |
| `LOG_FORMAT` | `json` | Log format (json or console) |
| `ANTHROPIC_API_KEY` | -- | Anthropic API key for Claude Haiku (required for AI features) |
| `TAVILY_API_KEY` | -- | Tavily API key for Twitter/X search (optional) |
| `USE_TWITTER_FETCHER` | `false` | Enable Twitter/X research via Tavily |
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
- Debate cooldown hours (default 1h)
- Auto-claim resolved positions (web3.py Polygon redemption)
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
1. **Test** -- pytest (1350+ tests, ~2min) + ruff lint + frontend build
2. **Build & Push** -- Docker image to GitHub Container Registry (GHCR)
3. **Deploy** -- SSH to server, pull image, restart containers

> **Note:** After deploy, runtime settings are restored from the database automatically. However, `docker compose -f docker-compose.prod.yml up -d --force-recreate app` is needed to reload `.env` changes.

### Monthly Cost

| Item | Cost |
|:---|:---:|
| Lightsail 1GB | $5.00 |
| Anthropic API (LLM) | ~$15-25 |
| S3 Backups | ~$0.05 |
| Monitoring | Free |
| CI/CD | Free |
| **Total** | **~$20-30** |

---

## Security

The bot handles real money -- security is enforced at every layer.

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

**1350+ tests** across **45+ test files** covering bot logic, API endpoints, strategies, research engine, and adaptive learning.

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
| `test_risk_manager.py` | 73 tests -- all cascading risk checks, tier configs |
| `test_risk_checks_new.py` | VaR gate, Z-Score gate, tighter tier defaults |
| `test_risk_metrics.py` | VaR, VPIN, Z-Score, profit factor formulas |
| `test_returns_tracker.py` | Rolling returns tracking from equity snapshots |
| `test_learner.py` | 84 tests -- edge multipliers, calibration, pauses, urgency |
| `test_time_decay_strategy.py` | Time decay probability, confidence, dynamic MAX_PRICE |
| `test_swing_trading.py` | Momentum detection, exit rules, position scoring |
| `test_engine.py` | Engine init, shutdown, liquidity checks, fill callbacks |
| `test_rebalance.py` | 12 tests -- all rebalance conditions and edge cases |
| `test_order_manager.py` | Order execution, monitoring, cancellation, min shares |
| `test_portfolio.py` | Position tracking, PnL, sync, settlement prices |
| `test_api_auth.py` | JWT creation, decoding, expiry, password verification |
| `test_settings_store.py` | Persist/restore settings across restarts |
| `test_market_analyzer.py` | Market scanning, quality filtering, deduplication |
| `test_llm_debate.py` | Debate parsers, cost tracker, full debate flow, position review |
| `test_llm_sentiment.py` | Haiku sentiment, clamping, errors, routing |
| `test_config.py` | Tier config, settings validation, capital tiers |
| `test_math_utils.py` | Kelly criterion, Sharpe ratio, drawdown |
| `test_price_tracker.py` | Momentum, trend, eviction, batch, cap |
| `test_post_mortem_feedback.py` | PM stats, learner integration, API response |
| `test_auto_claim.py` | Redeemer init/success/failure, portfolio integration |
| `test_research_improvements.py` | Volume detector, correlation detector, Reddit, resolution parser |
| `test_category_classifier.py` | Regex fast-path, LLM fallback, caching |
| `test_pattern_analyzer.py` | Pattern extraction, base rate, Jaccard matching |
| `test_probability_calibrator.py` | 5-bin calibration, Brier scores, strategy-level metrics |
| `test_whale_detector.py` | Whale detection thresholds, token tracking |
| `test_market_report.py` | Report generation, HTML formatting |
| + more | API routers, types, cache, strategies, price rounding, E2E |

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
|   |   |-- risk_manager.py           # 14 cascading risk checks (VaR, Z-Score, etc.)
|   |   |-- learner.py                # Adaptive learning engine
|   |   |-- position_closer.py        # Exit logic + rebalancing
|   |   +-- strategies/
|   |       |-- base.py               # Abstract strategy interface
|   |       |-- time_decay.py         # Near-resolution strategy (primary)
|   |       |-- arbitrage.py          # YES+NO arbitrage
|   |       |-- value_betting.py      # Order book analysis + VPIN filter
|   |       |-- price_divergence.py   # Crypto/sentiment divergence
|   |       |-- swing_trading.py      # Momentum-based short-term
|   |       +-- market_making.py      # Spread capture
|   |-- polymarket/
|   |   |-- client.py                 # CLOB API wrapper (async)
|   |   |-- gamma.py                  # Market discovery API (600 mkts/scan)
|   |   |-- data_api.py              # Positions & balance API
|   |   |-- redeemer.py               # Auto-claim via ConditionalTokens (web3.py)
|   |   |-- websocket_manager.py      # Real-time price feed
|   |   |-- heartbeat.py              # API session keepalive
|   |   +-- types.py                  # Pydantic models
|   |-- data/
|   |   |-- database.py               # SQLite async + WAL + migrations
|   |   |-- models.py                 # ORM models (Trade, Position, Snapshot, etc.)
|   |   |-- repositories.py           # CRUD operations
|   |   |-- activity.py               # Bot activity logger + post-mortem stats
|   |   |-- price_tracker.py          # Shared in-memory price momentum tracker
|   |   |-- returns_tracker.py        # Rolling 30-day returns for VaR/Sharpe/PF
|   |   |-- settings_store.py         # Persistent settings (survives restarts)
|   |   +-- market_cache.py           # In-memory TTL cache
|   |-- research/
|   |   |-- engine.py                 # Background market research engine (15-min scan)
|   |   |-- llm_sentiment.py          # Claude Haiku sentiment analysis
|   |   |-- llm_debate.py             # Proposer vs Challenger debate gate + Position reviewer
|   |   |-- sentiment.py              # VADER lexicon-based sentiment (fallback)
|   |   |-- cache.py                  # Research result cache (1h TTL)
|   |   |-- twitter_fetcher.py        # Twitter/X search via Tavily API
|   |   |-- reddit_fetcher.py         # Reddit JSON API (category-mapped subreddits)
|   |   |-- volume_detector.py        # Volume spike + price move anomaly detection
|   |   |-- correlation_detector.py   # Jaccard + Union-Find cross-market correlation
|   |   |-- category_classifier.py    # Regex + LLM market category classification
|   |   |-- pattern_analyzer.py       # Historical pattern matching + base rates
|   |   |-- probability_calibrator.py # 5-bin calibration model + Brier scores
|   |   |-- whale_detector.py         # CLOB order book whale detection (>$500)
|   |   |-- resolution_parser.py      # Resolution criteria extraction (regex + LLM)
|   |   |-- market_report.py          # Automated daily Telegram report
|   |   |-- keyword_extractor.py      # Question keyword extraction (regex + LLM)
|   |   |-- crypto_fetcher.py         # CoinGecko price + sentiment data
|   |   |-- news_fetcher.py           # Google News RSS parser
|   |   +-- types.py                  # ResearchResult, NewsItem dataclasses
|   +-- utils/
|       |-- logging_config.py         # structlog JSON logging
|       |-- math_utils.py             # Kelly, Sharpe, drawdown
|       |-- risk_metrics.py           # VaR, VPIN, Z-Score, profit factor
|       |-- retry.py                  # Exponential backoff
|       |-- notifications.py          # Telegram alerts
|       +-- push_notifications.py    # Web Push (VAPID) notifications
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
|       |-- push.py                   # Push notification subscriptions
|       |-- risk.py                   # GET /api/risk/* (VaR, Sharpe, PF)
|       |-- config.py                 # GET/PUT /api/config/ + pause/resume/reset
|       |-- activity.py               # GET /api/activity/ + event types
|       |-- learner.py                # GET/POST /api/learner/* (multipliers, calibration, pauses, unpause)
|       +-- websocket.py              # WS /ws/live
|-- frontend/                         # React 18 + TypeScript + Vite
|   +-- src/
|       |-- pages/                    # 12 pages (Login + 11 dashboard pages)
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
|-- tests/                            # 1350+ pytest tests (45+ files)
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
- **Python 3.11** -- asyncio runtime
- **FastAPI** -- REST API + WebSocket
- **SQLAlchemy 2.0** -- async ORM
- **SQLite** -- WAL mode database
- **py-clob-client** -- Polymarket CLOB
- **httpx** -- async HTTP client
- **structlog** -- JSON logging
- **Pydantic v2** -- validation & settings
- **PyJWT** -- JWT authentication
- **anthropic** -- Claude Haiku AI (debate, sentiment, post-mortem)
- **web3.py** -- Polygon auto-claim (optional)
- **tenacity** -- retry logic

</td>
<td valign="top" width="50%">

### Frontend
- **React 18** -- UI framework
- **TypeScript** -- type safety
- **Vite** -- build tool
- **TanStack Query** -- data fetching
- **Recharts** -- charts & graphs
- **Tailwind CSS** -- styling
- **Lucide React** -- icons

</td>
</tr>
<tr>
<td valign="top">

### Infrastructure
- **Docker Compose** -- orchestration
- **Nginx** -- reverse proxy + static + SSL
- **AWS Lightsail** -- $5/mo hosting (Mumbai)
- **GitHub Actions** -- CI/CD (3-job pipeline)
- **Let's Encrypt** -- HTTPS certificates
- **DuckDNS** -- Dynamic DNS

</td>
<td valign="top">

### Tooling
- **uv** -- Python package manager
- **ruff** -- Python linter
- **pytest** -- 1350+ tests
- **pytest-asyncio** -- async test support
- **Telegram Bot** -- trade alerts + daily reports
- **Tavily** -- Twitter/X search API
- **Health endpoint** -- `/api/health`

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
| `POST` | `/api/portfolio/positions/force-remove` | DB-only close (ghost positions) |
| `GET` | `/api/trades/history` | Trade history (filterable) |
| `GET` | `/api/trades/stats` | Trade statistics |
| `GET` | `/api/strategies/performance` | Strategy metrics |
| `GET` | `/api/markets/scanner` | Live market scanner |
| `GET` | `/api/markets/opportunities` | Cached market data |
| `GET` | `/api/risk/metrics` | Risk state (VaR, Sharpe, Profit Factor) |
| `GET` | `/api/risk/limits` | Risk limits for current tier |
| `GET` | `/api/activity/` | Activity log (filterable by type) |
| `GET` | `/api/activity/event-types` | Available event types |
| `GET` | `/api/learner/multipliers` | Edge multipliers + category confidences |
| `GET` | `/api/learner/calibration` | Probability calibration per bucket |
| `GET` | `/api/learner/pauses` | Strategy pause status + cooldowns |
| `POST` | `/api/learner/unpause` | Force-unpause a strategy (6h grace period) |
| `GET` | `/api/research/status` | Research engine status |
| `GET` | `/api/research/markets` | Per-market research (sentiment, anomalies, categories, whales) |
| `GET` | `/api/research/markets/{id}` | Detailed research for a specific market |
| `GET` | `/api/config/` | Bot configuration |
| `PUT` | `/api/config/` | Update configuration (persisted) |
| `POST` | `/api/trading/pause` | Pause trading |
| `POST` | `/api/trading/resume` | Resume trading |
| `POST` | `/api/config/risk/reset` | Reset risk state (peak equity, daily PnL) |
| `GET` | `/api/push/vapid-key` | VAPID public key for push subscription (no auth) |
| `POST` | `/api/push/subscribe` | Register push subscription |
| `POST` | `/api/push/unsubscribe` | Remove push subscription |
| `WS` | `/ws/live?token=KEY` | Real-time updates (token auth) |

---

<div align="center">

**Built with precision.** Paper trade first. Manage risk always.

</div>
