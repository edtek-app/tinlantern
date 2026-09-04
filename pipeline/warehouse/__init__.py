"""The dimensional model: table names and shapes, in one place.

The ETL and the data-quality checks both need to know what the warehouse
contains. Naming those tables in two places would let them drift, so this
module is the single source and the tests assert the migration agrees
with it.

See ADR-0006 for grain, keys, and the deliberate overlap between the two
fact tables.
"""

from pipeline.warehouse.schema import (
    DIMENSIONS,
    FACTS,
    GRADED_VERBS,
    TABLES,
    Table,
    qualified,
)

__all__ = [
    "DIMENSIONS",
    "FACTS",
    "GRADED_VERBS",
    "TABLES",
    "Table",
    "qualified",
]
