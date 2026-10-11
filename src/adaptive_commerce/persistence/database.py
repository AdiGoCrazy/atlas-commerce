"""Database connection and initialization for Atlas Commerce."""

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

from .schema import SCHEMA


def connect(path: str | Path) -> sqlite3.Connection:
    """Create a SQLite connection with row factory and foreign keys enabled."""
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def initialize(conn: sqlite3.Connection) -> None:
    """Create schema and apply additive migrations for existing demo databases."""
    conn.executescript(SCHEMA)
    conn.commit()


def _json_dumps(obj: Any) -> str:
    """Serialize Decimal losslessly as strings and datetimes as ISO strings."""
    def default(o: Any) -> str:
        if isinstance(o, Decimal):
            return str(o)
        if hasattr(o, "isoformat"):
            return o.isoformat()
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")
    
    return json.dumps(obj, default=default, allow_nan=False)


def _json_loads(text: str | None) -> Any:
    """Deserialize JSON, returning empty structure for None/empty."""
    if not text:
        return {} if text == "{}" else []
    return json.loads(text, parse_constant=_parse_decimal_constant)


def _parse_decimal_constant(s: str) -> Decimal:
    """Parse JSON numeric constants as Decimal for exact precision."""
    return Decimal(s)
