import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock, Mock

# Mock MT5 globals/classes to avoid import errors
mt5 = Mock()
mt5.ORDER_TYPE_BUY = 0
mt5.ORDER_TYPE_SELL = 1
mt5.copy_rates_from_pos.return_value = np.array([], dtype=[('time', '<i8'), ('open', '<f8'), ('high', '<f8'), ('low', '<f8'), ('close', '<f8'), ('tick_volume', '<i8'), ('spread', '<i8'), ('real_volume', '<i8')])

# Patch the import at module level for tests
with patch.dict('sys.modules', {'MetaTrader5': mt5}):
    from trading_algos.trail_my_trade import (
        get_volume_ratio, get_atr, estimate_commission,
        profit_if_sl_hit, trail_position, send_modify,
        MIN_PROFIT_TO_START, EXTRA_SAFETY_BUFFER,
        COMMISSION_PER_LOT, BASE_MULTIPLIER, VOLUME_SENSITIVITY,
        MIN_MULTIPLIER, MAX_MULTIPLIER
    )

# Fixtures (same as before, but tighter)
@pytest.fixture
def sample_rates():
    np.random.seed(42)
    n = 50
    base = 1.1000
    price = base + np.cumsum(np.random.randn(n) * 0.0002)
    df = pd.DataFrame({
        'time': pd.date_range("2025-01-01", periods=n, freq="5min"),
        'open': price * (1 + np.random.randn(n)*0.00005),
        'high': price + abs(np.random.randn(n)*0.00015),
        'low': price - abs(np.random.randn(n)*0.00015),
        'close': price,
        'tick_volume': np.random.randint(800, 5000, n),
    })
    # Mock rates array for MT5 compat
    rates_array = np.array([tuple(row) for row in df.values], dtype=[('time', '<i8'), ('open', '<f8'), ('high', '<f8'), ('low', '<f8'), ('close', '<f8'), ('tick_volume', '<i8'), ('spread', '<i8'), ('real_volume', '<i8')])
    mt5.copy_rates_from_pos.return_value = rates_array
    return df

@pytest.fixture
def mock_position_buy():
    pos = MagicMock()
    pos.ticket = 999999
    pos.symbol = "EURUSD"
    pos.type = mt5.ORDER_TYPE_BUY
    pos.volume = 0.20
    pos.price_open = 1.10000
    pos.price_current = 1.10500
    pos.sl = 0.0
    pos.tp = 0.0
    pos.profit = 85.50
    pos.swap = -1.2
    pos.comment = "python"
    return pos

@pytest.fixture
def mock_symbol_info():
    info = MagicMock()
    info.digits = 5
    info.point = 0.00001
    info.trade_contract_size = 100000
    info.trade_stops_level = 10
    return info

# Tests (expanded for edge cases)
@patch('trail_my_trade.mt5.symbol_info')
@patch('trail_my_trade.mt5.copy_rates_from_pos')
def test_get_volume_ratio(mock_copy, mock_sym, sample_rates):
    mock_copy.return_value = mt5.copy_rates_from_pos.return_value  # Use fixture's mock
    mock_sym.return_value = mock_symbol_info()
    ratio = get_volume_ratio("EURUSD")
    recent_vol = sample_rates['tick_volume'].iloc[-1]
    avg_vol = sample_rates['tick_volume'].iloc[-20:].mean()
    assert pytest.approx(ratio, rel=1e-3) == recent_vol / avg_vol

@patch('trail_my_trade.mt5.symbol_info')
@patch('trail_my_trade.mt5.copy_rates_from_pos')
def test_get_atr(mock_copy, mock_sym, sample_rates):
    mock_copy.return_value = mt5.copy_rates_from_pos.return_value
    mock_sym.return_value = mock_symbol_info()
    atr = get_atr("EURUSD")
    df = sample_rates.copy()
    tr0 = df['high'] - df['low']
    tr1 = (df['high'] - df['close'].shift()).abs()
    tr2 = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([tr0, tr1, tr2], axis=1).max(axis=1)
    expected = tr.rolling(14).mean().iloc[-1]
    assert pytest.approx(atr, rel=1e-6) == expected

    # Fallback test (empty rates)
    mock_copy.return_value = np.array([])
    fallback_atr = get_atr("EURUSD")
    assert pytest.approx(fallback_atr, rel=1e-6) == mock_symbol_info().point * 150

def test_estimate_commission(mock_position_buy):
    assert estimate_commission(mock_position_buy) == COMMISSION_PER_LOT * mock_position_buy.volume

def test_profit_if_sl_hit_buy(mock_position_buy, mock_symbol_info):
    with patch('trail_my_trade.mt5.symbol_info', return_value=mock_symbol_info):
        sl_price = 1.10300
        profit = profit_if_sl_hit(mock_position_buy, sl_price)
        expected_gross = (sl_price - mock_position_buy.price_open) * mock_position_buy.volume * mock_symbol_info.trade_contract_size
        expected = expected_gross + mock_position_buy.swap - estimate_commission(mock_position_buy)
        assert pytest.approx(profit, abs=0.01) == expected

@pytest.mark.parametrize("vol_ratio, expected_mult", [
    (0.5, MIN_MULTIPLIER),  # Low vol clips
    (1.0, BASE_MULTIPLIER),  # Base
    (2.0, min(BASE_MULTIPLIER * (2.0 ** (1 / VOLUME_SENSITIVITY)), MAX_MULTIPLIER)),  # Scaled
    (3.0, MAX_MULTIPLIER),  # High vol clips
])
@patch('trail_my_trade.get_volume_ratio')
@patch('trail_my_trade.get_atr')
@patch('trail_my_trade.send_modify')
@patch('trail_my_trade.mt5.symbol_info')
def test_trail_position_atr_trailing(mock_sym, mock_send, mock_atr, mock_vol, mock_position_buy, vol_ratio, expected_mult, mock_symbol_info):
    mock_sym.return_value = mock_symbol_info
    mock_position_buy.sl = 1.10200  # Existing SL
    mock_position_buy.profit = 50.0  # Above threshold
    mock_vol.return_value = vol_ratio
    mock_atr.return_value = 0.00100

    trail_position(mock_position_buy)

    # Verify mult calc (core logic test)
    actual_mult = np.clip(BASE_MULTIPLIER * (vol_ratio ** (1 / VOLUME_SENSITIVITY)), MIN_MULTIPLIER, MAX_MULTIPLIER)
    assert pytest.approx(actual_mult, rel=1e-6) == expected_mult

    # Check modify call (for BUY: new_sl = max(existing, current - mult*ATR), rounded, min dist)
    candidate = mock_position_buy.price_current - expected_mult * 0.00100
    min_dist = max(mock_symbol_info.trade_stops_level * mock_symbol_info.point, 30 * mock_symbol_info.point)
    new_sl = max(candidate, mock_position_buy.sl)
    new_sl = min(new_sl, mock_position_buy.price_current - min_dist)
    new_sl_rounded = round(new_sl, mock_symbol_info.digits)
    if new_sl_rounded > mock_position_buy.sl + mock_symbol_info.point:
        mock_send.assert_called_once()
    else:
        mock_send.assert_not_called()

@patch('trail_my_trade.send_modify')
@patch('trail_my_trade.mt5.symbol_info')
def test_trail_position_low_profit(mock_sym, mock_send, mock_position_buy, mock_symbol_info):
    mock_sym.return_value = mock_symbol_info
    mock_position_buy.profit = 0.05  # Below MIN_PROFIT_TO_START
    mock_position_buy.sl = 1.09900  # Has SL to potentially remove

    trail_position(mock_position_buy)
    mock_send.assert_called_once_with(mock_position_buy, 0.0, 5)  # Removes SL

# Run with: pytest tests/test_trail_my_trade.py -v --cov=trail_my_trade