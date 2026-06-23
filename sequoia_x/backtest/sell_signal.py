"""可插拔卖出信号：定义平仓条件协议及内置实现。"""

from abc import ABC, abstractmethod

import pandas as pd


class SellSignal(ABC):
    reason: str = "signal"

    @abstractmethod
    def should_sell(
        self,
        symbol: str,
        entry_price: float,
        entry_date: str,
        current_bar: pd.Series,
        days_held: int,
        peak_close: float = 0.0,
    ) -> bool: ...

    def triggered_reason(
        self,
        symbol: str,
        entry_price: float,
        entry_date: str,
        current_bar: pd.Series,
        days_held: int,
        peak_close: float = 0.0,
    ) -> str | None:
        """触发时返回原因字符串，否则返回 None。"""
        if self.should_sell(symbol, entry_price, entry_date, current_bar, days_held, peak_close):
            return self.reason
        return None


class HoldNDays(SellSignal):
    """持仓满 N 个交易日后卖出。"""

    reason = "hold_n_days"

    def __init__(self, n: int) -> None:
        self.n = n

    def should_sell(self, symbol, entry_price, entry_date, current_bar, days_held, peak_close=0.0) -> bool:
        return days_held >= self.n


class StopLoss(SellSignal):
    """收盘价跌破买入均价 × (1 - pct) 时止损。"""

    reason = "stop_loss"

    def __init__(self, pct: float) -> None:
        self.pct = pct

    def should_sell(self, symbol, entry_price, entry_date, current_bar, days_held, peak_close=0.0) -> bool:
        return float(current_bar["close"]) <= entry_price * (1 - self.pct)


class TakeProfit(SellSignal):
    """收盘价超过买入均价 × (1 + pct) 时止盈。"""

    reason = "take_profit"

    def __init__(self, pct: float) -> None:
        self.pct = pct

    def should_sell(self, symbol, entry_price, entry_date, current_bar, days_held, peak_close=0.0) -> bool:
        return float(current_bar["close"]) >= entry_price * (1 + self.pct)


class TrailingStop(SellSignal):
    """从持仓期最高收盘价（peak_close）回撤超过 pct 时止损。"""

    reason = "trailing_stop"

    def __init__(self, pct: float) -> None:
        self.pct = pct

    def should_sell(self, symbol, entry_price, entry_date, current_bar, days_held, peak_close=0.0) -> bool:
        ref = peak_close if peak_close > 0 else entry_price
        return float(current_bar["close"]) <= ref * (1 - self.pct)


class AnySell(SellSignal):
    """OR 组合：多个信号中任意一个触发即卖出。"""

    reason = "signal"

    def __init__(self, *signals: SellSignal) -> None:
        self.signals = signals

    def should_sell(self, *args, **kwargs) -> bool:
        return any(s.should_sell(*args, **kwargs) for s in self.signals)

    def triggered_reason(self, *args, **kwargs) -> str | None:
        for s in self.signals:
            r = s.triggered_reason(*args, **kwargs)
            if r is not None:
                return r
        return None


class AllSell(SellSignal):
    """AND 组合：所有信号同时触发才卖出。"""

    reason = "all_signals"

    def __init__(self, *signals: SellSignal) -> None:
        self.signals = signals

    def should_sell(self, *args, **kwargs) -> bool:
        return all(s.should_sell(*args, **kwargs) for s in self.signals)

    def triggered_reason(self, *args, **kwargs) -> str | None:
        if self.should_sell(*args, **kwargs):
            return self.reason
        return None
