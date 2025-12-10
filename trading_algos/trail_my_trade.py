# trading_algos/trail_my_trade.py
"""
Smart Trailing Engine Runner
- Choose trailing engine at startup
- Interactive position picker
- Full CLI support
"""

import sys
import time
from datetime import datetime
import MetaTrader5 as mt5
from argparse import ArgumentParser

from trading_algos.config import CHECK_INTERVAL_SEC
from trading_algos.core.position import Position

# ── Import all available engines here ─────────────────────────────────────
from trading_algos.trailing.volume_atr import VolumeATRTrailing
from trading_algos.trailing.fixed_pips import FixedPipsTrailing   # ← add more here

# Map name → engine class
AVAILABLE_ENGINES = {
    "volume_atr": VolumeATRTrailing,
    "fixed_pips": FixedPipsTrailing,
    # "chandelier": ChandelierTrailing,
    # "psar": PSARTrailing,
}

def select_engine_from_cli():
    parser = ArgumentParser(add_help=False)
    parser.add_argument("--engine", choices=AVAILABLE_ENGINES.keys(), help="Trailing engine")
    args, _ = parser.parse_known_args()
    if args.engine:
        return AVAILABLE_ENGINES[args.engine]()
    return None

def interactive_engine_selection():
    print("\nAVAILABLE TRAILING ENGINES:")
    for i, name in enumerate(AVAILABLE_ENGINES.keys(), 1):
        desc = {
            "volume_atr": "Smart Volume + ATR (default)",
            "fixed_pips": "Simple 50-pip trailing"
        }.get(name, name)
        print(f"  {i}. {name:12} → {desc}")
    while True:
        choice = input(f"\nSelect engine [1-{len(AVAILABLE_ENGINES)} or name] (default: volume_atr): ").strip()
        if not choice:
            return VolumeATRTrailing()
        if choice.isdigit() and 1 <= int(choice) <= len(AVAILABLE_ENGINES):
            return list(AVAILABLE_ENGINES.values())[int(choice)-1]()
        if choice in AVAILABLE_ENGINES:
            return AVAILABLE_ENGINES[choice]()
        print("Invalid choice — try again")

def select_position():
    positions = mt5.positions_get()
    if not positions:
        print("No open positions found.")
        mt5.shutdown()
        sys.exit(0)

    print("\nOPEN POSITIONS:")
    for i, p in enumerate(positions, 1):
        t = "BUY" if p.type == 0 else "SELL"
        sl = f"{p.sl:.5f}" if p.sl else "-"
        print(f"{i:2}. {p.ticket} | {p.symbol:12} | {t:4} | {p.volume:>5} lots | "
              f"Open {p.price_open:.5f} | SL {sl} | ${p.profit:+.2f}")

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

    # 1. Engine selection
    engine = select_engine_from_cli() or interactive_engine_selection()

    # 2. CLI filters (symbol, ticket, magic)
    parser = ArgumentParser(description="Smart Trailing Engine")
    parser.add_argument("symbol", nargs='?', help="Symbol filter")
    parser.add_argument("--ticket", type=int, help="Specific ticket")
    parser.add_argument("--magic", type=int, help="Magic number filter")
    parser.add_argument("--engine", choices=AVAILABLE_ENGINES.keys(), help="Trailing engine")
    args = parser.parse_args()

    # 3. Position selection
    if len(sys.argv) <= 2:  # Only script name + optional --engine → interactive
        pos = select_position()
    else:
        positions = mt5.positions_get(symbol=args.symbol) if args.symbol else mt5.positions_get()
        if not positions:
            print("No positions match your filters.")
            mt5.shutdown()
            sys.exit(1)
        pos = next((p for p in positions
                    if (not args.ticket or p.ticket == args.ticket)
                    and (not args.magic or p.magic == args.magic)), positions[0])

    print(f"\nENGINE: {engine.__class__.__name__}")
    print(f"Trailing → {pos.symbol} #{pos.ticket} {'BUY' if pos.type==0 else 'SELL'} {pos.volume} lots")
    print("Press Ctrl+C to stop\n" + "—" * 70)

    try:
        while True:
            current = mt5.positions_get(ticket=pos.ticket)
            if not current:
                print(f"[{datetime.now():%H:%M:%S}] Position closed.")
                break
            if "python" in getattr(current[0], "comment", "").lower():
                engine.trail(Position.from_mt5(current[0]))
            time.sleep(CHECK_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\nStopped by user")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main()