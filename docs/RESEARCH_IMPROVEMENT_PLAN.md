# Research & Market Discovery Improvement Plan

## Current State

### Research Engine (`bot/research/engine.py`)
- Runs every 15 min, scans up to 50 markets
- Uses VADER sentiment analysis (basic word-matching)
- Fetches news via `NewsFetcher` + crypto data via `CryptoFetcher`
- Research multiplier (0.7-1.3) adjusts edge requirements
- Limited keyword extraction (regex-based)

### Market Discovery (`bot/polymarket/gamma.py`)
- 5 sources: active, short-term, new, trending, breaking
- ~639 markets/cycle, but many are sports (blocked) or stale
- No real-time event detection
- No cross-market correlation analysis

### LLM Debate (`bot/research/llm_debate.py`)
- Proposer vs Challenger on trade signals
- Risk debate for rejected signals
- Position reviewer for open positions
- Uses Claude Haiku ($3/day budget)
- Crypto threshold extraction via regex + LLM fallback

---

## Proposed Improvements

### Phase 1: Enhanced Sentiment (Low Cost, High Impact)

#### 1.1 Replace VADER with LLM Sentiment
- **Current:** VADER word-matching gives crude sentiment scores
- **Proposed:** Use Claude Haiku for top 10-20 markets only (cost-aware)
- **Implementation:**
  - Add `analyze_sentiment_llm(question, articles)` in `bot/research/sentiment.py`
  - Structured output: `{sentiment: float, confidence: float, reasoning: str}`
  - Fall back to VADER for remaining markets
  - Cache results with 1h TTL
- **Estimated cost:** $0.50-1.00/day for 20 markets * 4 cycles

#### 1.2 Multi-Source News Aggregation
- **Current:** Single news source via `NewsFetcher`
- **Proposed:** Add 2-3 reliable sources:
  - **Polymarket comments/resolution criteria** (scrape from market page)
  - **Google News API** (free tier: 100 req/day)
  - **Reddit API** (relevant subreddits by category)
- **Implementation:**
  - Create `bot/research/sources/` package with pluggable fetchers
  - Each source returns `list[Article]` with title, snippet, timestamp, url
  - Deduplicate by similarity hash before sentiment analysis
  - Priority: Open positions > Recent signals > High-volume markets

#### 1.3 Resolution Criteria Parsing
- **Current:** Bot doesn't know HOW markets resolve
- **Proposed:** Extract resolution criteria from Polymarket market description
  - Parse with Haiku: "What exact condition resolves this market YES?"
  - Store structured: `{condition: str, data_source: str, binary_check: bool}`
  - Use for more accurate probability estimation
- **Cost:** One-time per market, cached indefinitely

---

### Phase 2: Smarter Market Discovery (Medium Effort)

#### 2.1 Event-Driven Market Detection
- **Current:** Scan all active markets every cycle
- **Proposed:** Detect markets that become interesting due to external events
  - Monitor top 20 active markets for sudden volume spikes (>3x 24h avg)
  - Flag markets where price moved >10% in last hour
  - Cross-reference with breaking news
- **Implementation:**
  - Add `VolumeAnomalyDetector` class tracking 24h rolling averages
  - On spike: immediately run full research + LLM analysis
  - Fast-track to signal evaluation (skip normal cycle wait)

#### 2.2 Category Intelligence
- **Current:** Category detection is keyword-based, often wrong
- **Proposed:** LLM-based category classification (one-time per market)
  - Haiku classifies: `{category: str, subcategory: str, topic_tags: list[str]}`
  - Cache indefinitely (markets don't change category)
  - Better category diversification in risk checks
  - Identify niche categories where bot has edge (crypto, geopolitics)
- **Cost:** ~$0.01 per market * 600 markets = $6 one-time

#### 2.3 Cross-Market Correlation
- **Current:** Each market evaluated independently
- **Proposed:** Detect correlated markets (same event, different angles)
  - Example: "Will BTC hit $100k?" and "Will BTC hit $110k?" are correlated
  - Use question embedding similarity (sentence-transformers, local)
  - Group correlated markets, trade the one with best edge
  - Prevent portfolio overexposure to same underlying event
- **Implementation:**
  - Add `CorrelationDetector` using sentence-transformers (local, no API cost)
  - Store correlation matrix in memory (refresh hourly)
  - Feed into risk_manager as additional exposure check

---

### Phase 3: Predictive Intelligence (Higher Cost, Higher Reward)

#### 3.1 Historical Pattern Matching
- **Current:** No historical awareness
- **Proposed:** Track how similar markets resolved in the past
  - "Previous 'Will X reach $Y by date?' markets resolved YES 40% of the time"
  - Store resolution outcomes in DB
  - Query similar historical markets by category + question pattern
  - Adjust estimated_prob based on base rate
- **Implementation:**
  - Add `market_outcomes` table (market_id, question_pattern, resolved_yes, resolution_date)
  - Populate from Polymarket API resolved markets (batch job)
  - Pattern matching: regex + embedding similarity
  - Base rate multiplier feeds into signal confidence

#### 3.2 Expert Source Tracking
- **Current:** Generic news search
- **Proposed:** Track specific prediction markets experts and analysts
  - Follow known-accurate Twitter/X accounts per category
  - Monitor Polymarket whale traders (large position changes)
  - Track PredictIt/Metaculus for cross-platform signal
- **Implementation:**
  - `bot/research/sources/expert_tracker.py`
  - X API (free tier) for specific accounts
  - Polymarket CLOB for large order detection (already have WS connection)
  - Weight expert opinions in confidence calculation

#### 3.3 Real-Time Price Feed Integration
- **Current:** PriceTracker stores in-memory snapshots every cycle
- **Proposed:** WebSocket-based real-time price feeds
  - Subscribe to price updates for open positions + watchlist
  - Immediate exit signals when SL/TP hit (don't wait for 60s cycle)
  - Detect sudden price movements for momentum trading
- **Implementation:**
  - Extend `WebSocketManager` with price channel subscriptions
  - Add callback system for price threshold alerts
  - Feed into SwingTradingStrategy for faster momentum detection

---

### Phase 4: Advanced Research (When Bankroll > $200)

#### 4.1 Multi-Model Consensus
- **Current:** Single Haiku call per debate
- **Proposed:** Run parallel Haiku calls with different prompts
  - 3 personas: Optimist, Pessimist, Analyst
  - Consensus voting (2/3 agree = trade)
  - Higher accuracy, 3x cost
- **Cost:** ~$3-5/day

#### 4.2 Calibrated Probability Model
- **Current:** Strategies estimate probabilities independently
- **Proposed:** Train a calibration model on historical accuracy
  - Input: strategy, edge, category, market_type, hours_to_resolution
  - Output: calibrated probability
  - Continuously updated from resolved trades
  - Already partially done with calibration buckets in Kelly sizing

#### 4.3 Automated Market Report
- **Current:** No research summary
- **Proposed:** Daily Haiku report summarizing:
  - Top 5 most promising markets (with reasoning)
  - Portfolio recommendations (hold/exit/add)
  - Risk alerts (concentrated exposure, upcoming resolutions)
  - Send via Telegram to user
- **Cost:** ~$0.10/day

---

## Implementation Priority

| Priority | Item | Cost/Day | Expected Impact | Effort |
|----------|------|----------|----------------|--------|
| 1 | 1.1 LLM Sentiment | $0.50-1.00 | High | Low |
| 2 | 1.3 Resolution Criteria | $0.10 | High | Low |
| 3 | 2.1 Volume Anomaly Detection | $0 | Medium | Medium |
| 4 | 1.2 Multi-Source News | $0 | Medium | Medium |
| 5 | 2.2 LLM Category Classification | $0.01 | Medium | Low |
| 6 | 3.1 Historical Pattern Matching | $0 | High | High |
| 7 | 2.3 Cross-Market Correlation | $0 | Medium | Medium |
| 8 | 3.3 Real-Time Price Feeds | $0 | High | High |
| 9 | 3.2 Expert Source Tracking | $0-0.50 | Medium | High |
| 10 | 4.1 Multi-Model Consensus | $3-5 | Medium | Low |
| 11 | 4.3 Automated Market Report | $0.10 | Low | Low |
| 12 | 4.2 Calibrated Probability Model | $0 | High | High |

## Key Principles

1. **Cost-aware:** Always check daily LLM budget before API calls
2. **Graceful degradation:** If LLM fails, fall back to VADER/regex
3. **Cache aggressively:** Market category, resolution criteria = cache forever
4. **Prioritize open positions:** Research open positions first, then signals
5. **Measure impact:** Track research multiplier correlation with trade outcomes
