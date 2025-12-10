r"""
trail_my_trade.py — Smart Volume-Adjusted Trailing Stop (MT5 Python)

FEATURES
→ ATR(14) × volume-scaled multiplier (high volume = wide stop, low volume = tight stop)
→ Only activates when gross profit > $0.10 (configurable)
→ Every SL move guarantees real profit after swap + commission
→ Ratchet-only — SL never moves backwards
→ Automatically removes any existing SL if conditions are not met
→ Respects broker minimum stop distance — no more error 10027
→ Tells you the exact price needed to safely trail

USAGE — EXACT COMMANDS
   cd C:\Users\Administrator\Desktop
   py -3.9 trail_my_trade.py                    # interactive
   py -3.9 trail_my_trade.py --ticket 123456789
   py -3.9 trail_my_trade.py EURUSD --ticket 123456789

CONFIG (top of file)
    MIN_PROFIT_TO_START  = 0.10    # $ threshold to begin trailing
    EXTRA_SAFETY_BUFFER  = 1.00    # extra $ extra profit to keep
    BASE_MULTIPLIER      = 3.0
    VOLUME_SENSITIVITY   = 1.5
"""

try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:  # Running on Mac / CI
    _MT5_AVAILABLE = False
    # Create a dummy mt5 object with the constants we use
    class _DummyMT5:
        TIMEFRAME_M5 = 5
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        ORDER_TIME_GTC = 0
        ORDER_FILLING_IOC = 2
        TRADE_ACTION_SLTP = 6
        TRADE_RETCODE_DONE = 10009

        @staticmethod
        def initialize():
            return True

        @staticmethod
        def shutdown():
            pass

        @staticmethod
        def symbol_info(symbol):
            # Return a realistic mock
            from types import SimpleNamespace
            info = SimpleNamespace()
            info.digits = 5
            info.point = 0.00001
            info.trade_contract_size = 100000
            info.trade_stops_level = 10
            return info

        @staticmethod
        def copy_rates_from_pos(*args, **kwargs):
            return None

        @staticmethod
        def positions_get(*args, **kwargs):
            return []

    mt5 = _DummyMT5()

import pandas as pd
import numpy as np
import time
import sys
from datetime import datetime

# ========================= CONFIG =========================
ATR_PERIOD           = 14
BASE_MULTIPLIER      = 3.0
VOLUME_LOOKBACK      = 20
VOLUME_SENSITIVITY   = 1.5
MIN_MULTIPLIER       = 1.5
MAX_MULTIPLIER       = 6.0
CHECK_INTERVAL_SEC   = 5
MIN_PROFIT_TO_START  = 0.10
EXTRA_SAFETY_BUFFER  = 1.00      # minimum $ profit we want to lock in
COMMISSION_PER_LOT   = 0     # adjust if your broker charges more/less
# =========================================================

if not mt5.initialize():
    print("MT5 not running or not logged in")
    sys.exit(1)

_active_tickets = set()
_sl_set_tickets = set()

# ====================== HELPERS ======================
def get_volume_ratio(symbol):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, VOLUME_LOOKBACK + 10)
    if rates is None or len(rates) < VOLUME_LOOKBACK:
        return 1.0
    df = pd.DataFrame(rates)
    avg = df['tick_volume'].rolling(VOLUME_LOOKBACK).mean().iloc[-2]
    cur = df['tick_volume'].iloc[-1]
    return cur / avg if avg > 0 else 1.0

def get_atr(symbol):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, ATR_PERIOD + 20)
    if rates is None or len(rates) <= ATR_PERIOD:
        return mt5.symbol_info(symbol).point * 150
    df = pd.DataFrame(rates)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift()).abs(),
        (df['low']  - df['close'].shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(ATR_PERIOD).mean().iloc[-1]

def estimate_commission(pos):
    return pos.volume * COMMISSION_PER_LOT

def profit_if_sl_hit(pos, sl_price):
    info = mt5.symbol_info(pos.symbol)
    if not info or sl_price == 0:
        return 0.0
    price_diff = (sl_price - pos.price_open) if pos.type == mt5.ORDER_TYPE_BUY else (pos.price_open - sl_price)
    gross = price_diff * pos.volume * info.trade_contract_size
    return gross + pos.swap - estimate_commission(pos)

def send_modify(pos, sl, digits):
    req = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": pos.ticket,
        "symbol": pos.symbol,
        "sl": round(sl, digits),
        "tp": pos.tp,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC
    }
    r = mt5.order_send(req)
    if r.retcode == mt5.TRADE_RETCODE_DONE:
        locked = profit_if_sl_hit(pos, sl)
        print(f"{datetime.now():%H:%M:%S} | {pos.symbol:12} {'BUY' if pos.type==0 else 'SELL':4} | "
              f"SL SET → {sl:.{digits}f} | Locks ${locked:+.2f}")
        return True
    else:
        print(f"FAILED {r.retcode} — {r.comment}")
        return False

# ====================== MAIN LOGIC ======================
def trail_position(pos):
    info = mt5.symbol_info(pos.symbol)
    if not info:
        return

    ticket = pos.ticket
    is_buy = pos.type == mt5.ORDER_TYPE_BUY
    digits = info.digits
    point  = info.point
    min_dist = max(info.trade_stops_level * point, 30 * point)

    commission = estimate_commission(pos)
    min_required_profit = commission + EXTRA_SAFETY_BUFFER - pos.swap
    required_profit = max(MIN_PROFIT_TO_START, min_required_profit)

    if ticket not in _active_tickets:
        print(f"{datetime.now():%H:%M:%S} | {pos.symbol} #{ticket} started — waiting for ≥ ${required_profit:.2f} profit")
        _active_tickets.add(ticket)

    # Not enough profit yet → remove SL
    if pos.profit < required_profit:
        if pos.sl != 0.0:
            send_modify(pos, 0.0, digits)
        print(f"{datetime.now():%H:%M:%S} | Waiting… ${pos.profit:+.2f} < ${required_profit:.2f}")
        return

    # FIRST PROTECTIVE SL — only when we can lock positive profit
    if ticket not in _sl_set_tickets:
        # We want to lock at least EXTRA_SAFETY_BUFFER after costs
        target_locked_profit = EXTRA_SAFETY_BUFFER

        if is_buy:
            # Solve: (SL - open) * vol * contract_size + swap - comm = target
            target_sl = pos.price_open + (target_locked_profit + commission - pos.swap) / (pos.volume * info.trade_contract_size)
            new_sl = min(target_sl, pos.price_current - min_dist)  # don't violate min distance
        else:
            target_sl = pos.price_open - (target_locked_profit + commission - pos.swap) / (pos.volume * info.trade_contract_size)
            new_sl = max(target_sl, pos.price_current + min_dist)

        new_sl = round(new_sl, digits)
        actual_locked = profit_if_sl_hit(pos, new_sl)

        # Only set if we actually lock positive profit
        if actual_locked >= 0.01:
            if send_modify(pos, new_sl, digits):
                _sl_set_tickets.add(ticket)
                print(f"{datetime.now():%H:%M:%S} | FIRST SAFE SL LOCKED @ {new_sl:.{digits}f} → ${actual_locked:+.2f}")
            return
        else:
            print(f"{datetime.now():%H:%M:%S} | Cannot set safe SL yet — would only lock ${actual_locked:+.2f}")
            return

    # NORMAL ATR TRAILING
    vol_ratio = get_volume_ratio(pos.symbol)
    mult = np.clip(BASE_MULTIPLIER * (vol_ratio ** (1/VOLUME_SENSITIVITY)), MIN_MULTIPLIER, MAX_MULTIPLIER)
    atr = get_atr(pos.symbol)

    if is_buy:
        candidate = pos.price_current - mult * atr
        new_sl = max(candidate, pos.sl)
        new_sl = min(new_sl, pos.price_current - min_dist)
    else:
        candidate = pos.price_current + mult * atr
        new_sl = min(candidate, pos.sl)
        new_sl = max(new_sl, pos.price_current + min_dist)

    new_sl = round(new_sl, digits)

    if (is_buy and new_sl > pos.sl + point) or (not is_buy and new_sl < pos.sl - point):
        send_modify(pos, new_sl, digits)
    else:
        locked = profit_if_sl_hit(pos, new_sl)
        print(f"{datetime.now():%H:%M:%S} | Holding SL {new_sl:.{digits}f} (×{mult:.2f}) → ${locked:+.2f}")

# ====================== START ======================
def select_position():
    positions = mt5.positions_get()
    if not positions:
        print("No open positions")
        mt5.shutdown()
        sys.exit(0)

    print("\nOPEN POSITIONS:")
    for i, p in enumerate(positions, 1):
        t = "BUY" if p.type == 0 else "SELL"
        sl = f"{p.sl:.5f}" if p.sl > 0 else "-"
        print(f"{i:2}. {p.ticket} | {p.symbol:12} | {t:4} | {p.volume:>5} lots | "
              f"Open {p.price_open:.5f} | SL {sl} | ${p.profit:+.2f}")

    while True:
        c = input("\nEnter number or ticket: ").strip()
        if c.isdigit():
            n = int(c)
            if 1 <= n <= len(positions):
                return positions[n-1]
            if len(c) >= 7:
                for p in positions:
                    if p.ticket == n:
                        return p
        print("Invalid input")

# CLI or interactive
def _run_trailing():
    # Original interactive selector
    def select_position():
        positions = mt5.positions_get()
        if not positions:
            print("No open positions")
            mt5.shutdown()
            sys.exit(0)

        print("\nOPEN POSITIONS:")
        for i, p in enumerate(positions, 1):
            t = "BUY" if p.type == 0 else "SELL"
            sl = f"{p.sl:.5f}" if p.sl > 0 else "-"
            print(f"{i:2}. {p.ticket} | {p.symbol:12} | {t:4} | {p.volume:>5} lots | "
                  f"Open {p.price_open:.5f} | SL {sl} | ${p.profit:+.2f}")

        while True:
            c = input("\nEnter number or ticket: ").strip()
            if c.isdigit():
                n = int(c)
                if 1 <= n <= len(positions):
                    return positions[n-1]
                # allow direct ticket entry
                if len(c) >= 7:
                    for p in positions:
                        if p.ticket == n:
                            return p
            print("Invalid input")

    # CLI arguments
    if len(sys.argv) > 1:
        from argparse import ArgumentParser
        parser = ArgumentParser()
        parser.add_argument("symbol", nargs='?', default=None)
        parser.add_argument("--ticket", type=int)
        parser.add_argument("--magic", type=int)
        args = parser.parse_args()

        positions = (mt5.positions_get(symbol=args.symbol)
                     if args.symbol else mt5.positions_get())
        if not positions:
            print("No positions found matching criteria")
            mt5.shutdown()
            sys.exit(1)

        pos = None
        for p in positions:
            if (args.ticket and p.ticket == args.ticket) or \
               (args.magic and p.magic == args.magic):
                pos = p
                break
        if not pos:
            pos = positions[0]  # fallback to first

    else:
        pos = select_position()

    if not pos:
        mt5.shutdown()
        sys.exit(1)

    print(f"\nTrailing started → {pos.symbol} #{pos.ticket} "
          f"{'BUY' if pos.type==0 else 'SELL'} {pos.volume} lots")
    print("Press Ctrl+C to stop\n" + "—" * 70)

    try:
        while True:
            cur = mt5.positions_get(ticket=pos.ticket)
            if not cur:
                print(f"[{datetime.now():%H:%M:%S}] Position closed")
                break
            trail_position(cur[0])
            time.sleep(CHECK_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\nStopped by user")
    finally:
        mt5.shutdown()


# ←←← ONLY run when executed directly (not when imported by pytest)
if __name__ == "__main__":
    _run_trailing()
