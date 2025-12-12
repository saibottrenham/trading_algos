# trading_algos/trailing/volume_atr.py
from trading_algos.trailing.base import TrailingEngine
from trading_algos.core.position import Position
from trading_algos.core.broker import Broker
from trading_algos.core.logger import log_event
import numpy as np
import pandas as pd
import time  # Added for throttle

from trading_algos.config import (
    PROFIT_TO_ACTIVATE_TRAILING, COMMISSION_PER_LOT,
    BASE_MULTIPLIER, VOLUME_SENSITIVITY, MIN_MULTIPLIER, MAX_MULTIPLIER,
    ATR_PERIOD, VOLUME_LOOKBACK
)

try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:
    _MT5_AVAILABLE = False
    mt5 = None

class VolumeATRTrailing(TrailingEngine):
    def __init__(self):
        self.first_sl_set = set()          # Tickets where WE set the first SL
        self.cleaned_preexisting_sl = set()  # Tickets where we removed someone else's SL
        self.last_profit = {}              # Per-ticket profit velocity tracking
        self.last_monitor_log = {}         # Per-ticket last monitor time (throttle)

    # ── Helpers ─────────────────────
    def _get_volume_ratio(self, symbol: str) -> float:
        if not _MT5_AVAILABLE: return 1.0
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, VOLUME_LOOKBACK + 10)
        if rates is None or len(rates) < VOLUME_LOOKBACK: return 1.0
        df = pd.DataFrame(rates)
        avg = df['tick_volume'].rolling(VOLUME_LOOKBACK).mean().iloc[-2]
        cur = df['tick_volume'].iloc[-1]
        return cur / avg if avg > 0 else 1.0

    def _get_atr(self, symbol: str, timeframe=mt5.TIMEFRAME_M5, period=ATR_PERIOD) -> float:
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
        if pos.ticket in self.first_sl_set:
            return False
        info = Broker.get_symbol_info(pos.symbol)
        min_dist = max(info.trade_stops_level * info.point, 30 * info.point)
        contract = pos.volume * info.trade_contract_size
        min_dist_dollars = min_dist * contract
        commission = COMMISSION_PER_LOT * pos.volume
        required_profit = PROFIT_TO_ACTIVATE_TRAILING + min_dist_dollars + commission - pos.swap
        if pos.profit < required_profit:
            return False
        return True

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

        # Optional velocity boost on crazy spikes
        now = pd.Timestamp.now().timestamp()
        prev = self.last_profit.get(pos.ticket, (pos.profit, now))
        velocity = (pos.profit - prev[0]) / max((now - prev[1]) / 60, 0.1)
        if velocity > 6.0:
            mult *= max(0.7, 1 - velocity/60)
        self.last_profit[pos.ticket] = (pos.profit, now)

        atr = 0.7 * self._get_atr(pos.symbol, mt5.TIMEFRAME_M5) + \
              0.3 * self._get_atr(pos.symbol, mt5.TIMEFRAME_M1, max(ATR_PERIOD//3, 5))

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

        # 1. Remove pre-existing foreign SL only once
        if pos.sl != 0.0 and pos.ticket not in self.cleaned_preexisting_sl and pos.ticket not in self.first_sl_set:
            Broker.modify_sl(pos.ticket, pos.symbol, 0.0, pos.tp, info.digits)
            self.cleaned_preexisting_sl.add(pos.ticket)
            log_event("REMOVED_FOREIGN_SL", ticket=pos.ticket, old_sl=pos.sl)
            return

        # 2. First time we hit +PROFIT_TO_ACTIVATE_TRAILING → lock exactly PROFIT_TO_ACTIVATE_TRAILING (only if buffer allows full lock)
        if self.should_set_initial_sl(pos):
            sl = self.calculate_initial_sl(pos)
            locked = self.profit_if_sl_hit(pos, sl)
            if locked >= PROFIT_TO_ACTIVATE_TRAILING - 0.01:  # Float tolerance, ensure >=PROFIT_TO_ACTIVATE_TRAILING lock
                if (pos.is_buy and sl > pos.price_open) or (not pos.is_buy and sl < pos.price_open):
                    if Broker.modify_sl(pos.ticket, pos.symbol, sl, pos.tp, info.digits):
                        self.first_sl_set.add(pos.ticket)
                        log_event("FIRST_SL_SET", ticket=pos.ticket, sl=sl, locked=round(locked,2), target=PROFIT_TO_ACTIVATE_TRAILING)
                else:
                    log_event("SKIPPED_INITIAL_NEGATIVE_LOCK", ticket=pos.ticket, proposed_sl=sl, open_price=pos.price_open)
            else:
                log_event("SKIPPED_INITIAL_INSUFFICIENT_BUFFER", ticket=pos.ticket, locked=round(locked,2), needed_buffer=round(PROFIT_TO_ACTIVATE_TRAILING - locked,2))
            return

        # 3. Once we own the SL → ratchet only, never remove, never move back
        if pos.ticket in self.first_sl_set:
            new_sl = self.calculate_next_sl(pos)
            point = info.point
            should_move = (pos.is_buy and new_sl > pos.sl + point) or \
                          (not pos.is_buy and new_sl < pos.sl - point)
            if should_move:
                Broker.modify_sl(pos.ticket, pos.symbol, new_sl, pos.tp, info.digits)
                log_event("SL_TRAILED", ticket=pos.ticket, sl=new_sl, profit=round(pos.profit,2))
            return

        # Throttled monitoring if below activation (60s/ticket)
        if pos.ticket not in self.last_monitor_log or time.time() - self.last_monitor_log[pos.ticket] > 60:
            needed_profit = PROFIT_TO_ACTIVATE_TRAILING - pos.profit
            log_event("POSITION_MONITOR", ticket=pos.ticket, current_profit=round(pos.profit, 2), needed_profit=round(needed_profit, 2))
            self.last_monitor_log[pos.ticket] = time.time()

    def profit_if_sl_hit(self, pos: Position, sl_price: float) -> float:
        if sl_price == 0: return 0.0
        info = Broker.get_symbol_info(pos.symbol)
        diff = (sl_price - pos.price_open) if pos.is_buy else (pos.price_open - sl_price)
        gross = diff * pos.volume * info.trade_contract_size
        return gross + pos.swap - (COMMISSION_PER_LOT * pos.volume)