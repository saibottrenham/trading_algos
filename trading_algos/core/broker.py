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
            from trading_algos.core.logger import log_event
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
        from trading_algos.core.logger import log_event
        log_event("SL_MODIFY", success=success, ticket=position_ticket, new_sl=sl, retcode=r.retcode)
        return success