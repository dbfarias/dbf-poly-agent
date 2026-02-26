"""Tests for direction-aware price rounding in PolymarketClient.place_order()."""

import math

from bot.polymarket.client import TICK_SIZE
from bot.polymarket.types import OrderSide


def _round_price(price: float, side: OrderSide) -> float:
    """Replicate the rounding logic from PolymarketClient.place_order()."""
    if side == OrderSide.BUY:
        return round(math.ceil(price / TICK_SIZE) * TICK_SIZE, 2)
    else:
        return round(math.floor(price / TICK_SIZE) * TICK_SIZE, 2)


class TestPriceRounding:
    def test_buy_rounds_up_fractional_cent(self):
        """BUY at $0.943 should round UP to $0.95 to beat the ask."""
        assert _round_price(0.943, OrderSide.BUY) == 0.95

    def test_buy_exact_cent_stays(self):
        """BUY at exactly $0.94 stays at $0.94."""
        assert _round_price(0.94, OrderSide.BUY) == 0.94

    def test_buy_rounds_up_tiny_fraction(self):
        """BUY at $0.9401 rounds UP to $0.95."""
        assert _round_price(0.9401, OrderSide.BUY) == 0.95

    def test_sell_rounds_down_fractional_cent(self):
        """SELL at $0.937 should round DOWN to $0.93 to beat the bid."""
        assert _round_price(0.937, OrderSide.SELL) == 0.93

    def test_sell_exact_cent_stays(self):
        """SELL at exactly $0.93 stays at $0.93."""
        assert _round_price(0.93, OrderSide.SELL) == 0.93

    def test_sell_rounds_down_tiny_fraction(self):
        """SELL at $0.9399 rounds DOWN to $0.93."""
        assert _round_price(0.9399, OrderSide.SELL) == 0.93

    def test_buy_low_price(self):
        """BUY at $0.055 rounds UP to $0.06."""
        assert _round_price(0.055, OrderSide.BUY) == 0.06

    def test_sell_low_price(self):
        """SELL at $0.055 rounds DOWN to $0.05."""
        assert _round_price(0.055, OrderSide.SELL) == 0.05

    def test_buy_near_one(self):
        """BUY at $0.991 rounds UP to $1.00."""
        assert _round_price(0.991, OrderSide.BUY) == 1.0

    def test_real_case_rojas_943(self):
        """Real case: ask was $0.943, BUY should round to $0.95 for fill."""
        assert _round_price(0.943, OrderSide.BUY) == 0.95
