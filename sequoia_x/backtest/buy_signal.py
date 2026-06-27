"""可插拔买入信号：定义入场条件协议及内置实现。

与 SellSignal 对称：策略在 D 日产生候选股票后，回测引擎在 D+1 日
用 BuySignal 决定 (a) 这笔信号是否真的成交、(b) 按什么价格成交。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


class BuySignal(ABC):
    """买入信号协议。子类必须实现 should_buy() 和 entry_price()。"""

    reason: str = "buy_signal"

    @abstractmethod
    def should_buy(
        self,
        symbol: str,
        signal_date: str,
        signal_bar: pd.Series,
        next_bar: pd.Series,
    ) -> bool:
        """判断这笔候选信号在 D+1 日是否应当成交。"""
        ...

    @abstractmethod
    def entry_price(
        self,
        signal_bar: pd.Series,
        next_bar: pd.Series,
    ) -> float | None:
        """返回成交价（原始市场价，滑点由 Portfolio 内部加）。

        返回 None 表示放弃这笔。
        """
        ...

    def triggered_reason(
        self,
        symbol: str,
        signal_date: str,
        signal_bar: pd.Series,
        next_bar: pd.Series,
    ) -> str | None:
        """should_buy 触发时返回 reason 字符串，否则返回 None。"""
        if self.should_buy(symbol, signal_date, signal_bar, next_bar):
            return self.reason
        return None

    def rejection_reason(
        self,
        symbol: str,
        signal_date: str,
        signal_bar: pd.Series,
        next_bar: pd.Series,
    ) -> str:
        """should_buy 返回 False 时给出具体原因。默认与 reason 相同。

        组合器（AllBuy）会重写此方法，定位到具体拒绝的子信号。
        """
        return self.reason


def _safe_float(bar: pd.Series, key: str) -> float | None:
    """从 bar 中读取数值列；非正数或异常一律返回 None。"""
    try:
        value = float(bar[key])
    except (TypeError, ValueError, KeyError):
        return None
    if value != value:  # NaN
        return None
    return value if value > 0 else None


class OpenPriceEntry(BuySignal):
    """默认实现：次日开盘价直接买入（等同 BuySignal 引入前的硬编码行为）。"""

    reason = "open_price"

    def should_buy(self, symbol, signal_date, signal_bar, next_bar) -> bool:
        return _safe_float(next_bar, "open") is not None

    def entry_price(self, signal_bar, next_bar) -> float | None:
        return _safe_float(next_bar, "open")


class LimitEntry(BuySignal):
    """限价单：次日开盘价相对 D 日收盘价溢价 ≤ max_premium_pct 才买。

    用于避免追高：若次日大幅跳开，则放弃这笔信号。
    """

    reason = "limit_buy"

    def __init__(self, max_premium_pct: float = 0.03) -> None:
        self.max_premium_pct = max_premium_pct

    def should_buy(self, symbol, signal_date, signal_bar, next_bar) -> bool:
        signal_close = _safe_float(signal_bar, "close")
        next_open = _safe_float(next_bar, "open")
        if signal_close is None or next_open is None:
            return False
        return next_open <= signal_close * (1 + self.max_premium_pct)

    def entry_price(self, signal_bar, next_bar) -> float | None:
        return _safe_float(next_bar, "open")


class BreakoutEntry(BuySignal):
    """突破确认：次日 high > D 日 high 才入场，按 D 日 high + tick 成交。

    模拟真实挂限价买单的成交方式：只有当价格真的突破信号高点时才认为入场。
    """

    reason = "breakout"

    def __init__(self, tick: float = 0.01) -> None:
        self.tick = tick

    def should_buy(self, symbol, signal_date, signal_bar, next_bar) -> bool:
        signal_high = _safe_float(signal_bar, "high")
        next_high = _safe_float(next_bar, "high")
        if signal_high is None or next_high is None:
            return False
        return next_high > signal_high

    def entry_price(self, signal_bar, next_bar) -> float | None:
        signal_high = _safe_float(signal_bar, "high")
        if signal_high is None:
            return None
        trigger_price = signal_high + self.tick
        # 若次日 open 已高于触发价，按 open 成交（更贴近真实滑点）
        next_open = _safe_float(next_bar, "open")
        if next_open is not None and next_open >= trigger_price:
            return next_open
        return trigger_price


class BuyFilter(BuySignal):
    """纯过滤器基类：只参与 should_buy 投票，不参与定价。

    `entry_price()` 强制返回 None，确保 `AllBuy` 中过滤器不会被用作 primary。
    子类只需要实现 should_buy()。
    """

    def entry_price(self, signal_bar, next_bar) -> float | None:
        return None


class SkipLimitUp(BuyFilter):
    """涨停过滤器：次日开盘涨幅 ≥ threshold 时拒绝（无法真实成交）。

    threshold 默认 9.7%，留 0.3% 缓冲避免边缘 case。
    """

    reason = "limit_up_open"

    def __init__(self, threshold: float = 0.097) -> None:
        self.threshold = threshold

    def should_buy(self, symbol, signal_date, signal_bar, next_bar) -> bool:
        signal_close = _safe_float(signal_bar, "close")
        next_open = _safe_float(next_bar, "open")
        if signal_close is None or next_open is None:
            return False
        gap_pct = (next_open / signal_close) - 1
        return gap_pct < self.threshold


class AnyBuy(BuySignal):
    """OR 组合：多个买入信号中任一触发即买入，按 first-match-wins 定价。

    `entry_price()` 使用 `should_buy()` 期间记录的「首个触发的子信号」来定价，
    避免被未触发的子信号「抢答」错误价格。要求引擎遵循「先 should_buy 再 entry_price」
    的调用顺序（Backtester 主循环天然满足）。
    """

    reason = "any_buy"

    def __init__(self, *signals: BuySignal) -> None:
        if not signals:
            raise ValueError("AnyBuy 至少需要 1 个子信号")
        self.signals = signals
        self._last_triggered: BuySignal | None = None

    def should_buy(self, *args, **kwargs) -> bool:
        for s in self.signals:
            if s.should_buy(*args, **kwargs):
                self._last_triggered = s
                return True
        self._last_triggered = None
        return False

    def entry_price(self, signal_bar, next_bar) -> float | None:
        # 优先使用上次 should_buy 锁定的触发者
        if self._last_triggered is not None:
            return self._last_triggered.entry_price(signal_bar, next_bar)
        # Fallback：若 entry_price 在 should_buy 之前被独立调用，
        # 返回第一个能给出有效价格的子信号
        for s in self.signals:
            price = s.entry_price(signal_bar, next_bar)
            if price is not None and price > 0:
                return price
        return None

    def triggered_reason(self, symbol, signal_date, signal_bar, next_bar) -> str | None:
        for s in self.signals:
            r = s.triggered_reason(symbol, signal_date, signal_bar, next_bar)
            if r is not None:
                return r
        return None


class AllBuy(BuySignal):
    """AND 组合：primary + filters 都满足才买，由 primary 显式定价。

    设计要点：
      - `primary` 必须是非过滤器型 BuySignal（OpenPriceEntry / LimitEntry / BreakoutEntry 等）
      - `filters` 全部为 BuyFilter 子类（entry_price 强制返回 None）
      - 实际定价完全交给 primary，与 filters 的顺序无关
      - 拒绝时通过 rejection_reason() 定位到具体子信号，便于诊断
    """

    reason = "all_buy"

    def __init__(
        self,
        primary: BuySignal,
        filters: list[BuyFilter] | tuple[BuyFilter, ...] = (),
    ) -> None:
        if isinstance(primary, BuyFilter):
            raise TypeError(
                f"AllBuy 的 primary 不能是 BuyFilter (收到 {type(primary).__name__})，"
                f"必须是能定价的 BuySignal 子类"
            )
        for f in filters:
            if not isinstance(f, BuyFilter):
                raise TypeError(
                    f"AllBuy 的 filters 只接受 BuyFilter 子类，收到 {type(f).__name__}"
                )
        self.primary = primary
        self.filters = tuple(filters)

    def should_buy(self, *args, **kwargs) -> bool:
        if not self.primary.should_buy(*args, **kwargs):
            return False
        return all(f.should_buy(*args, **kwargs) for f in self.filters)

    def entry_price(self, signal_bar, next_bar) -> float | None:
        return self.primary.entry_price(signal_bar, next_bar)

    def triggered_reason(self, symbol, signal_date, signal_bar, next_bar) -> str | None:
        if self.should_buy(symbol, signal_date, signal_bar, next_bar):
            return self.primary.reason
        return None

    def rejection_reason(self, symbol, signal_date, signal_bar, next_bar) -> str:
        if not self.primary.should_buy(symbol, signal_date, signal_bar, next_bar):
            return self.primary.rejection_reason(symbol, signal_date, signal_bar, next_bar)
        for f in self.filters:
            if not f.should_buy(symbol, signal_date, signal_bar, next_bar):
                return f.rejection_reason(symbol, signal_date, signal_bar, next_bar)
        return self.reason


@dataclass
class PendingBuy:
    """待执行买单：携带 D 日信号上下文，避免次日重复查询。"""

    symbol: str
    signal_date: str
    signal_bar: pd.Series  # D 日 OHLCV，给 BuySignal 用


@dataclass
class SkippedSignal:
    """被过滤掉的买入信号：用于报告诊断"策略选了股但没买进"的原因。"""

    symbol: str
    signal_date: str
    skip_date: str
    reason: str
