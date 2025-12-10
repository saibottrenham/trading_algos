# trading_algos/trailing/volume_atr.py
from trading_algos.trailing.base import TrailingEngine
from trading_algos.core.position import Position
from trading_algos.core.broker import Broker
from trading_algos.core.logger import log_event
import numpy as np

from trading_algos.config import (
    BASE_MULTIPLIER, VOLUME_SENSITIVITY, MIN_MULTIPLIER, MAX_MULTIPLIER,
    MIN_PROFIT_TO_START, EXTRA_SAFETY_BUFFER, COMMISSION_PER_LOT,
    ATR_PERIOD, VOLUME_LOOKBACK
)

# ── LAZY + MOCKABLE MT5 IMPORT (Mac-safe) ─────────────────────────────────────
try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:
    _MT5_AVAILABLE = False
    mt5 = None  # Will be mocked in tests

class VolumeATRTrailing(TrailingEngine):
    def __init__(self):
        self.first_sl_set = set()

    def _get_volume_ratio(self, symbol: str) -> float:
        if not _MT5_AVAILABLE:
            return 1.0
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, VOLUME_LOOKBACK + 10)
        if rates is None or len(rates) < VOLUME_LOOKBACK:
            return 1.0
        df = pd.DataFrame(rates)
        avg = df['tick_volume'].rolling(VOLUME_LOOKBACK).mean().iloc[-2]
        cur = df['tick_volume'].iloc[-1]
        return cur / avg if avg > 0 else 1.0

    def _get_atr(self, symbol: str) -> float:
        if not _MT5_AVAILABLE:
            info = Broker.get_symbol_info(symbol)
            return info.point * 150
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, ATR_PERIOD + 20)
        if rates is None or len(rates) <= ATR_PERIOD:
            info = Broker.get_symbol_info(symbol)
            return info.point * 150
        df = pd.DataFrame(rates)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift()).abs(),
            (df['low'] - df['close'].shift()).abs()
        ], axis=1).max(axis=1)
        return tr.rolling(ATR_PERIOD).mean().iloc[-1]

    def should_set_initial_sl(self, pos: Position) -> bool:
        required = max(MIN_PROFIT_TO_START,
                       COMMISSION_PER_LOT * pos.volume + EXTRA_SAFETY_BUFFER - pos.swap)
        return pos.profit >= required and pos.ticket not in self.first_sl_set

    def calculate_initial_sl(self, pos: Position) -> float:
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
        info = Broker.get_symbol_info(pos.symbol)
        vol_ratio = self._get_volume_ratio(pos.symbol)
        if vol_ratio <= 0:
            vol_ratio = 1.0

        mult = np.clip(BASE_MULTIPLIER * (vol_ratio ** (1 / VOLUME_SENSITIVITY)),
                       MIN_MULTIPLIER, MAX_MULTIPLIER)
        atr = self._get_atr(pos.symbol)

        min_dist = max(info.trade_stops_level * info.point, 30 * info.point)

        if pos.is_buy:
            candidate = pos.price_current - mult * atr
            new_sl = max(candidate, pos.sl)
            new_sl = min(new_sl, pos.price_current - min_dist)
        else:
            candidate = pos.price_current + mult * atr
            new_sl = min(candidate, pos.sl)
            new_sl = max(new_sl, pos.price_current + min_dist)

        return round(new_sl, info.digits)

    def trail(self, pos: Position) -> None:
        info = Broker.get_symbol_info(pos.symbol)

        if pos.profit < MIN_PROFIT_TO_START and pos.sl != 0.0:
            Broker.modify_sl(pos.ticket, pos.symbol, 0.0, pos.tp, info.digits)
            log_event("SL_REMOVED_LOW_PROFIT", ticket=pos.ticket, profit=round(pos.profit, 2))
            return

        if self.should_set_initial_sl(pos):
            sl = self.calculate_initial_sl(pos)
            locked = self.profit_if_sl_hit(pos, sl)
            if locked >= 0.01:
                if Broker.modify_sl(pos.ticket, pos.symbol, sl, pos.tp, info.digits):
                    self.first_sl_set.add(pos.ticket)
                    log_event("FIRST_SL_SET", ticket=pos.ticket, sl=sl, locked_profit=round(locked, 2))
            return

        if pos.sl != 0.0:
            new_sl = self.calculate_next_sl(pos)
            point = info.point
            should_move = (pos.is_buy and new_sl > pos.sl + point) or \
                         (not pos.is_buy and new_sl < pos.sl - point)

            if should_move:
                Broker.modify_sl(pos.ticket, pos.symbol, new_sl, pos.tp, info.digits)
                vol_ratio = self._get_volume_ratio(pos.symbol) or 1.0
                mult = round(np.clip(BASE_MULTIPLIER * (vol_ratio ** (1/VOLUME_SENSITIVITY)),
                                   MIN_MULTIPLIER, MAX_MULTIPLIER), 2)
                log_event("SL_TRAILED", ticket=pos.ticket, sl=new_sl, multiplier=mult)

    def profit_if_sl_hit(self, pos: Position, sl_price: float) -> float:
        info = Broker.get_symbol_info(pos.symbol)
        if sl_price == 0:
            return 0.0
        diff = (sl_price - pos.price_open) if pos.is_buy else (pos.price_open - sl_price)
        gross = diff * pos.volume * info.trade_contract_size
        return gross + pos.swap - (COMMISSION_PER_LOT * pos.volume)

# Required imports at bottom (safe for Mac)
import pandas as pd