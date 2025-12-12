import sys
import time
from datetime import datetime
import MetaTrader5 as mt5
from argparse import ArgumentParser
import logging  # Added for proper logging

from trading_algos.config import CHECK_INTERVAL_SEC
from trading_algos.core.position import Position
# Engines
from trading_algos.trailing.volume_atr import VolumeATRTrailing
AVAILABLE_ENGINES = {
    "volume_atr": VolumeATRTrailing,
}

def get_filtered_positions(symbol=None, ticket=None, magic=None, comment=None):
    """Fetch and filter open positions based on args."""
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if not positions:
        return []
    filtered = list(positions)
    if ticket:
        filtered = [p for p in filtered if p.ticket == ticket]
    if magic:
        filtered = [p for p in filtered if p.magic == magic]
    if comment:
        filtered = [p for p in filtered if comment.lower() in getattr(p, 'comment', '').lower()]
    return filtered

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
              f"P/L ${p.profit:+8.2f} | SL {sl} | Comment: {getattr(p, 'comment', 'N/A')}")
    while True:
        c = input("\nEnter number or ticket: ").strip().lower()  # Removed 'all' option
        if c.isdigit():
            n = int(c)
            if 1 <= n <= len(positions):
                return [positions[n-1]]
            for p in positions:
                if p.ticket == n:
                    return [p]
        print("Invalid — try again")
    return []

def main():
    if not mt5.initialize():
        print("MT5 not running or not logged in!")  # Kept print for init error
        sys.exit(1)

    # Setup logging
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')

    # CLI parsing
    parser = ArgumentParser(description="Smart trailing engine for one or all positions")
    parser.add_argument("symbol", nargs='?', default=None, help="Filter by symbol (e.g., EURUSD)")
    parser.add_argument("--ticket", type=int, help="Filter by ticket")
    parser.add_argument("--magic", type=int, help="Filter by magic number")
    parser.add_argument("--comment", type=str, help="Filter by comment substring (e.g., 'python')")
    parser.add_argument("--all", action="store_true", help="Run forever, trailing all (new/old) matching positions")
    args = parser.parse_args()

    engine = select_engine() if not (args.ticket or args.symbol or args.all) else VolumeATRTrailing()  # Default for CLI

    if not args.all:
        if args.symbol or args.ticket or args.magic or args.comment:
            positions = get_filtered_positions(args.symbol, args.ticket, args.magic, args.comment)
            if not positions:
                print("No matching positions found.")  # Kept print for early exit
                mt5.shutdown()
                sys.exit(0)
        else:
            positions = select_position()
        logging.info(f"Using engine: {engine.__class__.__name__}")
        logging.info(f"Trailing {len(positions)} position(s)")
        active_tickets = {pos.ticket for pos in positions}
    else:
        # --all mode: No initial positions, full dynamic scan every loop
        logging.info(f"Using engine: VolumeATRTrailing (eternal mode)")
        logging.info("Trailing all matching positions forever (new/old)")
        if args.symbol: logging.info(f"Symbol filter: {args.symbol}")
        if args.magic: logging.info(f"Magic filter: {args.magic}")
        if args.comment: logging.info(f"Comment filter: {args.comment}")
        active_tickets = set()

    try:
        while True:
            now = datetime.now().strftime("%H:%M:%S")  # Kept for potential, but logging has timestamp
            current_positions = get_filtered_positions(args.symbol, None, args.magic, args.comment)
            current_tickets = {p.ticket for p in current_positions}

            # Add new positions
            new_tickets = current_tickets - active_tickets
            for new_ticket in new_tickets:
                new_pos_data = mt5.positions_get(ticket=new_ticket)
                if new_pos_data:
                    new_p = new_pos_data[0]
                    new_pos_obj = Position.from_mt5(new_p)
                    engine.trail(new_pos_obj)
                    active_tickets.add(new_ticket)
                    logging.info(f"Started trailing new position #{new_ticket}")

            # Trail active ones (no verbose logging here)
            for ticket in list(active_tickets):
                cur_pos_data = mt5.positions_get(ticket=ticket)
                if not cur_pos_data:
                    logging.info(f"Position #{ticket} closed — removing from watch")
                    active_tickets.discard(ticket)
                    continue
                p = cur_pos_data[0]
                pos_obj = Position.from_mt5(p)
                engine.trail(pos_obj)

            if not active_tickets:
                if not args.all:
                    logging.info("No active positions left — exiting")
                    break
                else:
                    logging.info("No positions currently — sleeping")
            time.sleep(CHECK_INTERVAL_SEC)
    except KeyboardInterrupt:
        logging.info("Stopped by user")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main()