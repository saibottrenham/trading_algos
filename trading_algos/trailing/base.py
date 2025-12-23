# trading_algos/trailing/base.py
from abc import ABC, abstractmethod
from trading_algos.core.position import Position
import math  # Added for ceil and sqrt

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

    def _get_min_dist(self, pos: Position) -> float:
        """Shared helper for dynamic min_dist based on lot size."""
        from trading_algos.core.broker import Broker
        from trading_algos.config import SL_BUFFER_BASE_POINTS, SL_BUFFER_PER_LOT
        info = Broker.get_symbol_info(pos.symbol)
        buffer_points = SL_BUFFER_BASE_POINTS + pos.volume * SL_BUFFER_PER_LOT
        return max(info.trade_stops_level, buffer_points) * info.point

    def _get_profit_threshold(self, pos: Position) -> float:
        """Shared helper for dynamic profit threshold based on position margin and volatility."""
        from trading_algos.core.broker import Broker
        from trading_algos.config import BASE_PROFIT_TO_ACTIVATE, THRESHOLD_FACTOR_PER_MARGIN
        import MetaTrader5 as mt5
        action = mt5.ORDER_TYPE_BUY if pos.is_buy else mt5.ORDER_TYPE_SELL
        position_margin = Broker.robust_order_calc_margin(action, pos.symbol, pos.volume, pos.price_open)
        # Use sqrt for slower growth on larger lots/vol
        return BASE_PROFIT_TO_ACTIVATE + (math.sqrt(position_margin) * THRESHOLD_FACTOR_PER_MARGIN)

    def should_set_initial_sl(self, pos: Position) -> bool:
        threshold = self._get_profit_threshold(pos)
        return pos.profit >= threshold and pos.ticket not in self.first_sl_set

    def calculate_initial_sl(self, pos: Position) -> float:
        from trading_algos.core.broker import Broker
        from trading_algos.config import COMMISSION_PER_LOT
        import MetaTrader5 as mt5
        info = Broker.get_symbol_info(pos.symbol)
        target_profit = self._get_profit_threshold(pos)
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

        min_dist = self._get_min_dist(pos)  # Use shared helper

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
        info = Broker.get_symbol_info(pos.symbol)
        threshold = self._get_profit_threshold(pos)

        # Low profit: remove SL (override if unwanted)
        if pos.profit < threshold and pos.sl != 0.0:
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
            point = info.point / 2  # Halve buffer to 0.5pt for faster gold trails (was full point)
            should_move = (pos.is_buy and new_sl > pos.sl + point) or \
                          (not pos.is_buy and new_sl < pos.sl - point)
            if should_move:
                Broker.modify_sl(pos.ticket, pos.symbol, new_sl, pos.tp, info.digits)