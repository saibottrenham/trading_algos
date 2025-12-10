# trading_algos/trail_my_trade.py
import MetaTrader5 as mt5
import time

from trading_algos.config import CHECK_INTERVAL_SEC
from trading_algos.core.position import Position
from trading_algos.trailing.volume_atr import VolumeATRTrailing

engine = VolumeATRTrailing()

def main():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return

    print("Smart trailing engine started — Volume + ATR + Profit Lock")
    try:
        while True:
            positions = mt5.positions_get()
            if positions:
                for p in positions:
                    if "python" in getattr(p, "comment", "").lower():
                        pos = Position.from_mt5(p)
                        engine.trail(pos)
            time.sleep(CHECK_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\nStopped by user")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main()