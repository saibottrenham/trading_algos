# trading_algos/core/broker.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

# Lazy MT5 import — Mac-safe
try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:
    _MT5_AVAILABLE = False
    mt5 = None  # type: ignore

import numpy as np
import pandas as pd
from trading_algos.core.logger import log_event

@dataclass
class SymbolInfo:
    digits: int
    point: float
    trade_contract_size: float
    trade_stops_level: int

class Broker:
    @staticmethod
    def get_symbol_info(symbol: str) -> SymbolInfo:
        if not _MT5_AVAILABLE:
            return SymbolInfo(digits=5, point=0.00001, trade_contract_size=100000, trade_stops_level=10)
        info = mt5.symbol_info(symbol)
        return SymbolInfo(
            digits=info.digits,
            point=info.point,
            trade_contract_size=info.trade_contract_size,
            trade_stops_level=info.trade_stops_level,
        )

    @staticmethod
    def modify_sl(position_ticket: int, symbol: str, sl: float, tp: float, digits: int, comment: str = "") -> bool:
        if not _MT5_AVAILABLE:
            log_event("SL_MODIFY_MOCK", ticket=position_ticket, symbol=symbol, new_sl=sl)
            return True

        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": position_ticket,
            "symbol": symbol,
            "sl": round(sl, digits),
            "tp": tp,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if comment:
            req["comment"] = comment
        r = mt5.order_send(req)
        success = r.retcode == mt5.TRADE_RETCODE_DONE
        log_event("SL_MODIFY", success=success, ticket=position_ticket, new_sl=sl, retcode=r.retcode)
        return success

    @staticmethod
    def robust_positions_get(symbol: Optional[str] = None, ticket: Optional[int] = None) -> tuple:
        def fetch():
            if ticket is not None:
                return mt5.positions_get(ticket=ticket)
            return mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()

        positions = fetch()
        if positions is None:
            error_code, error_msg = mt5.last_error()
            log_event("CONNECTION_DROP_DETECTED", error_code=error_code, error_msg=error_msg)
            if not mt5.initialize():
                log_event("REINIT_FAILED")
                raise RuntimeError("MT5 reinitialization failed—check terminal status.")
            positions = fetch()
            if positions is None:
                log_event("RETRY_FAILED")
                raise RuntimeError("Failed to fetch positions after retry.")
            log_event("CONNECTION_RESTORED")
        return positions or ()

    @staticmethod
    def robust_copy_rates(symbol: str, timeframe: int, start_pos: int, count: int) -> Optional[np.ndarray]:
        rates = mt5.copy_rates_from_pos(symbol, timeframe, start_pos, count)
        if rates is None:
            error_code, error_msg = mt5.last_error()
            log_event("CONNECTION_DROP_DETECTED", error_code=error_code, error_msg=error_msg)
            if not mt5.initialize():
                log_event("REINIT_FAILED")
                raise RuntimeError("MT5 reinitialization failed.")
            rates = mt5.copy_rates_from_pos(symbol, timeframe, start_pos, count)
            if rates is None:
                log_event("RETRY_FAILED")
                raise RuntimeError("Failed to fetch rates after retry.")
            log_event("CONNECTION_RESTORED")
        return rates

    @staticmethod
    def robust_order_calc_profit(action: int, symbol: str, volume: float, price_open: float, price_close: float) -> float:
        if not _MT5_AVAILABLE:
            # Fallback assuming USD account
            info = Broker.get_symbol_info(symbol)
            diff = (price_close - price_open) if action == mt5.ORDER_TYPE_BUY else (price_open - price_close)
            return diff * volume * info.trade_contract_size

        def fetch():
            return mt5.order_calc_profit(action, symbol, volume, price_open, price_close)

        profit = fetch()
        if profit is None:
            error_code, error_msg = mt5.last_error()
            log_event("CONNECTION_DROP_DETECTED", error_code=error_code, error_msg=error_msg)
            if not mt5.initialize():
                log_event("REINIT_FAILED")
                raise RuntimeError("MT5 reinitialization failed—check terminal status.")
            profit = fetch()
            if profit is None:
                log_event("RETRY_FAILED")
                raise RuntimeError("Failed to calc profit after retry.")
            log_event("CONNECTION_RESTORED")
        return profit or 0.0

    @staticmethod
    def robust_order_calc_margin(action: int, symbol: str, volume: float, price: float) -> float:
        if not _MT5_AVAILABLE:
            # Fallback: approximate margin = (volume * contract_size * price) / leverage (assume 500)
            info = Broker.get_symbol_info(symbol)
            return (volume * info.trade_contract_size * price) / 500

        def fetch():
            return mt5.order_calc_margin(action, symbol, volume, price)

        margin = fetch()
        if margin is None:
            error_code, error_msg = mt5.last_error()
            log_event("CONNECTION_DROP_DETECTED", error_code=error_code, error_msg=error_msg)
            if not mt5.initialize():
                log_event("REINIT_FAILED")
                raise RuntimeError("MT5 reinitialization failed—check terminal status.")
            margin = fetch()
            if margin is None:
                log_event("RETRY_FAILED")
                raise RuntimeError("Failed to calc margin after retry.")
            log_event("CONNECTION_RESTORED")
        return margin or 0.0

    @staticmethod
    def _get_atr(symbol: str, timeframe: int = mt5.TIMEFRAME_M5, period: int = 14) -> float:
        if not _MT5_AVAILABLE:
            return 1.0  # Mock
        rates = Broker.robust_copy_rates(symbol, timeframe, 0, period + 1)
        if rates is None or len(rates) < period + 1:
            return 0.0
        df = pd.DataFrame(rates)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift()).abs(),
            (df['low'] - df['close'].shift()).abs()
        ], axis=1).max(axis=1)
        return tr.rolling(period).mean().iloc[-1]

    @staticmethod
    def get_trend(symbol: str) -> str:
        if not _MT5_AVAILABLE:
            return "neutral"
        timeframe = mt5.TIMEFRAME_M5
        fast_period = 10
        slow_period = 30
        rates = Broker.robust_copy_rates(symbol, timeframe, 0, slow_period + 1)
        if rates is None or len(rates) < slow_period + 1:
            log_event("RATES_FETCH_FAIL", symbol=symbol)
            return "neutral"
        df = pd.DataFrame(rates)
        fast_ema = df['close'].ewm(span=fast_period, adjust=False).mean().iloc[-1]
        slow_ema = df['close'].ewm(span=slow_period, adjust=False).mean().iloc[-1]
        atr = Broker._get_atr(symbol)
        if abs(fast_ema - slow_ema) < 0.1 * atr:
            return "neutral"
        return "buy" if fast_ema > slow_ema else "sell"

    @staticmethod
    def open_market_position(symbol: str, action: int, volume: float, sl: float = 0.0, tp: float = 0.0, deviation: int = 20, comment: str = "auto_reopen") -> int:
        if not _MT5_AVAILABLE:
            log_event("OPEN_MOCK", symbol=symbol, action=action, volume=volume)
            return 0  # Mock ticket
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": action,  # 0=buy, 1=sell
            "sl": sl,
            "tp": tp,
            "deviation": deviation,
            "magic": 0,
            "comment": comment[:31],  # Truncate to MT5 limit
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(req)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log_event("OPEN_FAILED", symbol=symbol, retcode=result.retcode, comment=result.comment)
            return 0
        log_event("OPEN_SUCCESS", ticket=result.order, symbol=symbol, volume=volume)
        return result.order