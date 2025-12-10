# trading_algos/core/position.py
from dataclasses import dataclass
from typing import Any

@dataclass
class Position:
    ticket: int
    symbol: str
    type: int          # 0=buy, 1=sell
    volume: float
    price_open: float
    price_current: float
    sl: float
    tp: float
    profit: float
    swap: float
    comment: str = ""

    @property
    def is_buy(self) -> bool:
        return self.type == 0

    @classmethod
    def from_mt5(cls, mt5_pos: Any) -> "Position":
        return cls(
            ticket=mt5_pos.ticket,
            symbol=mt5_pos.symbol,
            type=mt5_pos.type,
            volume=mt5_pos.volume,
            price_open=mt5_pos.price_open,
            price_current=mt5_pos.price_current,
            sl=mt5_pos.sl,
            tp=mt5_pos.tp,
            profit=mt5_pos.profit,
            swap=mt5_pos.swap,
            comment=getattr(mt5_pos, "comment", ""),
        )