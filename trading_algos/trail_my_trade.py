# trading_algos/trail_my_trade.py
import sys
import time
from datetime import datetime
import MetaTrader5 as mt5
from argparse import ArgumentParser

from trading_algos.config import CHECK_INTERVAL_SEC
from trading_algos.core.position import Position
from trading_algos.core.logger import log_event  # Unified JSON logging
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

    # CLI parsing (added --ignore-tp-positions)
    parser = ArgumentParser(description="Smart trailing engine for one or all positions")
    parser.add_argument("symbol", nargs='?', default=None, help="Filter by symbol (e.g., EURUSD)")
    parser.add_argument("--ticket", type=int, help="Filter by ticket")
    parser.add_argument("--magic", type=int, help="Filter by magic number")
    parser.add_argument("--comment", type=str, help="Filter by comment substring (e.g., 'python')")
    parser.add_argument("--all", action="store_true", help="Run forever, trailing all (new/old) matching positions")
    parser.add_argument("--ignore-tp-positions", action="store_true", help="Ignore positions with take profit set (skip trailing, no SL touch)")
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
        # Filter out TP positions if flag set
        if args.ignore_tp_positions:
            positions = [p for p in positions if p.tp == 0.0]
        log_event("ENGINE_INIT", engine=engine.__class__.__name__)
        log_event("TRAILING_START", num_positions=len(positions))
        active_tickets = set()
        for pos in positions:
            pos_obj = Position.from_mt5(pos)
            engine.trail(pos_obj)
            active_tickets.add(pos.ticket)
            log_event("START_TRAILING", ticket=pos.ticket)
    else:
        # --all mode: No initial positions, full dynamic scan every loop
        log_event("ENGINE_INIT", engine="VolumeATRTrailing", mode="eternal")
        log_event("TRAILING_FOREVER")
        if args.symbol: log_event("FILTER_SET", filter_type="symbol", value=args.symbol)
        if args.magic: log_event("FILTER_SET", filter_type="magic", value=args.magic)
        if args.comment: log_event("FILTER_SET", filter_type="comment", value=args.comment)
        active_tickets = set()

    last_sleep_log = time.time()  # Throttle sleeping log
    last_skip_log = {}  # Per-ticket throttle for skipped logs
    try:
        while True:
            current_positions = get_filtered_positions(args.symbol, None, args.magic, args.comment)
            current_tickets = {p.ticket for p in current_positions}

            # Add new positions
            new_tickets = current_tickets - active_tickets
            for new_ticket in new_tickets:
                new_pos_data = mt5.positions_get(ticket=new_ticket)
                if new_pos_data:
                    new_p = new_pos_data[0]
                    if args.ignore_tp_positions and new_p.tp != 0.0:
                        if new_ticket not in last_skip_log or time.time() - last_skip_log[new_ticket] > 60:
                            log_event("SKIPPED_TP_POSITION", ticket=new_ticket, tp_value=new_p.tp)
                            last_skip_log[new_ticket] = time.time()
                        continue
                    new_pos_obj = Position.from_mt5(new_p)
                    engine.trail(new_pos_obj)
                    active_tickets.add(new_ticket)
                    log_event("START_TRAILING", ticket=new_ticket)

            # Trail active ones (no verbose logging here)
            for ticket in list(active_tickets):
                cur_pos_data = mt5.positions_get(ticket=ticket)
                if not cur_pos_data:
                    log_event("POSITION_CLOSED", ticket=ticket)
                    active_tickets.discard(ticket)
                    continue
                p = cur_pos_data[0]
                # Mid-run check: If TP added later and flag set, skip trail + drop
                if args.ignore_tp_positions and p.tp != 0.0:
                    if ticket not in last_skip_log or time.time() - last_skip_log[ticket] > 60:
                        log_event("SKIPPED_TP_POSITION", ticket=ticket, tp_value=p.tp)
                        last_skip_log[ticket] = time.time()
                    active_tickets.discard(ticket)
                    continue
                pos_obj = Position.from_mt5(p)
                engine.trail(pos_obj)

            if not active_tickets:
                if not args.all:
                    log_event("NO_ACTIVE_EXITING")
                    break
                else:
                    if time.time() - last_sleep_log > 60:
                        log_event("NO_POSITIONS_SLEEPING")
                        last_sleep_log = time.time()
            time.sleep(CHECK_INTERVAL_SEC)
    except KeyboardInterrupt:
        log_event("USER_STOP")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main()