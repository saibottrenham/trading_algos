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