# tests/conftest.py
import pytest
from unittest.mock import Mock, MagicMock
import numpy as np

@pytest.fixture(autouse=True)
def mock_mt5_and_broker(monkeypatch):
    # Mock MetaTrader5
    mock_mt5 = MagicMock()
    mock_mt5.TIMEFRAME_M5 = 5
    mock_mt5.TIMEFRAME_M1 = 1
    monkeypatch.setattr("trading_algos.trailing.volume_atr.mt5", mock_mt5)
    monkeypatch.setattr("trading_algos.core.broker.mt5", mock_mt5)

    # Mock Broker.get_symbol_info
    mock_info = Mock()
    mock_info.point = 0.00001
    mock_info.digits = 5
    mock_info.trade_stops_level = 10
    mock_info.trade_contract_size = 100000

    def get_symbol_info(symbol):
        return mock_info

    monkeypatch.setattr("trading_algos.core.broker.Broker.get_symbol_info", get_symbol_info)

    # Treat np.float64 as float for assertions
    monkeypatch.setattr(np, "float64", float)