from unittest.mock import Mock, MagicMock, patch
import pytest
from trading_algos.core.position import Position
from trading_algos.trailing.volume_atr import VolumeATRTrailing
from trading_algos.config import PROFIT_TO_ACTIVATE_TRAILING


def create_mock_position(ticket=123456, symbol="EURUSD", volume=0.1, price_open=1.10000,
                         price_current=1.11000, profit=15.0, sl=0.0, tp=0.0, swap=0.0, is_buy=True):
    pos = Mock()
    pos.ticket = ticket
    pos.symbol = symbol
    pos.volume = volume
    pos.price_open = price_open
    pos.price_current = price_current
    pos.profit = profit
    pos.sl = sl
    pos.tp = tp
    pos.swap = swap
    pos.type = 0 if is_buy else 1
    pos.comment = "test"
    return Position.from_mt5(pos)


@pytest.mark.parametrize("profit, expected_sl", [
    (9.99, 0.0),        # below $10 → no SL
    (10.00, 1.10103),   # exactly $10 → locks ~$10
    (25.00, 1.10103),   # higher → still initial lock $10
])
@patch("trading_algos.core.broker.Broker.modify_sl")
def test_volume_scaled_atr_trailing(mock_modify, profit, expected_sl):
    engine = VolumeATRTrailing()

    # First call: hit $10+ → should set initial SL
    pos = create_mock_position(profit=profit, price_current=1.11000)

    with patch("trading_algos.trailing.volume_atr.pd.Timestamp") as mock_ts:
        mock_ts.now.return_value.timestamp.return_value = 1700000000.0
        engine.trail(pos)

    if profit >= PROFIT_TO_ACTIVATE_TRAILING:
        # Allow tiny rounding differences (0.00001)
        called_sl = mock_modify.call_args[0][2]
        assert abs(called_sl - expected_sl) < 0.00001
        mock_modify.assert_called_once()
    else:
        mock_modify.assert_not_called()

    mock_modify.reset_mock()

    # Second call: trail further up
    pos2 = create_mock_position(profit=profit + 10, price_current=1.11200, sl=called_sl if profit >= 10 else 0.0)
    engine.trail(pos2)

    if profit >= PROFIT_TO_ACTIVATE_TRAILING:
        assert mock_modify.called
        new_sl = mock_modify.call_args[0][2]
        assert new_sl > called_sl  # ratchet only