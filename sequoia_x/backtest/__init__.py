"""回测模块：历史策略回放与绩效评估。"""

from .config import BacktestConfig, TransactionCost
from .engine import Backtester, SlicedDataEngine
from .portfolio import Portfolio, Position, Trade
from .report import BacktestReport
from .sell_signal import AllSell, AnySell, HoldNDays, SellSignal, StopLoss, TakeProfit, TrailingStop

__all__ = [
    "BacktestConfig",
    "TransactionCost",
    "Backtester",
    "SlicedDataEngine",
    "Portfolio",
    "Position",
    "Trade",
    "BacktestReport",
    "SellSignal",
    "HoldNDays",
    "StopLoss",
    "TakeProfit",
    "TrailingStop",
    "AnySell",
    "AllSell",
]
