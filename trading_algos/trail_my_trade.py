# trading_algos/trail_my_trade.py
"""
Smart Trailing Engine Runner
- NO comment filter by default
- Optional: --filter-comment  → only trail positions with that text in comment
"""

import sys
import time
from datetime import datetime
import MetaTrader5 as mt5
from argparse import ArgumentParser

from trading_algos.config import CHECK_INTERVAL_SEC
from trading_algos.core.position import Position

# ── Engines ─────────────────────────────────────
from trading_algos.trailing.volume_atr import VolumeATRTrailing
# from trading_algos.trailing.fixed_pips import FixedPipsTrailing

AVAILABLE_ENGINES = {
    "volume_atr": VolumeATRTrailing,
    # "fixed_pips": FixedPipsTrailing,
}

def select_engine():
    print("\nAVAILABLE ENGINES:")
    for i, name in enumerate(AVAILABLE_ENGINES.keys(), 1):
        print(f"  {i}. {name:12} → {'Smart Volume + ATR' if name == 'volume_atr' else name}")
    while True:
        c = input(f"\nSelect engine (1-{len(AVAILABLE_ENGINES)} or name) [default: volume_atr]: ").strip()
        if not c:
            return VolumeATRTrailing()
        if c.isdigit() and 1 <= int(c) <= len(AVAILABLE_ENGINES):
            return list(AVAILABLE_ENGINES.values())[int(c)-1]()
        if c in AVAILABLE_ENGINES:
            return AVAILABLE_ENGINES[c]()
        print("Invalid — try again")

def select_position():
    positions = mt5.positions_get()
    if not positions:
        print("No open positions.")
        mt5.shutdown()
        sys.exit(0)

    print("\nOPEN POSITIONS:")
    for i, p in enumerate(positions, 1):
        t = "BUY" if p.type == 0 else "SELL"
        sl = f"{p.sl:.5f}" if p.sl else "-"
        comment = getattr(p, "comment", "")[:20]
        print(f"{i:2}. {p.ticket} | {p.symbol:12} | {t:4} | {p.volume:>5} lots | "
              f"Open {p.price_open:.5f} | SL {sl} | ${p.profit:+.2f} | {comment}")

    while True:
        c = input("\nEnter number or ticket: ").strip()
        if c.isdigit():
            n = int(c)
            if 1 <= n <= len(positions):
                return positions[n-1]
            for p in positions:
                if p.ticket == n:
                    return p
        print("Invalid input")

def main():
    if not mt5.initialize():
        print("Failed to initialize MT5 — is it running?")
        sys.exit(1)

    engine = select_engine()

    parser = ArgumentParser(description="Smart Trailing Engine")
    parser.add_argument("symbol", nargs='?', help="Symbol filter")
    parser.add_argument("--ticket", type=int, help="Specific ticket")
    parser.add_argument("--magic", type=int, help="Magic number filter")
    parser.add_argument("--filter-comment", type=str, default=None,
                        help="Only trail if comment contains this text (e.g. --filter-comment python)")
    args = parser.parse_args()

    manual_mode = len(sys.argv) <= 1

    if manual_mode:
        pos = select_position()
    else:
        positions = mt5.positions_get(symbol=args.symbol) if args.symbol else mt5.positions_get()
        if not positions:
            print("No positions match filters.")
            mt5.shutdown()
            sys.exit(1)
        pos = next((p for p in positions
                    if (not args.ticket or p.ticket == args.ticket)
                    and (not args.magic or p.magic == args.magic)), positions[0])

    print(f"\nENGINE: {engine.__class__.__name__}")
    print(f"Trailing → {pos.symbol} #{pos.ticket} {'BUY' if pos.type==0 else 'SELL'} {pos.volume} lots")
    if args.filter_comment:
        print(f"Comment filter active: '{args.filter_comment}'")
    print("Press Ctrl+C to stop\n" + "—" * 70)

    try:
        while True:
            current = mt5.positions_get(ticket=pos.ticket)
            if not current:
                print(f"[{datetime.now():%H:%M:%S}] Position closed.")
                break

            p = current[0]
            pos_obj = Position.from_mt5(p)

            # Only apply comment filter if --filter-comment was used
            if args.filter_comment:
                if args.filter_comment.lower() not in getattr(p, "comment", "").lower():
                    continue

            engine.trail(pos_obj)

            time.sleep(CHECK_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\nStopped by user")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main()