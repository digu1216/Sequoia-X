# Sequoia-X V2 使用说明

> A 股量化选股系统，基于 baostock 免费数据，覆盖约 5200 只 A 股，每日收盘后自动选股并推送飞书通知。

---

## 目录

1. [环境要求](#环境要求)
2. [安装与配置](#安装与配置)
3. [运行方式](#运行方式)
4. [策略详解](#策略详解)
5. [飞书 Webhook 路由](#飞书-webhook-路由)
6. [数据库说明](#数据库说明)
7. [定时任务配置](#定时任务配置)
8. [新增策略](#新增策略)
9. [测试与代码检查](#测试与代码检查)
10. [常见问题](#常见问题)

---

## 环境要求

- Python **3.10 或以上**
- 推荐使用 [uv](https://github.com/astral-sh/uv) 管理依赖（也支持 pip）
- 飞书群聊并创建自定义机器人（获取 Webhook URL）

---

## 安装与配置

### 1. 安装依赖

```bash
# 推荐方式（uv）
uv sync

# 或使用 pip
pip install .
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，填写以下内容：

| 变量名 | 是否必填 | 默认值 | 说明 |
|---|---|---|---|
| `FEISHU_WEBHOOK_URL` | **必填** | — | 飞书默认机器人 Webhook，所有未单独配置的策略都使用此地址 |
| `DB_PATH` | 可选 | `data/sequoia_v2.db` | SQLite 数据库文件路径，不存在时自动创建 |
| `START_DATE` | 可选 | `2024-01-01` | 回填历史数据的起始日期 |
| `STRATEGY_WEBHOOK_TURTLE` | 可选 | — | 海龟突破策略专属 Webhook |
| `STRATEGY_WEBHOOK_MA_VOLUME` | 可选 | — | 均线放量策略专属 Webhook |
| `STRATEGY_WEBHOOK_FLAG` | 可选 | — | 高紧旗形策略专属 Webhook |
| `STRATEGY_WEBHOOK_SHAKEOUT` | 可选 | — | 涨停洗盘策略专属 Webhook |
| `STRATEGY_WEBHOOK_LIMIT_DOWN` | 可选 | — | 上升趋势跌停策略专属 Webhook |
| `STRATEGY_WEBHOOK_RPS` | 可选 | — | RPS 突破策略专属 Webhook |
| `STRATEGY_WEBHOOK_PRIVATE_PLACEMENT` | 可选 | — | 定增监控策略专属 Webhook |

`.env` 文件已加入 `.gitignore`，不会被提交到代码仓库。

### 3. 首次数据回填

首次运行前必须执行回填，拉取全市场历史 K 线数据（后复权）：

```bash
python main.py --backfill
```

- 耗时约 **12 分钟**，覆盖约 5200 只 A 股
- 单线程运行，每 200 只股票自动重连 baostock，每只最多重试 3 次（指数退避：2s / 4s / 8s）
- 中途中断后可重新执行，已完成的股票会自动跳过
- 完成后输出：`回填完成 — 成功: X | 跳过: Y | 失败: Z`

---

## 运行方式

### 每日运行（正常使用）

```bash
python main.py
```

执行流程：

1. 从 baostock 拉取今日 K 线数据（8 进程并行，约 2-3 分钟）
2. 依次运行 7 个策略
3. 有选股结果的策略，通过对应 Webhook 推送飞书通知

### 回填历史数据

```bash
python main.py --backfill
```

仅同步历史数据，不执行策略，不发飞书通知。

---

## 策略详解

系统内置 7 个策略，每日顺序执行，各自独立推送。

### 1. 均线放量策略（MaVolumeStrategy）

**Webhook Key：** `ma_volume`

选股条件（同时满足）：

- **金叉**：5 日均线从下方穿越 20 日均线（昨日 MA5 < MA20，今日 MA5 > MA20）
- **放量**：今日成交量 > 20 日平均成交量的 **1.5 倍**

适用场景：捕捉趋势初期均线共振突破机会。

---

### 2. 海龟突破策略（TurtleTradeStrategy）

**Webhook Key：** `turtle`

选股条件（同时满足）：

- **20 日新高突破**：今日收盘价 > 过去 20 日（不含今日）最高价
- **流动性**：今日成交额 > **1 亿元**
- **阳线**：今日收盘价 > 今日开盘价（实体阳线）
- **真实上涨**：今日收盘价 > 昨日收盘价

选出后按**流通市值**从大到小排序推送。

适用场景：趋势跟踪，捕捉有资金承接的价格突破。

---

### 3. 高紧旗形策略（HighTightFlagStrategy）

**Webhook Key：** `flag`

选股条件（同时满足）：

- **强势上涨**：过去 40 日最高价 / 最低价 > **1.6**（涨幅超过 60%）
- **极度收敛**：过去 10 日最高价 / 最低价 < **1.15**（区间小于 15%）
- **高位抗跌**：过去 10 日最低价 ≥ 过去 40 日最高价的 **80%**（不大幅回落）
- **缩量整理**：今日成交量 < 过去 20 日平均成交量的 **60%**

适用场景：识别强势股在高位的旗形整理形态，等待二次突破。

---

### 4. 涨停洗盘策略（LimitUpShakeoutStrategy）

**Webhook Key：** `shakeout`

选股条件（同时满足）：

- **昨日涨停**：昨收 ≥ 前日收盘 × **1.095**（约 10% 涨幅门槛）
- **今日收阴**：今收 < 今开（阴线，出现回调）
- **今日放量**：今日成交量 > 昨日成交量的 **2 倍**
- **支撑不破**：今日最低价 ≥ 昨日收盘价（关键支撑位未跌破）

适用场景：捕捉涨停次日高位换手洗盘后的持续上涨机会。

---

### 5. 上升趋势跌停策略（UptrendLimitDownStrategy）

**Webhook Key：** `limit_down`

选股条件（同时满足）：

- **上升趋势**：昨日 20 日均线 > 昨日 60 日均线（多头排列）
- **今日跌停**：今收 ≤ 昨收 × **0.905**（约 10% 跌幅门槛）
- **放量**：今日成交量 > 20 日平均成交量的 **2 倍**

适用场景：在趋势良好的股票中，捕捉非理性杀跌带来的超跌反弹机会。

---

### 6. RPS 相对强度突破策略（RpsBreakoutStrategy）

**Webhook Key：** `rps`

选股条件（两步筛选）：

**第一步 — 横向 RPS 排名：**
- 计算全市场所有股票过去 **120 日**的涨跌幅
- 按百分位排名，取 RPS ≥ **90**（前 10% 强势股）

**第二步 — 纵向突破：**
- 今日收盘价 ≥ 过去 120 日（最少 60 日有效）最高价的 **90%**

适用场景：基于欧奈尔相对强度理论，选出市场中最强势且接近历史高点的股票。

---

### 7. 定增监控策略（PrivatePlacementStrategy）

**Webhook Key：** `private_placement`

选股逻辑：

- 通过 akshare 抓取东方财富全部融资公告
- 筛选发行方式为**定向增发**的公告
- 仅保留**发行日期在最近 7 天**内的公告
- 去重后按发行日期**从新到旧**排序

数据来源：东方财富（akshare.stock_qbzf_em）

适用场景：监控近期定增完成的股票，关注资金解禁及机构进场动向。

---

## 飞书 Webhook 路由

每个策略有一个 `webhook_key`，系统按以下规则解析推送地址：

```
STRATEGY_WEBHOOK_{KEY} 存在 → 使用对应地址
否则 → 使用 FEISHU_WEBHOOK_URL（默认地址）
```

**完整映射关系：**

| 策略 | webhook_key | 对应环境变量 |
|---|---|---|
| 均线放量 | `ma_volume` | `STRATEGY_WEBHOOK_MA_VOLUME` |
| 海龟突破 | `turtle` | `STRATEGY_WEBHOOK_TURTLE` |
| 高紧旗形 | `flag` | `STRATEGY_WEBHOOK_FLAG` |
| 涨停洗盘 | `shakeout` | `STRATEGY_WEBHOOK_SHAKEOUT` |
| 上升趋势跌停 | `limit_down` | `STRATEGY_WEBHOOK_LIMIT_DOWN` |
| RPS 突破 | `rps` | `STRATEGY_WEBHOOK_RPS` |
| 定增监控 | `private_placement` | `STRATEGY_WEBHOOK_PRIVATE_PLACEMENT` |

未在 `.env` 中配置专属 Webhook 的策略，统一推送到 `FEISHU_WEBHOOK_URL`。

---

## 数据库说明

- 文件路径：`data/sequoia_v2.db`（首次运行自动创建，已加入 `.gitignore`）
- 数据表：`stock_daily`
- 字段：`symbol`（股票代码）、`date`（交易日）、`open`、`high`、`low`、`close`、`volume`（成交量）、`turnover`（成交额）
- 价格类型：**后复权**（adjustflag="1"），历史价格不因送股、配股而变动
- 索引：`(symbol, date)` 联合唯一索引，同一股票同一日期只保留一条记录

---

## 定时任务配置

在 Linux/macOS 服务器上使用 crontab 实现每日自动运行：

```bash
crontab -e
```

添加以下内容（每个交易日 19:15 执行）：

```cron
15 19 * * 1-5 cd /root/Sequoia-X && .venv/bin/python main.py >> log.txt 2>&1
```

- `1-5`：周一至周五
- `19:15`：A 股收盘（15:00）后运行，确保数据完整
- 日志输出到 `log.txt`（追加模式）

---

## 新增策略

1. 在 `sequoia_x/strategy/` 下新建 `my_strategy.py`，继承 `BaseStrategy`：

```python
from sequoia_x.strategy.base import BaseStrategy
import pandas as pd

class MyStrategy(BaseStrategy):
    webhook_key = "my_strategy"   # 对应 STRATEGY_WEBHOOK_MY_STRATEGY

    def run(self) -> list[str]:
        results = []
        for symbol in self.engine.get_local_symbols():
            df = self.engine.get_ohlcv(symbol)
            if len(df) < 20:
                continue
            # 向量化计算，禁止使用 iterrows()
            last = df.iloc[-1]
            if last["close"] > last["open"]:
                results.append(symbol)
        return results
```

2. 在 `main.py` 的策略列表中注册：

```python
strategies = [
    MaVolumeStrategy(engine, settings),
    # ... 其他策略 ...
    MyStrategy(engine, settings),   # 添加新策略
]
```

3. 可选：在 `.env.example` 和 `.env` 中添加：

```bash
STRATEGY_WEBHOOK_MY_STRATEGY=https://open.feishu.cn/open-apis/bot/v2/hook/...
```

**关键约束：**
- `run()` 必须返回 `list[str]`，元素为 6 位纯数字股票代码（如 `'000001'`）
- 所有计算必须使用 pandas 向量化操作，**不允许** `iterrows()` 或逐行循环

---

## 测试与代码检查

```bash
# 运行全部测试
pytest

# 详细输出
pytest -v

# 运行单个文件
pytest tests/test_strategy.py

# 代码风格检查
ruff check .

# 自动格式化
ruff format .
```

测试框架：`pytest` + `hypothesis`（属性测试）+ `pytest-mock`（模拟 baostock/HTTP 请求）

---

## 常见问题

**Q：回填时中途报错退出，需要重新从头开始吗？**
A：不需要。重新执行 `python main.py --backfill`，已同步到今日的股票会自动跳过。

**Q：飞书收不到消息怎么排查？**
A：查看终端日志，若提示 `HTTP 状态码非 200` 或 `code != 0`，检查 Webhook URL 是否正确。飞书机器人若设置了关键词过滤，确保推送内容包含该关键词。

**Q：某策略当日无选股结果，是否会发送消息？**
A：不会。只有 `run()` 返回非空列表时才调用飞书推送。

**Q：数据使用的是前复权还是后复权？**
A：后复权（adjustflag="1"）。历史价格保持不变，复权因子作用于最新价格，适合长期技术分析。

**Q：baostock 是否需要注册账号？**
A：不需要，baostock 完全免费，无需注册，程序自动调用 `bs.login()` / `bs.logout()`。
