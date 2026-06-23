"""持仓管理：仓位状态、交易记录与资金账本。"""

from dataclasses import dataclass

from .config import BacktestConfig


@dataclass
class Position:
    symbol: str
    entry_date: str
    entry_price: float   # 含买入成本的全成本均价
    shares: float
    cost_basis: float    # 买入总成本（元）
    peak_close: float    # 持仓期最高收盘价（供 TrailingStop 使用）
    days_held: int = 0


@dataclass
class Trade:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: float
    pnl: float           # 净盈亏（元）
    pnl_pct: float       # 净收益率
    exit_reason: str
    days_held: int = 0


class Portfolio:
    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.cash: float = config.initial_capital
        self.positions: dict[str, Position] = {}
        self._total_equity: float = config.initial_capital

    def open(self, symbol: str, date: str, open_price: float) -> bool:
        """以 open_price 开仓。返回 True 表示开仓成功。"""
        cost = self.config.cost
        target_amount = self._total_equity * self.config.position_size
        target_amount = min(target_amount, self.cash)

        if target_amount <= cost.min_commission:
            return False

        # 买入滑点：实际成交价略高于挂单价
        effective_price = open_price * (1 + cost.slippage)
        if effective_price <= 0:
            return False

        commission = max(target_amount * cost.commission_rate, cost.min_commission)
        investable = target_amount - commission
        if investable <= 0:
            return False

        shares = investable / effective_price
        actual_cost = shares * effective_price + commission

        if actual_cost > self.cash:
            return False

        self.cash -= actual_cost
        self.positions[symbol] = Position(
            symbol=symbol,
            entry_date=date,
            entry_price=actual_cost / shares,
            shares=shares,
            cost_basis=actual_cost,
            peak_close=open_price,
        )
        return True

    def close(self, symbol: str, date: str, close_price: float, reason: str) -> Trade:
        """以 close_price 平仓，返回交易记录。"""
        pos = self.positions.pop(symbol)
        cost = self.config.cost

        # 卖出滑点：实际成交价略低于收盘价
        effective_sell = close_price * (1 - cost.slippage)
        gross = pos.shares * effective_sell
        sell_cost = max(gross * (cost.commission_rate + cost.stamp_duty), cost.min_commission)
        net_proceeds = gross - sell_cost

        self.cash += net_proceeds
        pnl = net_proceeds - pos.cost_basis

        return Trade(
            symbol=symbol,
            entry_date=pos.entry_date,
            exit_date=date,
            entry_price=pos.entry_price,
            exit_price=effective_sell,
            shares=pos.shares,
            pnl=pnl,
            pnl_pct=pnl / pos.cost_basis,
            exit_reason=reason,
            days_held=pos.days_held,
        )

    def update_peaks(self, close_prices: dict[str, float]) -> None:
        """更新各持仓的历史最高收盘价（供 TrailingStop 使用）。"""
        for symbol, pos in self.positions.items():
            price = close_prices.get(symbol)
            if price and price > pos.peak_close:
                pos.peak_close = price

    def increment_days_held(self) -> None:
        for pos in self.positions.values():
            pos.days_held += 1

    def mark_to_market(self, close_prices: dict[str, float]) -> float:
        """按当日收盘价估值，更新并返回总资产。"""
        positions_value = sum(
            pos.shares * close_prices.get(symbol, pos.entry_price)
            for symbol, pos in self.positions.items()
        )
        equity = self.cash + positions_value
        self._total_equity = equity
        return equity

    def accrue_interest(self) -> None:
        """对闲置现金按年化利率计日息。"""
        daily_rate = (1 + self.config.cash_interest) ** (1 / 252) - 1
        self.cash *= 1 + daily_rate
