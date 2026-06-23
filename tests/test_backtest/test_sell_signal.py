"""卖出信号单元测试。"""

import pandas as pd
import pytest

from sequoia_x.backtest.sell_signal import (
    AllSell,
    AnySell,
    HoldNDays,
    StopLoss,
    TakeProfit,
    TrailingStop,
)


def bar(close: float) -> pd.Series:
    return pd.Series({"open": close * 0.99, "high": close * 1.02, "low": close * 0.97, "close": close})


ARGS = ("000001", 10.0, "2024-01-02")  # symbol, entry_price, entry_date


class TestHoldNDays:
    def test_not_triggered_before_n(self):
        assert not HoldNDays(5).should_sell(*ARGS, bar(10.0), 4)

    def test_triggered_at_n(self):
        assert HoldNDays(5).should_sell(*ARGS, bar(10.0), 5)

    def test_triggered_past_n(self):
        assert HoldNDays(5).should_sell(*ARGS, bar(10.0), 10)

    def test_reason(self):
        assert HoldNDays(1).reason == "hold_n_days"

    def test_triggered_reason_returns_string(self):
        assert HoldNDays(1).triggered_reason(*ARGS, bar(10.0), 1) == "hold_n_days"

    def test_triggered_reason_returns_none_when_not_triggered(self):
        assert HoldNDays(5).triggered_reason(*ARGS, bar(10.0), 3) is None


class TestStopLoss:
    def test_not_triggered_above_threshold(self):
        assert not StopLoss(0.05).should_sell(*ARGS, bar(9.51), 1)

    def test_triggered_exactly_at_threshold(self):
        assert StopLoss(0.05).should_sell(*ARGS, bar(9.5), 1)

    def test_triggered_below_threshold(self):
        assert StopLoss(0.05).should_sell(*ARGS, bar(8.0), 1)

    def test_reason(self):
        assert StopLoss(0.05).reason == "stop_loss"


class TestTakeProfit:
    def test_not_triggered_below_threshold(self):
        assert not TakeProfit(0.15).should_sell(*ARGS, bar(11.49), 1)

    def test_triggered_exactly_at_threshold(self):
        assert TakeProfit(0.15).should_sell(*ARGS, bar(11.5), 1)

    def test_triggered_above_threshold(self):
        assert TakeProfit(0.15).should_sell(*ARGS, bar(15.0), 1)

    def test_reason(self):
        assert TakeProfit(0.1).reason == "take_profit"


class TestTrailingStop:
    def test_not_triggered_small_drawdown(self):
        # peak=12, close=11 → drawdown=8.3% < 10%
        assert not TrailingStop(0.10).should_sell(*ARGS, bar(11.0), 3, peak_close=12.0)

    def test_triggered_exceeds_pct(self):
        # peak=12, close=10.7 → drawdown=10.8% > 10%
        assert TrailingStop(0.10).should_sell(*ARGS, bar(10.7), 3, peak_close=12.0)

    def test_falls_back_to_entry_price_when_peak_zero(self):
        # entry=10, close=8.9 → drop=11% > 10%，peak_close=0 时使用 entry_price
        assert TrailingStop(0.10).should_sell(*ARGS, bar(8.9), 3, peak_close=0.0)

    def test_reason(self):
        assert TrailingStop(0.1).reason == "trailing_stop"


class TestAnySell:
    def test_triggers_on_stop_loss(self):
        s = AnySell(StopLoss(0.05), TakeProfit(0.15))
        assert s.should_sell(*ARGS, bar(9.4), 1)

    def test_triggers_on_take_profit(self):
        s = AnySell(StopLoss(0.05), TakeProfit(0.15))
        assert s.should_sell(*ARGS, bar(11.6), 1)

    def test_no_trigger_when_none_match(self):
        s = AnySell(StopLoss(0.05), TakeProfit(0.15))
        assert not s.should_sell(*ARGS, bar(10.5), 1)

    def test_triggered_reason_returns_first_match(self):
        s = AnySell(StopLoss(0.05), HoldNDays(10))
        assert s.triggered_reason(*ARGS, bar(9.0), 3) == "stop_loss"

    def test_triggered_reason_second_match(self):
        s = AnySell(StopLoss(0.05), HoldNDays(3))
        # close=10 → stop loss not triggered; days_held=3 >= 3 → hold triggered
        assert s.triggered_reason(*ARGS, bar(10.0), 3) == "hold_n_days"

    def test_triggered_reason_none_when_nothing_matches(self):
        s = AnySell(StopLoss(0.05))
        assert s.triggered_reason(*ARGS, bar(10.0), 1) is None


class TestAllSell:
    def test_requires_all_signals(self):
        s = AllSell(StopLoss(0.05), HoldNDays(3))
        # stop loss triggered but not hold days
        assert not s.should_sell(*ARGS, bar(9.0), 2)
        # hold days triggered but not stop loss
        assert not s.should_sell(*ARGS, bar(10.5), 5)
        # both triggered
        assert s.should_sell(*ARGS, bar(9.0), 5)

    def test_triggered_reason(self):
        s = AllSell(StopLoss(0.05), HoldNDays(3))
        assert s.triggered_reason(*ARGS, bar(9.0), 5) == "all_signals"
        assert s.triggered_reason(*ARGS, bar(9.0), 2) is None
