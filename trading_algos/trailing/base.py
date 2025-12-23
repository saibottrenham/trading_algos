# trading_algos/trailing/base.py
from abc import ABC, abstractmethod
from trading_algos.core.position import Position
import math  # Added for ceil

class TrailingEngine(ABC):
    @abstractmethod
    def should_set_initial_sl(self, pos: Position) -> bool:
        ...

    @abstractmethod
    def calculate_initial_sl(self, pos: Position) -> float:
        ...

    @abstractmethod
    def calculate_next_sl(self, pos: Position) -> float:
        ...

class BasicTrailingEngine(TrailingEngine):
    """Concrete base for simple engines—override as needed. Implements trail() logic."""
    def __init__(self):
        self.first_sl_set = set()

    def should_set_initial_sl(self, pos: Position) -> bool:
        from trading_algos.config import PROFIT_TO_ACTIVATE_TRAILING
        return pos.profit >= PROFIT_TO_ACTIVATE_TRAILING and pos.ticket not in self.first_sl_set

    def calculate_initial_sl(self, pos: Position) -> float:
        from trading_algos.core.broker import Broker
        from trading_algos.config import PROFIT_TO_ACTIVATE_TRAILING, COMMISSION_PER_LOT, SL_BUFFER_BASE_POINTS, SL_BUFFER_PER_LOT
        import MetaTrader5 as mt5
        info = Broker.get_symbol_info(pos.symbol)
        target_profit = PROFIT_TO_ACTIVATE_TRAILING
        action = mt5.ORDER_TYPE_BUY if pos.is_buy else mt5.ORDER_TYPE_SELL

        # Estimate k from current position (handles account currency conversion)
        raw_current = Broker.robust_order_calc_profit(action, pos.symbol, pos.volume, pos.price_open, pos.price_current)
        diff = (pos.price_current - pos.price_open) if pos.is_buy else (pos.price_open - pos.price_current)
        if diff == 0:
            return 0.0
        k = raw_current / diff

        required_raw = target_profit - pos.swap + (COMMISSION_PER_LOT * pos.volume)
        required_diff = required_raw / k

        # Ceil to next full tick to guarantee >= target after rounding
        required_points = math.ceil(required_diff / info.point)
        required_diff = required_points * info.point

        # Dynamic min_dist based on lot size
        buffer_points = SL_BUFFER_BASE_POINTS + pos.volume * SL_BUFFER_PER_LOT
        min_dist = max(info.trade_stops_level, buffer_points) * info.point

        if pos.is_buy:
            sl = pos.price_open + required_diff
            sl = min(sl, pos.price_current - min_dist)
        else:
            sl = pos.price_open - required_diff
            sl = max(sl, pos.price_current + min_dist)

        return round(sl, info.digits)

    def calculate_next_sl(self, pos: Position) -> float:
        raise NotImplementedError("Override in subclass for specific trailing logic")

    def trail(self, pos: Position) -> None:
        from trading_algos.core.broker import Broker
        from trading_algos.config import PROFIT_TO_ACTIVATE_TRAILING
        info = Broker.get_symbol_info(pos.symbol)

        # Low profit: remove SL (override if unwanted)
        if pos.profit < PROFIT_TO_ACTIVATE_TRAILING and pos.sl != 0.0:
            Broker.modify_sl(pos.ticket, pos.symbol, 0.0, pos.tp, info.digits)
            return

        # Initial SL
        if self.should_set_initial_sl(pos):
            sl = self.calculate_initial_sl(pos)
            if Broker.modify_sl(pos.ticket, pos.symbol, sl, pos.tp, info.digits):
                self.first_sl_set.add(pos.ticket)

        # Trail
        if pos.sl != 0.0:
            new_sl = self.calculate_next_sl(pos)
            point = info.point
            should_move = (pos.is_buy and new_sl > pos.sl + point) or \
                          (not pos.is_buy and new_sl < pos.sl - point)
            if should_move:
                Broker.modify_sl(pos.ticket, pos.symbol, new_sl, pos.tp, info.digits)