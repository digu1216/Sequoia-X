# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此代码仓库中工作时提供指引。

## 项目概述

Sequoia-X V2 是一套 A 股量化选股系统。它通过 baostock 拉取约 5200 只股票的后复权 K 线数据，运行可配置的技术分析策略，并将选出的股票通过 webhook 推送到飞书群聊。

## 常用命令

```bash
# 安装依赖
uv sync

# 首次历史数据回填（约 12 分钟，约 5200 只股票）
python main.py --backfill

# 每日运行（增量同步 + 全部策略 + 飞书推送）
python main.py

# 运行测试
pytest
pytest -v
pytest tests/test_strategy.py   # 运行单个测试文件

# 代码检查与格式化
ruff check .
ruff format .
```

## 环境配置

```bash
cp .env.example .env
# 设置 FEISHU_WEBHOOK_URL（必填）；所有 STRATEGY_WEBHOOK_* 变量为可选
```

`.env` 文件已加入 `.gitignore`。`DB_PATH` 默认为 `data/sequoia_v2.db`（自动创建）。

## 系统架构

### 数据流

```
baostock API → DataEngine (SQLite) → Strategy.run() → FeishuNotifier → webhook
```

**DataEngine**（`sequoia_x/data/engine.py`）管理 SQLite 数据库（`stock_daily` 表）。回填模式为单线程并支持重试/重连；每日模式使用 8 个工作进程的 `multiprocessing.Pool`。

**策略**（`sequoia_x/strategy/`）均继承 `BaseStrategy`，实现 `run(df: DataFrame) -> list[str]`。每个策略接收某只股票的完整 OHLCV 历史数据，返回选中的股票代码列表。所有计算必须向量化——禁止使用 `iterrows()`。

**通知器**（`sequoia_x/notify/feishu.py`）构建飞书互动卡片并 POST 到对应的 webhook 地址。股票代码会转换为雪球行情链接（6 开头 → SH 前缀，其余 → SZ/BJ）。

**配置**（`sequoia_x/core/config.py`）是基于 Pydantic-settings 的单例。启动时扫描 `STRATEGY_WEBHOOK_<KEY>` 环境变量并存入 `strategy_webhooks` 字典，`get_settings()` 为统一入口。

### Webhook 路由

每个策略类有一个 `webhook_key` 属性（例如 `webhook_key = "turtle"`），对应环境变量 `STRATEGY_WEBHOOK_TURTLE`。未配置专属 key 的策略统一回退到 `FEISHU_WEBHOOK_URL`。

## 新增策略

1. 创建 `sequoia_x/strategy/my_strategy.py`，继承 `BaseStrategy`。
2. 用向量化 pandas 实现 `run(self, df: pd.DataFrame) -> list[str]`。
3. 如需独立飞书推送，设置 `webhook_key = "my_strategy"`。
4. 在 `main.py` 的策略列表中注册该策略。
5. 可选：在 `.env.example` 和 `.env` 中添加 `STRATEGY_WEBHOOK_MY_STRATEGY=...`。

## 测试规范

测试使用 `pytest` + `hypothesis`（基于属性的测试）以及 `pytest-mock`（模拟 baostock/HTTP 调用）。测试文件与源码目录结构对应，位于 `tests/` 下。

## 关键约束

- 需要 Python 3.10+（部分地方使用了结构化模式匹配 `match` 语法）。
- baostock 每次会话需调用 `bs.login()` / `bs.logout()`；多进程工作进程各自独立调用 login。
- 所有策略必须返回 `list[str]`，元素为 6 位股票代码（如 `'000001'`）。
- `socket.setdefaulttimeout(10.0)` 在 `main.py` 中全局设置。
