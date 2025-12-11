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
    def __init__(self, fixed_pips=20):
        self.fixed_pips = fixed_pips
        self.first_sl_set = set()

    def should_set_initial_sl(self, pos: Position) -> bool:
        # Default: profit > $0.10 and not set yet
        from trading_algos.config import MIN_PROFIT_TO_START
        return pos.profit >= MIN_PROFIT_TO_START and pos.ticket not in self.first_sl_set

    def calculate_initial_sl(self, pos: Position) -> float:
        # Default: lock $1 buffer
        from trading_algos.core.broker import Broker
        from trading_algos.config import EXTRA_SAFETY_BUFFER, COMMISSION_PER_LOT
        info = Broker.get_symbol_info(pos.symbol)
        target_profit = EXTRA_SAFETY_BUFFER
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
        # Default: fixed 20-pip trail (override for ATR/PSAR)
        from trading_algos.core.broker import Broker
        info = Broker.get_symbol_info(pos.symbol)
        point = info.point
        min_dist = max(info.trade_stops_level * point, 30 * point)

        if pos.is_buy:
            candidate = pos.price_current - self.fixed_pips * point * 10  # pips to points
            new_sl = max(candidate, pos.sl)
            new_sl = min(new_sl, pos.price_current - min_dist)
        else:
            candidate = pos.price_current + self.fixed_pips * point * 10
            new_sl = min(candidate, pos.sl)
            new_sl = max(new_sl, pos.price_current + min_dist)

        return round(new_sl, info.digits)

    def trail(self, pos: Position) -> None:
        from trading_algos.core.broker import Broker
        from trading_algos.config import MIN_PROFIT_TO_START
        info = Broker.get_symbol_info(pos.symbol)

        # Low profit: remove SL
        if pos.profit < MIN_PROFIT_TO_START and pos.sl != 0.0:
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