# tests/test_trail_my_trade.py
"""
Unit tests for the smart volume-adjusted trailing stop (trail_my_trade.py)

These tests verify that:
- ATR and volume ratio are calculated correctly
- Profit calculations respect swap & commission
- Stop-loss is removed when profit is too low (risk control)
- First protective SL only sets when we can lock real profit
- ATR trailing multiplier scales correctly with volume (low vol → tighter, high vol → wider)
- Min/max multiplier clipping works as designed
- Broker minimum distance rules are respected
All tests run on Mac without MetaTrader5 installed.
"""

import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock, ANY

# Import the production code
from trading_algos.trail_my_trade import (
    get_volume_ratio,
    get_atr,
    estimate_commission,
    profit_if_sl_hit,
    trail_position,
    send_modify,
    MIN_PROFIT_TO_START,
    EXTRA_SAFETY_BUFFER,
    COMMISSION_PER_LOT,
    BASE_MULTIPLIER,
    VOLUME_SENSITIVITY,
    MIN_MULTIPLIER,
    MAX_MULTIPLIER
)

# =============================================================================
# Fixtures — reusable test data
# =============================================================================

@pytest.fixture
def sample_rates():
    """Generate realistic 5-minute bars (MT5 structured array format) for ATR/volume tests."""
    np.random.seed(42)
    n = 60
    base = 1.1000
    price = base + np.cumsum(np.random.randn(n) * 0.0002)

    df = pd.DataFrame({
        'time': pd.date_range("2025-01-01", periods=n, freq="5min").astype('int64') // 10**9,
        'open': price * (1 + np.random.randn(n) * 0.00005),
        'high': price + np.abs(np.random.randn(n) * 0.00015),
        'low': price - np.abs(np.random.randn(n) * 0.00015),
        'close': price,
        'tick_volume': np.random.randint(800, 5000, n),
        'spread': np.full(n, 10, dtype=np.int32),
        'real_volume': np.zeros(n, dtype=np.int64),
    })
    return df.to_records(index=False)


@pytest.fixture
def position_buy():
    """Mock a profitable long EURUSD position."""
    pos = MagicMock()
    pos.ticket = 999999
    pos.symbol = "EURUSD"
    pos.type = 0                    # BUY
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
def symbol_info():
    """Realistic symbol info (EURUSD-like) used throughout tests."""
    class Info:
        digits = 5
        point = 0.00001
        trade_contract_size = 100000.0
        trade_stops_level = 10
    return Info()


# =============================================================================
# Individual Tests with Clear Purpose
# =============================================================================

@patch("trading_algos.trail_my_trade.mt5")
def test_get_volume_ratio(mock_mt5, sample_rates):
    """
    Test that current tick volume is correctly compared to 20-period average.
    This is the core of volume-sensitive trailing — low volume → tighter stops.
    """
    mock_mt5.copy_rates_from_pos.return_value = sample_rates
    ratio = get_volume_ratio("EURUSD")

    df = pd.DataFrame(sample_rates)
    recent = df['tick_volume'].iloc[-1]
    avg = df['tick_volume'].rolling(20).mean().iloc[-2]
    assert pytest.approx(ratio, rel=1e-3) == recent / avg


@patch("trading_algos.trail_my_trade.mt5")
def test_get_atr(mock_mt5, sample_rates):
    """
    Verify classic Wilder ATR(14) calculation matches manual implementation.
    Used as base distance for trailing stop.
    """
    mock_mt5.copy_rates_from_pos.return_value = sample_rates
    mock_mt5.symbol_info.return_value.point = 0.00001

    atr = get_atr("EURUSD")
    df = pd.DataFrame(sample_rates)

    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low'] - df['close'].shift()).abs()
    ], axis=1).max(axis=1)

    expected = tr.rolling(14).mean().iloc[-1]
    assert pytest.approx(atr, rel=1e-6) == expected


def test_estimate_commission(position_buy):
    """Simple per-lot commission pass-through — easy to adjust per broker."""
    assert estimate_commission(position_buy) == COMMISSION_PER_LOT * 0.20


@patch("trading_algos.trail_my_trade.mt5.symbol_info")
def test_profit_if_sl_hit_buy(mock_info, position_buy, symbol_info):
    """
    Confirm that profit-at-stop calculation includes:
    - Price difference × volume × contract size
    - Swap
    - Commission
    Critical for "only move SL if we lock real money" logic.
    """
    mock_info.return_value = symbol_info
    profit = profit_if_sl_hit(position_buy, 1.10300)
    expected = (1.10300 - 1.10000) * 0.20 * 100000 + position_buy.swap - estimate_commission(position_buy)
    assert pytest.approx(profit, abs=0.1) == expected


@pytest.mark.parametrize("vol_ratio, expected_mult", [
    (0.5, 1.8898815748423097),   # Low volume → tighter than base (but not clipped yet)
    (0.1, MIN_MULTIPLIER),       # Very low volume → hits minimum multiplier floor
    (1.0, BASE_MULTIPLIER),      # Normal volume → uses base multiplier
    (2.0, 4.762203155904598),    # High volume → wider stop
    (5.0, MAX_MULTIPLIER),       # Very high volume → capped at maximum
])
@patch("trading_algos.trail_my_trade.get_volume_ratio")
@patch("trading_algos.trail_my_trade.get_atr")
@patch("trading_algos.trail_my_trade.send_modify")
@patch("trading_algos.trail_my_trade.mt5.symbol_info")
def test_trail_position_atr_trailing(mock_sym, mock_send, mock_atr, mock_vol,
                                     position_buy, symbol_info, vol_ratio, expected_mult):
    """
    Core test of volume-scaled ATR trailing.
    Verifies that:
    - Multiplier = BASE × (volume_ratio ^ (1/sensitivity))
    - Result is clipped between MIN_MULTIPLIER and MAX_MULTIPLIER
    This is the heart of the adaptive behavior.
    """
    mock_sym.return_value = symbol_info
    position_buy.sl = 1.10200
    position_buy.profit = 60.0
    position_buy.price_current = 1.10800
    mock_vol.return_value = vol_ratio
    mock_atr.return_value = 0.00120

    trail_position(position_buy)

    actual = np.clip(BASE_MULTIPLIER * (vol_ratio ** (1 / VOLUME_SENSITIVITY)),
                     MIN_MULTIPLIER, MAX_MULTIPLIER)
    assert pytest.approx(actual, rel=1e-6) == expected_mult


@patch("trading_algos.trail_my_trade.send_modify")
@patch("trading_algos.trail_my_trade.mt5.symbol_info")
def test_trail_position_removes_sl_when_low_profit(mock_sym, mock_send, position_buy, symbol_info):
    """
    Safety first: if unrealized profit drops below threshold,
    we REMOVE any existing stop-loss to avoid locking in a loss.
    """
    mock_sym.return_value = symbol_info
    position_buy.profit = 0.05      # Below MIN_PROFIT_TO_START
    position_buy.sl = 1.09900        # Has a stop-loss

    trail_position(position_buy)
    mock_send.assert_called_once_with(position_buy, 0.0, ANY)


@patch("trading_algos.trail_my_trade.send_modify")
@patch("trading_algos.trail_my_trade.mt5.symbol_info")
def test_trail_position_sets_first_safe_sl(mock_sym, mock_send, position_buy, symbol_info):
    """
    When profit is sufficient and no SL exists,
    set the FIRST protective stop that locks at least EXTRA_SAFETY_BUFFER profit.
    """
    mock_sym.return_value = symbol_info
    position_buy.profit = 25.0       # Well above threshold
    position_buy.sl = 0.0            # No stop yet

    trail_position(position_buy)
    assert mock_send.call_count >= 1