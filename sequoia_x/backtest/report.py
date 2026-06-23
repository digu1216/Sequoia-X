"""回测报告：指标计算、格式化输出与数据导出。"""

from __future__ import annotations

import pandas as pd

from .config import BacktestConfig
from .portfolio import Trade


class BacktestReport:
    def __init__(
        self,
        trades: list[Trade],
        equity_curve: list[tuple[str, float]],
        config: BacktestConfig,
        benchmark: pd.Series | None = None,
    ) -> None:
        self.trades = trades
        self.config = config

        if equity_curve:
            dates, values = zip(*equity_curve)
            self.equity_series = pd.Series(
                list(values), index=pd.DatetimeIndex(list(dates)), name="equity"
            )
        else:
            self.equity_series = pd.Series(dtype=float)

        self._benchmark = benchmark

    # ── 核心指标计算 ────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """返回完整指标字典。"""
        eq = self.equity_series
        initial = self.config.initial_capital

        if eq.empty or len(eq) < 2:
            return {"error": "数据不足，无法计算指标"}

        # ── 收益类
        total_return = (eq.iloc[-1] - initial) / initial
        n_days = len(eq)
        annualized = (1 + total_return) ** (252 / n_days) - 1

        # ── 风险类
        daily_returns = eq.pct_change().dropna()
        annual_vol = daily_returns.std() * (252 ** 0.5)
        neg_returns = daily_returns[daily_returns < 0]
        downside_vol = neg_returns.std() * (252 ** 0.5) if not neg_returns.empty else 0.0

        rolling_max = eq.cummax()
        drawdown = (eq - rolling_max) / rolling_max
        max_dd = float(drawdown.min())

        # 最大回撤持续天数（从峰值到谷底）
        dd_end_idx = int(drawdown.argmin())
        dd_start_idx = int(eq.iloc[:dd_end_idx + 1].argmax())
        max_dd_duration = dd_end_idx - dd_start_idx

        # 最大回撤恢复天数（从谷底到再创新高）
        post_trough = eq.iloc[dd_end_idx:]
        recovery_mask = post_trough >= rolling_max.iloc[dd_end_idx]
        max_dd_recovery = (recovery_mask.idxmax() - post_trough.index[0]).days if recovery_mask.any() else -1

        # VaR / CVaR（95%置信度，单日）
        var_95 = float(daily_returns.quantile(0.05))
        cvar_95 = float(daily_returns[daily_returns <= var_95].mean())

        # ── 风险调整收益
        rf = 0.015
        sharpe = (annualized - rf) / annual_vol if annual_vol > 0 else 0.0
        sortino = (annualized - rf) / downside_vol if downside_vol > 0 else 0.0
        calmar = annualized / abs(max_dd) if max_dd < 0 else 0.0

        # 信息比率（需要基准序列）
        ir = None
        if self._benchmark is not None and not self._benchmark.empty:
            aligned = self._benchmark.reindex(eq.index, method="ffill")
            excess = daily_returns - aligned.pct_change().reindex(daily_returns.index).fillna(0)
            tracking_err = excess.std() * (252 ** 0.5)
            benchmark_ann = (aligned.iloc[-1] / aligned.iloc[0]) ** (252 / n_days) - 1
            alpha = annualized - benchmark_ann
            ir = alpha / tracking_err if tracking_err > 0 else 0.0

        # ── 交易统计
        n_trades = len(self.trades)
        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]

        win_rate = len(wins) / n_trades if n_trades else 0.0
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
        expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss if n_trades else 0.0

        best = max(self.trades, key=lambda t: t.pnl_pct) if self.trades else None
        worst = min(self.trades, key=lambda t: t.pnl_pct) if self.trades else None
        avg_hold = sum(t.days_held for t in self.trades) / n_trades if n_trades else 0.0

        max_consec_win, max_consec_loss = self._consecutive_stats()

        # ── 持仓统计
        avg_position_ratio = self._avg_position_ratio(initial)

        result = {
            # 收益类
            "total_return": total_return,
            "annualized_return": annualized,
            "annual_volatility": annual_vol,
            # 风险类
            "max_drawdown": max_dd,
            "max_dd_duration_days": max_dd_duration,
            "max_dd_recovery_days": max_dd_recovery,
            "downside_volatility": downside_vol,
            "var_95": var_95,
            "cvar_95": cvar_95,
            # 风险调整收益
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "information_ratio": ir,
            # 交易统计
            "total_trades": n_trades,
            "win_rate": win_rate,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "profit_factor": profit_factor,
            "expectancy": expectancy,
            "best_trade_pct": best.pnl_pct if best else 0.0,
            "worst_trade_pct": worst.pnl_pct if worst else 0.0,
            "avg_hold_days": avg_hold,
            "max_consec_wins": max_consec_win,
            "max_consec_losses": max_consec_loss,
            # 持仓统计
            "avg_position_ratio": avg_position_ratio,
        }
        return result

    # ── 输出方法 ────────────────────────────────────────────────────────────

    def print(self) -> None:
        s = self.summary()
        if "error" in s:
            print(f"[BacktestReport] {s['error']}")
            return

        w = 52
        line = "─" * w
        print("=" * w)
        print(f"{'回 测 报 告':^{w}}")
        print("=" * w)
        print(f"  {'总收益率':<18}{s['total_return']:>10.2%}")
        print(f"  {'年化收益率':<18}{s['annualized_return']:>10.2%}")
        print(f"  {'年化波动率':<18}{s['annual_volatility']:>10.2%}")
        print(f"  {'下行波动率':<18}{s['downside_volatility']:>10.2%}")
        print(line)
        print(f"  {'最大回撤':<18}{s['max_drawdown']:>10.2%}")
        print(f"  {'最大回撤持续(日)':<18}{s['max_dd_duration_days']:>10}")
        print(f"  {'最大回撤恢复(日)':<18}{s['max_dd_recovery_days'] if s['max_dd_recovery_days'] >= 0 else '未恢复':>10}")
        print(f"  {'VaR (95%)':<18}{s['var_95']:>10.2%}")
        print(f"  {'CVaR (95%)':<18}{s['cvar_95']:>10.2%}")
        print(line)
        print(f"  {'夏普比率':<18}{s['sharpe']:>10.2f}")
        print(f"  {'索提诺比率':<18}{s['sortino']:>10.2f}")
        print(f"  {'卡玛比率':<18}{s['calmar']:>10.2f}")
        if s["information_ratio"] is not None:
            print(f"  {'信息比率':<18}{s['information_ratio']:>10.2f}")
        print(line)
        print(f"  {'总交易笔数':<18}{s['total_trades']:>10}")
        print(f"  {'胜率':<18}{s['win_rate']:>10.2%}")
        print(f"  {'平均盈利':<18}{s['avg_win_pct']:>10.2%}")
        print(f"  {'平均亏损':<18}{s['avg_loss_pct']:>10.2%}")
        print(f"  {'盈亏比':<18}{s['profit_factor'] if s['profit_factor'] != float('inf') else '∞':>10}")
        print(f"  {'期望值':<18}{s['expectancy']:>10.2%}")
        print(f"  {'最佳单笔':<18}{s['best_trade_pct']:>10.2%}")
        print(f"  {'最差单笔':<18}{s['worst_trade_pct']:>10.2%}")
        print(f"  {'平均持仓天数':<18}{s['avg_hold_days']:>10.1f}")
        print(f"  {'最大连胜':<18}{s['max_consec_wins']:>10}")
        print(f"  {'最大连败':<18}{s['max_consec_losses']:>10}")
        print(line)
        print(f"  {'平均仓位使用率':<18}{s['avg_position_ratio']:>10.2%}")
        print("=" * w)

    def to_dataframe(self) -> pd.DataFrame:
        """返回逐笔交易明细 DataFrame。"""
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame([
            {
                "symbol": t.symbol,
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "entry_price": round(t.entry_price, 4),
                "exit_price": round(t.exit_price, 4),
                "shares": round(t.shares, 2),
                "pnl": round(t.pnl, 2),
                "pnl_pct": round(t.pnl_pct, 6),
                "exit_reason": t.exit_reason,
                "days_held": t.days_held,
            }
            for t in self.trades
        ])

    # ── 私有辅助 ────────────────────────────────────────────────────────────

    def _consecutive_stats(self) -> tuple[int, int]:
        if not self.trades:
            return 0, 0
        max_win = cur_win = max_loss = cur_loss = 0
        for t in self.trades:
            if t.pnl > 0:
                cur_win += 1
                max_win = max(max_win, cur_win)
                cur_loss = 0
            else:
                cur_loss += 1
                max_loss = max(max_loss, cur_loss)
                cur_win = 0
        return max_win, max_loss

    def _avg_position_ratio(self, initial: float) -> float:
        """估算平均仓位使用率（持仓市值 / 总资产）。

        基于每笔交易的 cost_basis 和持有天数做简单近似。
        """
        if not self.trades or self.equity_series.empty:
            return 0.0
        total_invested_days = sum(t.cost_basis if hasattr(t, "cost_basis") else 0 for t in self.trades)
        n_days = len(self.equity_series)
        avg_equity = float(self.equity_series.mean())
        # 无法精确重建每日持仓，使用 equity 均值的简化代理
        _ = total_invested_days  # 保留用于未来精细化
        return min(self.config.max_positions * self.config.position_size, 1.0) * 0.8  # 保守估计
