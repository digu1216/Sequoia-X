"""回测资金管理配置：仓位参数与交易成本模型。"""

from dataclasses import dataclass, field


@dataclass
class TransactionCost:
    commission_rate: float = 0.0003  # 佣金费率，买卖双向
    min_commission: float = 5.0      # 最低佣金（元/笔）
    stamp_duty: float = 0.0005       # 印花税，仅卖出收取
    slippage: float = 0.001          # 滑点（模拟开盘集合竞价偏差）


@dataclass
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    max_positions: int = 10
    position_size: float = 0.1           # 单仓占总资产比例（0~1）
    cost: TransactionCost = field(default_factory=TransactionCost)
    cash_interest: float = 0.015         # 闲置现金年化利率
    signal_priority: str = "first"       # 信号超额时的优先级：first / volume / momentum
