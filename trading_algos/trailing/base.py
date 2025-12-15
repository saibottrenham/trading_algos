# trading_algos/trailing/base.py
from abc import ABC, abstractmethod
from trading_algos.core.position import Position

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
        from trading_algos.config import PROFIT_TO_ACTIVATE_TRAILING, COMMISSION_PER_LOT
        info = Broker.get_symbol_info(pos.symbol)
        target_profit = PROFIT_TO_ACTIVATE_TRAILING
        commission = COMMISSION_PER_LOT * pos.volume
        contract = pos.volume * info.trade_contract_size

        if pos.is_buy:
            sl = pos.price_open + (target_profit + commission - pos.swap) / contract
            sl = min(sl, pos.price_current - max(info.trade_stops_level * info.point, 30 * info.point))
        else:
            sl = pos.price_open - (target_profit + commission - pos.swap) / contract
            sl = max(sl, pos.price_current + max(info.trade_stops_level * info.point, 30 * info.point))

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