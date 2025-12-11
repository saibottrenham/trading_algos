import sys
import time
from datetime import datetime
import MetaTrader5 as mt5
from argparse import ArgumentParser
from trading_algos.config import CHECK_INTERVAL_SEC, PROFIT_TO_ACTIVATE_TRAILING, COMMISSION_PER_LOT
from trading_algos.core.position import Position
from trading_algos.core.broker import Broker
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
        c = input("\nEnter number, ticket, or 'all': ").strip().lower()
        if c == 'all':
            return list(positions)
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
        print("MT5 not running or not logged in!")
        sys.exit(1)

    # CLI parsing (enhanced)
    parser = ArgumentParser(description="Smart trailing engine for one or all positions")
    parser.add_argument("symbol", nargs='?', default=None, help="Filter by symbol (e.g., EURUSD)")
    parser.add_argument("--ticket", type=int, help="Filter by ticket")
    parser.add_argument("--magic", type=int, help="Filter by magic number")
    parser.add_argument("--comment", type=str, help="Filter by comment substring (e.g., 'python')")
    args = parser.parse_args()

    engine = select_engine() if not args.ticket and not args.symbol else VolumeATRTrailing()  # Default to volume_atr for CLI

    if args.symbol or args.ticket or args.magic or args.comment:
        positions = get_filtered_positions(args.symbol, args.ticket, args.magic, args.comment)
        if not positions:
            print("No matching positions found.")
            mt5.shutdown()
            sys.exit(0)
    else:
        positions = select_position()

    print(f"\nENGINE: {engine.__class__.__name__}")
    print(f"TRAILING {len(positions)} position(s):")
    for pos in positions:
        direction = "BUY" if pos.type == 0 else "SELL"
        print(f"  → #{pos.ticket} {pos.symbol} | {direction} {pos.volume} lots | Comment: {getattr(pos, 'comment', 'N/A')}")
    print("LOGS BELOW — EVERY 5 SECONDS\n" + "—" * 80)

    active_tickets = {pos.ticket for pos in positions}
    try:
        while True:
            now = datetime.now().strftime("%H:%M:%S")
            current_positions = get_filtered_positions(args.symbol, None, args.magic, args.comment) if len(positions) > 1 else mt5.positions_get()
            current_tickets = {p.ticket for p in current_positions}

            # Trail active ones
            for ticket in list(active_tickets):
                cur_pos_data = mt5.positions_get(ticket=ticket)
                if not cur_pos_data:
                    print(f"[{now}] Position #{ticket} closed — removing from watch")
                    active_tickets.discard(ticket)
                    continue
                p = cur_pos_data[0]
                pos_obj = Position.from_mt5(p)
                direction = "BUY" if p.type == 0 else "SELL"
                sl_status = f"{p.sl:.5f}" if p.sl else "None"

                info = Broker.get_symbol_info(p.symbol)
                contract = p.volume * info.trade_contract_size
                comm_cost = COMMISSION_PER_LOT * p.volume

                if p.profit < PROFIT_TO_ACTIVATE_TRAILING:
                    dollars_short = PROFIT_TO_ACTIVATE_TRAILING - p.profit
                    if p.type == 0:  # BUY
                        price_needed = p.price_open + (dollars_short + comm_cost - p.swap) / contract
                    else:  # SELL
                        price_needed = p.price_open - (dollars_short + comm_cost - p.swap) / contract
                    price_needed = round(price_needed, info.digits)
                    pips_needed = abs(p.price_current - price_needed) / info.point

                    print(f"[{now}] #{p.ticket} | {p.symbol} | {direction} | "
                          f"Price: {p.price_current:.5f} | P/L: ${p.profit:+.2f} | SL: {sl_status}")
                    print(f"           → Need +${dollars_short:.2f} more "
                          f"(≈ {pips_needed:.0f} pips) → SL will lock at {price_needed}")
                else:
                    print(f"[{now}] #{p.ticket} | {p.symbol} | {direction} | "
                          f"Price: {p.price_current:.5f} | P/L: ${p.profit:+.2f} | SL: {sl_status} ← LOCKED & TRAILING")

                engine.trail(pos_obj)

            # Dynamic add new in 'all' mode (CLI or interactive all)
            if len(positions) > 1:
                new_tickets = current_tickets - active_tickets
                for new_ticket in new_tickets:
                    new_pos_data = mt5.positions_get(ticket=new_ticket)
                    if new_pos_data:
                        print(f"[{now}] New position detected: #{new_ticket}")
                        new_p = new_pos_data[0]
                        new_pos_obj = Position.from_mt5(new_p)
                        engine.trail(new_pos_obj)
                        active_tickets.add(new_ticket)

            if not active_tickets:
                print(f"[{now}] No active positions left — exiting.")
                break

            time.sleep(CHECK_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main()