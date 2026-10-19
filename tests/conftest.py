"""
conftest.py - shared pytest fixtures for the Smart Donation Distribution
System test suite.

Every test runs against its own temporary SQLite file, so tests never touch
the real donations.db and can run in any order.
"""

import pathlib
import re
import sqlite3
import sys

import pytest

# Make app.py / database.py / rule_engine.py importable from the tests.
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Database isolation
# ---------------------------------------------------------------------------
@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Point database.DB_PATH at a fresh temp file and create the schema."""
    import database
    path = tmp_path / "audit.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    return path


@pytest.fixture
def client(db_path, monkeypatch):
    """Flask test client bound to the temporary database."""
    import app as app_module
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Row factories for the rule engine (plain dicts, like dict(sqlite3.Row))
# ---------------------------------------------------------------------------
def make_donation(**overrides):
    quantity = overrides.pop("quantity", 50)
    # Accept `expiry=` as a shorthand for the DB column `expiry_date`.
    if "expiry" in overrides:
        overrides["expiry_date"] = overrides.pop("expiry")
    row = {
        "id": overrides.pop("id", 1),
        "donor_name": overrides.pop("donor_name", "Sharma Family Trust"),
        "category": overrides.pop("category", "Food"),
        "item_name": overrides.pop("item_name", "Basmati Rice"),
        "quantity": quantity,
        "available_quantity": overrides.pop("available_quantity", quantity),
        "expiry_date": overrides.pop("expiry_date", None),
        "condition": overrides.pop("condition", "Sealed"),
    }
    row.update(overrides)
    return row


def make_request(**overrides):
    quantity = overrides.pop("quantity", 40)
    row = {
        "id": overrides.pop("id", 1),
        "recipient_name": overrides.pop("recipient_name", "Hope Orphanage"),
        "category": overrides.pop("category", "Food"),
        "item_name": overrides.pop("item_name", "Rice"),
        "quantity_required": quantity,
        "quantity_remaining": overrides.pop("quantity_remaining", quantity),
        "urgency": overrides.pop("urgency", "Critical"),
        "beneficiaries": overrides.pop("beneficiaries", 80),
        "request_date": overrides.pop("request_date", "2026-10-01"),
    }
    row.update(overrides)
    return row


@pytest.fixture
def donation_factory():
    return make_donation


@pytest.fixture
def request_factory():
    return make_request


# ---------------------------------------------------------------------------
# Direct SQL seeding (setup only - behaviour is always tested through HTTP)
# ---------------------------------------------------------------------------
def seed_donation(path, **overrides):
    row = make_donation(**overrides)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO donations (id, donor_name, category, item_name, "
        "description, quantity, available_quantity, expiry_date, condition, "
        "status, is_demo, created_at) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, "
        "'available', 0, '2026-10-01')",
        (row["id"], row["donor_name"], row["category"], row["item_name"],
         row["quantity"], row["available_quantity"], row["expiry_date"],
         row["condition"]))
    conn.commit()
    conn.close()


def seed_request(path, **overrides):
    row = make_request(**overrides)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO community_requests (id, recipient_name, category, "
        "item_name, quantity_required, quantity_remaining, urgency, "
        "beneficiaries, request_date, status, is_demo, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, '2026-10-01')",
        (row["id"], row["recipient_name"], row["category"], row["item_name"],
         row["quantity_required"], row["quantity_remaining"], row["urgency"],
         row["beneficiaries"], row["request_date"]))
    conn.commit()
    conn.close()


@pytest.fixture
def seed():
    return {"donation": seed_donation, "request": seed_request}


# ---------------------------------------------------------------------------
# Assertions helpers
# ---------------------------------------------------------------------------
def snapshot(path):
    """Full copy of all inventory state, for 'nothing changed' assertions."""
    conn = sqlite3.connect(path)
    state = {}
    for table in ("donations", "community_requests", "allocations"):
        state[table] = conn.execute(
            "SELECT * FROM {t} ORDER BY id".format(t=table)).fetchall()
    conn.close()
    return state


@pytest.fixture
def snapshot_fn():
    return snapshot


def stat_values(html):
    """Extract the four dashboard statistic numbers in display order."""
    return [int(v) for v in
            re.findall(r'<div class="stat-value">(\d+)</div>', html)]


@pytest.fixture
def stats():
    return stat_values
