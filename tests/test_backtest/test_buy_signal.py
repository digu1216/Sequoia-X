"""买入信号单元测试。"""

import math

import pandas as pd
import pytest

from sequoia_x.backtest.buy_signal import (
    AllBuy,
    AnyBuy,
    BreakoutEntry,
    BuyFilter,
    LimitEntry,
    OpenPriceEntry,
    PendingBuy,
    SkipLimitUp,
    SkippedSignal,
)


def bar(
    open_: float,
    close: float | None = None,
    high: float | None = None,
    low: float | None = None,
) -> pd.Series:
    close = close if close is not None else open_ * 1.01
    high = high if high is not None else max(open_, close) * 1.02
    low = low if low is not None else min(open_, close) * 0.97
    return pd.Series({"open": open_, "high": high, "low": low, "close": close})


ARGS = ("000001", "2024-01-02")  # symbol, signal_date


class TestOpenPriceEntry:
    def test_should_buy_returns_true_for_positive_open(self):
        assert OpenPriceEntry().should_buy(*ARGS, bar(10.0), bar(10.5))

    def test_should_buy_returns_false_for_zero_open(self):
        assert not OpenPriceEntry().should_buy(*ARGS, bar(10.0), bar(0.0))

    def test_should_buy_returns_false_for_negative_open(self):
        assert not OpenPriceEntry().should_buy(*ARGS, bar(10.0), bar(-1.0))

    def test_should_buy_returns_false_for_nan_open(self):
        next_bar = pd.Series({"open": math.nan, "high": 11.0, "low": 9.0, "close": 10.0})
        assert not OpenPriceEntry().should_buy(*ARGS, bar(10.0), next_bar)

    def test_entry_price_returns_next_bar_open(self):
        assert OpenPriceEntry().entry_price(bar(10.0), bar(10.5)) == 10.5

    def test_entry_price_returns_none_for_zero(self):
        assert OpenPriceEntry().entry_price(bar(10.0), bar(0.0)) is None

    def test_reason_string(self):
        assert OpenPriceEntry().reason == "open_price"

    def test_triggered_reason_returns_string_on_match(self):
        assert OpenPriceEntry().triggered_reason(*ARGS, bar(10.0), bar(10.5)) == "open_price"

    def test_triggered_reason_returns_none_on_miss(self):
        assert OpenPriceEntry().triggered_reason(*ARGS, bar(10.0), bar(0.0)) is None


class TestPendingBuy:
    def test_carries_signal_context(self):
        pb = PendingBuy(symbol="000001", signal_date="2024-01-02", signal_bar=bar(10.0))
        assert pb.symbol == "000001"
        assert pb.signal_date == "2024-01-02"
        assert float(pb.signal_bar["open"]) == 10.0


class TestSkippedSignal:
    def test_records_reason(self):
        s = SkippedSignal(
            symbol="000001",
            signal_date="2024-01-02",
            skip_date="2024-01-03",
            reason="limit_up_open",
        )
        assert s.reason == "limit_up_open"
        assert s.skip_date == "2024-01-03"


# ── LimitEntry ──────────────────────────────────────────────────────────────

class TestLimitEntry:
    def test_accepts_open_within_premium(self):
        # signal_close=10, next_open=10.2 → 溢价 2% < 3%
        assert LimitEntry(0.03).should_buy(*ARGS, bar(10.0, close=10.0), bar(10.2))

    def test_rejects_open_above_premium(self):
        # signal_close=10, next_open=10.5 → 溢价 5% > 3%
        assert not LimitEntry(0.03).should_buy(*ARGS, bar(10.0, close=10.0), bar(10.5))

    def test_accepts_boundary_exact(self):
        # signal_close=10, next_open=10.3 → 溢价正好 3%
        assert LimitEntry(0.03).should_buy(*ARGS, bar(10.0, close=10.0), bar(10.3))

    def test_accepts_lower_open(self):
        # 跳低开盘 - 应该允许买入（绝对是个好价钱）
        assert LimitEntry(0.03).should_buy(*ARGS, bar(10.0, close=10.0), bar(9.5))

    def test_rejects_when_signal_close_invalid(self):
        assert not LimitEntry(0.03).should_buy(*ARGS, bar(10.0, close=0.0), bar(10.2))

    def test_entry_price_is_next_open(self):
        assert LimitEntry(0.03).entry_price(bar(10.0, close=10.0), bar(10.2)) == 10.2

    def test_reason(self):
        assert LimitEntry(0.03).reason == "limit_buy"


# ── BreakoutEntry ───────────────────────────────────────────────────────────

class TestBreakoutEntry:
    def test_accepts_when_high_breaks(self):
        # signal_high=11, next_high=11.5 → 突破成功
        assert BreakoutEntry().should_buy(*ARGS, bar(10.0, high=11.0), bar(11.0, high=11.5))

    def test_rejects_when_high_not_break(self):
        # signal_high=11, next_high=10.8 → 未突破
        assert not BreakoutEntry().should_buy(*ARGS, bar(10.0, high=11.0), bar(10.5, high=10.8))

    def test_rejects_at_exact_signal_high(self):
        # 严格大于：等于不算突破
        assert not BreakoutEntry().should_buy(*ARGS, bar(10.0, high=11.0), bar(11.0, high=11.0))

    def test_entry_price_uses_signal_high_plus_tick(self):
        # next_open=10.5 (低于 trigger=11.01)，按 trigger 价成交
        result = BreakoutEntry(tick=0.01).entry_price(
            bar(10.0, high=11.0),
            bar(10.5, high=11.5),
        )
        assert result == pytest.approx(11.01)

    def test_entry_price_uses_next_open_when_gap_above_trigger(self):
        # next_open=12.0 (已经在 trigger 之上)，按 open 成交
        result = BreakoutEntry(tick=0.01).entry_price(
            bar(10.0, high=11.0),
            bar(12.0, high=12.5),
        )
        assert result == 12.0

    def test_reason(self):
        assert BreakoutEntry().reason == "breakout"


# ── SkipLimitUp ─────────────────────────────────────────────────────────────

class TestSkipLimitUp:
    def test_accepts_normal_open(self):
        # signal_close=10, next_open=10.5 → 跳开 5% < 9.7%
        assert SkipLimitUp().should_buy(*ARGS, bar(10.0, close=10.0), bar(10.5))

    def test_rejects_limit_up_open(self):
        # signal_close=10, next_open=10.98 → 跳开 9.8% >= 9.7%
        assert not SkipLimitUp().should_buy(*ARGS, bar(10.0, close=10.0), bar(10.98))

    def test_accepts_low_open(self):
        # 跳低开 - 当然不是涨停
        assert SkipLimitUp().should_buy(*ARGS, bar(10.0, close=10.0), bar(9.0))

    def test_custom_threshold(self):
        # 自定义 5% 阈值
        assert not SkipLimitUp(threshold=0.05).should_buy(*ARGS, bar(10.0, close=10.0), bar(10.6))
        assert SkipLimitUp(threshold=0.05).should_buy(*ARGS, bar(10.0, close=10.0), bar(10.3))

    def test_entry_price_always_none(self):
        # 过滤器不参与定价
        assert SkipLimitUp().entry_price(bar(10.0), bar(10.5)) is None

    def test_reason(self):
        assert SkipLimitUp().reason == "limit_up_open"

    def test_is_buyfilter_subclass(self):
        assert isinstance(SkipLimitUp(), BuyFilter)


# ── AnyBuy ──────────────────────────────────────────────────────────────────

class TestAnyBuy:
    def test_triggers_on_first_signal(self):
        # OpenPriceEntry 总是 True
        s = AnyBuy(OpenPriceEntry(), BreakoutEntry())
        assert s.should_buy(*ARGS, bar(10.0), bar(10.5))

    def test_triggers_on_second_signal(self):
        # LimitEntry 拒绝高跳开，但 OpenPriceEntry 接受
        s = AnyBuy(LimitEntry(0.02), OpenPriceEntry())
        assert s.should_buy(*ARGS, bar(10.0, close=10.0), bar(10.5))

    def test_rejects_when_all_reject(self):
        # signal close=10, next open=15 → 溢价 50%
        s = AnyBuy(LimitEntry(0.02), BreakoutEntry())
        assert not s.should_buy(*ARGS, bar(10.0, close=10.0, high=11.0), bar(15.0, high=10.5))

    def test_entry_price_uses_first_pricing_subsignal(self):
        # AnyBuy 中第一个能定价的子信号决定价格
        s = AnyBuy(OpenPriceEntry(), BreakoutEntry())
        # OpenPriceEntry 给出 10.5
        assert s.entry_price(bar(10.0), bar(10.5)) == 10.5

    def test_triggered_reason_returns_first_match(self):
        s = AnyBuy(LimitEntry(0.02), OpenPriceEntry())
        # LimitEntry 拒绝（跳开 5% > 2%），OpenPriceEntry 接受
        assert s.triggered_reason(*ARGS, bar(10.0, close=10.0), bar(10.5)) == "open_price"

    def test_empty_signals_raises(self):
        with pytest.raises(ValueError):
            AnyBuy()

    def test_entry_price_picks_triggered_subsignal_not_first(self):
        """回归用例：LimitEntry 拒绝高跳开，BreakoutEntry 接受时，应使用 BreakoutEntry 的定价。"""
        # LimitEntry(0.02) 在 5% 跳开下拒绝；BreakoutEntry 在 high 突破时接受
        signal = bar(10.0, close=10.0, high=11.0)
        next_b = bar(10.5, high=11.5)
        s = AnyBuy(LimitEntry(0.02), BreakoutEntry(tick=0.01))
        # 必须先调用 should_buy（与引擎调用顺序一致）
        assert s.should_buy(*ARGS, signal, next_b)
        # entry_price 应该来自 BreakoutEntry（11.01），而不是 LimitEntry.next_open（10.5）
        assert s.entry_price(signal, next_b) == pytest.approx(11.01)

    def test_entry_price_fallback_when_should_buy_not_called(self):
        """容错用例：若直接调用 entry_price 而未先调用 should_buy，应回退到首个有效定价。"""
        s = AnyBuy(OpenPriceEntry(), BreakoutEntry())
        # 不调用 should_buy，直接 entry_price
        price = s.entry_price(bar(10.0, high=11.0), bar(10.5, high=11.5))
        assert price == 10.5  # OpenPriceEntry 的价


# ── AllBuy ──────────────────────────────────────────────────────────────────

class TestAllBuy:
    def test_accepts_when_all_pass(self):
        s = AllBuy(primary=OpenPriceEntry(), filters=[SkipLimitUp()])
        assert s.should_buy(*ARGS, bar(10.0, close=10.0), bar(10.3))

    def test_rejects_when_primary_rejects(self):
        # primary LimitEntry 拒绝高跳开
        s = AllBuy(primary=LimitEntry(0.02), filters=[SkipLimitUp()])
        assert not s.should_buy(*ARGS, bar(10.0, close=10.0), bar(10.5))

    def test_rejects_when_filter_rejects(self):
        # primary 通过但 SkipLimitUp 拒绝涨停
        s = AllBuy(primary=OpenPriceEntry(), filters=[SkipLimitUp()])
        assert not s.should_buy(*ARGS, bar(10.0, close=10.0), bar(11.0))

    def test_entry_price_from_primary(self):
        # primary=BreakoutEntry → 用突破价定价
        s = AllBuy(primary=BreakoutEntry(tick=0.01), filters=[SkipLimitUp()])
        price = s.entry_price(bar(10.0, high=11.0), bar(10.5, high=11.5))
        assert price == pytest.approx(11.01)

    def test_filter_order_does_not_change_price(self):
        # 即使过滤器顺序不同，定价始终由 primary 决定
        s1 = AllBuy(primary=OpenPriceEntry(), filters=[SkipLimitUp(0.097), SkipLimitUp(0.05)])
        s2 = AllBuy(primary=OpenPriceEntry(), filters=[SkipLimitUp(0.05), SkipLimitUp(0.097)])
        assert s1.entry_price(bar(10.0), bar(10.3)) == s2.entry_price(bar(10.0), bar(10.3))

    def test_buyfilter_cannot_be_primary(self):
        with pytest.raises(TypeError, match="primary 不能是 BuyFilter"):
            AllBuy(primary=SkipLimitUp(), filters=[])

    def test_non_filter_cannot_be_in_filters(self):
        with pytest.raises(TypeError, match="filters 只接受 BuyFilter"):
            AllBuy(primary=OpenPriceEntry(), filters=[OpenPriceEntry()])  # type: ignore[list-item]

    def test_rejection_reason_identifies_primary(self):
        s = AllBuy(primary=LimitEntry(0.02), filters=[SkipLimitUp()])
        # 跳开 5% → primary LimitEntry 拒绝
        reason = s.rejection_reason(*ARGS, bar(10.0, close=10.0), bar(10.5))
        assert reason == "limit_buy"

    def test_rejection_reason_identifies_filter(self):
        s = AllBuy(primary=OpenPriceEntry(), filters=[SkipLimitUp()])
        # 涨停跳开 → SkipLimitUp 拒绝（primary 接受）
        reason = s.rejection_reason(*ARGS, bar(10.0, close=10.0), bar(11.0))
        assert reason == "limit_up_open"

    def test_triggered_reason_returns_primary_reason(self):
        s = AllBuy(primary=OpenPriceEntry(), filters=[SkipLimitUp()])
        assert s.triggered_reason(*ARGS, bar(10.0, close=10.0), bar(10.3)) == "open_price"

    def test_empty_filters_works(self):
        s = AllBuy(primary=OpenPriceEntry())
        assert s.should_buy(*ARGS, bar(10.0), bar(10.5))


# ── BuyFilter 抽象基类 ─────────────────────────────────────────────────────

class TestBuyFilter:
    def test_entry_price_always_none(self):
        # 自定义一个 BuyFilter 子类
        class AlwaysAcceptFilter(BuyFilter):
            reason = "always"
            def should_buy(self, *args, **kwargs):
                return True

        f = AlwaysAcceptFilter()
        assert f.entry_price(bar(10.0), bar(10.5)) is None
