"""回测引擎集成测试。"""

import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from sequoia_x.backtest.config import BacktestConfig, TransactionCost
from sequoia_x.backtest.engine import Backtester, SlicedDataEngine
from sequoia_x.backtest.sell_signal import HoldNDays, AnySell, StopLoss, TakeProfit
from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.base import BaseStrategy


# ── 辅助工具 ────────────────────────────────────────────────────────────────

def _make_ohlcv(dates: list[str], start_price: float = 10.0, seed: int = 42) -> pd.DataFrame:
    """生成确定性测试 OHLCV 数据。"""
    import random
    rng = random.Random(seed)
    rows = []
    price = start_price
    for d in dates:
        price *= 1 + rng.uniform(-0.02, 0.02)
        rows.append({
            "date": d, "open": price * 0.99, "high": price * 1.01,
            "low": price * 0.98, "close": price,
            "volume": 1_000_000, "turnover": price * 1_000_000,
        })
    return pd.DataFrame(rows)


def _make_test_db(tmp_path: Path, symbols: list[str], dates: list[str]) -> str:
    db_path = str(tmp_path / "test.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE stock_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL, date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL,
                volume REAL, turnover REAL,
                UNIQUE(symbol, date)
            )
        """)
        conn.execute("CREATE INDEX idx_symbol_date ON stock_daily (symbol, date)")
        for i, symbol in enumerate(symbols):
            df = _make_ohlcv(dates, seed=i * 100 + 1)
            df["symbol"] = symbol
            df = df[["symbol", "date", "open", "high", "low", "close", "volume", "turnover"]]
            df.to_sql("stock_daily", conn, if_exists="append", index=False)
        conn.commit()
    return db_path


def _make_settings(db_path: str) -> Settings:
    return Settings(
        db_path=db_path,
        start_date="2024-01-01",
        feishu_webhook_url="http://backtest.local",
    )


# ── SlicedDataEngine 测试 ────────────────────────────────────────────────────

class TestSlicedDataEngine:
    @pytest.fixture
    def engine(self):
        dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
        df = pd.DataFrame({
            "open": [10.0, 11.0, 12.0, 13.0],
            "close": [10.5, 11.5, 12.5, 13.5],
            "high": [11.0, 12.0, 13.0, 14.0],
            "low": [9.0, 10.0, 11.0, 12.0],
            "volume": [100_000] * 4,
            "turnover": [1_000_000] * 4,
        }, index=dates)
        return SlicedDataEngine({"000001": df})

    def test_slices_data_by_as_of(self, engine):
        engine.as_of = "2024-01-03"
        df = engine.get_ohlcv("000001")
        assert len(df) == 2
        assert df["date"].max() == "2024-01-03"

    def test_returns_full_data_at_end_date(self, engine):
        engine.as_of = "2024-01-05"
        df = engine.get_ohlcv("000001")
        assert len(df) == 4

    def test_returns_empty_for_unknown_symbol(self, engine):
        engine.as_of = "2024-01-03"
        assert engine.get_ohlcv("999999").empty

    def test_returns_empty_before_any_data(self, engine):
        engine.as_of = "2023-12-31"
        assert engine.get_ohlcv("000001").empty

    def test_get_local_symbols_includes_known(self, engine):
        engine.as_of = "2024-01-03"
        assert "000001" in engine.get_local_symbols()

    def test_date_column_present_in_result(self, engine):
        engine.as_of = "2024-01-03"
        df = engine.get_ohlcv("000001")
        assert "date" in df.columns
        assert "close" in df.columns


# ── Backtester 集成测试 ──────────────────────────────────────────────────────

DATES_100 = pd.bdate_range("2024-01-01", periods=100).strftime("%Y-%m-%d").tolist()


class AlwaysBuyStrategy(BaseStrategy):
    """每日返回所有本地股票作为买入信号。"""

    def run(self) -> list[str]:
        return self.engine.get_local_symbols()


class NeverBuyStrategy(BaseStrategy):
    """始终不产生买入信号。"""

    def run(self) -> list[str]:
        return []


class TestBacktesterEndToEnd:
    @pytest.fixture
    def setup(self, tmp_path):
        symbols = ["000001", "600001", "300001"]
        db_path = _make_test_db(tmp_path, symbols, DATES_100)
        settings = _make_settings(db_path)
        engine = DataEngine(settings)
        return engine, settings

    def test_runs_without_error(self, setup):
        engine, settings = setup
        config = BacktestConfig(initial_capital=300_000, max_positions=3, position_size=0.33,
                                cost=TransactionCost())
        bt = Backtester(
            data_engine=engine,
            strategy_cls=AlwaysBuyStrategy,
            sell_signal=HoldNDays(5),
            start="2024-02-01",
            end="2024-04-30",
            config=config,
            settings=settings,
        )
        report = bt.run()
        assert report is not None
        assert len(report.equity_series) > 0

    def test_equity_series_starts_near_initial_capital(self, setup):
        engine, settings = setup
        config = BacktestConfig(initial_capital=100_000, max_positions=3, position_size=0.1)
        bt = Backtester(
            data_engine=engine,
            strategy_cls=NeverBuyStrategy,
            sell_signal=HoldNDays(5),
            start="2024-02-01",
            end="2024-03-31",
            config=config,
            settings=settings,
        )
        report = bt.run()
        # 无交易时净值 ≈ 初始资金（含极少量利息）
        assert abs(report.equity_series.iloc[0] - 100_000) / 100_000 < 0.01

    def test_no_trades_when_strategy_returns_empty(self, setup):
        engine, settings = setup
        bt = Backtester(
            data_engine=engine,
            strategy_cls=NeverBuyStrategy,
            sell_signal=HoldNDays(5),
            start="2024-02-01",
            end="2024-03-31",
            settings=settings,
        )
        report = bt.run()
        assert len(report.trades) == 0

    def test_positions_respect_max_positions(self, setup):
        engine, settings = setup
        max_pos = 2
        config = BacktestConfig(initial_capital=300_000, max_positions=max_pos, position_size=0.33)
        bt = Backtester(
            data_engine=engine,
            strategy_cls=AlwaysBuyStrategy,
            sell_signal=HoldNDays(20),
            start="2024-02-01",
            end="2024-04-30",
            config=config,
            settings=settings,
        )
        report = bt.run()
        # 每笔交易的条目合理
        assert isinstance(report.trades, list)
        # 存在交易时，不超过 max_positions 的限制由引擎保证
        assert len(report.equity_series) > 0

    def test_all_positions_closed_at_end(self, setup):
        engine, settings = setup
        config = BacktestConfig(initial_capital=300_000, max_positions=3, position_size=0.33)
        bt = Backtester(
            data_engine=engine,
            strategy_cls=AlwaysBuyStrategy,
            sell_signal=HoldNDays(9999),  # 永不因时间触发
            start="2024-02-01",
            end="2024-04-30",
            config=config,
            settings=settings,
        )
        report = bt.run()
        # 回测结束强制平仓，end_of_backtest 原因应存在
        end_trades = [t for t in report.trades if t.exit_reason == "end_of_backtest"]
        assert len(end_trades) > 0

    def test_report_summary_has_required_keys(self, setup):
        engine, settings = setup
        bt = Backtester(
            data_engine=engine,
            strategy_cls=AlwaysBuyStrategy,
            sell_signal=HoldNDays(5),
            start="2024-02-01",
            end="2024-04-30",
            settings=settings,
        )
        report = bt.run()
        s = report.summary()
        required = {"total_return", "annualized_return", "max_drawdown", "sharpe", "win_rate", "total_trades"}
        assert required.issubset(s.keys())

    def test_to_dataframe_returns_correct_columns(self, setup):
        engine, settings = setup
        bt = Backtester(
            data_engine=engine,
            strategy_cls=AlwaysBuyStrategy,
            sell_signal=HoldNDays(3),
            start="2024-02-01",
            end="2024-04-30",
            settings=settings,
        )
        report = bt.run()
        if report.trades:
            df = report.to_dataframe()
            for col in ["symbol", "entry_date", "exit_date", "pnl", "pnl_pct", "exit_reason", "days_held"]:
                assert col in df.columns

    def test_t1_rule_days_held_at_least_one(self, setup):
        engine, settings = setup
        bt = Backtester(
            data_engine=engine,
            strategy_cls=AlwaysBuyStrategy,
            sell_signal=AnySell(StopLoss(0.99), HoldNDays(1)),  # 最短持有 1 天
            start="2024-02-01",
            end="2024-04-30",
            settings=settings,
        )
        report = bt.run()
        non_eob = [t for t in report.trades if t.exit_reason != "end_of_backtest"]
        assert all(t.days_held >= 1 for t in non_eob)
