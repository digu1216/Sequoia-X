"""回测引擎：SlicedDataEngine（防未来函数）+ Backtester 主回放循环。"""

import sqlite3
from datetime import datetime, timedelta

import pandas as pd

from sequoia_x.core.config import Settings
from sequoia_x.core.logger import get_logger
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.base import BaseStrategy

from .buy_signal import BuySignal, OpenPriceEntry, PendingBuy, SkippedSignal
from .config import BacktestConfig
from .portfolio import Portfolio, Trade
from .sell_signal import HoldNDays, SellSignal

logger = get_logger(__name__)


class SlicedDataEngine:
    """包装预加载数据，使 get_ohlcv() 只返回 as_of 日期及之前的数据。

    策略持有此引擎的引用，回测主循环每日更新 as_of，
    策略完全感知不到未来数据的存在，无需改动任何策略代码。
    """

    def __init__(self, all_data: dict[str, pd.DataFrame]) -> None:
        # all_data: symbol -> DataFrame，date 为 index，按日期升序
        self._data = all_data
        self.as_of: str = ""

    def get_ohlcv(self, symbol: str) -> pd.DataFrame:
        df = self._data.get(symbol)
        if df is None or df.empty:
            return pd.DataFrame()
        filtered = df[df.index <= self.as_of]
        if filtered.empty:
            return pd.DataFrame()
        # reset_index 将 date 变回列，与 DataEngine.get_ohlcv() 保持一致
        result = filtered.reset_index().rename(columns={"index": "date"})
        return result

    def get_local_symbols(self) -> list[str]:
        return list(self._data.keys())

    @staticmethod
    def _to_baostock_code(symbol: str) -> str:
        prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
        return f"{prefix}.{symbol}"


class Backtester:
    """历史回测引擎。

    按时间轴逐日回放：
      1. 执行上日 pending_buys：由 BuySignal 协议决定是否成交 + 按什么价成交（T+1）
      2. 当日收盘检查卖出信号（止损/止盈/时间/自定义）
      3. 生成当日信号 → 封装为 PendingBuy 队列，供次日 BuySignal 协议筛选
    """

    def __init__(
        self,
        data_engine: DataEngine,
        strategy_cls: type[BaseStrategy],
        buy_signal: BuySignal | None = None,
        sell_signal: SellSignal | None = None,
        start: str = "2023-01-01",
        end: str = "2024-12-31",
        config: BacktestConfig | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.data_engine = data_engine
        self.strategy_cls = strategy_cls
        self._buy_signal = buy_signal
        self._sell_signal = sell_signal
        self.start = start
        self.end = end
        self.config = config or BacktestConfig()
        self._settings = settings

    def run(self) -> "BacktestReport":
        from .report import BacktestReport

        logger.info(f"开始回测：{self.strategy_cls.__name__} [{self.start} → {self.end}]")

        # ── 1. 预加载历史数据（含 120 日缓冲，保证指标计算足够的历史 K 线）
        buffer_start = (
            datetime.strptime(self.start, "%Y-%m-%d") - timedelta(days=120)
        ).strftime("%Y-%m-%d")
        logger.info("预加载历史数据...")
        all_data = self._preload(buffer_start, self.end)
        if not all_data:
            raise RuntimeError("无本地历史数据，请先执行 python main.py --backfill")

        # ── 2. 提取回测区间内的交易日（从实际数据中获取，天然排除非交易日）
        trading_days = sorted({
            d for df in all_data.values()
            for d in df.index.tolist()
            if self.start <= d <= self.end
        })
        if not trading_days:
            raise RuntimeError(f"[{self.start}, {self.end}] 区间内无交易日数据")
        logger.info(f"共 {len(trading_days)} 个交易日")

        # ── 3. 初始化组件
        sliced_engine = SlicedDataEngine(all_data)
        settings = self._settings or Settings(
            db_path=self.data_engine.db_path,
            start_date=self.start,
            feishu_webhook_url="http://backtest.local",
        )
        strategy = self.strategy_cls(sliced_engine, settings)  # type: ignore[arg-type]

        # 卖出信号优先级：显式传入 > 策略自定义 > 默认 HoldNDays(10)
        if self._sell_signal is not None:
            sell_signal: SellSignal = self._sell_signal
        elif hasattr(strategy, "sell_signal") and callable(getattr(strategy, "sell_signal")):
            sell_signal = strategy.sell_signal()
        else:
            sell_signal = HoldNDays(10)

        # 买入信号优先级：显式传入 > 策略自定义 > 默认 OpenPriceEntry
        if self._buy_signal is not None:
            buy_signal: BuySignal = self._buy_signal
        elif hasattr(strategy, "buy_signal") and callable(getattr(strategy, "buy_signal")):
            buy_signal = strategy.buy_signal()
        else:
            buy_signal = OpenPriceEntry()

        portfolio = Portfolio(self.config)
        trades: list[Trade] = []
        equity_curve: list[tuple[str, float]] = []
        pending_buys: list[PendingBuy] = []
        skipped_signals: list[SkippedSignal] = []

        # ── 4. 主回放循环
        for date in trading_days:
            sliced_engine.as_of = date

            # 4a. 执行待买入队列：走 BuySignal 协议决定是否成交、按什么价成交
            self._execute_pending_buys(
                pending_buys, portfolio, all_data, date, buy_signal, skipped_signals
            )

            # 4b. 获取当日收盘价（仅针对持仓股）
            close_prices = self._get_close_prices(all_data, portfolio.positions, date)

            # 4c. 更新持仓最高价（用于移动止损 TrailingStop）
            portfolio.update_peaks(close_prices)

            # 4d. 检查卖出信号（T+1：当日买入的仓位 days_held=0，不可当日卖出）
            for symbol, pos in list(portfolio.positions.items()):
                if pos.days_held == 0:
                    continue
                bar = self._get_bar(all_data, symbol, date)
                if bar is None:
                    continue
                reason = sell_signal.triggered_reason(
                    symbol, pos.entry_price, pos.entry_date, bar, pos.days_held, pos.peak_close
                )
                if reason:
                    close_price = close_prices.get(symbol, pos.entry_price)
                    trades.append(portfolio.close(symbol, date, close_price, reason))

            # 4e. 持仓计日 & 闲置现金计息
            portfolio.increment_days_held()
            portfolio.accrue_interest()

            # 4f. 记录当日净值快照
            equity = portfolio.mark_to_market(close_prices)
            equity_curve.append((date, equity))

            # 4g. 生成次日买入信号（策略只看截止当日的数据）
            try:
                signals = strategy.run()
            except Exception as exc:
                logger.warning(f"[{date}] 策略执行异常，跳过当日信号：{exc}")
                signals = []
            pending_buys = self._filter_signals(signals, portfolio, all_data, date)

        # ── 5. 回测结束：强制平仓全部持仓（记录为 end_of_backtest）
        if trading_days:
            last_date = trading_days[-1]
            close_prices = self._get_close_prices(all_data, portfolio.positions, last_date)
            for symbol, pos in list(portfolio.positions.items()):
                close_price = close_prices.get(symbol, pos.entry_price)
                trades.append(portfolio.close(symbol, last_date, close_price, "end_of_backtest"))

        logger.info(f"回测完成：{len(trades)} 笔交易，最终净值 {equity_curve[-1][1]:,.0f} 元" if equity_curve else "回测完成：无交易日数据")
        return BacktestReport(
            trades, equity_curve, self.config, skipped_signals=skipped_signals
        )

    # ── 内部辅助方法 ────────────────────────────────────────────────────────

    def _preload(self, buffer_start: str, end: str) -> dict[str, pd.DataFrame]:
        """一次性从 SQLite 读取所有股票数据并按 symbol 分组，date 作为 index。"""
        with sqlite3.connect(self.data_engine.db_path) as conn:
            df = pd.read_sql(
                "SELECT symbol, date, open, high, low, close, volume, turnover "
                "FROM stock_daily WHERE date >= ? AND date <= ? ORDER BY symbol, date",
                conn,
                params=(buffer_start, end),
            )
        if df.empty:
            return {}
        result: dict[str, pd.DataFrame] = {}
        for symbol, group in df.groupby("symbol"):
            result[str(symbol)] = group.drop(columns="symbol").set_index("date")
        return result

    def _get_price(self, all_data: dict, symbol: str, date: str, col: str) -> float | None:
        df = all_data.get(symbol)
        if df is None or date not in df.index:
            return None
        val = df.loc[date, col]
        try:
            f = float(val)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    def _get_bar(self, all_data: dict, symbol: str, date: str) -> pd.Series | None:
        df = all_data.get(symbol)
        if df is None or date not in df.index:
            return None
        return df.loc[date]

    def _get_close_prices(
        self, all_data: dict, positions: dict, date: str
    ) -> dict[str, float]:
        prices: dict[str, float] = {}
        for symbol in positions:
            price = self._get_price(all_data, symbol, date, "close")
            if price is not None:
                prices[symbol] = price
        return prices

    def _execute_pending_buys(
        self,
        pending_buys: list[PendingBuy],
        portfolio: Portfolio,
        all_data: dict[str, pd.DataFrame],
        trade_date: str,
        buy_signal: BuySignal,
        skipped_signals: list[SkippedSignal],
    ) -> None:
        """T+1 开盘时刻，按 BuySignal 协议决定哪些 pending 单子真的成交、按什么价成交。

        所有未成交的 PendingBuy 都会落到 skipped_signals 用于报告诊断；
        队列结束后清空（未成交不滚动到下一日）。
        """
        slots = self.config.max_positions - len(portfolio.positions)
        if not pending_buys:
            return

        if slots <= 0:
            for pb in pending_buys:
                skipped_signals.append(
                    SkippedSignal(pb.symbol, pb.signal_date, trade_date, "no_slot")
                )
            pending_buys.clear()
            return

        executed = 0
        for pb in pending_buys:
            if executed >= slots:
                skipped_signals.append(
                    SkippedSignal(pb.symbol, pb.signal_date, trade_date, "no_slot")
                )
                continue

            if pb.symbol in portfolio.positions:
                skipped_signals.append(
                    SkippedSignal(pb.symbol, pb.signal_date, trade_date, "already_held")
                )
                continue

            next_bar = self._get_bar(all_data, pb.symbol, trade_date)
            if next_bar is None:
                skipped_signals.append(
                    SkippedSignal(pb.symbol, pb.signal_date, trade_date, "no_bar")
                )
                continue

            if not buy_signal.should_buy(
                pb.symbol, pb.signal_date, pb.signal_bar, next_bar
            ):
                reason = buy_signal.rejection_reason(
                    pb.symbol, pb.signal_date, pb.signal_bar, next_bar
                )
                skipped_signals.append(
                    SkippedSignal(pb.symbol, pb.signal_date, trade_date, reason)
                )
                continue

            price = buy_signal.entry_price(pb.signal_bar, next_bar)
            if price is None or price <= 0:
                skipped_signals.append(
                    SkippedSignal(pb.symbol, pb.signal_date, trade_date, "price_none")
                )
                continue

            if portfolio.open(pb.symbol, trade_date, price):
                executed += 1
            else:
                skipped_signals.append(
                    SkippedSignal(pb.symbol, pb.signal_date, trade_date, "cash_insufficient")
                )

        pending_buys.clear()

    def _filter_signals(
        self, signals: list[str], portfolio: Portfolio, all_data: dict, date: str
    ) -> list[PendingBuy]:
        """过滤、排序、截断信号列表，返回次日可执行的 PendingBuy 队列。"""
        new = [s for s in signals if s not in portfolio.positions]
        slots = self.config.max_positions - len(portfolio.positions)
        if slots <= 0 or not new:
            return []

        if self.config.signal_priority == "volume":
            def vol_key(s: str) -> float:
                df = all_data.get(s)
                if df is None or date not in df.index:
                    return 0.0
                try:
                    return float(df.loc[date, "turnover"] or 0)
                except (TypeError, ValueError):
                    return 0.0
            new.sort(key=vol_key, reverse=True)

        elif self.config.signal_priority == "momentum":
            def mom_key(s: str) -> float:
                df = all_data.get(s)
                if df is None or date not in df.index:
                    return 0.0
                try:
                    idx = df.index.get_loc(date)
                    if idx == 0:
                        return 0.0
                    prev = float(df.iloc[idx - 1]["close"] or 1)
                    curr = float(df.loc[date, "close"] or 0)
                    return (curr / prev - 1) if prev > 0 else 0.0
                except (TypeError, ValueError, KeyError):
                    return 0.0
            new.sort(key=mom_key, reverse=True)

        truncated = new[:slots]

        result: list[PendingBuy] = []
        for sym in truncated:
            bar = self._get_bar(all_data, sym, date)
            if bar is None:
                continue
            result.append(PendingBuy(symbol=sym, signal_date=date, signal_bar=bar))
        return result


# 延迟导入，避免循环引用
from .report import BacktestReport  # noqa: E402
