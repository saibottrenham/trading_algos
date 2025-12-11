from trading_algos.trailing.base import TrailingEngine
from trading_algos.core.position import Position
from trading_algos.core.broker import Broker
from trading_algos.core.logger import log_event
import numpy as np
import pandas as pd
from trading_algos.config import (
    PROFIT_TO_ACTIVATE_TRAILING, COMMISSION_PER_LOT,
    BASE_MULTIPLIER, VOLUME_SENSITIVITY, MIN_MULTIPLIER, MAX_MULTIPLIER,
    ATR_PERIOD, VOLUME_LOOKBACK
)

# Safe MT5 import
try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:
    _MT5_AVAILABLE = False
    mt5 = None


class VolumeATRTrailing(TrailingEngine):
    def __init__(self):
        self.first_sl_set = set()              # We set this SL
        self.cleaned_preexisting_sl = set()    # We removed someone else's SL
        self.last_profit = {}                  # ← THIS WAS MISSING ON WINDOWS!

    # ── Helpers ─────────────────────
    def _get_volume_ratio(self, symbol: str) -> float:
        if not _MT5_AVAILABLE: return 1.0
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, VOLUME_LOOKBACK + 10)
        if rates is None or len(rates) < VOLUME_LOOKBACK: return 1.0
        df = pd.DataFrame(rates)
        avg = df['tick_volume'].rolling(VOLUME_LOOKBACK).mean().iloc[-2]
        cur = df['tick_volume'].iloc[-1]
        return cur / avg if avg > 0 else 1.0

    def _get_atr(self, symbol: str, timeframe=None, period=None) -> float:
        if timeframe is None:
            timeframe = mt5.TIMEFRAME_M5 if _MT5_AVAILABLE else None
        if period is None:
            period = ATR_PERIOD
        if not _MT5_AVAILABLE:
            info = Broker.get_symbol_info(symbol)
            return info.point * 150

        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, period + 20)
        if rates is None or len(rates) <= period:
            info = Broker.get_symbol_info(symbol)
            return info.point * 150
        df = pd.DataFrame(rates)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift()).abs(),
            (df['low'] - df['close'].shift()).abs()
        ], axis=1).max(axis=1)
        return tr.rolling(period).mean().iloc[-1]

    # ── Core logic ─────────────────────────────
    def should_set_initial_sl(self, pos: Position) -> bool:
        # CRITICAL: Only activate when profit is actually ≥ $10
        return pos.profit >= PROFIT_TO_ACTIVATE_TRAILING and pos.ticket not in self.first_sl_set

    def calculate_initial_sl(self, pos: Position) -> float:
        info = Broker.get_symbol_info(pos.symbol)
        target = PROFIT_TO_ACTIVATE_TRAILING
        commission = COMMISSION_PER_LOT * pos.volume
        contract = pos.volume * info.trade_contract_size

        if pos.is_buy:
            sl = pos.price_open + (target + commission - pos.swap) / contract
            sl = min(sl, pos.price_current - max(info.trade_stops_level * info.point, 30 * info.point))
        else:
            sl = pos.price_open - (target + commission - pos.swap) / contract
            sl = max(sl, pos.price_current + max(info.trade_stops_level * info.point, 30 * info.point))

        return round(sl, info.digits)

    def calculate_next_sl(self, pos: Position) -> float:
        info = Broker.get_symbol_info(pos.symbol)
        vol_ratio = max(self._get_volume_ratio(pos.symbol), 0.1)

        mult = np.clip(BASE_MULTIPLIER * (vol_ratio ** (1 / VOLUME_SENSITIVITY)),
                       MIN_MULTIPLIER, MAX_MULTIPLIER)

        # Velocity aggression (safe now)
        now = pd.Timestamp.now().timestamp()
        prev_profit, prev_time = self.last_profit.get(pos.ticket, (pos.profit, now))
        delta_time = max(now - prev_time, 0.1)
        velocity = (pos.profit - prev_profit) / (delta_time / 60)
        if velocity > 6.0:
            mult *= max(0.7, 1 - velocity/60)
        self.last_profit[pos.ticket] = (pos.profit, now)

        # Hybrid ATR
        atr_m5 = self._get_atr(pos.symbol, mt5.TIMEFRAME_M5 if _MT5_AVAILABLE else None)
        atr_m1 = self._get_atr(pos.symbol, mt5.TIMEFRAME_M1 if _MT5_AVAILABLE else None, max(ATR_PERIOD//3, 5))
        atr = 0.7 * atr_m5 + 0.3 * atr_m1

        min_dist = max(info.trade_stops_level * info.point, 30 * info.point)

        if pos.is_buy:
            candidate = pos.price_current - mult * atr
            new_sl = max(candidate, pos.sl or 0)
            new_sl = min(new_sl, pos.price_current - min_dist)
        else:
            candidate = pos.price_current + mult * atr
            new_sl = min(candidate, pos.sl or float('inf'))
            new_sl = max(new_sl, pos.price_current + min_dist)

        return round(new_sl, info.digits)

    def trail(self, pos: Position) -> None:
        info = Broker.get_symbol_info(pos.symbol)

        # 1. Remove any foreign SL once
        if pos.sl != 0.0 and pos.ticket not in self.cleaned_preexisting_sl and pos.ticket not in self.first_sl_set:
            Broker.modify_sl(pos.ticket, pos.symbol, 0.0, pos.tp, info.digits)
            self.cleaned_preexisting_sl.add(pos.ticket)
            log_event("REMOVED_FOREIGN_SL", ticket=pos.ticket, old_sl=pos.sl)
            return

        # 2. First time we hit +$10 → lock it (GUARANTEED profit ≥ $10 here)
        if self.should_set_initial_sl(pos):
            sl = self.calculate_initial_sl(pos)
            locked_profit = self.profit_if_sl_hit(pos, sl)

            # EXTRA SAFETY: double-check we are not locking a loss
            if locked_profit < 9.0:  # allow tiny rounding
                log_event("BLOCKED_BAD_SL", ticket=pos.ticket, would_lock=locked_profit, profit=pos.profit)
                return

            if Broker.modify_sl(pos.ticket, pos.symbol, sl, pos.tp, info.digits):
                self.first_sl_set.add(pos.ticket)
                log_event("FIRST_SL_SET_10USD", ticket=pos.ticket, sl=sl, locked=round(locked_profit, 2))
            return

        # 3. Trail aggressively (ratchet only)
        if pos.ticket in self.first_sl_set and pos.sl != 0.0:
            new_sl = self.calculate_next_sl(pos)
            point = info.point
            move_forward = (pos.is_buy and new_sl > pos.sl + point) or \
                           (not pos.is_buy and new_sl < pos.sl - point)

            if move_forward:
                Broker.modify_sl(pos.ticket, pos.symbol, new_sl, pos.tp, info.digits)
                log_event("SL_TRAILED", ticket=pos.ticket, sl=new_sl, profit=round(pos.profit, 2))

    def profit_if_sl_hit(self, pos: Position, sl_price: float) -> float:
        if sl_price == 0: return 0.0
        info = Broker.get_symbol_info(pos.symbol)
        diff = (sl_price - pos.price_open) if pos.is_buy else (pos.price_open - sl_price)
        gross = diff * pos.volume * info.trade_contract_size
        return gross + pos.swap - (COMMISSION_PER_LOT * pos.volume)