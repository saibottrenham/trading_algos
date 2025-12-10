# trading_algos/config.py
"""
Central configuration for the trailing engine.
Easy to tweak, no circular imports.
"""

# Trailing logic
BASE_MULTIPLIER = 3.0
VOLUME_SENSITIVITY = 1.5
MIN_MULTIPLIER = 1.5
MAX_MULTIPLIER = 6.0

# Profit & safety
MIN_PROFIT_TO_START = 0.10
EXTRA_SAFETY_BUFFER = 1.00
COMMISSION_PER_LOT = 0.0

# Data lookbacks
ATR_PERIOD = 14
VOLUME_LOOKBACK = 20

# Runtime
CHECK_INTERVAL_SEC = 5