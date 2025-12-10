import sys
import time
from datetime import datetime
import MetaTrader5 as mt5
from argparse import ArgumentParser
from trading_algos.config import CHECK_INTERVAL_SEC
from trading_algos.core.position import Position
# Engines
from trading_algos.trailing.volume_atr import VolumeATRTrailing
AVAILABLE_ENGINES = {
    "volume_atr": VolumeATRTrailing,
}
def select_engine():
    print("\nAVAILABLE ENGINES:")
    for i, name in enumerate(AVAILABLE_ENGINES, 1):
        print(f" {i}. {name}")
    while True:
        c = input(f"\nSelect engine (1-{len(AVAILABLE_ENGINES)}) [default: volume_atr]: ").strip()
        if not c or c == "1":
            return VolumeATRTrailing()
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
        sl = f"{p.sl:.5f}" if p.sl else "None"
        print(f"{i:2}. #{p.ticket} | {p.symbol:8} | {t:4} | {p.volume:4} lots | "
              f"P/L ${p.profit:+8.2f} | SL {sl}")
    while True:
        c = input("\nEnter number or ticket: ").strip()
        if c.isdigit():
            n = int(c)
            if 1 <= n <= len(positions):
                return positions[n-1]
            for p in positions:
                if p.ticket == n:
                    return p
        print("Invalid — try again")
def main():
    if not mt5.initialize():
        print("MT5 not running or not logged in!")
        sys.exit(1)
    engine = select_engine()
    parser = ArgumentParser()
    parser.add_argument("--ticket", type=int)
    parser.add_argument("--filter-comment", type=str)
    args = parser.parse_args()
    if args.ticket:
        pos_data = mt5.positions_get(ticket=args.ticket)
        if not pos_data:
            print(f"No position with ticket {args.ticket}")
            mt5.shutdown()
            return
        pos = pos_data[0]
        manual_mode = False
    else:
        pos = select_position()
        manual_mode = True
    print(f"\nENGINE: {engine.__class__.__name__}")
    print(f"TRAILING: #{pos.ticket} {pos.symbol} | {'BUY' if pos.type==0 else 'SELL'} {pos.volume} lots")
    print("LOGS BELOW — EVERY 5 SECONDS\n" + "—" * 80)
    try:
        while True:
            now = datetime.now().strftime("%H:%M:%S")
            current = mt5.positions_get(ticket=pos.ticket)
           
            if not current:
                print(f"[{now}] Position #{pos.ticket} closed.")
                break
            p = current[0]
            pos_obj = Position.from_mt5(p)
            # Optional comment filter
            if args.filter_comment and args.filter_comment.lower() not in getattr(p, "comment", "").lower():
                print(f"[{now}] Skipped (comment filter: '{args.filter_comment}')")
                time.sleep(CHECK_INTERVAL_SEC)
                continue
            # ALWAYS PRINT CURRENT STATE
            direction = "BUY" if p.type == 0 else "SELL"
            sl_status = f"{p.sl:.5f}" if p.sl else "None"
            print(f"[{now}] #{p.ticket} | {p.symbol} | {direction} | "
                  f"Price: {p.price_current:.5f} | P/L: ${p.profit:+.2f} | SL: {sl_status}")
            # Run the engine — it will print its own actions
            engine.trail(pos_obj)
            time.sleep(CHECK_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        mt5.shutdown()
if __name__ == "__main__":
    main()