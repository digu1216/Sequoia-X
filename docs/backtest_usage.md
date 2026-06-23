# 回测模块使用文档

> 适用版本：Sequoia-X V2 | 更新日期：2026-06-21

---

## 目录

1. [前置条件](#1-前置条件)
2. [快速开始](#2-快速开始)
3. [核心概念](#3-核心概念)
4. [Backtester 参数详解](#4-backtester-参数详解)
5. [BacktestConfig 配置说明](#5-backtestconfig-配置说明)
6. [TransactionCost 交易成本](#6-transactioncost-交易成本)
7. [卖出信号 SellSignal 完整参考](#7-卖出信号-sellsignal-完整参考)
8. [BacktestReport 报告解读](#8-backtestReport-报告解读)
9. [多策略对比](#9-多策略对比)
10. [自定义卖出信号](#10-自定义卖出信号)
11. [策略级卖出信号（进阶）](#11-策略级卖出信号进阶)
12. [常见问题](#12-常见问题)

---

## 1. 前置条件

回测依赖本地 SQLite 数据库中的历史 K 线数据。运行前请确认：

```bash
# 确认数据库已回填（约 12 分钟，~5200 只股票）
python main.py --backfill

# 验证数据量
python -c "
import sqlite3
with sqlite3.connect('data/sequoia_v2.db') as c:
    n = c.execute('SELECT COUNT(DISTINCT symbol) FROM stock_daily').fetchone()[0]
    print(f'本地股票数：{n}')
"
```

**注意**：回测区间必须在本地数据的时间范围之内，否则会抛出 `RuntimeError`。

---

## 2. 快速开始

### 最简示例（3 行核心代码）

```python
from sequoia_x.backtest import Backtester, AnySell, StopLoss, TakeProfit, HoldNDays
from sequoia_x.data.engine import DataEngine
from sequoia_x.core.config import get_settings
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy

engine = DataEngine(get_settings())

report = Backtester(
    data_engine=engine,
    strategy_cls=TurtleTradeStrategy,
    sell_signal=AnySell(
        StopLoss(pct=0.05),       # 跌 5% 止损
        TakeProfit(pct=0.15),     # 涨 15% 止盈
        HoldNDays(n=20),          # 最长持有 20 个交易日
    ),
    start="2024-01-01",
    end="2024-12-31",
).run()

report.print()
```

### 预期输出

```
====================================================
                    回 测 报 告
====================================================
  总收益率                        +23.47%
  年化收益率                      +23.47%
  年化波动率                      +18.32%
  下行波动率                       +9.14%
────────────────────────────────────────────────────
  最大回撤                        -12.35%
  最大回撤持续(日)                      47
  最大回撤恢复(日)                      23
  VaR (95%)                        -1.82%
  CVaR (95%)                       -2.94%
────────────────────────────────────────────────────
  夏普比率                          1.18
  索提诺比率                         2.36
  卡玛比率                           1.90
────────────────────────────────────────────────────
  总交易笔数                           87
  胜率                            54.02%
  平均盈利                         +8.73%
  平均亏损                         -4.21%
  盈亏比                             2.07
  期望值                           +2.79%
  最佳单笔                         +14.8%
  最差单笔                          -5.0%
  平均持仓天数                        11.3
  最大连胜                              6
  最大连败                              4
────────────────────────────────────────────────────
  平均仓位使用率                      80.00%
====================================================
```

---

## 3. 核心概念

### 回测时序模型

```
Day D 收盘后（数据截止 D）:
  ┌─────────────────────────────────┐
  │  策略.run() → [信号列表]         │
  └──────────────┬──────────────────┘
                 │ 次日开盘挂单
Day D+1 开盘:
  ┌─────────────────────────────────┐
  │  按开盘价成交，扣佣金+滑点        │
  └──────────────┬──────────────────┘
                 │ 持有中
Day D+1 收盘:
  ┌─────────────────────────────────┐
  │  检查卖出信号（止损/止盈/天数）   │
  │  更新持仓最高价（峰值追踪）       │
  │  记录当日净值                    │
  └─────────────────────────────────┘
```

**关键设计：**
- **T+1 规则**：当日开盘买入的仓位，当日收盘不检查卖出信号（`days_held == 0` 时跳过）
- **零未来函数**：策略在日期 D 只能看到 `date <= D` 的数据，由 `SlicedDataEngine` 自动保证
- **强制平仓**：回测结束日，所有持仓以当日收盘价强制平仓，原因标记为 `end_of_backtest`

---

## 4. Backtester 参数详解

```python
Backtester(
    data_engine:  DataEngine,           # 必填：数据引擎实例
    strategy_cls: type[BaseStrategy],   # 必填：策略类（传类本身，不是实例）
    sell_signal:  SellSignal | None,    # 可选：卖出信号（None 则用策略默认或 HoldNDays(10)）
    start:        str,                  # 必填："YYYY-MM-DD"，回测开始日期
    end:          str,                  # 必填："YYYY-MM-DD"，回测结束日期
    config:       BacktestConfig | None,# 可选：资金管理配置，默认见第 5 节
    settings:     Settings | None,      # 可选：项目配置，None 时自动从 .env 加载
)
```

**注意事项：**
- `strategy_cls` 传**类**，不是实例。回测引擎内部会用 `SlicedDataEngine` 代替真实引擎实例化策略。
- `start` / `end` 必须在本地数据范围内，建议至少有 3 个月的回测区间。
- `settings` 用于初始化策略，若策略不读取 `settings` 中的内容可留空（自动创建哑实例）。

---

## 5. BacktestConfig 配置说明

```python
from sequoia_x.backtest import BacktestConfig, TransactionCost

config = BacktestConfig(
    initial_capital = 1_000_000.0,  # 初始资金（元），默认 100 万
    max_positions   = 10,           # 最大并发持仓数，默认 10
    position_size   = 0.1,          # 单仓占总资产比例，默认 10%
    cost            = TransactionCost(),  # 交易成本，见第 6 节
    cash_interest   = 0.015,        # 闲置现金年化利率，默认 1.5%
    signal_priority = "first",      # 信号优先级，见下表
)
```

### signal_priority 优先级说明

当某天策略产生的信号数量超过可开仓数时，按此规则截断：

| 值 | 含义 | 适用场景 |
|---|---|---|
| `"first"` | 保留策略返回列表的前 N 个 | 策略自身已按强度排序时 |
| `"volume"` | 按当日成交额（turnover）降序，取前 N 个 | 偏好流动性好的股票 |
| `"momentum"` | 按当日涨幅降序，取前 N 个 | 追涨动量风格 |

### 单仓资金计算公式

```
单笔投入 = min(当日总资产 × position_size, 可用现金) - 预估佣金
```

**示例**：总资产 100 万，`position_size=0.1`，每仓约 10 万元。持有 5 仓时，
剩余可用资金仍可再开 5 仓（若现金充足）。

---

## 6. TransactionCost 交易成本

```python
from sequoia_x.backtest import TransactionCost

cost = TransactionCost(
    commission_rate = 0.0003,  # 佣金费率（万三），买卖双向，默认 0.03%
    min_commission  = 5.0,     # 最低佣金（元/笔），默认 5 元
    stamp_duty      = 0.0005,  # 印花税，仅卖出收取，默认 0.05%
    slippage        = 0.001,   # 滑点，默认 0.1%（模拟集合竞价偏差）
)
```

### 实际成本计算示意

以买入 **10 万元**、卖出 **11 万元** 为例：

```
买入摩擦 = max(100,000 × (0.0003 + 0.001), 5.0) = 130 元
实际买入 = 100,000 + 130 = 100,130 元（含成本均价略高于市价）

卖出摩擦 = max(110,000 × (0.0003 + 0.0005 + 0.001), 5.0) = 198 元
净收入   = 110,000 × (1 - 0.001) - 198 = 109,602 元

净盈亏   = 109,602 - 100,130 = 9,472 元（名义盈利 10,000 元，实际 9,472 元）
往返摩擦率 ≈ 0.53%（含双向佣金+印花税+滑点）
```

### 常见券商配置

```python
# 普通网络券商（万三）
cost = TransactionCost()  # 使用默认值

# 优惠佣金（万一点五）
cost = TransactionCost(commission_rate=0.00015, min_commission=5.0)

# 保守估计（含较大滑点，适合小盘股）
cost = TransactionCost(slippage=0.002)
```

---

## 7. 卖出信号 SellSignal 完整参考

### 7.1 内置信号一览

#### `HoldNDays(n)` — 固定持仓天数

```python
HoldNDays(n=10)   # 持仓满 10 个交易日后，以当日收盘价卖出
```

- `n=1`：最短持有，次日收盘即可卖出（T+1 最小单位）
- 触发原因：`"hold_n_days"`

---

#### `StopLoss(pct)` — 固定止损

```python
StopLoss(pct=0.05)   # 收盘价跌破买入均价 5% 时止损
```

- 以**全成本均价**（含佣金）为基准
- 触发条件：`close <= entry_price × (1 - pct)`
- 触发原因：`"stop_loss"`

---

#### `TakeProfit(pct)` — 固定止盈

```python
TakeProfit(pct=0.15)   # 收盘价超过买入均价 15% 时止盈
```

- 触发条件：`close >= entry_price × (1 + pct)`
- 触发原因：`"take_profit"`

---

#### `TrailingStop(pct)` — 移动止损（追踪止损）

```python
TrailingStop(pct=0.10)   # 从持仓期最高收盘价回撤超过 10% 时止损
```

- 追踪的是**持仓期间的历史最高收盘价**（`peak_close`），每日自动更新
- 触发条件：`close <= peak_close × (1 - pct)`
- 若尚无峰值记录，退化为以买入均价为基准
- 触发原因：`"trailing_stop"`

**适用场景**：趋势跟踪策略，允许盈利充分扩大，一旦回撤即锁定

---

#### `AnySell(*signals)` — OR 组合

```python
AnySell(StopLoss(0.05), TakeProfit(0.15), HoldNDays(20))
# 上述三个条件，任意一个满足即触发卖出
```

- 触发原因：返回**第一个触发**的子信号的原因
- 推荐的默认组合：止损兜底 + 止盈锁利 + 时间兜底

---

#### `AllSell(*signals)` — AND 组合

```python
AllSell(StopLoss(0.05), HoldNDays(3))
# 必须同时满足：亏损超 5% 且持仓至少 3 天，才卖出
```

- 触发原因：`"all_signals"`
- 适用场景：防止因单日大波动被过早止损（需满足时间条件再止损）

---

### 7.2 组合示例

```python
from sequoia_x.backtest import AnySell, AllSell, StopLoss, TakeProfit, HoldNDays, TrailingStop

# 经典三合一（推荐新手）
sell = AnySell(StopLoss(0.05), TakeProfit(0.15), HoldNDays(20))

# 趋势策略（移动止损 + 时间兜底）
sell = AnySell(TrailingStop(0.08), HoldNDays(30))

# 宽松止损 + 至少持有 3 天才能触发止损（防日内波动）
sell = AnySell(
    AllSell(StopLoss(0.07), HoldNDays(3)),
    TakeProfit(0.20),
    HoldNDays(25),
)
```

---

## 8. BacktestReport 报告解读

### 8.1 获取报告对象

```python
report = Backtester(...).run()
```

### 8.2 打印到控制台

```python
report.print()
```

### 8.3 获取指标字典

```python
s = report.summary()

# 核心指标
print(f"年化收益率: {s['annualized_return']:.2%}")
print(f"最大回撤:   {s['max_drawdown']:.2%}")
print(f"夏普比率:   {s['sharpe']:.2f}")
print(f"卡玛比率:   {s['calmar']:.2f}")
print(f"胜率:       {s['win_rate']:.2%}")
print(f"盈亏比:     {s['profit_factor']:.2f}")
```

### 8.4 完整指标字典键名

| 键名 | 类型 | 含义 |
|---|---|---|
| `total_return` | float | 总收益率 |
| `annualized_return` | float | 年化收益率 |
| `annual_volatility` | float | 年化波动率 |
| `downside_volatility` | float | 下行波动率 |
| `max_drawdown` | float | 最大回撤（负数） |
| `max_dd_duration_days` | int | 最大回撤持续天数 |
| `max_dd_recovery_days` | int | 最大回撤恢复天数（-1 表示未恢复） |
| `var_95` | float | 单日 VaR（95% 置信度） |
| `cvar_95` | float | 单日 CVaR（条件 VaR） |
| `sharpe` | float | 夏普比率（无风险利率 1.5%） |
| `sortino` | float | 索提诺比率 |
| `calmar` | float | 卡玛比率 |
| `information_ratio` | float\|None | 信息比率（需传入基准序列） |
| `total_trades` | int | 总交易笔数 |
| `win_rate` | float | 胜率 |
| `avg_win_pct` | float | 盈利交易平均收益率 |
| `avg_loss_pct` | float | 亏损交易平均亏损率（负数） |
| `profit_factor` | float | 盈亏比（inf 表示无亏损交易） |
| `expectancy` | float | 单笔期望收益率 |
| `best_trade_pct` | float | 最佳单笔收益率 |
| `worst_trade_pct` | float | 最差单笔收益率 |
| `avg_hold_days` | float | 平均持仓天数 |
| `max_consec_wins` | int | 最大连胜次数 |
| `max_consec_losses` | int | 最大连败次数 |
| `avg_position_ratio` | float | 平均仓位使用率（估算） |

### 8.5 导出交易明细

```python
df = report.to_dataframe()
print(df.head())
df.to_csv("backtest_trades.csv", index=False, encoding="utf-8-sig")
```

输出列：`symbol` / `entry_date` / `exit_date` / `entry_price` / `exit_price` /
`shares` / `pnl` / `pnl_pct` / `exit_reason` / `days_held`

### 8.6 访问资金曲线

```python
# equity_series 是 pandas.Series，index 为 DatetimeIndex
print(report.equity_series.tail())

# 绘制资金曲线（需安装 matplotlib）
import matplotlib.pyplot as plt
report.equity_series.plot(title="净值曲线")
plt.tight_layout()
plt.savefig("equity_curve.png", dpi=150)
```

### 8.7 按卖出原因统计

```python
df = report.to_dataframe()
print(df.groupby("exit_reason")["pnl_pct"].agg(["count", "mean"]))
```

示例输出：

```
                   count      mean
exit_reason
end_of_backtest        3  0.042100
hold_n_days           41 -0.012300
stop_loss             18 -0.048900
take_profit           25  0.149700
```

---

## 9. 多策略对比

```python
from sequoia_x.backtest import Backtester, AnySell, StopLoss, TakeProfit, HoldNDays
from sequoia_x.data.engine import DataEngine
from sequoia_x.core.config import get_settings
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy

engine = DataEngine(get_settings())

default_sell = AnySell(StopLoss(0.05), TakeProfit(0.15), HoldNDays(20))
period = {"start": "2024-01-01", "end": "2024-12-31"}

strategies = {
    "海龟突破": TurtleTradeStrategy,
    "均线金叉": MaVolumeStrategy,
    "RPS 突破": RpsBreakoutStrategy,
}

results = {}
for name, cls in strategies.items():
    report = Backtester(
        data_engine=engine,
        strategy_cls=cls,
        sell_signal=default_sell,
        **period,
    ).run()
    results[name] = report.summary()

# 对比表格
print(f"{'策略':<12} {'年化':<10} {'最大回撤':<10} {'夏普':<8} {'胜率':<8} {'笔数'}")
print("-" * 60)
for name, s in results.items():
    print(
        f"{name:<12} "
        f"{s['annualized_return']:>8.2%}  "
        f"{s['max_drawdown']:>8.2%}  "
        f"{s['sharpe']:>6.2f}  "
        f"{s['win_rate']:>6.2%}  "
        f"{s['total_trades']}"
    )
```

---

## 10. 自定义卖出信号

继承 `SellSignal` 并实现 `should_sell()` 即可：

```python
import pandas as pd
from sequoia_x.backtest import SellSignal

class MaCrossExit(SellSignal):
    """均线死叉卖出：5日均线下穿20日均线时平仓。"""

    reason = "ma_cross_exit"

    def __init__(self, fast: int = 5, slow: int = 20) -> None:
        self.fast = fast
        self.slow = slow

    def should_sell(
        self,
        symbol: str,
        entry_price: float,
        entry_date: str,
        current_bar: pd.Series,
        days_held: int,
        peak_close: float = 0.0,
    ) -> bool:
        # current_bar 包含当日的 OHLCV 数据
        # 注意：此处 current_bar 是单行 Series，不含历史序列
        # 若需要历史序列，需在 should_sell 外部预先计算并注入
        # 此示例仅为演示接口用法
        return False  # 替换为实际逻辑
```

**注意**：`current_bar` 是当日的单行 `pd.Series`（字段：`open/high/low/close/volume/turnover`），
不包含历史序列。如果卖出逻辑需要多日数据（如均线），建议将逻辑移入策略的 `sell_signal()` 方法，
或在回调前预先计算好指标值（参见第 11 节）。

### 与内置信号组合使用

```python
# 均线死叉 OR 持有 30 天兜底
from sequoia_x.backtest import AnySell, HoldNDays

sell = AnySell(MaCrossExit(), HoldNDays(30))
```

---

## 11. 策略级卖出信号（进阶）

如果某策略有专属的技术面卖出逻辑，可在策略类中覆盖 `sell_signal()` 方法，
使买卖信号同源、逻辑内聚：

```python
from sequoia_x.backtest import HoldNDays, SellSignal, AnySell, TakeProfit, StopLoss
from sequoia_x.strategy.base import BaseStrategy

class MyTrendStrategy(BaseStrategy):
    webhook_key = "my_trend"

    def run(self) -> list[str]:
        # ... 选股逻辑 ...
        return []

    def sell_signal(self) -> SellSignal:
        """策略专属卖出逻辑：止损 5% + 止盈 20% + 最长持有 15 天。"""
        return AnySell(
            StopLoss(pct=0.05),
            TakeProfit(pct=0.20),
            HoldNDays(n=15),
        )
```

回测时不传 `sell_signal` 参数，引擎自动调用策略的 `sell_signal()` 方法：

```python
report = Backtester(
    data_engine=engine,
    strategy_cls=MyTrendStrategy,
    # sell_signal 不传，自动使用策略定义的 sell_signal()
    start="2024-01-01",
    end="2024-12-31",
).run()
```

**优先级**：`Backtester(sell_signal=...)` 显式传入 > 策略的 `sell_signal()` 方法 > 默认 `HoldNDays(10)`

---

## 12. 常见问题

### Q1：回测抛出 `RuntimeError: 无本地历史数据`

**原因**：本地 SQLite 中没有回测区间内的数据。

**解决**：
```bash
python main.py --backfill   # 先回填数据
```

---

### Q2：回测区间太短，summary() 返回 `{"error": "数据不足"}`

**原因**：`equity_series` 少于 2 个交易日，无法计算收益率序列。

**解决**：将 `end` 推后，保证回测区间至少包含 5 个交易日。

---

### Q3：某些策略（如 TurtleTradeStrategy）在回测中执行很慢

**原因**：`TurtleTradeStrategy` 内部调用了 `baostock` 实时 API 获取流通市值（`_get_market_caps()`），
该调用在回测模式下会对每个候选股票发起网络请求，导致速度极慢。

**解决方案（推荐）**：子类化策略，覆盖 `_get_market_caps()` 返回空字典（跳过市值排序）：

```python
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy

class TurtleBacktestStrategy(TurtleTradeStrategy):
    def _get_market_caps(self, symbols):
        return {}   # 回测时跳过网络请求，保留原始顺序
```

然后用 `TurtleBacktestStrategy` 替代 `TurtleTradeStrategy` 进行回测。

---

### Q4：如何对比同一策略在不同卖出参数下的表现（参数网格搜索）

```python
from itertools import product

stop_loss_list = [0.03, 0.05, 0.07]
take_profit_list = [0.10, 0.15, 0.20]
hold_days_list = [10, 15, 20]

best = None
best_sharpe = -float("inf")

for sl, tp, hd in product(stop_loss_list, take_profit_list, hold_days_list):
    report = Backtester(
        data_engine=engine,
        strategy_cls=TurtleTradeStrategy,
        sell_signal=AnySell(StopLoss(sl), TakeProfit(tp), HoldNDays(hd)),
        start="2024-01-01",
        end="2024-12-31",
    ).run()
    s = report.summary()
    if isinstance(s.get("sharpe"), float) and s["sharpe"] > best_sharpe:
        best_sharpe = s["sharpe"]
        best = (sl, tp, hd, s)

print(f"最优参数：止损={best[0]}, 止盈={best[1]}, 持仓={best[2]} 天")
print(f"夏普比率：{best[3]['sharpe']:.2f}，年化：{best[3]['annualized_return']:.2%}")
```

**警告**：参数搜索存在过拟合风险，建议用样本外数据（不同时间段）验证最优参数。

---

### Q5：如何加入基准对比（如沪深300）

目前 `BacktestReport` 支持传入基准净值序列来计算信息比率：

```python
import pandas as pd

# 从本地或数据源获取沪深300日收益率（此处为示例）
hs300 = pd.Series(
    {...},  # {date_str: net_value}
    name="hs300"
)

# 初始化 report 后手动设置基准
report._benchmark = hs300
s = report.summary()
print(f"信息比率：{s['information_ratio']:.2f}")
```

---

### Q6：回测内存占用过大

**原因**：预加载全市场（~5200 只股票）若干年的历史数据占用约 1-2 GB RAM。

**解决**：缩短回测区间，或只对部分股票回测：

```python
# 方式一：缩短区间（建议至少 6 个月）
Backtester(..., start="2024-06-01", end="2024-12-31")

# 方式二（高级）：用子集策略只迭代特定股票
class SectorStrategy(BaseStrategy):
    UNIVERSE = ["000001", "600519", "000858", ...]   # 指定股票池

    def run(self) -> list[str]:
        return [s for s in self.UNIVERSE if ...]
```
