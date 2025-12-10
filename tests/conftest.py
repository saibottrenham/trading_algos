import pytest
import numpy as np
import pandas as pd

@pytest.fixture
def sample_rates():
    np.random.seed(42)
    n = 100
    base = 1.1000
    price = base + np.cumsum(np.random.randn(n) * 0.0002)
    return pd.DataFrame({
        'time': pd.date_range("2025-01-01", periods=n, freq="5min"),
        'open': price * (1 + np.random.randn(n)*0.00005),
        'high': price + abs(np.random.randn(n)*0.00015),
        'low': price - abs(np.random.randn(n)*0.00015),
        'close': price,
        'tick_volume': np.random.randint(800, 5000, n),
    })