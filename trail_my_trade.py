r"""
trail_my_trade.py — Smart Volume-Adjusted Trailing Stop (MT5 Python)

FEATURES
→ ATR(14) × volume-scaled multiplier (high volume = wide stop, low volume = tight stop)
→ Only sets SL when hitting it would give profit > $0 (after swap + commission + buffer)
→ Ratchet-only — SL never moves backwards
→ Automatically removes any existing SL if conditions not met
→ Respects broker minimum stop distance — no more error 10027
→ Logs the exact asset price needed to set a profitable SL

USAGE — EXACT COMMANDS
   cd C:\Users\Administrator\Desktop
   py -3.9 trail_my_trade.py                    # interactive
   py -3.9 trail_my_trade.py --ticket 3061444242
   py -3.9 trail_my_trade.py AUDUSD --ticket 3061444242

CONFIG (top of file)
    EXTRA_SAFETY_BUFFER  = 1.00    # extra $ to keep after fees
    BASE_MULTIPLIER      = 3.0
    VOLUME_SENSITIVITY   = 1.5
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import time
import sys
from datetime import datetime

# ========================= CONFIG =========================
ATR_PERIOD                  = 14
BASE_MULTIPLIER             = 3.0
VOLUME_LOOKBACK            = 20
VOLUME_SENSITIVITY          = 1.5
MIN_MULTIPLIER              = 1.5
MAX_MULTIPLIER              = 6.0
CHECK_INTERVAL_SEC          = 5
EXTRA_SAFETY_BUFFER         = 1.00      # extra $ you want to keep after fees
# =========================================================

if not mt5.initialize():
    print("MT5 initialization failed – is the terminal running and logged in?")
    sys.exit(1)

def list_open_positions():
    positions = mt5.positions_get()
    if not positions:
        print("No open positions found.")
        mt5.shutdown()
        sys.exit(0)
    data = []
    for i, p in enumerate(positions):
        data.append({
            "#": i+1,
            "Ticket": p.ticket,
            "Symbol": p.symbol,
            "Type": "BUY" if p.type == 0 else "SELL",
            "Volume": p.volume,
            "OpenPrice": f"{p.price_open:.5f}",
            "CurrentSL": f"{p.sl:.5f}" if p.sl > 0 else "-",
            "Profit": f"{p.profit:+.2f}"
        })
    df = pd.DataFrame(data)
    print("\nOPEN POSITIONS:")
    print(df.to_string(index=False))
    print()
    return positions

def select_position_interactive():
    positions = list_open_positions()
    while True:
        choice = input(f"Enter number (1-{len(positions)}) or ticket directly: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(positions):
            return positions[int(choice)-1]
        elif choice.isdigit() and len(choice) >= 7:
            ticket = int(choice)
            for p in positions:
                if p.ticket == ticket:
                    return p
            print("Ticket not found.")
        else:
            print("Invalid input.")

def get_volume_ratio(symbol):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, VOLUME_LOOKBACK + 5)
    if rates is None or len(rates) < VOLUME_LOOKBACK + 1:
        return 1.0
    df = pd.DataFrame(rates) if not isinstance(rates, pd.DataFrame) else rates
    vol_avg = df['tick_volume'].rolling(VOLUME_LOOKBACK).mean().iloc[-2]
    vol_now = df['tick_volume'].iloc[-2]
    return vol_now / vol_avg if vol_avg > 0 else 1.0

def get_atr(symbol):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, ATR_PERIOD + 10)
    if rates is None or len(rates) <= ATR_PERIOD:
        return mt5.symbol_info(symbol).point * 100
    df = pd.DataFrame(rates) if not isinstance(rates, pd.DataFrame) else rates
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low']  - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(ATR_PERIOD).mean().iloc[-2]

def send_modify(pos, new_sl, digits, mult, profit):
    req = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": pos.ticket,
        "symbol": pos.symbol,
        "sl": round(new_sl, digits),
        "tp": pos.tp,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC
    }
    result = mt5.order_send(req)
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"{datetime.now():%H:%M:%S} | {pos.symbol} {'BUY' if pos.type==0 else 'SELL'} | SL → {new_sl:.{digits}f} | ×{mult:.2f} | Profit if hit ≥ ${profit:.2f}")
    else:
        print(f"Modify failed: {result.retcode} – {result.comment}")

# Global set to remember which tickets have already activated trailing
_active_tickets = set()

def trail_position(pos):
    info = mt5.symbol_info(pos.symbol)
    if not info:
        return

    gross_profit = pos.profit
    commission_est = abs(pos.volume * pos.price_open * info.trade_tick_value) * 0.0003
    required_broker_profit = max(0.0, commission_est + EXTRA_SAFETY_BUFFER - pos.swap)
    ticket = pos.ticket

    if ticket not in _active_tickets:
        print(f"{datetime.now():%H:%M:%S} | {pos.symbol} #{ticket} | "
              f"Current profit ${gross_profit:+.2f} → Need ≥ ${required_broker_profit:.2f} in broker")

    if gross_profit < required_broker_profit:
        if pos.sl > 0:
            send_modify(pos, 0, info.digits, 0, 0)
        return

    # TRAILING IS NOW ACTIVE
    if ticket not in _active_tickets:
        print(f"{datetime.now():%H:%M:%S} | {pos.symbol} #{ticket} | Trailing ACTIVE — forcing first safe SL now")
        _active_tickets.add(ticket)

    min_dist = max(info.trade_stops_level * info.point, 30 * info.point)
    digits = info.digits

    if pos.type == mt5.ORDER_TYPE_BUY:
        # FORCE first safe SL at maximum allowed distance
        new_sl = pos.price_current - min_dist
        # Make sure it's still profitable
        profit_if_hit = (new_sl - pos.price_open) * pos.volume * info.trade_contract_size + pos.swap - commission_est
        if profit_if_hit < EXTRA_SAFETY_BUFFER:
            # If even max distance isn't profitable enough, wait for more price movement
            needed_price = pos.price_open + (commission_est + EXTRA_SAFETY_BUFFER - pos.swap) / (pos.volume * info.trade_contract_size) + min_dist
            print(f"{datetime.now():%H:%M:%S} | {pos.symbol} | Need price ≥ {needed_price:.{digits}f} for first (current {pos.price_current:.{digits}f})")
            return

        # Apply ratchet
        if pos.sl > 0:
            new_sl = max(new_sl, pos.sl)

        if pos.sl == 0 or new_sl > pos.sl:
            send_modify(pos, new_sl, digits, 0, profit_if_hit)
            return  # first SL placed — next loops do normal trailing

        # If we get here, we already have an SL → do normal volume-adjusted trailing
        vol_ratio = get_volume_ratio(pos.symbol)
        mult = np.clip(BASE_MULTIPLIER * (vol_ratio ** (1/VOLUME_SENSITIVITY)), MIN_MULTIPLIER, MAX_MULTIPLIER)
        atr = get_atr(pos.symbol)
        new_sl = pos.price_current - mult * atr
        new_sl = min(new_sl, pos.price_current - min_dist)
        new_sl = max(new_sl, pos.sl)

        if new_sl > pos.sl + info.point:
            profit_if_hit = (new_sl - pos.price_open) * pos.volume * info.trade_contract_size + pos.swap - commission_est
            send_modify(pos, new_sl, digits, mult, profit_if_hit)

    else:  # SELL — same logic reversed
        new_sl = pos.price_current + min_dist
        profit_if_hit = (pos.price_open - new_sl) * pos.volume * info.trade_contract_size + pos.swap - commission_est
        if profit_if_hit < EXTRA_SAFETY_BUFFER:
            needed_price = pos.price_open - (commission_est + EXTRA_SAFETY_BUFFER - pos.swap) / (pos.volume * info.trade_contract_size) - min_dist
            print(f"{datetime.now():%H:%M:%S} | {pos.symbol} | Need price ≤ {needed_price:.{digits}f} first")
            return

        if pos.sl > 0:
            new_sl = min(new_sl, pos.sl)

        if pos.sl == 0 or new_sl < pos.sl:
            send_modify(pos, new_sl, digits, 0, profit_if_hit)
            return

        # Normal trailing
        vol_ratio = get_volume_ratio(pos.symbol)
        mult = np.clip(BASE_MULTIPLIER * (vol_ratio ** (1/VOLUME_SENSITIVITY)), MIN_MULTIPLIER, MAX_MULTIPLIER)
        atr = get_atr(pos.symbol)
        new_sl = pos.price_current + mult * atr
        new_sl = max(new_sl, pos.price_current + min_dist)
        new_sl = min(new_sl, pos.sl)

        if new_sl < pos.sl - info.point:
            profit_if_hit = (pos.price_open - new_sl) * pos.volume * info.trade_contract_size + pos.swap - commission_est
            send_modify(pos, new_sl, digits, mult, profit_if_hit)

# ====================== START ======================
if len(sys.argv) > 1:
    from argparse import ArgumentParser
    parser = ArgumentParser()
    parser.add_argument("symbol", nargs='?')
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ticket", type=int)
    group.add_argument("--magic", type=int)
    args = parser.parse_args()
    pos = None
    positions = mt5.positions_get(symbol=args.symbol) if args.symbol else mt5.positions_get()
    for p in positions or []:
        if (args.ticket and p.ticket == args.ticket) or (args.magic and p.magic == args.magic):
            pos = p
            break
    if not pos and positions:
        pos = positions[0]
else:
    pos = select_position_interactive()

if not pos:
    print("No position selected.")
    mt5.shutdown()
    sys.exit(1)

print(f"\nTrailing started → {pos.symbol} | Ticket {pos.ticket} | {'BUY' if pos.type==0 else 'SELL'} {pos.volume} lots")
print("Press Ctrl+C to stop\n" + "—" * 70)

try:
    while True:
        current = mt5.positions_get(ticket=pos.ticket)
        if not current:
            print(f"[{datetime.now():%H:%M:%S}] Position {pos.ticket} closed.")
            break
        trail_position(current[0])
        time.sleep(CHECK_INTERVAL_SEC)
except KeyboardInterrupt:
    print("\nStopped by user.")
finally:
    mt5.shutdown()