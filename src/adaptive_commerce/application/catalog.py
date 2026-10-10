"""Validated read and maintenance operations for the shared product catalog."""

import logging
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Literal
from uuid import uuid4

import neatlogs

from adaptive_commerce.domain import Product
from adaptive_commerce.persistence import Repository, connect
from adaptive_commerce.persistence.database import _json_dumps

logger = logging.getLogger(__name__)


def list_catalog_products(path: Path) -> list[Product]:
    """Return every product, including unavailable products, in stable order."""
    with connect(path) as conn:
        return Repository(conn).get_all_products()


def list_catalog_actions(path: Path, limit: int = 30) -> list[dict[str, Any]]:
    """Return recent catalog change audit records in newest-first order."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM catalog_actions ORDER BY created_at DESC, id DESC LIMIT ?", (limit,),
        ).fetchall()
    return [
        {**dict(row), "before": json.loads(row["before_json"]) if row["before_json"] else None,
         "after": json.loads(row["after_json"])}
        for row in rows
    ]


@neatlogs.span(kind="WORKFLOW", name="manage_catalog_product")
def manage_catalog_product(
    path: Path,
    action: Literal["create", "update", "remove"],
    product_id: str,
    changes: dict[str, Any] | None = None,
) -> Product:
    """Create, edit, or soft-remove a validated product without deleting history."""
    if not product_id.strip() or len(product_id) > 120:
        raise ValueError("product ID must contain 1 to 120 characters")
    changes = changes or {}
    allowed = {"name", "tags", "price", "cost", "in_stock", "eligible", "base_popularity"}
    if set(changes) - allowed:
        raise ValueError("product update contains unsupported fields")

    with connect(path) as conn, conn:
        repository = Repository(conn)
        existing = repository.get_product(product_id)
        before = existing.model_dump(mode="json") if existing else None
        if action == "create":
            if existing is not None:
                raise ValueError("a product with that ID already exists")
            product = Product.model_validate({"id": product_id, **changes})
            if any(item.name.casefold() == product.name.casefold() for item in repository.get_all_products()):
                raise ValueError("a product with that name already exists")
            repository.create_product(product)
        elif action == "update":
            if existing is None:
                raise ValueError("product was not found")
            if not changes:
                raise ValueError("provide at least one product field to update")
            payload = existing.model_dump()
            payload.update(changes)
            product = Product.model_validate(payload)
            if any(item.id != product.id and item.name.casefold() == product.name.casefold()
                   for item in repository.get_all_products()):
                raise ValueError("a product with that name already exists")
            _write_product(conn, product)
        elif action == "remove":
            if existing is None:
                raise ValueError("product was not found")
            product = Product.model_validate({
                **existing.model_dump(), "in_stock": False, "eligible": False,
            })
            _write_product(conn, product)
        else:
            raise ValueError("unsupported product action")
        conn.execute(
            """INSERT INTO catalog_actions
            (id, product_id, action, before_json, after_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (str(uuid4()), product.id, action,
             _json_dumps(before) if before is not None else None,
             _json_dumps(product.model_dump(mode="json")), datetime.now(timezone.utc).isoformat()),
        )

    logger.info("catalog_product_%s", action, extra={
        "product_id": product.id, "result_status": action,
    })
    return product


def _write_product(conn: sqlite3.Connection, product: Product) -> None:
    """Persist a complete Pydantic-validated product projection."""
    conn.execute(
        """UPDATE products SET name = ?, tags = ?, price = ?, cost = ?, in_stock = ?,
        eligible = ?, base_popularity = ? WHERE id = ?""",
        (product.name, _json_dumps(product.tags), str(product.price), str(product.cost),
         int(product.in_stock), int(product.eligible), str(product.base_popularity), product.id),
    )
