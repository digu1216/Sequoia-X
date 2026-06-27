# Sequoia-X 回测模块设计文档

> 版本：v0.2 | 日期：2026-06-27（v0.2 新增可插拔 BuySignal 协议）

---

## 1. 概述

### 1.1 目标

为 Sequoia-X 现有的选股策略体系增加历史回测能力，使每个策略的信号质量可量化评估。

### 1.2 核心模型

```
每个交易日 D（历史回放）：
  1. 策略收到截止 D 日的 OHLCV 数据 → 输出买入信号（股票列表）
  2. 信号入队 PendingBuy（携带 D 日 signal_bar）
  3. D+1 开盘后由 BuySignal 协议决定：是否成交 + 按什么价成交
       · 默认 OpenPriceEntry：次日开盘价直接买入（向后兼容）
       · 可自定义：LimitEntry 限价、BreakoutEntry 突破确认、SkipLimitUp 涨停过滤等
  4. 每日检查持仓是否触发卖出信号（止损/止盈/时间/自定义）
  5. 触发则以当日收盘价卖出
  6. 记录交易、更新净值；被过滤的买入信号落入 BacktestReport.skipped_signals
```

### 1.3 设计原则

- **零未来函数**：策略在日期 D 只能看到 `date <= D` 的数据
- **可插拔买入信号**：入场条件与价格逻辑解耦，可组合（v0.2 新增）
- **可插拔卖出信号**：卖出逻辑与买入策略解耦，可自由组合
- **A股真实成本**：内置佣金、印花税、滑点
- **与现有架构最小耦合**：复用 `DataEngine.get_ohlcv()`，不改动现有策略代码

---

## 2. 目录结构

```
sequoia_x/
└── backtest/
    ├── __init__.py
    ├── config.py          # BacktestConfig、TransactionCost 数据类
    ├── buy_signal.py      # BuySignal 抽象类及内置实现（v0.2 新增）
    ├── sell_signal.py     # SellSignal 抽象类及内置实现
    ├── portfolio.py       # Portfolio：持仓管理、交易记录
    ├── engine.py          # Backtester：主回测引擎
    └── report.py          # BacktestReport：指标计算与输出

docs/
└── backtest_design.md     # 本文档

tests/
└── test_backtest/
    ├── test_buy_signal.py
    ├── test_sell_signal.py
    ├── test_portfolio.py
    └── test_engine.py
```

---

## 3. 模块设计

### 3.1 BacktestConfig（`config.py`）

回测的全局参数配置，使用 `dataclass` 保持轻量。

```python
@dataclass
class TransactionCost:
    commission_rate: float = 0.0003   # 万三，买卖双向
    min_commission:  float = 5.0      # 最低佣金（元/笔）
    stamp_duty:      float = 0.0005   # 印花税，仅卖出收取
    slippage:        float = 0.001    # 滑点（开盘集合竞价偏差）

@dataclass
class BacktestConfig:
    initial_capital:  float = 1_000_000.0   # 初始资金（元）
    max_positions:    int   = 10             # 最大并发持仓数
    position_size:    float = 0.1            # 单仓占总资金比例（0~1）
    cost:             TransactionCost = field(default_factory=TransactionCost)
    cash_interest:    float = 0.015          # 闲置现金年化利率
    signal_priority:  str   = "first"        # 信号超额时的优先级：first / volume / momentum
```

**仓位金额计算**：

```
单笔投入 = min(总资金 × position_size, 可用现金)
```

当日信号超过可开仓数时，按 `signal_priority` 排序后截断：
- `first`：取列表前 N 个（策略自然排序）
- `volume`：优先选当日成交额最大的 N 个
- `momentum`：优先选当日涨幅最大的 N 个

---

### 3.2 SellSignal（`sell_signal.py`）

卖出信号是独立的可插拔协议，与策略解耦。

#### 抽象基类

```python
class SellSignal(ABC):
    @abstractmethod
    def should_sell(
        self,
        symbol:      str,
        entry_price: float,       # 买入均价（含成本）
        entry_date:  str,         # 买入日期 "YYYY-MM-DD"
        current_bar: pd.Series,   # 当日 OHLCV（含 date）
        days_held:   int,         # 已持仓交易日数
    ) -> bool: ...
```

#### 内置实现

| 类名 | 触发条件 | 关键参数 |
|---|---|---|
| `HoldNDays` | 持仓满 N 个交易日 | `n: int` |
| `StopLoss` | 当日收盘跌破买入价 × (1 - pct) | `pct: float` |
| `TakeProfit` | 当日收盘超过买入价 × (1 + pct) | `pct: float` |
| `TrailingStop` | 从持仓期最高收盘价回撤超过 pct | `pct: float` |
| `AnySell` | 多个信号满足任意一个（OR 组合） | `*signals` |
| `AllSell` | 多个信号同时满足（AND 组合） | `*signals` |

#### 典型组合示例

```python
# 止损 5%，止盈 15%，兜底持有 20 天
sell = AnySell(
    StopLoss(pct=0.05),
    TakeProfit(pct=0.15),
    HoldNDays(n=20),
)
```

#### 策略自定义卖出（可选扩展）

`BaseStrategy` 新增可选方法（**不影响现有策略**）：

```python
class BaseStrategy(ABC):
    def run(self) -> list[str]: ...           # 现有方法，不变

    def sell_signal(self) -> SellSignal:      # 新增，默认兜底
        return HoldNDays(n=10)
```

如果回测时未传入 `sell_signal`，则调用策略的 `sell_signal()` 方法。

---

### 3.2.5 BuySignal（`buy_signal.py`）— v0.2 新增

与 SellSignal 对称：将"如何入场"从引擎硬编码中分离，变成可插拔协议。
策略在 D 日产生候选股票后，BuySignal 决定 (a) 这笔信号是否真的成交、(b) 按什么价成交。

#### 抽象基类

```python
class BuySignal(ABC):
    reason: str = "buy_signal"

    @abstractmethod
    def should_buy(
        self,
        symbol:      str,
        signal_date: str,
        signal_bar:  pd.Series,   # D 日 OHLCV（信号产生当日）
        next_bar:    pd.Series,   # D+1 日 OHLCV（计划成交当日）
    ) -> bool: ...

    @abstractmethod
    def entry_price(
        self,
        signal_bar: pd.Series,
        next_bar:   pd.Series,
    ) -> float | None: ...        # None 表示放弃这笔
```

#### 内置实现

| 类名 | 含义 | 关键参数 |
|---|---|---|
| `OpenPriceEntry` | **默认**：次日开盘价直接买入 | — |
| `LimitEntry` | 次日开盘溢价 ≤ `max_premium_pct` 才买 | `max_premium_pct: float = 0.03` |
| `BreakoutEntry` | 次日 high > D 日 high 才入场，按 `signal_high + tick` 成交 | `tick: float = 0.01` |
| `SkipLimitUp` | **过滤器**：次日开盘涨幅 ≥ threshold 时拒绝 | `threshold: float = 0.097` |
| `AnyBuy(*signals)` | OR 组合，first-match-wins 定价 | 子信号列表 |
| `AllBuy(primary, filters)` | AND 组合，**显式 primary 定价 + filters 仅过滤** | `primary`, `filters` |

#### BuyFilter 抽象子类

```python
class BuyFilter(BuySignal):
    """过滤器基类：entry_price 强制返回 None，确保不能用作 AllBuy 的 primary。"""
    def entry_price(self, signal_bar, next_bar) -> None: ...
```

`SkipLimitUp` 是 `BuyFilter` 的内置实现；用户自定义过滤器只需继承 `BuyFilter` 并实现 `should_buy()`。

#### 典型组合示例

```python
# 突破确认 + 涨停过滤 + 限价兜底
buy = AllBuy(
    primary=BreakoutEntry(tick=0.01),
    filters=[SkipLimitUp(), LimitEntry(max_premium_pct=0.05)],
)
```

**注意**：`LimitEntry` 不是 `BuyFilter` 子类，因为它本身可以独立用作 primary（提供开盘价）；
若希望它仅作为过滤器使用，需用 `AllBuy(primary=OpenPriceEntry(), filters=[...])` 这样的结构。

#### 策略自定义入场（可选扩展）

`BaseStrategy` 可以选择性覆盖 `buy_signal()` 方法：

```python
class BaseStrategy(ABC):
    def run(self) -> list[str]: ...           # 现有方法，不变

    def buy_signal(self) -> BuySignal:        # 可选，默认 OpenPriceEntry
        return OpenPriceEntry()
```

**优先级链**：`Backtester(buy_signal=...)` 显式传入 > `strategy.buy_signal()` 方法 > 默认 `OpenPriceEntry()`

#### 拒绝原因与诊断

被 BuySignal 拒绝的信号会落入 `BacktestReport.skipped_signals`，可通过：

- `report.skipped_summary()` — 按原因分组的 DataFrame
- `report.signal_fill_rate` — 信号成交率（已成交/已成交+被过滤）
- `report.summary()["skipped_count"]` / `["signal_fill_rate"]`

`AllBuy` 的 `rejection_reason()` 会精确定位到具体拒绝的子信号（primary 还是某个 filter），
便于回答"我的信号为什么没成交"。

---

### 3.3 Portfolio（`portfolio.py`）

维护持仓状态与交易记录，是回测引擎的账本。

#### 数据结构

```python
@dataclass
class Position:
    symbol:       str
    entry_date:   str
    entry_price:  float     # 含买入成本的均价
    shares:       float     # 持仓股数
    cost_basis:   float     # 买入总成本（含佣金）
    peak_close:   float     # 持仓期间最高收盘价（供 TrailingStop 使用）
    days_held:    int = 0

@dataclass
class Trade:
    symbol:       str
    entry_date:   str
    exit_date:    str
    entry_price:  float
    exit_price:   float
    shares:       float
    pnl:          float     # 净盈亏（元）
    pnl_pct:      float     # 净收益率
    exit_reason:  str       # "stop_loss" / "take_profit" / "hold_n_days" / "end_of_backtest"
```

#### 核心方法

```python
class Portfolio:
    def open(self, symbol, date, price, cash_available, config) -> float
        # 开仓，返回实际消耗现金，自动计算买入佣金+滑点

    def close(self, symbol, date, price, reason) -> Trade
        # 平仓，计算净盈亏（扣除印花税+佣金+滑点），返回 Trade 记录

    def update_peaks(self, date, prices: dict[str, float]) -> None
        # 每日收盘后更新各持仓的 peak_close

    @property
    def equity(self) -> float
        # 当日总资产 = 现金 + 持仓市值（按最新收盘价）

    @property
    def positions_count(self) -> int
    
    @property
    def cash(self) -> float
```

**交易成本计算**：

```
买入成本 = 成交金额 × (commission_rate + slippage)
         但不低于 min_commission

卖出成本 = 成交金额 × (commission_rate + stamp_duty + slippage)
         但不低于 min_commission

entry_price（含成本均价）= (成交金额 + 买入成本) / 股数
```

---

### 3.4 Backtester（`engine.py`）

主回测引擎，按时间轴驱动整个回放过程。

#### 接口

```python
class Backtester:
    def __init__(
        self,
        data_engine:  DataEngine,
        strategy_cls: type[BaseStrategy],    # 策略类（非实例）
        buy_signal:   BuySignal | None,      # v0.2 新增；None 则用 OpenPriceEntry
        sell_signal:  SellSignal | None,     # None 则使用策略默认
        start:        str,                   # "YYYY-MM-DD"
        end:          str,
        config:       BacktestConfig = BacktestConfig(),
    ): ...

    def run(self) -> BacktestReport: ...
```

#### 核心回放循环（伪代码）

```python
def run(self) -> BacktestReport:
    # 1. 预加载所有股票的完整历史（仅读一次，提升性能）
    all_data: dict[str, pd.DataFrame] = self._preload()
    
    # 2. 构造回测专用策略实例（注入 SlicedDataEngine）
    sliced_engine = SlicedDataEngine(all_data, as_of=None)
    strategy = self.strategy_cls(sliced_engine, settings)
    
    # 3. 按交易日回放
    for date in self._trading_days:
        sliced_engine.as_of = date           # 切换数据视角到当日

        # ── 开盘阶段：执行 pending_buys（经 BuySignal 协议筛选）──
        self._execute_pending_buys(pending_buys, portfolio, all_data,
                                    date, buy_signal, skipped_signals)

        # ── 收盘阶段：检查卖出信号 ──
        current_prices = self._get_close_prices(date)
        portfolio.update_peaks(date, current_prices)

        for symbol, pos in list(portfolio.positions.items()):
            bar = all_data[symbol].loc[date]
            if sell_signal.should_sell(symbol, pos.entry_price,
                                       pos.entry_date, bar, pos.days_held):
                trade = portfolio.close(symbol, date, bar["close"], reason=...)
                trades.append(trade)

        # ── 信号阶段：生成次日买入信号 ──
        signals = strategy.run()             # 策略只能看 date 及之前的数据
        # _filter_signals 返回 list[PendingBuy]（携带 D 日 signal_bar）
        pending_buys = self._filter_signals(signals, portfolio, all_data, date)

        # ── 记录当日净值快照 ──
        equity_curve.append((date, portfolio.equity))

    return BacktestReport(trades, equity_curve, config)
```

#### SlicedDataEngine（防未来函数的关键）

```python
class SlicedDataEngine:
    """包装 DataEngine，使 get_ohlcv() 只返回 as_of 日期及之前的数据。"""

    def __init__(self, all_data: dict[str, pd.DataFrame], as_of: str): ...

    def get_ohlcv(self, symbol: str) -> pd.DataFrame:
        df = self._all_data[symbol]
        return df[df["date"] <= self.as_of].copy()   # 严格切片

    def get_local_symbols(self) -> list[str]:
        return list(self._all_data.keys())
```

策略实例收到的 `engine` 是 `SlicedDataEngine`，完全感知不到未来数据的存在，无需改动任何策略代码。

---

### 3.5 BacktestReport（`report.py`）

接收 `trades` 列表和 `equity_curve`，计算并输出所有指标。

```python
class BacktestReport:
    def __init__(
        self,
        trades:          list[Trade],
        equity_curve:    list[tuple[str, float]],         # [(date, equity), ...]
        config:          BacktestConfig,
        benchmark:       pd.Series | None = None,         # 基准净值序列（如 HS300）
        skipped_signals: list[SkippedSignal] | None = None,  # v0.2 新增
    ): ...

    def summary(self) -> dict                    # 返回核心指标字典
    def print(self) -> None                      # 格式化打印到控制台
    def to_dataframe(self) -> pd.DataFrame       # 逐笔交易明细
    def skipped_summary(self) -> pd.DataFrame    # 按 reason 分组的过滤诊断（v0.2）

    @property
    def signal_fill_rate(self) -> float          # 信号成交率（v0.2）
```

---

## 4. 指标体系

### 4.1 收益类

| 指标 | 计算方式 |
|---|---|
| 总收益率 | `(final_equity - initial_capital) / initial_capital` |
| 年化收益率 | `(1 + total_return) ^ (252 / trading_days) - 1` |
| 基准收益率 | 同期沪深300净值涨跌幅 |
| 超额收益（Alpha） | `annualized_return - benchmark_return` |
| 月度收益分布 | 按自然月分组的收益率序列 |

### 4.2 风险类

| 指标 | 计算方式 |
|---|---|
| 年化波动率 | `daily_returns.std() × √252` |
| 下行波动率 | `daily_returns[daily_returns < 0].std() × √252` |
| 最大回撤（MDD） | `max((peak - trough) / peak)` over equity curve |
| 最大回撤持续天数 | 从峰值到谷底的交易日数 |
| 最大回撤恢复天数 | 从谷底回升至创新高的交易日数 |
| VaR（95%） | `daily_returns.quantile(0.05)` |
| CVaR（95%） | `daily_returns[daily_returns <= VaR].mean()` |

### 4.3 风险调整收益类

| 指标 | 计算方式 | 备注 |
|---|---|---|
| 夏普比率 | `(annualized_return - risk_free) / annual_vol` | 无风险利率默认 1.5% |
| 索提诺比率 | `(annualized_return - risk_free) / downside_vol` | 只惩罚下行波动 |
| 卡玛比率 | `annualized_return / abs(max_drawdown)` | 越高越好 |
| 信息比率 | `alpha / tracking_error` | 需要基准序列 |

### 4.4 交易统计类

| 指标 | 计算方式 |
|---|---|
| 总交易笔数 | `len(trades)` |
| 胜率 | `len([t for t in trades if t.pnl > 0]) / total_trades` |
| 平均盈利 | `mean(t.pnl_pct for t in trades if t.pnl > 0)` |
| 平均亏损 | `mean(t.pnl_pct for t in trades if t.pnl < 0)` |
| 盈亏比 | `abs(avg_win / avg_loss)` |
| 期望值 | `win_rate × avg_win - (1 - win_rate) × abs(avg_loss)` |
| 最大连胜 | 连续盈利的最长序列长度 |
| 最大连败 | 连续亏损的最长序列长度 |
| 最佳单笔 | `max(t.pnl_pct for t in trades)` |
| 最差单笔 | `min(t.pnl_pct for t in trades)` |
| 平均持仓天数 | `mean(t.days_held for t in trades)` |

### 4.5 信号与持仓类

| 指标 | 说明 | 字段名 |
|---|---|---|
| 平均每日信号数 | 策略每天平均产生多少买入信号 | — |
| **信号成交率** | 已成交笔数 / (已成交 + 被过滤) | `signal_fill_rate` |
| **被过滤信号数** | BuySignal 协议拒绝 + 仓位/现金等引擎兜底过滤的总数 | `skipped_count` |
| 最大并发持仓 | 任意时刻的最大同时持仓数 | — |
| 平均仓位使用率 | 持仓市值 / 总资产 的均值 |

---

## 5. 资金管理详述

### 5.1 开仓决策流程

```
[D 日收盘后]
当日信号 [S1, S2, ..., Sn]
    ↓
过滤已持仓股票
    ↓
检查并发持仓上限：可开仓数 = max_positions - current_positions
    ↓
超额时按 signal_priority 排序，取前 N 个
    ↓
封装为 PendingBuy（携带 D 日 signal_bar）
    ↓
─────────── 跨日 ───────────
    ↓
[D+1 日开盘]
逐个 PendingBuy 走 BuySignal 协议：
  · buy_signal.should_buy(symbol, signal_date, signal_bar, next_bar) → 是否成交？
  · buy_signal.entry_price(signal_bar, next_bar)                     → 按什么价？
  · 被拒绝的进入 skipped_signals（供报告诊断）
    ↓
逐一检查现金是否充足（可用现金 >= position_size × total_equity）
    ↓
Portfolio.open() 内部加滑点 + 佣金成交
```

### 5.2 T+1 约束

买入当日不计入可卖出持仓（A 股 T+1 规则）。`days_held` 从 0 开始，`HoldNDays(n=1)` 意味着次日收盘即可卖出。

### 5.3 闲置现金计息

每日收盘后，对未使用的现金按日折算年化利率：

```python
daily_rate = (1 + cash_interest) ** (1 / 252) - 1
cash += cash × daily_rate
```

### 5.4 交易成本汇总

```
单笔买入摩擦 = 成交金额 × (commission_rate + slippage)
              max(以上, min_commission)

单笔卖出摩擦 = 成交金额 × (commission_rate + stamp_duty + slippage)
              max(以上, min_commission)

往返摩擦合计 ≈ 成交金额 × 0.0022（默认参数下）
```

---

## 6. 使用示例

### 6.1 最简回测

```python
from sequoia_x.backtest import Backtester, BacktestConfig
from sequoia_x.backtest.sell_signal import AnySell, StopLoss, TakeProfit, HoldNDays
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
from sequoia_x.data.engine import DataEngine
from sequoia_x.core.config import get_settings

engine = DataEngine(get_settings())

report = Backtester(
    data_engine=engine,
    strategy_cls=TurtleTradeStrategy,
    sell_signal=AnySell(
        StopLoss(pct=0.05),
        TakeProfit(pct=0.15),
        HoldNDays(n=20),
    ),
    start="2023-01-01",
    end="2024-12-31",
).run()

report.print()
```

### 6.2 自定义配置

```python
from sequoia_x.backtest.config import BacktestConfig, TransactionCost

config = BacktestConfig(
    initial_capital=500_000,
    max_positions=5,
    position_size=0.2,
    signal_priority="volume",
    cost=TransactionCost(
        commission_rate=0.00025,   # 优惠佣金
        slippage=0.002,            # 小盘股滑点更大
    ),
)
```

### 6.3 对比多个策略

```python
strategies = [TurtleTradeStrategy, MaVolumeStrategy, RpsBreakoutStrategy]
reports = {}

for cls in strategies:
    reports[cls.__name__] = Backtester(
        data_engine=engine,
        strategy_cls=cls,
        sell_signal=default_sell,
        start="2023-01-01",
        end="2024-12-31",
    ).run()

# 对比核心指标
for name, r in reports.items():
    s = r.summary()
    print(f"{name}: 年化={s['annualized_return']:.1%}, 夏普={s['sharpe']:.2f}, MDD={s['max_drawdown']:.1%}")
```

### 6.4 自定义买入信号（v0.2）

```python
from sequoia_x.backtest import (
    AllBuy, BreakoutEntry, SkipLimitUp, LimitEntry, OpenPriceEntry,
)

# 突破确认 + 涨停过滤
report = Backtester(
    data_engine=engine,
    strategy_cls=TurtleTradeStrategy,
    buy_signal=AllBuy(
        primary=BreakoutEntry(tick=0.01),
        filters=[SkipLimitUp()],
    ),
    sell_signal=AnySell(StopLoss(0.05), HoldNDays(20)),
    start="2024-01-01", end="2024-12-31",
).run()

print(f"信号成交率: {report.signal_fill_rate:.2%}")
print(report.skipped_summary())
```

---

## 7. 扩展点

| 扩展场景 | 实现方式 |
|---|---|
| 新卖出信号 | 继承 `SellSignal`，实现 `should_sell()` |
| 新买入信号（出价型） | 继承 `BuySignal`，实现 `should_buy()` + `entry_price()` |
| 新买入过滤器（仅过滤） | 继承 `BuyFilter`，只需实现 `should_buy()`（`entry_price` 自动为 None）|
| 策略专属买入/卖出逻辑 | 在策略类中覆盖 `buy_signal()` / `sell_signal()` 方法 |
| 对比基准（沪深300） | 向 `BacktestReport` 传入 `benchmark` 序列 |
| 分行业 / 分市值统计 | 在 `BacktestReport` 中扩展 `breakdown()` 方法 |
| 参数扫描（网格搜索） | 在 `BacktestConfig` 上循环，复用 `Backtester` |
| CLI 入口 | `main.py` 新增 `--backtest` 参数 |

---

## 8. 关键约束与限制

1. **数据依赖**：回测依赖本地 SQLite 历史数据，需先完成 `--backfill`
2. **涨停过滤**：内置 `SkipLimitUp` 过滤器可以拒绝涨停跳开的信号；默认未启用，需用户显式组合（v0.2 起）
3. **复权方式**：使用后复权价格（`adjustflag="1"`），回测收益率已隐含除权影响
4. **并发安全**：`SlicedDataEngine` 是只读的，多策略并行回测时可共享 `all_data` 字典
5. **内存**：预加载 ~5200 只股票的完整历史约占 1-2 GB RAM，可按需改为懒加载
6. **未成交信号不滚动**：BuySignal 拒绝的 PendingBuy 直接丢弃，不会顺延到下一日（与改造前隐式行为一致）。
   未来若需要"挂单等待"语义，可引入 `OrderQueue` 跨日机制

---

*本文档随实现迭代更新。*
