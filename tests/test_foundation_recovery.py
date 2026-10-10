"""Regression checks for precision and atomic seed failures."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from adaptive_commerce.persistence import connect, initialize, load_fixtures
from adaptive_commerce.persistence import seed
from adaptive_commerce.persistence.database import _json_dumps


def test_decimal_json_is_lossless():
    value = Decimal("0.1234567890123456789012345678")
    assert Decimal(json.loads(_json_dumps({"weight": value}))["weight"]) == value


def test_fractional_values_survive_seed_storage(monkeypatch):
    value = "0.1234567890123456789012345678"
    original = seed._load_json

    def precise_products(path, filename):
        records = original(path, filename)
        if filename == "products.json":
            records[0]["price"] = value
        if filename == "region_profiles.json":
            records[0]["weights"]["outdoor"] = value
        return records

    monkeypatch.setattr(seed, "_load_json", precise_products)
    conn = connect(":memory:")
    try:
        initialize(conn)
        load_fixtures(conn, Path("data/fixtures"))
        price = conn.execute("SELECT price FROM products WHERE id='boots'").fetchone()[0]
        weights = conn.execute("SELECT weights FROM region_profiles WHERE region_id='cedar'").fetchone()[0]
        assert Decimal(price) == Decimal(value)
        assert Decimal(json.loads(weights)["outdoor"]) == Decimal(value)
    finally:
        conn.close()


def test_seed_failure_rolls_back_only_seed_work(monkeypatch):
    conn = connect(":memory:")
    try:
        initialize(conn)
        conn.execute("INSERT INTO products (id,name,price) VALUES ('existing','Existing','1')")
        original = seed._load_json

        def bad_regions(path, filename):
            if filename == "regions.json":
                return [{"state": "invalid"}]
            return original(path, filename)

        monkeypatch.setattr(seed, "_load_json", bad_regions)
        with pytest.raises(ValueError):
            load_fixtures(conn, Path("data/fixtures"))
        assert [row[0] for row in conn.execute("SELECT id FROM products")] == ["existing"]
        assert conn.in_transaction
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
    finally:
        conn.close()
