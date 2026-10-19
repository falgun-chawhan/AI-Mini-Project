"""
rule_engine.py
--------------
Rule-based (IF-THEN) inference engine for the Smart Donation Distribution
System.

IMPORTANT: this module contains NO machine learning and calls NO external
AI API.  Every decision comes from the explicit IF-THEN rules below, so a
student can explain the whole engine line-by-line during a viva.

The six rules required by the project brief:

    RULE 1 - CATEGORY MATCH       : donation category must match the request
                                    category and the items must be compatible.
    RULE 2 - URGENCY PRIORITY     : Critical and High requests are ranked
                                    ahead of lower priority requests.
    RULE 3 - EXPIRY SAFETY        : expired donations are blocked, perishable
                                    food inside the safety window is blocked,
                                    medicines are never auto-allocated.
    RULE 4 - QUANTITY CHECK       : allocate min(available, still required).
    RULE 5 - BENEFICIARY PRIORITY : same urgency -> more beneficiaries first.
    RULE 6 - EXPLANATION          : every pair carries readable reasons.

Public functions
----------------
    evaluate_pair(donation, request)      -> list of rule strings (Rule 1/3)
    rank_requests(requests)               -> deterministically ordered list
    recommend_matches(donations, requests)-> {"recommendations": [...],
                                              "skipped": [...]}
    allocate(db_conn, donation, request, quantity, reasons)
                                          -> applies one allocation in DB
"""

from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Rule configuration (easy to explain and to change during the viva)
# ---------------------------------------------------------------------------
URGENCY_RANK = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}

# Items that spoil quickly: Rule 3 checks them against a safety window.
PERISHABLE_CATEGORIES = {"Food"}

# How many days ahead a perishable item must still be good for.
SAFETY_WINDOW_DAYS = 2

# Medicines are never allocated automatically (Rule 3).
MEDICAL_CATEGORIES = {"Medicines"}

# Words that are too generic to prove two items are compatible.
GENERIC_WORDS = {"item", "items", "essential", "essentials", "general", "misc"}

# Small synonym table so "Rice" matches "Basmati Rice", "Jacket" matches
# "Winter Jacket", etc.  Purely dictionary based - not machine learning.
SYNONYMS = {
    "rice": {"rice", "basmati", "basmatirice", "sona", "masoori"},
    "jacket": {"jacket", "jackets", "winter", "winterjacket", "sweater", "coat"},
    "blanket": {"blanket", "blankets", "quilt"},
    "book": {"book", "books", "textbook", "notebook", "notebooks", "copy"},
    "medicine": {"medicine", "medicines", "tablet", "tablets", "syrup", "capsule"},
    "cloth": {"cloth", "clothes", "clothing", "garment", "garments", "dress"},
    "food": {"food", "grocery", "groceries", "ration"},
    "milk": {"milk", "dairy"},
    "bread": {"bread", "loaf", "loaves"},
    "water": {"water", "bottle", "bottles", "drinking"},
    "hygiene": {"hygiene", "kit", "kits", "sanitary", "toiletries"},
    "cereal": {"cereal", "cornflakes", "oats"},
}


def _parse_date(value):
    """Return a datetime.date from a string, or None if missing/invalid."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _item_words(name):
    """Split an item name into lowercase words, e.g. 'Winter Jacket' -> set."""
    text = (name or "").lower().replace("-", " ").replace("_", " ")
    return {w for w in text.split() if w and w not in GENERIC_WORDS}


def _same_synonym_group(word_a, word_b):
    """True when two words belong to the same dictionary synonym group."""
    for group in SYNONYMS.values():
        if word_a in group and word_b in group:
            return True
    return False


def items_compatible(donation_item, request_item):
    """
    RULE 1 helper - are the two item names compatible?

    Compatible when the names share a word, one name contains the other,
    or both words belong to the same synonym group from the table above.
    """
    words_a = _item_words(donation_item)
    words_b = _item_words(request_item)
    if not words_a or not words_b:
        return False
    for word_a in words_a:
        for word_b in words_b:
            if word_a == word_b or word_a in word_b or word_b in word_a:
                return True
            if _same_synonym_group(word_a, word_b):
                return True
    return False


# ---------------------------------------------------------------------------
# RULE 1 + RULE 3 : can this donation ever be given to this request?
# ---------------------------------------------------------------------------
def evaluate_pair(donation, request):
    """
    Evaluate one donation-request pair with RULE 1 (category match) and
    RULE 3 (expiry safety).

    Returns a tuple (eligible, rule_list):
        eligible  -> True when the pair may enter the ranked candidate list
        rule_list -> human readable explanation strings (Rule 6)
    """
    reasons = []

    # ----- RULE 1 - CATEGORY MATCH + ITEM COMPATIBILITY -------------------
    if donation["category"] != request["category"]:
        return False, ["Rule 1: categories do not match "
                       "({donation} vs {request}).".format(
                           donation=donation["category"],
                           request=request["category"])]
    if not items_compatible(donation["item_name"], request["item_name"]):
        return False, ["Rule 1: categories match but items are not "
                       "compatible ({donation} vs {request}).".format(
                           donation=donation["item_name"],
                           request=request["item_name"])]
    reasons.append(
        "Rule 1: category '{cat}' matches and items are compatible "
        "('{don}' <-> '{req}').".format(
            cat=donation["category"],
            don=donation["item_name"],
            req=request["item_name"]))

    # ----- RULE 3 - EXPIRY SAFETY -----------------------------------------
    today = date.today()
    raw_expiry = donation.get("expiry_date")
    expiry = _parse_date(raw_expiry)

    # An expiry date that we cannot read is NOT the same as "no expiry":
    # treat it as unsafe (it could be in the past).
    if raw_expiry and expiry is None:
        return False, [
            "Rule 3: BLOCKED - expiry date '{raw}' is not a valid date, "
            "so the item cannot be verified as safe.".format(
                raw=raw_expiry)]

    if expiry is not None and expiry < today:
        return False, ["Rule 3: BLOCKED - donation expired on {exp}.".format(
            exp=expiry.isoformat())]

    if donation["category"] in PERISHABLE_CATEGORIES:
        # Perishable food MUST have a verifiable expiry date, otherwise
        # the safety window cannot be checked.
        if expiry is None:
            return False, [
                "Rule 3: BLOCKED - perishable food has no expiry date, so "
                "it cannot be verified as safe within the {days}-day "
                "safety window.".format(days=SAFETY_WINDOW_DAYS)]
        safe_until = today + timedelta(days=SAFETY_WINDOW_DAYS)
        if expiry <= safe_until:
            return False, [
                "Rule 3: BLOCKED - perishable food expires on {exp}, which "
                "is not {days} days away, so it falls inside the safety "
                "window and cannot be safely distributed.".format(
                    exp=expiry.isoformat(), days=SAFETY_WINDOW_DAYS)]
        reasons.append(
            "Rule 3: expiry date {exp} is beyond the {days}-day safety "
            "window, so distribution is safe.".format(
                exp=expiry.isoformat(), days=SAFETY_WINDOW_DAYS))
    elif expiry is not None:
        reasons.append("Rule 3: item has not expired (expiry {exp}).".format(
            exp=expiry.isoformat()))

    if donation["category"] in MEDICAL_CATEGORIES:
        reasons.append(
            "Rule 3: MEDICINE - requires authorized verification before "
            "it can be allocated.")
        # The pair stays eligible but allocate() will refuse automatic
        # confirmation, so an administrator must verify it first.

    return True, reasons


# ---------------------------------------------------------------------------
# RULE 2 + RULE 5 : deterministic ranking of requests
# ---------------------------------------------------------------------------
def rank_requests(requests):
    """
    RULE 2 (urgency) and RULE 5 (beneficiaries).

    Sort order, all else being equal:
        1. higher urgency first            (Rule 2)
        2. more beneficiaries first        (Rule 5)
        3. earlier request date            (stable, deterministic)
        4. lower request id                (stable, deterministic)
    """
    return sorted(
        requests,
        key=lambda r: (
            -URGENCY_RANK.get(r.get("urgency", "Low"), 0),
            -int(r.get("beneficiaries", 0) or 0),
            str(r.get("request_date", "")),
            int(r.get("id", 0)),
        ),
    )


# ---------------------------------------------------------------------------
# RULE 4 + RULE 6 : build the full recommendation list
# ---------------------------------------------------------------------------
def recommend_matches(donations, requests):
    """
    Evaluate every donation-request pair and build a ranked list of
    recommended allocations.

    Both arguments are lists of dictionaries (rows from SQLite work fine).

    Returns a dictionary:
        recommendations -> list of dicts ready to show the administrator
        skipped         -> pairs that were considered and rejected, with
                           the rule that rejected them (good for the demo)
    """
    ranked_requests = rank_requests(requests)

    # Deterministic donation order: first-expiry-first-out (perishable goods
    # with the nearest expiry are used first), then lower id as the
    # tiebreak.  This makes the engine independent of the row order SQLite
    # happens to return.
    donations = sorted(
        donations,
        key=lambda d: (_parse_date(d.get("expiry_date")) or date.max,
                       int(d.get("id", 0))))

    # Working copy of availability so nothing can be double allocated.
    available = {int(d["id"]): int(d["available_quantity"]) for d in donations}

    recommendations = []
    skipped = []

    for request in ranked_requests:
        remaining_needed = int(request["quantity_remaining"])

        # Find eligible donations for this request (RULE 1 + RULE 3).
        eligible_pairs = []
        for donation in donations:
            if available.get(int(donation["id"]), 0) <= 0:
                continue
            eligible, reasons = evaluate_pair(donation, request)
            if eligible:
                eligible_pairs.append((donation, reasons))
            else:
                skipped.append({
                    "donation_id": int(donation["id"]),
                    "request_id": int(request["id"]),
                    "donation": "{item} (from {donor})".format(
                        item=donation["item_name"],
                        donor=donation["donor_name"]),
                    "request": "{item} (for {who})".format(
                        item=request["item_name"],
                        who=request["recipient_name"]),
                    "reasons": reasons,
                })

        # RULE 2 + RULE 5: order the eligible donations by the priority of
        # the request they are helping (requests were already ranked above,
        # so we simply follow the ranked request order).
        for donation, reasons in eligible_pairs:
            if remaining_needed <= 0:
                break

            donation_id = int(donation["id"])
            free_qty = available[donation_id]
            if free_qty <= 0:
                continue

            # ----- RULE 4 - QUANTITY CHECK --------------------------------
            quantity = min(free_qty, remaining_needed)

            # Build the full explanation (RULE 6).
            explanation = list(reasons)
            explanation.append(
                "Rule 4: allocating {qty} unit(s) - limited by "
                "{why}.".format(
                    qty=quantity,
                    why=("available donation stock ({free})".format(free=free_qty)
                         if free_qty <= remaining_needed
                         else "remaining request ({need})".format(
                             need=remaining_needed))))

            urgency = request.get("urgency", "Low")
            if urgency in ("Critical", "High"):
                explanation.append(
                    "Rule 2: request urgency is '{u}', which is ranked "
                    "ahead of Medium and Low requests.".format(u=urgency))
            explanation.append(
                "Rule 5: request serves {b} beneficiary(ies).".format(
                    b=request.get("beneficiaries", 0)))

            requires_verification = donation["category"] in MEDICAL_CATEGORIES
            if requires_verification:
                explanation.append(
                    "Rule 3: this is a MEDICINE donation - the "
                    "administrator must verify it before it is "
                    "allocated.")

            recommendations.append({
                "donation_id": donation_id,
                "request_id": int(request["id"]),
                "donation_item": donation["item_name"],
                "donor_name": donation["donor_name"],
                "request_item": request["item_name"],
                "recipient_name": request["recipient_name"],
                "category": donation["category"],
                "urgency": urgency,
                "quantity": quantity,
                "requires_verification": requires_verification,
                "explanation": explanation,
            })

            # Update the working copies so nothing is over-allocated.
            available[donation_id] -= quantity
            remaining_needed -= quantity

    return {"recommendations": recommendations, "skipped": skipped}


# ---------------------------------------------------------------------------
# Applying a confirmed allocation (called by app.py inside a transaction)
# ---------------------------------------------------------------------------
def allocate(db, donation_id, request_id, quantity, reasons, today=None):
    """
    Apply ONE confirmed allocation.

    - refuses expired donations (Rule 3)
    - refuses over-allocation (Rule 4)
    - updates donation + request quantities and statuses
    - inserts a row into the allocation history (Rule 6 explanation stored)

    The caller (app.py) wraps several allocate() calls in one SQLite
    transaction, so a failure in the middle never leaves half-updated data.
    """
    today = today or date.today().isoformat()

    donation = db.execute(
        "SELECT * FROM donations WHERE id = ?", (donation_id,)).fetchone()
    request_row = db.execute(
        "SELECT * FROM community_requests WHERE id = ?",
        (request_id,)).fetchone()

    if donation is None or request_row is None:
        raise ValueError("Donation or request not found.")
    if quantity <= 0:
        raise ValueError("Allocation quantity must be greater than zero.")

    # RULE 3 - never allocate an item whose expiry cannot be verified,
    # and never allocate an expired donation.
    raw_expiry = donation["expiry_date"]
    expiry = _parse_date(raw_expiry)
    if raw_expiry and expiry is None:
        raise ValueError(
            "Rule 3 blocked allocation: expiry date '{raw}' is not a valid "
            "date.".format(raw=raw_expiry))
    if expiry is not None and expiry < date.fromisoformat(today):
        raise ValueError(
            "Rule 3 blocked allocation: donation expired on {exp}.".format(
                exp=expiry.isoformat()))
    if donation["category"] in PERISHABLE_CATEGORIES and expiry is None:
        raise ValueError(
            "Rule 3 blocked allocation: perishable food has no expiry "
            "date.")

    # RULE 4 - never over-allocate either side.
    if quantity > donation["available_quantity"]:
        raise ValueError(
            "Rule 4 blocked allocation: only {qty} unit(s) available.".format(
                qty=donation["available_quantity"]))
    if quantity > request_row["quantity_remaining"]:
        raise ValueError(
            "Rule 4 blocked allocation: request only needs {qty} more "
            "unit(s).".format(qty=request_row["quantity_remaining"]))

    # Update donation quantities + status.
    new_donation_available = donation["available_quantity"] - quantity
    donation_status = ("fully_allocated" if new_donation_available <= 0
                       else "partially_allocated")
    db.execute(
        "UPDATE donations SET available_quantity = ?, status = ? "
        "WHERE id = ?",
        (new_donation_available, donation_status, donation_id))

    # Update request quantities + status.
    new_request_remaining = request_row["quantity_remaining"] - quantity
    req_status = ("fulfilled" if new_request_remaining <= 0
                  else "partially_fulfilled")
    db.execute(
        "UPDATE community_requests SET quantity_remaining = ?, status = ? "
        "WHERE id = ?",
        (new_request_remaining, req_status, request_id))

    # Keep the history (Rule 6 explanation is stored with the record).
    db.execute(
        "INSERT INTO allocations "
        "(donation_id, request_id, quantity, allocation_date, reasons, "
        " status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'confirmed', ?)",
        (donation_id, request_id, quantity, today,
         " | ".join(reasons), today))

    return {
        "donation_id": donation_id,
        "request_id": request_id,
        "quantity": quantity,
        "donation_status": donation_status,
        "request_status": req_status,
    }
