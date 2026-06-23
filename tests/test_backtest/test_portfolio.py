"""持仓管理单元测试。"""

import pytest

from sequoia_x.backtest.config import BacktestConfig, TransactionCost
from sequoia_x.backtest.portfolio import Portfolio


def make_config(**kwargs) -> BacktestConfig:
    defaults = dict(
        initial_capital=100_000.0,
        max_positions=5,
        position_size=0.2,
        cost=TransactionCost(commission_rate=0.0003, min_commission=5.0, stamp_duty=0.0005, slippage=0.001),
        cash_interest=0.0,
    )
    defaults.update(kwargs)
    return BacktestConfig(**defaults)


class TestPortfolioOpen:
    def test_open_succeeds_and_creates_position(self):
        p = Portfolio(make_config())
        assert p.open("000001", "2024-01-02", 10.0)
        assert "000001" in p.positions

    def test_open_reduces_cash(self):
        p = Portfolio(make_config())
        cash_before = p.cash
        p.open("000001", "2024-01-02", 10.0)
        assert p.cash < cash_before

    def test_open_cost_does_not_exceed_position_size(self):
        p = Portfolio(make_config(position_size=0.1))
        p.open("000001", "2024-01-02", 10.0)
        # 实际成本 <= 总资产 × 10% × (1 + 极小误差)
        assert p.positions["000001"].cost_basis <= 100_000 * 0.1 * 1.005

    def test_open_fails_when_cash_insufficient(self):
        p = Portfolio(make_config())
        p.cash = 1.0
        result = p.open("000001", "2024-01-02", 10.0)
        assert not result
        assert "000001" not in p.positions

    def test_open_sets_peak_close_to_open_price(self):
        p = Portfolio(make_config())
        p.open("000001", "2024-01-02", 12.5)
        assert p.positions["000001"].peak_close == 12.5

    def test_open_initial_days_held_zero(self):
        p = Portfolio(make_config())
        p.open("000001", "2024-01-02", 10.0)
        assert p.positions["000001"].days_held == 0

    def test_entry_price_includes_cost(self):
        p = Portfolio(make_config())
        p.open("000001", "2024-01-02", 10.0)
        pos = p.positions["000001"]
        # 含成本均价 = 总成本 / 股数，应略高于开盘价
        assert pos.entry_price > 10.0


class TestPortfolioClose:
    def setup_method(self):
        self.p = Portfolio(make_config())
        self.p.open("000001", "2024-01-02", 10.0)
        self.entry_cost = self.p.positions["000001"].cost_basis

    def test_close_removes_position(self):
        self.p.close("000001", "2024-01-10", 11.0, "take_profit")
        assert "000001" not in self.p.positions

    def test_close_returns_trade_with_correct_metadata(self):
        trade = self.p.close("000001", "2024-01-10", 11.0, "take_profit")
        assert trade.symbol == "000001"
        assert trade.entry_date == "2024-01-02"
        assert trade.exit_date == "2024-01-10"
        assert trade.exit_reason == "take_profit"

    def test_profitable_trade_positive_pnl(self):
        trade = self.p.close("000001", "2024-01-10", 15.0, "take_profit")
        assert trade.pnl > 0
        assert trade.pnl_pct > 0

    def test_losing_trade_negative_pnl(self):
        trade = self.p.close("000001", "2024-01-10", 5.0, "stop_loss")
        assert trade.pnl < 0
        assert trade.pnl_pct < 0

    def test_close_restores_cash(self):
        cash_after_open = self.p.cash
        self.p.close("000001", "2024-01-10", 11.0, "take_profit")
        assert self.p.cash > cash_after_open

    def test_pnl_consistency_with_cash(self):
        cost_basis = self.p.positions["000001"].cost_basis
        cash_before = self.p.cash
        trade = self.p.close("000001", "2024-01-10", 11.0, "take_profit")
        # net_proceeds = cash_gained，pnl = net_proceeds - cost_basis
        cash_gained = self.p.cash - cash_before
        assert abs(trade.pnl - (cash_gained - cost_basis)) < 1.0

    def test_days_held_recorded_in_trade(self):
        self.p.positions["000001"].days_held = 8
        trade = self.p.close("000001", "2024-01-10", 11.0, "hold_n_days")
        assert trade.days_held == 8


class TestPortfolioUpdatePeaks:
    def test_peak_updated_on_price_rise(self):
        p = Portfolio(make_config())
        p.open("000001", "2024-01-02", 10.0)
        p.update_peaks({"000001": 15.0})
        assert p.positions["000001"].peak_close == 15.0

    def test_peak_not_downgraded_on_price_drop(self):
        p = Portfolio(make_config())
        p.open("000001", "2024-01-02", 10.0)
        p.update_peaks({"000001": 15.0})
        p.update_peaks({"000001": 8.0})
        assert p.positions["000001"].peak_close == 15.0

    def test_peak_unchanged_for_missing_symbol(self):
        p = Portfolio(make_config())
        p.open("000001", "2024-01-02", 10.0)
        p.update_peaks({"999999": 100.0})
        assert p.positions["000001"].peak_close == 10.0


class TestPortfolioMarkToMarket:
    def test_equity_equals_cash_when_no_positions(self):
        p = Portfolio(make_config())
        assert p.mark_to_market({}) == p.cash

    def test_equity_reflects_unrealized_gain(self):
        p = Portfolio(make_config())
        p.open("000001", "2024-01-02", 10.0)
        equity_at_cost = p.mark_to_market({"000001": 10.0})
        equity_at_gain = p.mark_to_market({"000001": 15.0})
        assert equity_at_gain > equity_at_cost

    def test_equity_updates_total_equity_attribute(self):
        p = Portfolio(make_config())
        p.open("000001", "2024-01-02", 10.0)
        equity = p.mark_to_market({"000001": 12.0})
        assert p._total_equity == equity


class TestPortfolioIncrementDaysHeld:
    def test_increments_all_positions(self):
        p = Portfolio(make_config(max_positions=3))
        p.open("000001", "2024-01-02", 10.0)
        p.open("600001", "2024-01-02", 20.0)
        p.increment_days_held()
        assert p.positions["000001"].days_held == 1
        assert p.positions["600001"].days_held == 1
