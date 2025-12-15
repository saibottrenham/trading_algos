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
    def modify_sl(position_ticket: int, symbol: str, sl: float, tp: float, digits: int) -> bool:
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