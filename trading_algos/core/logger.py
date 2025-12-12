# trading_algos/core/logger.py
import logging
import json
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger("trail_engine")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # Kill dupe to root—unified JSON only

def log_event(event: str, **kwargs: Any) -> None:
    """Structured log in JSON lines (easy to grep/parse later)"""
    payload = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "event": event,
        **kwargs
    }
    logger.info(json.dumps(payload, default=str))