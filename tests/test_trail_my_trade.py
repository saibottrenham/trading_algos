# tests/test_volume_atr_engine.py
"""
Complete test suite for the modular Volume + ATR trailing engine.
All tests pass on Mac, no MetaTrader5 needed.
"""

import pytest
from unittest.mock import patch, MagicMock

from trading_algos.trailing.volume_atr import VolumeATRTrailing
from trading_algos.core.position import Position
from trading_algos.config import (
    BASE_MULTIPLIER, VOLUME_SENSITIVITY, MIN_MULTIPLIER, MAX_MULTIPLIER,
    EXTRA_SAFETY_BUFFER, COMMISSION_PER_LOT
)


@pytest.fixture
def engine():
    return VolumeATRTrailing()


@pytest.fixture
def base_position():
    """Base MT5 position mock — we convert to our Position object."""
    pos = MagicMock()
    pos.ticket = 123456
    pos.symbol = "EURUSD"
    pos.type = 0
    pos.volume = 0.20
    pos.price_open = 1.10000
    pos.price_current = 1.10800
    pos.sl = 1.10200          # Pretend first protective SL already set
    pos.tp = 0.0
    pos.profit = 160.0
    pos.swap = -1.2
    pos.comment = "python"
    return pos


@pytest.fixture
def symbol_info():
    info = MagicMock()
    info.digits = 5
    info.point = 0.00001
    info.trade_contract_size = 100000.0
    info.trade_stops_level = 10
    return info


def make_position(mock_pos):
    return Position.from_mt5(mock_pos)


# =============================================================================
# Tests
# =============================================================================

@patch("trading_algos.core.broker.Broker.get_symbol_info")
@patch("trading_algos.core.broker.Broker.modify_sl")
def test_first_protective_sl(mock_modify, mock_info, engine, base_position, symbol_info):
    """First call with profit → set protective SL that locks ~$1+ profit."""
    mock_info.return_value = symbol_info
    base_position.profit = 30.0
    base_position.sl = 0.0
    pos = make_position(base_position)

    engine.trail(pos)

    new_sl = mock_modify.call_args[0][2]
    assert 1.1000 < new_sl < 1.1010
    # Mark as set internally
    engine.first_sl_set.add(pos.ticket)


@patch("trading_algos.core.broker.Broker.get_symbol_info")
@patch("trading_algos.core.broker.Broker.modify_sl")
def test_removes_sl_when_profit_too_low(mock_modify, mock_info, engine, base_position, symbol_info):
    mock_info.return_value = symbol_info
    base_position.profit = 0.05
    base_position.sl = 1.09900
    pos = make_position(base_position)

    engine.trail(pos)
    mock_modify.assert_called_once_with(123456, "EURUSD", 0.0, 0.0, 5)


@patch("trading_algos.core.broker.Broker.get_symbol_info")
@patch("trading_algos.core.broker.Broker.modify_sl")
def test_volume_scaled_atr_trailing(mock_modify, mock_info, engine, base_position, symbol_info):
    """
    After first SL is set → ATR trailing with volume scaling takes over.
    Tests all volume regimes: low → tight, high → wide, clipping.
    """
    mock_info.return_value = symbol_info
    base_position.sl = 1.10200
    base_position.profit = 160.0
    pos = make_position(base_position)

    # Mark first SL as already done
    engine.first_sl_set.add(pos.ticket)

    test_cases = [
        (0.3, 1.5),        # Clipped to MIN_MULTIPLIER
        (0.5, 1.88988),    # Calculated
        (1.0, 3.0),        # Base
        (2.0, 4.76220),    # Wider
        (10.0, 6.0),       # Clipped to MAX_MULTIPLIER
    ]

    for vol_ratio, expected_mult in test_cases:
        mock_modify.reset_mock()

        with patch.object(engine, '_get_volume_ratio', return_value=vol_ratio):
            with patch.object(engine, '_get_atr', return_value=0.00100):
                engine.trail(pos)

                expected_sl = pos.price_current - expected_mult * 0.00100
                # Ratchet: only move up
                expected_sl = max(expected_sl, pos.sl)
                # Respect min distance (~30 pips)
                expected_sl = min(expected_sl, pos.price_current - 0.00030)
                expected_sl = round(expected_sl, 5)

                if expected_sl > pos.sl + 0.00001:
                    mock_modify.assert_called_once_with(
                        123456, "EURUSD", expected_sl, 0.0, 5
                    )
                else:
                    mock_modify.assert_not_called()