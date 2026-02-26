"""Tests for trades, risk, markets, and strategies API endpoints."""



from bot.data.models import MarketScan, Trade

# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


class TestTradeHistory:
    async def test_empty_history(self, client):
        resp = await client.get("/api/trades/history")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_with_trades(self, client, db_session):
        trade = Trade(
            market_id="mkt1",
            token_id="tok1",
            question="Q?",
            outcome="Yes",
            side="BUY",
            price=0.90,
            size=5.0,
            cost_usd=4.5,
            strategy="time_decay",
            edge=0.05,
            estimated_prob=0.92,
            confidence=0.85,
            reasoning="Test trade",
            status="completed",
            pnl=0.5,
        )
        db_session.add(trade)
        await db_session.commit()

        resp = await client.get("/api/trades/history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["strategy"] == "time_decay"

    async def test_strategy_filter(self, client, db_session):
        for strat in ("time_decay", "arbitrage", "time_decay"):
            trade = Trade(
                market_id=f"mkt_{strat}_{id(strat)}",
                token_id="tok1",
                side="BUY",
                price=0.90,
                size=5.0,
                strategy=strat,
            )
            db_session.add(trade)
        await db_session.commit()

        resp = await client.get("/api/trades/history?strategy=arbitrage")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["strategy"] == "arbitrage"


class TestTradeStats:
    async def test_empty_db_defaults(self, client):
        resp = await client.get("/api/trades/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_trades"] == 0
        assert data["win_rate"] == 0.0


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


class TestRiskMetrics:
    async def test_correct_shape(self, client):
        resp = await client.get("/api/risk/metrics")
        assert resp.status_code == 200
        data = resp.json()
        expected_keys = {
            "bankroll",
            "peak_equity",
            "current_drawdown_pct",
            "max_drawdown_limit_pct",
            "daily_pnl",
            "daily_loss_limit_pct",
            "max_positions",
            "is_paused",
            "daily_var_95",
            "rolling_sharpe",
            "profit_factor",
        }
        assert set(data.keys()) == expected_keys


class TestRiskLimits:
    async def test_returns_risk_config(self, client):
        resp = await client.get("/api/risk/limits")
        assert resp.status_code == 200
        data = resp.json()
        assert "max_positions" in data
        assert "kelly_fraction" in data


# ---------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------


class TestMarketScanner:
    async def test_empty_scanner(self, client):
        resp = await client.get("/api/markets/scanner")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_with_scan_data(self, client, db_session):
        scan = MarketScan(
            market_id="scan_mkt",
            question="Scan Q?",
            category="crypto",
            yes_price=0.90,
            no_price=0.10,
            volume=10000.0,
            liquidity=2000.0,
            signal_strategy="time_decay",
            signal_edge=0.05,
            signal_confidence=0.85,
        )
        db_session.add(scan)
        await db_session.commit()

        resp = await client.get("/api/markets/scanner")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["market_id"] == "scan_mkt"


class TestMarketOpportunities:
    async def test_empty_cache(self, client, mock_engine):
        mock_engine.cache.get_all_markets.return_value = []
        resp = await client.get("/api/markets/opportunities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_cached"] == 0
        assert data["markets"] == []


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


class TestStrategyPerformance:
    async def test_empty_performance(self, client):
        resp = await client.get("/api/strategies/performance")
        assert resp.status_code == 200
        assert resp.json() == []
