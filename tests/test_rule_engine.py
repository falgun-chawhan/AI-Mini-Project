"""
test_rule_engine.py - automated tests for the IF-THEN rule engine.

Covers audit scenarios 1-10 plus the cross-cutting engine guarantees:
deterministic results, understandable Rule 6 reasoning, and quantities
that never go negative or over-allocate.
"""

from datetime import date, timedelta

import pytest

import rule_engine as engine
from conftest import seed_donation, seed_request

TODAY = date.today()


def days(offset):
    """Expiry date `offset` days from today (negative = already expired)."""
    return (TODAY + timedelta(days=offset)).isoformat()


# ---------------------------------------------------------------------------
# Scenario 1 - matching categories and compatible items
# ---------------------------------------------------------------------------
class TestScenario1CategoryAndItemMatch:
    def test_matching_category_and_compatible_items_is_recommended(self):
        donations = [make_d(id=1, category="Food", item_name="Basmati Rice",
                            expiry=days(40))]
        requests = [make_r(id=1, category="Food", item_name="Rice",
                           urgency="Critical")]
        result = engine.recommend_matches(donations, requests)
        assert len(result["recommendations"]) == 1
        rec = result["recommendations"][0]
        assert rec["donation_id"] == 1 and rec["request_id"] == 1

    def test_rule1_reason_is_present_in_explanation(self):
        result = engine.recommend_matches(
            [make_d(expiry=days(40))], [make_r()])
        assert any("Rule 1" in reason
                   for reason in result["recommendations"][0]["explanation"])

    @pytest.mark.parametrize("donation_item,request_item", [
        ("Winter Jackets", "Jackets"),
        ("School Textbooks", "Textbooks"),
        ("Hygiene Kits", "Hygiene Kit"),
        ("Basmati Rice", "Sona Masoori Rice"),
    ])
    def test_compatible_item_word_pairs(self, donation_item, request_item):
        assert engine.items_compatible(donation_item, request_item) is True

    def test_same_category_but_incompatible_items_not_recommended(self):
        donations = [make_d(id=1, category="Food", item_name="Basmati Rice")]
        requests = [make_r(id=1, category="Food", item_name="Bread")]
        result = engine.recommend_matches(donations, requests)
        assert result["recommendations"] == []
        assert any("not compatible" in s["reasons"][0]
                   for s in result["skipped"])


# ---------------------------------------------------------------------------
# Scenario 2 - category mismatch
# ---------------------------------------------------------------------------
class TestScenario2CategoryMismatch:
    def test_mismatched_category_is_rejected_with_rule1_reason(self):
        donations = [make_d(category="Food", item_name="Rice")]
        requests = [make_r(category="Medicines", item_name="Rice")]
        eligible, reasons = engine.evaluate_pair(donations[0], requests[0])
        assert eligible is False
        assert "categories do not match" in reasons[0]
        assert "Rule 1" in reasons[0]

    def test_mismatched_pair_never_appears_in_recommendations(self):
        result = engine.recommend_matches(
            [make_d(category="Clothes", item_name="Jackets")],
            [make_r(category="Food", item_name="Jackets")])
        assert result["recommendations"] == []


# ---------------------------------------------------------------------------
# Scenario 3 - Critical vs Low competing for the same donation
# ---------------------------------------------------------------------------
class TestScenario3UrgencyCompetition:
    def test_critical_request_wins_limited_donation(self):
        donations = [make_d(id=1, quantity=10, expiry=days(40))]
        requests = [
            make_r(id=1, recipient_name="Low Shelter", urgency="Low",
                   quantity=10, beneficiaries=500),
            make_r(id=2, recipient_name="Critical Shelter",
                   urgency="Critical", quantity=10, beneficiaries=10),
        ]
        result = engine.recommend_matches(donations, requests)
        assert len(result["recommendations"]) == 1
        rec = result["recommendations"][0]
        assert rec["request_id"] == 2, "Critical must be served first"
        assert rec["quantity"] == 10, "donation fully consumed by Critical"

    def test_rank_requests_orders_urgency_before_beneficiaries(self):
        ranked = engine.rank_requests([
            make_r(id=1, urgency="Low", beneficiaries=9999),
            make_r(id=2, urgency="Critical", beneficiaries=1),
        ])
        assert [r["id"] for r in ranked] == [2, 1], \
            "Rule 2 beats Rule 5 when urgencies differ"

    def test_rule2_and_rule5_reasons_in_explanation(self):
        result = engine.recommend_matches(
            [make_d(expiry=days(40))],
            [make_r(urgency="Critical", beneficiaries=120)])
        text = " ".join(result["recommendations"][0]["explanation"])
        assert "Rule 2" in text and "Critical" in text
        assert "Rule 5" in text and "120" in text


# ---------------------------------------------------------------------------
# Scenario 4 - donation smaller than the request (partial fulfilment)
# ---------------------------------------------------------------------------
class TestScenario4PartialAllocation:
    def test_allocates_min_of_available_and_needed(self):
        donations = [make_d(id=1, quantity=5, expiry=days(40))]
        requests = [make_r(id=1, quantity=20)]
        result = engine.recommend_matches(donations, requests)
        assert len(result["recommendations"]) == 1
        assert result["recommendations"][0]["quantity"] == 5

    def test_rule4_explanation_names_the_limited_side(self):
        result = engine.recommend_matches(
            [make_d(quantity=5, expiry=days(40))], [make_r(quantity=20)])
        text = " ".join(result["recommendations"][0]["explanation"])
        assert "Rule 4" in text and "available donation stock (5)" in text

    def test_allocate_updates_statuses_for_partial_result(self, db_path):
        """Apply the recommendation and verify both statuses in SQLite."""
        import sqlite3
        seed_donation(db_path, id=1, quantity=5, expiry_date=days(40))
        seed_request(db_path, id=1, quantity=20)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        engine.allocate(conn, 1, 1, 5, ["Rule 4: partial"])
        conn.commit()
        donation = conn.execute("SELECT * FROM donations").fetchone()
        request = conn.execute("SELECT * FROM community_requests").fetchone()
        assert donation["available_quantity"] == 0
        assert donation["status"] == "fully_allocated"
        assert request["quantity_remaining"] == 15
        assert request["status"] == "partially_fulfilled"
        conn.close()


# ---------------------------------------------------------------------------
# Scenario 5 - fully allocated donation cannot be reused
# ---------------------------------------------------------------------------
class TestScenario5FullyAllocated:
    def test_zero_available_donation_not_recommended(self):
        donations = [make_d(id=1, quantity=50, available_quantity=0,
                            expiry=days(40))]
        requests = [make_r(id=1)]
        result = engine.recommend_matches(donations, requests)
        assert result["recommendations"] == []

    def test_allocate_refuses_quantity_beyond_availability(self, db_path):
        import sqlite3
        seed_donation(db_path, id=1, quantity=50, expiry_date=days(40))
        seed_request(db_path, id=1, quantity=100)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        with pytest.raises(ValueError, match="only 50 unit"):
            engine.allocate(conn, 1, 1, 51, ["Rule 4"])
        conn.close()

    def test_second_allocation_after_exhaustion_fails(self, db_path):
        import sqlite3
        seed_donation(db_path, id=1, quantity=10, expiry_date=days(40))
        seed_request(db_path, id=1, quantity=10)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        engine.allocate(conn, 1, 1, 10, ["first"])
        conn.commit()
        with pytest.raises(ValueError, match="only 0 unit"):
            engine.allocate(conn, 1, 1, 1, ["second"])
        conn.close()


# ---------------------------------------------------------------------------
# Scenario 6 - expired donation rejected
# ---------------------------------------------------------------------------
class TestScenario6ExpiredRejected:
    def test_expired_donation_pair_is_blocked(self):
        eligible, reasons = engine.evaluate_pair(
            make_d(expiry=days(-3), category="Food", item_name="Bread"),
            make_r(category="Food", item_name="Bread"))
        assert eligible is False
        assert "BLOCKED" in reasons[0] and "Rule 3" in reasons[0]

    def test_expired_donation_never_recommended(self):
        result = engine.recommend_matches(
            [make_d(expiry=days(-1))], [make_r()])
        assert result["recommendations"] == []
        assert any("expired" in r for s in result["skipped"]
                   for r in s["reasons"])

    def test_allocate_refuses_expired_donation(self, db_path):
        import sqlite3
        seed_donation(db_path, id=1, expiry_date=days(-5))
        seed_request(db_path, id=1, quantity=10)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        with pytest.raises(ValueError, match="Rule 3 blocked"):
            engine.allocate(conn, 1, 1, 1, ["x"])
        conn.close()

    def test_perishable_food_inside_safety_window_blocked(self):
        eligible, reasons = engine.evaluate_pair(
            make_d(expiry=days(1), category="Food", item_name="Milk"),
            make_r(category="Food", item_name="Milk"))
        assert eligible is False
        assert "safety" in reasons[0].lower() or "window" in reasons[0].lower()

    def test_food_beyond_safety_window_allowed(self):
        eligible, _ = engine.evaluate_pair(
            make_d(expiry=days(40), category="Food", item_name="Rice"),
            make_r(category="Food", item_name="Rice"))
        assert eligible is True


# ---------------------------------------------------------------------------
# Scenario 7 - invalid or missing expiry date handled safely
# ---------------------------------------------------------------------------
class TestScenario7InvalidOrMissingExpiry:
    def test_missing_expiry_on_non_perishable_is_allowed(self):
        """Clothes/books legitimately have no expiry date."""
        eligible, reasons = engine.evaluate_pair(
            make_d(category="Clothes", item_name="Jackets", expiry=None),
            make_r(category="Clothes", item_name="Jackets"))
        assert eligible is True

    def test_missing_expiry_on_perishable_food_is_blocked(self):
        """Safety window cannot be verified -> must not be allocated."""
        eligible, reasons = engine.evaluate_pair(
            make_d(category="Food", item_name="Rice", expiry=None),
            make_r(category="Food", item_name="Rice"))
        assert eligible is False, \
            "perishable food without expiry must not pass Rule 3"
        assert "Rule 3" in reasons[0]

    @pytest.mark.parametrize("bad_date", ["not-a-date", "2026-13-45",
                                          "31/02/2026", "banana"])
    def test_invalid_expiry_string_is_blocked(self, bad_date):
        eligible, reasons = engine.evaluate_pair(
            make_d(category="Food", item_name="Rice", expiry=bad_date),
            make_r(category="Food", item_name="Rice"))
        assert eligible is False, \
            "expiry '{0}' must not be treated as no-expiry".format(bad_date)
        assert "Rule 3" in reasons[0]

    def test_invalid_expiry_on_non_food_is_blocked(self):
        """A date we cannot read may be in the past - refuse safely."""
        eligible, reasons = engine.evaluate_pair(
            make_d(category="Medicines", item_name="Paracetamol Tablets",
                   expiry="garbage"),
            make_r(category="Medicines", item_name="Paracetamol Tablets"))
        assert eligible is False
        assert "Rule 3" in reasons[0]

    def test_allocate_refuses_unreadable_expiry(self, db_path):
        import sqlite3
        seed_donation(db_path, id=1, expiry_date="not-a-date")
        seed_request(db_path, id=1, quantity=10)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        with pytest.raises(ValueError, match="Rule 3"):
            engine.allocate(conn, 1, 1, 1, ["x"])
        conn.close()


# ---------------------------------------------------------------------------
# Scenario 8 - two requests compete for one limited donation
# ---------------------------------------------------------------------------
class TestScenario8TwoRequestsOneDonation:
    def test_split_between_two_competing_requests(self):
        donations = [make_d(id=1, quantity=10, expiry=days(40))]
        requests = [
            make_r(id=1, urgency="High", quantity=8, beneficiaries=100),
            make_r(id=2, urgency="High", quantity=8, beneficiaries=100),
        ]
        result = engine.recommend_matches(donations, requests)
        assert len(result["recommendations"]) == 2
        total = sum(r["quantity"] for r in result["recommendations"])
        assert total == 10, "sum allocated must equal available stock"
        assert result["recommendations"][1]["quantity"] == 2, \
            "second request gets only the remainder (partial)"

    def test_deterministic_tiebreak_by_date_then_id(self):
        donations = [make_d(id=1, quantity=10, expiry=days(40))]
        requests = [
            make_r(id=2, urgency="High", beneficiaries=100, quantity=5,
                   request_date="2026-10-05"),
            make_r(id=1, urgency="High", beneficiaries=100, quantity=5,
                   request_date="2026-10-01"),
        ]
        result = engine.recommend_matches(donations, requests)
        assert [r["request_id"] for r in result["recommendations"]] == [1, 2], \
            "earlier request date wins when urgency and beneficiaries tie"


# ---------------------------------------------------------------------------
# Scenario 9 - one request served by multiple donations
# ---------------------------------------------------------------------------
class TestScenario9MultipleDonationsOneRequest:
    def test_request_fulfilled_from_two_donations(self):
        donations = [
            make_d(id=1, item_name="Rice", quantity=10, expiry=days(10)),
            make_d(id=2, donor_name="Second Donor", item_name="Rice",
                   quantity=25, expiry=days(30)),
        ]
        requests = [make_r(id=1, quantity=30)]
        result = engine.recommend_matches(donations, requests)
        by_donation = {r["donation_id"]: r["quantity"]
                       for r in result["recommendations"]}
        assert by_donation == {1: 10, 2: 20}, \
            "both donations used, second covers the remainder"
        assert sum(by_donation.values()) == 30

    def test_total_never_exceeds_either_side(self):
        donations = [make_d(id=1, quantity=7, expiry=days(40)),
                     make_d(id=2, quantity=7, expiry=days(40))]
        requests = [make_r(id=1, quantity=100)]
        result = engine.recommend_matches(donations, requests)
        assert sum(r["quantity"] for r in result["recommendations"]) == 14


# ---------------------------------------------------------------------------
# Scenario 10 - medicines flagged for verification
# ---------------------------------------------------------------------------
class TestScenario10MedicinesVerification:
    def test_medicine_recommendation_requires_verification(self):
        result = engine.recommend_matches(
            [make_d(id=1, category="Medicines",
                    item_name="Paracetamol Tablets", expiry=days(180))],
            [make_r(id=1, category="Medicines",
                    item_name="Paracetamol Tablets", urgency="Critical")])
        assert len(result["recommendations"]) == 1
        rec = result["recommendations"][0]
        assert rec["requires_verification"] is True

    def test_explanation_marks_medicine_for_verification(self):
        result = engine.recommend_matches(
            [make_d(category="Medicines", item_name="ORS Sachets",
                    expiry=days(120))],
            [make_r(category="Medicines", item_name="ORS Sachets")])
        text = " ".join(result["recommendations"][0]["explanation"])
        assert "MEDICINE" in text and "verify" in text.lower()

    def test_non_medicine_recommendation_needs_no_verification(self):
        result = engine.recommend_matches(
            [make_d(expiry=days(40))], [make_r()])
        assert result["recommendations"][0]["requires_verification"] is False


# ---------------------------------------------------------------------------
# Cross-cutting: determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_repeated_runs_identical(self):
        donations = [make_d(id=1, expiry=days(40)),
                     make_d(id=2, quantity=10, expiry=days(20))]
        requests = [make_r(id=1, urgency="Critical"),
                    make_r(id=2, urgency="Low", beneficiaries=999)]
        first = engine.recommend_matches(donations, requests)
        for _ in range(4):
            assert engine.recommend_matches(donations, requests) == first

    def test_input_order_does_not_change_result(self):
        """Same rows in any order -> byte-identical recommendations."""
        donations = [make_d(id=1, item_name="Rice", quantity=10,
                            expiry=days(10)),
                     make_d(id=2, item_name="Rice", quantity=25,
                            expiry=days(30)),
                     make_d(id=3, category="Books", item_name="Textbooks",
                            expiry=None)]
        requests = [make_r(id=1, quantity=30),
                    make_r(id=2, category="Books", item_name="Textbooks",
                           urgency="Low", beneficiaries=5)]
        baseline = engine.recommend_matches(donations, requests)

        for shuffled in (list(reversed(donations)),
                         [donations[1], donations[2], donations[0]]):
            shuffled_requests = list(reversed(requests))
            again = engine.recommend_matches(shuffled, shuffled_requests)
            assert _strip(again) == _strip(baseline), \
                "donation/request input order must not change the outcome"

    def test_rank_requests_stable_sort(self):
        same = [make_r(id=i, urgency="Medium", beneficiaries=10,
                       request_date="2026-10-01") for i in (5, 3, 8)]
        first = [r["id"] for r in engine.rank_requests(same)]
        second = [r["id"] for r in engine.rank_requests(list(reversed(same)))]
        assert first == second == [3, 5, 8], "id tiebreak must be stable"


def _strip(result):
    """Recommendations and skipped lists must match exactly."""
    return (result["recommendations"], result["skipped"])


# ---------------------------------------------------------------------------
# Cross-cutting: Rule 6 reasoning quality
# ---------------------------------------------------------------------------
class TestReasoning:
    @pytest.fixture
    def mixed_result(self):
        from datetime import date as _d, timedelta as _td
        expired = (_d.today() - _td(days=3)).isoformat()
        soon = (_d.today() + _td(days=1)).isoformat()
        safe = (_d.today() + _td(days=40)).isoformat()
        return engine.recommend_matches(
            donations=[
                make_d(id=1, item_name="Rice", expiry=safe),
                make_d(id=2, category="Medicines",
                       item_name="Paracetamol Tablets", expiry=safe),
                make_d(id=3, item_name="Bread", expiry=expired),
                make_d(id=4, item_name="Milk", expiry=soon),
            ],
            requests=[
                make_r(id=1, urgency="Critical", beneficiaries=500),
                make_r(id=2, category="Medicines",
                       item_name="Paracetamol Tablets", urgency="High"),
            ])

    def test_every_recommendation_has_reasoning(self, mixed_result):
        assert mixed_result["recommendations"], "test needs recommendations"
        for rec in mixed_result["recommendations"]:
            assert rec["explanation"], "empty explanation on {0}".format(rec)
            text = " ".join(rec["explanation"])
            assert "Rule 1" in text, "must explain the category match"
            assert "Rule 4" in text, "must explain the quantity decision"
            assert "Rule 5" in text, "must mention beneficiaries"

    def test_every_rejection_has_a_rule_reason(self, mixed_result):
        assert mixed_result["skipped"], "test needs rejected pairs"
        for skipped in mixed_result["skipped"]:
            assert skipped["reasons"]
            assert any(r.startswith("Rule") for r in skipped["reasons"])

    def test_explanations_use_plain_readable_language(self, mixed_result):
        for rec in mixed_result["recommendations"]:
            for reason in rec["explanation"]:
                assert len(reason) > 20, "reason too terse: " + reason
                assert "\n" not in reason, "keep reasons single-line"


# ---------------------------------------------------------------------------
# Cross-cutting: quantities never negative, never over-allocated
# ---------------------------------------------------------------------------
class TestQuantityInvariants:
    def test_recommendations_stay_within_stock_and_need(self):
        donations = [make_d(id=1, quantity=13, expiry=days(40)),
                     make_d(id=2, quantity=6, expiry=days(40))]
        requests = [make_r(id=1, quantity=5),
                    make_r(id=2, urgency="Low", quantity=100),
                    make_r(id=3, urgency="Medium", quantity=100)]
        result = engine.recommend_matches(donations, requests)

        used_per_donation = {}
        for rec in result["recommendations"]:
            assert rec["quantity"] > 0, "zero/negative recommendation"
            used_per_donation[rec["donation_id"]] = (
                used_per_donation.get(rec["donation_id"], 0) + rec["quantity"])
        stock = {1: 13, 2: 6}
        for donation_id, used in used_per_donation.items():
            assert used <= stock[donation_id], "over-allocated a donation"

        needed_per_request = {}
        for rec in result["recommendations"]:
            needed_per_request[rec["request_id"]] = (
                needed_per_request.get(rec["request_id"], 0) + rec["quantity"])
        for request_id, used in needed_per_request.items():
            assert used <= 100, "over-allocated a request"

    def test_applying_every_recommendation_keeps_non_negative(self, db_path):
        import sqlite3
        seed_donation(db_path, id=1, quantity=13, expiry_date=days(40))
        seed_donation(db_path, id=2, quantity=6, expiry_date=days(40))
        seed_request(db_path, id=1, quantity=5)
        seed_request(db_path, id=2, urgency="Low", quantity=100)
        seed_request(db_path, id=3, urgency="Medium", quantity=100)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        donations = [dict(r) for r in conn.execute("SELECT * FROM donations")]
        requests = [dict(r) for r in
                    conn.execute("SELECT * FROM community_requests")]
        result = engine.recommend_matches(donations, requests)

        for rec in result["recommendations"]:
            engine.allocate(conn, rec["donation_id"], rec["request_id"],
                            rec["quantity"], rec["explanation"])
        conn.commit()

        for row in conn.execute("SELECT * FROM donations"):
            assert row["available_quantity"] >= 0
            assert row["available_quantity"] <= row["quantity"]
        for row in conn.execute("SELECT * FROM community_requests"):
            assert row["quantity_remaining"] >= 0
            assert row["quantity_remaining"] <= row["quantity_required"]
        conn.close()

    @pytest.mark.parametrize("bad_qty", [0, -1, -100])
    def test_allocate_rejects_zero_and_negative(self, db_path, bad_qty):
        import sqlite3
        seed_donation(db_path, id=1, expiry_date=days(40))
        seed_request(db_path, id=1, quantity=10)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        with pytest.raises(ValueError, match="greater than zero"):
            engine.allocate(conn, 1, 1, bad_qty, ["x"])
        conn.close()


# ---------------------------------------------------------------------------
# Small local factories (kept here so engine tests read top-to-bottom)
# ---------------------------------------------------------------------------
def make_d(**kw):
    from conftest import make_donation
    return make_donation(**kw)


def make_r(**kw):
    from conftest import make_request
    return make_request(**kw)
