"""Shared inventory command layer.

The single place stock is mutated. REST (`api/stock.py`), MCP (`edibl_mcp.py` via
REST), and the chat assistant (`services/assistant.py`) all call these commands so
their behaviour cannot diverge (it used to — see docs/stock-redesign/DESIGN.md §1.3).
Every command is transactional, idempotency-keyed, appends an immutable
`InventoryEvent`, and returns a user-readable summary + an undo descriptor.
"""
from .commands import (  # noqa: F401
    add_lot, open_lot, consume_lot, reverse_event, CommandResult,
    UnsupportedReversal,
)
