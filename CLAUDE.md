# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Sequoia-X V2 is an A-share (中国A股) quantitative stock screening system. It pulls post-adjusted K-line data for ~5200 stocks from baostock, runs configurable technical-analysis strategies, and pushes selected stocks to Feishu (飞书) group chats via webhook.

## Common Commands

```bash
# Install dependencies
uv sync

# First-time data backfill (~12 min, ~5200 stocks)
python main.py --backfill

# Daily run (incremental sync + all strategies + Feishu push)
python main.py

# Run tests
pytest
pytest -v
pytest tests/test_strategy.py   # single test file

# Lint and format
ruff check .
ruff format .
```

## Environment Setup

```bash
cp .env.example .env
# Set FEISHU_WEBHOOK_URL (required); all STRATEGY_WEBHOOK_* vars are optional
```

The `.env` file is gitignored. `DB_PATH` defaults to `data/sequoia_v2.db` (auto-created).

## Architecture

### Data Flow

```
baostock API → DataEngine (SQLite) → Strategy.run() → FeishuNotifier → webhook
```

**DataEngine** (`sequoia_x/data/engine.py`) manages the SQLite database (`stock_daily` table). Backfill mode is single-threaded with retry/reconnect; daily mode uses an 8-worker `multiprocessing.Pool`.

**Strategies** (`sequoia_x/strategy/`) each extend `BaseStrategy` and implement `run(df: DataFrame) -> list[str]`. They receive the full OHLCV history for a symbol and return a list of selected codes. All computation must be vectorized — no `iterrows()`.

**Notifier** (`sequoia_x/notify/feishu.py`) builds a Feishu interactive card and POSTs to the resolved webhook URL. Stock codes are linked to Xueqiu chart URLs (6xxx → SH prefix, others → SZ/BJ).

**Config** (`sequoia_x/core/config.py`) is a Pydantic-settings singleton. `STRATEGY_WEBHOOK_<KEY>` env vars are scanned at load time and stored as `strategy_webhooks` dict. `get_settings()` is the entry point.

### Webhook Routing

Each strategy class has a `webhook_key` attribute (e.g., `webhook_key = "turtle"`). This maps to the env var `STRATEGY_WEBHOOK_TURTLE`. Strategies without a specific key fall back to `FEISHU_WEBHOOK_URL`.

## Adding a New Strategy

1. Create `sequoia_x/strategy/my_strategy.py`, subclassing `BaseStrategy`.
2. Implement `run(self, df: pd.DataFrame) -> list[str]` using vectorized pandas.
3. Set `webhook_key = "my_strategy"` if a separate Feishu webhook is needed.
4. Register the strategy in `main.py` where the strategy list is built.
5. Optionally add `STRATEGY_WEBHOOK_MY_STRATEGY=...` to `.env.example` and `.env`.

## Testing Pattern

Tests use `pytest` + `hypothesis` for property-based testing and `pytest-mock` for mocking baostock/HTTP calls. Test files mirror the source structure under `tests/`.

## Key Constraints

- Python 3.10+ (uses structural pattern matching and `match` syntax in places).
- baostock requires `bs.login()` / `bs.logout()` per session; multiprocess workers each call login independently.
- All strategies must return `list[str]` of 6-digit stock codes (e.g., `'000001'`).
- `socket.setdefaulttimeout(10.0)` is set globally in `main.py`.
