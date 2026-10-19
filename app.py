"""
app.py
------
Smart Donation Distribution System - Flask application.

Run it with:

    python app.py

Then open http://127.0.0.1:5000 in your browser.

Routes
    /                     dashboard
    /donations            list donations
    /donations/new        add a donation
    /requests             list / search / filter community requests
    /requests/new         register a community request
    /matching             run the rule engine, show recommendations
    /matching/confirm     save the administrator's confirmed allocations
    /allocations          allocation history
    /demo/load            load sample data (safe to click repeatedly)
    /demo/clear           remove the sample data
"""

import sqlite3
from datetime import date, datetime

from flask import (Flask, flash, redirect, render_template, request,
                   url_for)

import database as db
import rule_engine as engine

app = Flask(__name__)
app.secret_key = "smart-donation-college-project"   # demo secret key
db.init_app(app)

# Valid choices for the dropdowns (server-side validation uses these too).
CATEGORIES = ["Food", "Clothes", "Books", "Medicines", "Other Essentials"]
URGENCIES = ["Critical", "High", "Medium", "Low"]
CONDITIONS = ["New", "Like New", "Usable"]

# Basic protection against accidental duplicate submissions: a hidden
# "form_id" token is generated with every form and can only be used once.
_last_form_tokens = {}


def _new_form_token():
    import secrets
    token = secrets.token_hex(8)
    _last_form_tokens[token] = True
    return token


def _consume_form_token(token):
    """Return True when this form has not been submitted before."""
    if token and _last_form_tokens.pop(token, None):
        return True
    return False


# ---------------------------------------------------------------------------
# Validation helpers (Section 10 - server-side validation)
# ---------------------------------------------------------------------------
def _read_positive_int(form, field, errors, label=None, required=True):
    """Read an integer that must be > 0."""
    raw = (form.get(field) or "").strip()
    label = label or field
    if not raw:
        if required:
            errors.append("{label} is required.".format(label=label))
        return None
    try:
        value = int(raw)
    except ValueError:
        errors.append("{label} must be a whole number.".format(label=label))
        return None
    if value < 0:
        errors.append("{label} cannot be negative.".format(label=label))
        return None
    if value == 0:
        errors.append("{label} must be greater than zero.".format(label=label))
        return None
    return value


def _read_choice(form, field, choices, errors, label=None, required=True):
    """Read a value that must be one of the allowed choices."""
    raw = (form.get(field) or "").strip()
    label = label or field
    if not raw:
        if required:
            errors.append("{label} is required.".format(label=label))
        return None
    if raw not in choices:
        errors.append("{label} must be one of: {opts}.".format(
            label=label, opts=", ".join(choices)))
        return None
    return raw


def _read_text(form, field, errors, label=None, max_length=200):
    raw = (form.get(field) or "").strip()
    label = label or field
    if not raw:
        errors.append("{label} is required.".format(label=label))
        return None
    if len(raw) > max_length:
        errors.append("{label} is too long (max {n} characters).".format(
            label=label, n=max_length))
        return None
    return raw


def _read_date(form, field, errors, label=None, optional=True):
    """Read a YYYY-MM-DD date and validate it with datetime."""
    raw = (form.get(field) or "").strip()
    label = label or field
    if not raw:
        if optional:
            return None
        errors.append("{label} is required.".format(label=label))
        return None
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        errors.append("{label} must be a valid date (YYYY-MM-DD).".format(
            label=label))
        return None
    return parsed.isoformat()


# ---------------------------------------------------------------------------
# Dashboard (Section 3)
# ---------------------------------------------------------------------------
@app.route("/")
def dashboard():
    conn = db.get_db()
    stats = db.fetch_dashboard_stats(conn)
    recent_donations = conn.execute(
        "SELECT * FROM donations ORDER BY id DESC LIMIT 5").fetchall()
    recent_requests = conn.execute(
        "SELECT * FROM community_requests ORDER BY id DESC LIMIT 5"
    ).fetchall()
    return render_template("dashboard.html",
                           stats=stats,
                           recent_donations=recent_donations,
                           recent_requests=recent_requests,
                           today=date.today().isoformat())


# ---------------------------------------------------------------------------
# Donations (Section 4)
# ---------------------------------------------------------------------------
@app.route("/donations")
def donations():
    conn = db.get_db()
    rows = conn.execute(
        "SELECT * FROM donations ORDER BY id DESC").fetchall()
    return render_template("donations.html", donations=rows,
                           form_token=_new_form_token())


@app.route("/donations/new", methods=["POST"])
def donations_new():
    conn = db.get_db()
    errors = []

    if not _consume_form_token(request.form.get("form_token")):
        errors.append("This form was already submitted. Please refresh "
                      "the page and try again.")

    donor = _read_text(request.form, "donor_name", errors,
                       label="Donor name", max_length=120)
    category = _read_choice(request.form, "category", CATEGORIES, errors,
                            label="Item category")
    item = _read_text(request.form, "item_name", errors,
                      label="Item name", max_length=120)
    description = (request.form.get("description") or "").strip()
    quantity = _read_positive_int(request.form, "quantity", errors,
                                  label="Quantity")
    expiry = _read_date(request.form, "expiry_date", errors,
                        label="Expiry date")
    condition = _read_choice(request.form, "condition", CONDITIONS, errors,
                             label="Condition")
    status = (request.form.get("status") or "available").strip()
    if status not in ("available",):
        status = "available"

    if errors:
        for message in errors:
            flash(message, "danger")
        return redirect(url_for("donations"))

    conn.execute(
        "INSERT INTO donations (donor_name, category, item_name, "
        "description, quantity, available_quantity, expiry_date, "
        "condition, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?)",
        (donor, category, item, description, quantity, quantity, expiry,
         condition, status, date.today().isoformat()))
    conn.commit()
    flash("Donation added successfully.", "success")
    return redirect(url_for("donations"))


# ---------------------------------------------------------------------------
# Community requests (Section 5)
# ---------------------------------------------------------------------------
@app.route("/requests")
def community_requests():
    conn = db.get_db()

    # Simple search + filter, all done with parameterised SQL.
    search = (request.args.get("q") or "").strip()
    category = request.args.get("category") or ""
    urgency = request.args.get("urgency") or ""
    status = request.args.get("status") or ""

    sql = "SELECT * FROM community_requests WHERE 1 = 1"
    params = []
    if search:
        sql += (" AND (recipient_name LIKE ? OR item_name LIKE ?)")
        params.extend(["%" + search + "%", "%" + search + "%"])
    if category in CATEGORIES:
        sql += " AND category = ?"
        params.append(category)
    if urgency in URGENCIES:
        sql += " AND urgency = ?"
        params.append(urgency)
    if status in ("pending", "partially_fulfilled", "fulfilled"):
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC"

    rows = conn.execute(sql, params).fetchall()
    return render_template("requests.html", requests=rows,
                           categories=CATEGORIES, urgencies=URGENCIES,
                           search=search, selected_category=category,
                           selected_urgency=urgency,
                           selected_status=status,
                           form_token=_new_form_token())


@app.route("/requests/new", methods=["POST"])
def requests_new():
    conn = db.get_db()
    errors = []

    if not _consume_form_token(request.form.get("form_token")):
        errors.append("This form was already submitted. Please refresh "
                      "the page and try again.")

    recipient = _read_text(request.form, "recipient_name", errors,
                           label="Recipient / organization name",
                           max_length=120)
    category = _read_choice(request.form, "category", CATEGORIES, errors,
                            label="Item category required")
    item = _read_text(request.form, "item_name", errors,
                      label="Item name / requirement", max_length=120)
    quantity = _read_positive_int(request.form, "quantity", errors,
                                  label="Quantity required")
    urgency = _read_choice(request.form, "urgency", URGENCIES, errors,
                           label="Urgency")
    beneficiaries = _read_positive_int(request.form, "beneficiaries", errors,
                                       label="Number of beneficiaries")
    request_date = _read_date(request.form, "request_date", errors,
                              label="Request date", optional=False)
    status = (request.form.get("status") or "pending").strip()
    if status not in ("pending",):
        status = "pending"

    if errors:
        for message in errors:
            flash(message, "danger")
        return redirect(url_for("community_requests"))

    conn.execute(
        "INSERT INTO community_requests (recipient_name, category, "
        "item_name, quantity_required, quantity_remaining, urgency, "
        "beneficiaries, request_date, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (recipient, category, item, quantity, quantity, urgency,
         beneficiaries, request_date, status,
         date.today().isoformat()))
    conn.commit()
    flash("Community request registered successfully.", "success")
    return redirect(url_for("community_requests"))


# ---------------------------------------------------------------------------
# Smart matching (Section 6)
# ---------------------------------------------------------------------------
@app.route("/matching")
def matching():
    conn = db.get_db()
    donations = [dict(row) for row in conn.execute(
        "SELECT * FROM donations WHERE available_quantity > 0").fetchall()]
    requests = [dict(row) for row in conn.execute(
        "SELECT * FROM community_requests WHERE status != 'fulfilled'"
    ).fetchall()]

    result = engine.recommend_matches(donations, requests)
    return render_template("matching.html",
                           recommendations=result["recommendations"],
                           skipped=result["skipped"])


@app.route("/matching/confirm", methods=["POST"])
def matching_confirm():
    """Save the allocations the administrator confirmed (Section 6)."""
    conn = db.get_db()
    confirmed = 0
    blocked = 0

    # Collect the ids the administrator ticked.
    selections = request.form.getlist("recommendation")
    if not selections:
        flash("No recommendations were selected.", "warning")
        return redirect(url_for("matching"))

    try:
        # ONE database transaction: either everything is saved, or nothing.
        conn.execute("BEGIN")
        for selection in selections:
            # selection looks like "donation_id:request_id:quantity"
            parts = selection.split(":")
            if len(parts) != 3:
                raise ValueError("Malformed recommendation.")
            donation_id, request_id, quantity = (int(parts[0]),
                                                  int(parts[1]),
                                                  int(parts[2]))
            donation = conn.execute(
                "SELECT * FROM donations WHERE id = ?",
                (donation_id,)).fetchone()
            request_row = conn.execute(
                "SELECT * FROM community_requests WHERE id = ?",
                (request_id,)).fetchone()
            if donation is None or request_row is None:
                raise ValueError("Donation or request not found.")

            eligible, reasons = engine.evaluate_pair(dict(donation),
                                                     dict(request_row))
            if not eligible:
                blocked += 1
                continue

            try:
                engine.allocate(conn, donation_id, request_id, quantity,
                                reasons)
                confirmed += 1
            except ValueError as problem:
                # Never leave the database half updated.
                conn.rollback()
                flash(str(problem), "danger")
                return redirect(url_for("allocations"))
        conn.commit()
    except (sqlite3.Error, ValueError) as problem:
        # Malformed selections, unknown ids or a database error all leave
        # the transaction rolled back - never half-saved data.
        conn.rollback()
        flash("Allocation was not saved: {err}".format(err=problem),
              "danger")
        return redirect(url_for("matching"))

    flash("Confirmed {ok} allocation(s); {bad} were skipped by the "
          "rules.".format(ok=confirmed, bad=blocked), "success")
    return redirect(url_for("allocations"))


# ---------------------------------------------------------------------------
# Allocation management (Section 7)
# ---------------------------------------------------------------------------
@app.route("/allocations")
def allocations():
    conn = db.get_db()
    rows = conn.execute(
        "SELECT a.*, d.item_name AS donation_item, d.donor_name, "
        "       r.item_name AS request_item, r.recipient_name, "
        "       r.urgency "
        "FROM allocations a "
        "JOIN donations d ON d.id = a.donation_id "
        "JOIN community_requests r ON r.id = a.request_id "
        "ORDER BY a.id DESC").fetchall()
    return render_template("allocations.html", allocations=rows)


# ---------------------------------------------------------------------------
# Sample data (Section 8)
# ---------------------------------------------------------------------------
DEMO_DONATIONS = [
    # donor, category, item, description, qty, expiry, condition
    ("Sharma Family Trust", "Food", "Basmati Rice",
     "50 kg sealed rice bags for community kitchens", 50,
     date.today().isoformat(), "Sealed"),
    ("City Hospital Trust", "Medicines", "Paracetamol Tablets",
     "Strip packs of 500 mg tablets, unopened", 200,
     date.today().isoformat(), "New"),
    ("Green Leaf Charity", "Food", "Fresh Milk Cartons",
     "1 litre cartons, cold-chain maintained", 30,
     date.today().isoformat(), "Sealed"),
    ("Sunrise Shelter", "Food", "Packaged Bread",
     "Bread loaves (this batch is intentionally expired for the demo)",
     40, date.today().isoformat(), "Sealed"),
    ("Lions Club Chapter", "Clothes", "Winter Jackets",
     "Adult jackets, freshly laundered", 60, None, "Like New"),
    ("Municipal School Drive", "Books", "School Textbooks",
     "Class 5-8 textbooks from book donation drive", 80, None,
     "Used - good condition"),
    ("Pharma Care CSR", "Medicines", "ORS Sachets",
     "Oral rehydration salts, 21 g sachets", 150,
     date.today().isoformat(), "New"),
    ("Rotary Youth Wing", "Other Essentials", "Hygiene Kits",
     "Soap, toothpaste, towels and sanitizer", 25, None, "New"),
]

DEMO_REQUESTS = [
    # recipient, category, item, qty, urgency, beneficiaries, note
    ("Hope Orphanage", "Food", "Rice", 40, "Critical", 80),
    ("Elder Care Home", "Food", "Basmati Rice", 20, "High", 30),
    ("Slum School Collective", "Books", "Textbooks", 60, "High", 150),
    ("Winter Relief Camp", "Clothes", "Jackets", 50, "Medium", 200),
    ("Community Health Post", "Medicines", "Paracetamol Tablets", 100,
     "Critical", 500),
    ("Flood Relief Kitchen", "Food", "Bread", 30, "Medium", 120),
    ("Night Shelter Trust", "Other Essentials", "Hygiene Kits", 20,
     "Low", 40),
    ("Rural Health Camp", "Medicines", "ORS Sachets", 100, "High", 300),
]


@app.route("/demo/load", methods=["POST"])
def demo_load():
    """Insert sample data once - clicking again will not duplicate it."""
    conn = db.get_db()

    already = conn.execute(
        "SELECT COUNT(*) AS n FROM donations WHERE is_demo = 1"
    ).fetchone()["n"]
    if already:
        flash("Sample data is already loaded (it is never duplicated).",
              "info")
        return redirect(url_for("dashboard"))

    today = date.today().isoformat()

    # Donation expiry dates: offset in days from today, None = no expiry.
    expiry_offsets = [40, 180, 1, -3, None, None, 120, None]

    conn.execute("BEGIN")
    for row, offset in zip(DEMO_DONATIONS, expiry_offsets):
        donor, category, item, desc, qty, _, condition = row
        if offset is None:
            expiry = None
        else:
            expiry = date.fromordinal(date.today().toordinal() + offset)
            expiry = expiry.isoformat()
        conn.execute(
            "INSERT INTO donations (donor_name, category, item_name, "
            "description, quantity, available_quantity, expiry_date, "
            "condition, status, is_demo, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'available', 1, ?)",
            (donor, category, item, desc, qty, qty, expiry, condition,
             today))

    for row in DEMO_REQUESTS:
        recipient, category, item, qty, urgency, beneficiaries = row
        conn.execute(
            "INSERT INTO community_requests (recipient_name, category, "
            "item_name, quantity_required, quantity_remaining, urgency, "
            "beneficiaries, request_date, status, is_demo, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?)",
            (recipient, category, item, qty, qty, urgency, beneficiaries,
             today, today))
    conn.commit()

    flash("Sample data loaded: 8 donations and 8 community requests "
          "(labelled as demo records).", "success")
    return redirect(url_for("dashboard"))


@app.route("/demo/clear", methods=["POST"])
def demo_clear():
    """Remove sample data (and its allocations) to reset the demo."""
    conn = db.get_db()
    conn.execute("BEGIN")
    conn.execute(
        "DELETE FROM allocations WHERE donation_id IN "
        "(SELECT id FROM donations WHERE is_demo = 1) OR request_id IN "
        "(SELECT id FROM community_requests WHERE is_demo = 1)")
    conn.execute("DELETE FROM donations WHERE is_demo = 1")
    conn.execute("DELETE FROM community_requests WHERE is_demo = 1")
    conn.commit()
    flash("Sample data removed.", "info")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    db.init_db()          # create tables on first run
    app.run(debug=True, host="127.0.0.1", port=5000)
