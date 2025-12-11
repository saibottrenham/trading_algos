# trading_algos/config.py
"""Ultra-clean config – only what we actually use now"""

CHECK_INTERVAL_SEC = 2                  # Poll fast for spikes

# ONE AND ONLY PROFIT RULE
PROFIT_TO_ACTIVATE_TRAILING = 10.0      # Wait for +$10 before doing ANYTHING

# Broker costs
COMMISSION_PER_LOT = 3.0                # Your round-turn commission per lot

# Volume-ATR aggression (tuned for 2-3 min spikes)
BASE_MULTIPLIER = 2.0
VOLUME_SENSITIVITY = 2.0
MIN_MULTIPLIER = 1.0
MAX_MULTIPLIER = 4.0
ATR_PERIOD = 14
VOLUME_LOOKBACK = 14