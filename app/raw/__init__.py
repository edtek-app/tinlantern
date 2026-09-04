"""The append-only landing zone for ingested xAPI statements.

`raw` holds statements exactly as received. Shredding them into a
dimensional model is the warehouse's job (M2); this layer's only
responsibilities are durability, idempotency, and never losing a write
silently. See ADR-0005.
"""

from app.raw.storage import (
    RejectionReason,
    StoreOutcome,
    StoreStatus,
    canonical,
    high_water_mark,
    record_rejection,
    store_statement,
    store_statements,
    wire_payload,
)

__all__ = [
    "RejectionReason",
    "StoreOutcome",
    "StoreStatus",
    "canonical",
    "high_water_mark",
    "record_rejection",
    "store_statement",
    "store_statements",
    "wire_payload",
]
