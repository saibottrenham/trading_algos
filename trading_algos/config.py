# trading_algos/config.py
"""Ultra-clean config – only what we actually use now"""

CHECK_INTERVAL_SEC = 1  # Faster poll for gold spikes (was 2)

# Dynamic profit rule base
BASE_PROFIT_TO_ACTIVATE = 10.0  # Base for 0.05 lots; scales up
BASE_LOT_FOR_PROFIT = 0.05  # Reference lot for base profit

# Broker costs
COMMISSION_PER_LOT = 0  # Your round-turn commission per lot

# Volume-ATR aggression (tuned for 2-3 min spikes; optimize for XAUUSD)
BASE_MULTIPLIER = 2.0  # Base trail tightness
VOLUME_SENSITIVITY = 2.0  # Vol response
MIN_MULTIPLIER = 1.0
MAX_MULTIPLIER = 4.0  # Cap for over-vol
ATR_PERIOD = 10  # Shorter for gold vol (was 14; 5-10 optimal per research)
VOLUME_LOOKBACK = 10  # Match ATR for quick adapts

# Dynamic SL buffer (points from current price)
SL_BUFFER_BASE_POINTS = 30  # Fixed min for small lots
SL_BUFFER_PER_LOT = 20  # Extra per full lot to handle slippage on bigger vols