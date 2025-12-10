"""
Thin CLI runner for the modular trailing engine.
Supports interactive selection, CLI filters, and pluggable engines.
"""

import sys
import time
import MetaTrader5 as mt5
from datetime import datetime
from argparse import ArgumentParser

from trading_algos.config import CHECK_INTERVAL_SEC
from trading_algos.core.position import Position
from trading_algos.trailing.volume_atr import VolumeATRTrailing  # ← swap this line for new engines

engine = VolumeATRTrailing()  # ← one-line engine swap

def select_position():
    """Interactive position selector — runs when no CLI args given."""
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
            # Direct ticket entry
            for p in positions:
                if p.ticket == n:
                    return p
        print("Invalid input")

def main():
    if not mt5.initialize():
        print("MT5 not running or not logged in")
        sys.exit(1)

    # Parse args — if no args, go interactive
    parser = ArgumentParser(description="Smart Trailing Engine")
    parser.add_argument("symbol", nargs='?', default=None, help="Symbol filter")
    parser.add_argument("--ticket", type=int, help="Specific ticket")
    parser.add_argument("--magic", type=int, help="Magic number filter")
    args = parser.parse_args()

    if len(sys.argv) == 1:  # No args → interactive
        pos = select_position()
    else:
        positions = mt5.positions_get(symbol=args.symbol) if args.symbol else mt5.positions_get()
        if not positions:
            print("No positions matching criteria")
            mt5.shutdown()
            sys.exit(1)

        pos = None
        for p in positions:
            if (args.ticket and p.ticket == args.ticket) or (args.magic and p.magic == args.magic):
                pos = p
                break
        if not pos:
            pos = positions[0]  # Fallback to first

    print(f"\nTrailing started → {pos.symbol} #{pos.ticket} {'BUY' if pos.type==0 else 'SELL'} {pos.volume} lots")
    print("Press Ctrl+C to stop\n" + "—" * 70)

    try:
        while True:
            cur = mt5.positions_get(ticket=pos.ticket)
            if not cur:
                print(f"[{datetime.now():%H:%M:%S}] Position closed")
                break
            if "python" in getattr(cur[0], "comment", "").lower():  # Safety filter
                current_pos = Position.from_mt5(cur[0])
                engine.trail(current_pos)
            time.sleep(CHECK_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\nStopped by user")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main()