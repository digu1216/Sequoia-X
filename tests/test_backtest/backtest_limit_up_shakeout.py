"""涨停洗盘策略回测脚本（可直接运行）。

回测对象：sequoia_x.strategy.limit_up_shakeout.LimitUpShakeoutStrategy
回测区间：2025-06-01 → 2025-12-31
卖出逻辑：AnySell(StopLoss 5% / TakeProfit 12% / HoldNDays 5)
其它配置：均采用回测引擎默认值（100 万本金、10 仓、单仓 10%、万三佣金等）

运行前提：本地 SQLite 已含 2025 年 6-12 月（含 120 日缓冲）的 K 线数据。
    python main.py --backfill

运行方式：
    python tests/test_backtest/backtest_limit_up_shakeout.py

产物（写在本脚本同目录）：
    limit_up_shakeout_trades.csv   逐笔交易明细
    limit_up_shakeout_equity.png   净值曲线（matplotlib 缺失时自动跳过）
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许从任意位置直接运行：将项目根目录加入 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sequoia_x.backtest import (
    AnySell,
    Backtester,
    HoldNDays,
    StopLoss,
    TakeProfit,
)
from sequoia_x.core.config import get_settings
from sequoia_x.data.engine import DataEngine

# ── 回测参数 ──────────────────────────────────────────────────────────────────
START = "2025-06-01"
END = "2025-12-31"

# 卖出信号：止损 5% / 止盈 12% / 最多持有 5 个交易日，任一触发即平仓
SELL_SIGNAL = AnySell(
    StopLoss(pct=0.05),
    TakeProfit(pct=0.12),
    HoldNDays(n=5),
)

# 输出产物目录（与本脚本同目录）
OUTPUT_DIR = Path(__file__).resolve().parent
TRADES_CSV = OUTPUT_DIR / "limit_up_shakeout_trades.csv"
EQUITY_PNG = OUTPUT_DIR / "limit_up_shakeout_equity.png"


def _export_trades(report) -> None:
    """导出逐笔交易明细到 CSV。"""
    df = report.to_dataframe()
    if df.empty:
        print("\n[交易明细] 无成交，跳过 CSV 导出。")
        return
    df.to_csv(TRADES_CSV, index=False, encoding="utf-8-sig")
    print(f"\n[交易明细] 已导出 {len(df)} 笔交易 → {TRADES_CSV}")


def _print_exit_reason_stats(report) -> None:
    """按卖出原因分组统计笔数与平均收益率。"""
    df = report.to_dataframe()
    if df.empty:
        return
    grouped = df.groupby("exit_reason")["pnl_pct"].agg(["count", "mean"])
    print("\n按卖出原因分组：")
    print(f"  {'exit_reason':<18}{'count':>8}{'mean':>12}")
    print("  " + "-" * 38)
    for reason, row in grouped.iterrows():
        print(f"  {reason:<18}{int(row['count']):>8}{row['mean']:>11.2%}")


def _plot_equity(report) -> None:
    """绘制净值曲线并保存为 PNG（matplotlib 缺失时优雅跳过）。"""
    if report.equity_series.empty:
        print("\n[净值曲线] 净值序列为空，跳过绘图。")
        return
    try:
        import matplotlib

        matplotlib.use("Agg")  # 无显示环境也能保存
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n[净值曲线] 未安装 matplotlib，跳过绘图（pip install matplotlib）。")
        return

    ax = report.equity_series.plot(
        title=f"涨停洗盘策略净值曲线 {START} → {END}",
        figsize=(10, 5),
    )
    ax.set_xlabel("日期")
    ax.set_ylabel("净值（元）")
    ax.grid(True, alpha=0.3)
    fig = ax.get_figure()
    fig.tight_layout()
    fig.savefig(EQUITY_PNG, dpi=150)
    plt.close(fig)
    print(f"\n[净值曲线] 已保存 → {EQUITY_PNG}")


def main() -> None:
    settings = get_settings()
    engine = DataEngine(settings)

    # 延迟导入策略，保证依赖链清晰
    from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy

    report = Backtester(
        data_engine=engine,
        strategy_cls=LimitUpShakeoutStrategy,
        sell_signal=SELL_SIGNAL,
        start=START,
        end=END,
        # config / settings 均采用默认设置
        settings=settings,
    ).run()

    # ── 输出 ──────────────────────────────────────────────────────────────────
    report.print()
    _export_trades(report)
    _print_exit_reason_stats(report)
    _plot_equity(report)


if __name__ == "__main__":
    main()
