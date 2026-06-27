"""回测模块：历史策略回放与绩效评估。"""

from .buy_signal import (
    AllBuy,
    AnyBuy,
    BreakoutEntry,
    BuyFilter,
    BuySignal,
    LimitEntry,
    OpenPriceEntry,
    PendingBuy,
    SkipLimitUp,
    SkippedSignal,
)
from .config import BacktestConfig, TransactionCost
from .engine import Backtester, SlicedDataEngine
from .portfolio import Portfolio, Position, Trade
from .report import BacktestReport
from .sell_signal import AllSell, AnySell, HoldNDays, SellSignal, StopLoss, TakeProfit, TrailingStop

__all__ = [
    # config
    "BacktestConfig",
    "TransactionCost",
    # engine
    "Backtester",
    "SlicedDataEngine",
    # portfolio
    "Portfolio",
    "Position",
    "Trade",
    # report
    "BacktestReport",
    # buy signals
    "BuySignal",
    "OpenPriceEntry",
    "LimitEntry",
    "BreakoutEntry",
    "BuyFilter",
    "SkipLimitUp",
    "AnyBuy",
    "AllBuy",
    "PendingBuy",
    "SkippedSignal",
    # sell signals
    "SellSignal",
    "HoldNDays",
    "StopLoss",
    "TakeProfit",
    "TrailingStop",
    "AnySell",
    "AllSell",
]
