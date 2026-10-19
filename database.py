"""
database.py
-----------
All SQLite work for the Smart Donation Distribution System lives here.

The database file is "donations.db" (created automatically the first time
the application runs).  Three tables are used:

    donations         -> items donated by donors
    community_requests-> what the community organisations need
    allocations       -> history of confirmed donation -> request matches

Every query in this project uses parameterised SQL (question marks),
so user input can never change the structure of the SQL statement.
"""

import sqlite3
from pathlib import Path

from flask import g

# The database file sits next to app.py
DB_PATH = Path(__file__).resolve().parent / "donations.db"

# ---------------------------------------------------------------------------
# Schema.  "IF NOT EXISTS" makes init_db() safe to call every time.
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS donations (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_name         TEXT    NOT NULL,
    category           TEXT    NOT NULL,
    item_name          TEXT    NOT NULL,
    description        TEXT    NOT NULL DEFAULT '',
    quantity           INTEGER NOT NULL,          -- original quantity donated
    available_quantity INTEGER NOT NULL,          -- quantity still unallocated
    expiry_date        TEXT,                       -- 'YYYY-MM-DD' or NULL
    condition          TEXT    NOT NULL,
    status             TEXT    NOT NULL DEFAULT 'available',
    is_demo            INTEGER NOT NULL DEFAULT 0, -- 1 = sample/demo record
    created_at         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS community_requests (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_name     TEXT    NOT NULL,
    category           TEXT    NOT NULL,
    item_name          TEXT    NOT NULL,
    quantity_required  INTEGER NOT NULL,
    quantity_remaining INTEGER NOT NULL,          -- still needed after matches
    urgency            TEXT    NOT NULL,
    beneficiaries      INTEGER NOT NULL,
    request_date       TEXT    NOT NULL,
    status             TEXT    NOT NULL DEFAULT 'pending',
    is_demo            INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS allocations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    donation_id     INTEGER NOT NULL REFERENCES donations(id),
    request_id      INTEGER NOT NULL REFERENCES community_requests(id),
    quantity        INTEGER NOT NULL,
    allocation_date TEXT    NOT NULL,
    reasons         TEXT    NOT NULL,             -- explanation text (Rule 6)
    status          TEXT    NOT NULL DEFAULT 'confirmed',
    created_at      TEXT    NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Flask connection handling: one connection per request, closed automatically.
# ---------------------------------------------------------------------------
def get_db():
    """Return the SQLite connection for the current request."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row   # rows behave like dictionaries
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(exception=None):
    """Called by Flask when the request ends."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app):
    """Attach close_db() to the Flask application."""
    app.teardown_appcontext(close_db)


def init_db():
    """Create the tables if they do not exist yet."""
    connection = sqlite3.connect(DB_PATH)
    connection.executescript(SCHEMA)
    connection.commit()
    connection.close()


# ---------------------------------------------------------------------------
# Small helpers shared by app.py and the rule engine.
# ---------------------------------------------------------------------------
def donation_status(total, available):
    """Rule: available == total -> 'available', 0 -> 'fully_allocated'."""
    if available <= 0:
        return "fully_allocated"
    if available < total:
        return "partially_allocated"
    return "available"


def request_status(required, remaining):
    """Rule: remaining == 0 -> 'fulfilled', else partial or pending."""
    if remaining <= 0:
        return "fulfilled"
    if remaining < required:
        return "partially_fulfilled"
    return "pending"


def row_to_dict(row):
    """Convert a sqlite3.Row into a normal dictionary."""
    return dict(row) if row is not None else None


def fetch_dashboard_stats(db):
    """Numbers shown on the dashboard (Section 3 of the brief)."""
    stats = {}

    row = db.execute(
        "SELECT COUNT(*) AS total_donations, "
        "       COALESCE(SUM(quantity), 0) AS total_quantity "
        "FROM donations"
    ).fetchone()
    stats["total_donations"] = row["total_donations"]
    stats["total_quantity"] = row["total_quantity"]

    row = db.execute(
        "SELECT COUNT(*) AS pending_requests "
        "FROM community_requests WHERE status != 'fulfilled'"
    ).fetchone()
    stats["pending_requests"] = row["pending_requests"]

    row = db.execute(
        "SELECT COUNT(*) AS successful_allocations, "
        "       COALESCE(SUM(quantity), 0) AS allocated_quantity "
        "FROM allocations WHERE status = 'confirmed'"
    ).fetchone()
    stats["successful_allocations"] = row["successful_allocations"]
    stats["allocated_quantity"] = row["allocated_quantity"]

    row = db.execute(
        "SELECT COUNT(*) AS unallocated_items, "
        "       COALESCE(SUM(available_quantity), 0) AS unallocated_quantity "
        "FROM donations WHERE available_quantity > 0"
    ).fetchone()
    stats["unallocated_items"] = row["unallocated_items"]
    stats["unallocated_quantity"] = row["unallocated_quantity"]

    return stats
