from trading_algos.trailing.base import TrailingEngine
from trading_algos.core.position import Position
from trading_algos.core.broker import Broker
from trading_algos.core.logger import log_event
from trading_algos.config import (
    BASE_MULTIPLIER, VOLUME_SENSITIVITY, MIN_MULTIPLIER, MAX_MULTIPLIER,
    MIN_PROFIT_TO_START, EXTRA_SAFETY_BUFFER, COMMISSION_PER_LOT,
    ATR_PERIOD, VOLUME_LOOKBACK
)
import numpy as np

class VolumeATRTrailing(TrailingEngine):
    def __init__(self):
        self.first_sl_set = set()

    def _get_volume_ratio(self, symbol: str) -> float:
        # ... copy your get_volume_ratio logic
        ...

    def _get_atr(self, symbol: str) -> float:
        # ... copy your get_atr logic
        ...

    def should_set_initial_sl(self, pos: Position) -> bool:
        required = max(MIN_PROFIT_TO_START, COMMISSION_PER_LOT * pos.volume + EXTRA_SAFETY_BUFFER - pos.swap)
        return pos.profit >= required and pos.ticket not in self.first_sl_set

    def calculate_initial_sl(self, pos: Position) -> float:
        info = Broker.get_symbol_info(pos.symbol)
        target_profit = EXTRA_SAFETY_BUFFER
        commission = COMMISSION_PER_LOT * pos.volume

        if pos.is_buy:
            sl = pos.price_open + (target_profit + commission - pos.swap) / (pos.volume * info.trade_contract_size)
            sl = min(sl, pos.price_current - max(info.trade_stops_level * info.point, 30 * info.point))
        else:
            sl = pos.price_open - (target_profit + commission - pos.swap) / (pos.volume * info.trade_contract_size)
            sl = max(sl, pos.price_current + max(info.trade_stops_level * info.point, 30 * info.point))

        return round(sl, info.digits)

    def calculate_next_sl(self, pos: Position) -> float:
        info = Broker.get_symbol_info(pos.symbol)
        vol_ratio = self._get_volume_ratio(pos.symbol)
        mult = np.clip(BASE_MULTIPLIER * (vol_ratio ** (1 / VOLUME_SENSITIVITY)), MIN_MULTIPLIER, MAX_MULTIPLIER)
        atr = self._get_atr(pos.symbol)

        if pos.is_buy:
            candidate = pos.price_current - mult * atr
            new_sl = max(candidate, pos.sl)
            new_sl = min(new_sl, pos.price_current - max(info.trade_stops_level * info.point, 30 * info.point))
        else:
            candidate = pos.price_current + mult * atr
            new_sl = min(candidate, pos.sl)
            new_sl = max(new_sl, pos.price_current + max(info.trade_stops_level * info.point, 30 * info.point))

        return round(new_sl, info.digits)

    def trail(self, pos: Position) -> None:
        if pos.profit < MIN_PROFIT_TO_START and pos.sl != 0.0:
            Broker.modify_sl(pos.ticket, pos.symbol, 0.0, pos.tp, Broker.get_symbol_info(pos.symbol).digits)
            log_event("SL_REMOVED_LOW_PROFIT", ticket=pos.ticket, profit=pos.profit)
            return

        if self.should_set_initial_sl(pos):
            sl = self.calculate_initial_sl(pos)
            if Broker.modify_sl(pos.ticket, pos.symbol, sl, pos.tp, Broker.get_symbol_info(pos.symbol).digits):
                self.first_sl_set.add(pos.ticket)

        elif pos.sl != 0.0:
            new_sl = self.calculate_next_sl(pos)
            if (pos.is_buy and new_sl > pos.sl + 0.00001) or (not pos.is_buy and new_sl < pos.sl - 0.00001):
                Broker.modify_sl(pos.ticket, pos.symbol, new_sl, pos.tp, Broker.get_symbol_info(pos.symbol).digits)