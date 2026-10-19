"""
test_app.py - end-to-end tests through the Flask routes (HTTP level).

Covers audit scenarios 11-12 plus: transaction consistency, dashboard
counts, persistence across a restart, and forms/filters/navigation links.
"""

import sqlite3
from datetime import date, timedelta

import pytest

from conftest import seed_donation, seed_request, snapshot, stat_values

TODAY = date.today()


def days(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


def get_token(client, path):
    """Fetch a fresh one-time form token from a page."""
    html = client.get(path).get_data(as_text=True)
    start = html.index('name="form_token" value="') + len('name="form_token" value="')
    return html[start:start + 16]


# ---------------------------------------------------------------------------
# Scenario 11 - invalid, zero and negative quantities rejected
# ---------------------------------------------------------------------------
class TestScenario11InvalidQuantities:
    def _post_donation(self, client, token, **overrides):
        data = {"form_token": token, "donor_name": "Test Donor",
                "category": "Food", "item_name": "Rice", "quantity": "10",
                "condition": "New"}
        data.update(overrides)
        return client.post("/donations/new", data=data, follow_redirects=True)

    @pytest.mark.parametrize("bad_quantity", ["0", "-5", "-100"])
    def test_donation_with_zero_or_negative_quantity_rejected(
            self, client, db_path, bad_quantity):
        token = get_token(client, "/donations")
        response = self._post_donation(client, token, quantity=bad_quantity)
        html = response.get_data(as_text=True)
        assert "alert-danger" in html
        assert "greater than zero" in html or "cannot be negative" in html
        conn = sqlite3.connect(db_path)
        assert conn.execute("SELECT COUNT(*) FROM donations").fetchone()[0] == 0
        conn.close()

    def test_donation_with_non_numeric_quantity_rejected(
            self, client, db_path):
        token = get_token(client, "/donations")
        response = self._post_donation(client, token, quantity="many")
        assert "must be a whole number" in response.get_data(as_text=True)
        conn = sqlite3.connect(db_path)
        assert conn.execute("SELECT COUNT(*) FROM donations").fetchone()[0] == 0
        conn.close()

    @pytest.mark.parametrize("bad_quantity", ["0", "-3"])
    def test_request_with_zero_or_negative_quantity_rejected(
            self, client, db_path, bad_quantity):
        token = get_token(client, "/requests")
        response = client.post("/requests/new", data={
            "form_token": token, "recipient_name": "Needy Home",
            "category": "Food", "item_name": "Rice",
            "quantity": bad_quantity, "urgency": "High",
            "beneficiaries": "10",
            "request_date": TODAY.isoformat()}, follow_redirects=True)
        assert "alert-danger" in response.get_data(as_text=True)
        conn = sqlite3.connect(db_path)
        assert conn.execute(
            "SELECT COUNT(*) FROM community_requests").fetchone()[0] == 0
        conn.close()

    def test_negative_beneficiaries_rejected(self, client, db_path):
        token = get_token(client, "/requests")
        response = client.post("/requests/new", data={
            "form_token": token, "recipient_name": "Needy Home",
            "category": "Food", "item_name": "Rice", "quantity": "5",
            "urgency": "High", "beneficiaries": "-2",
            "request_date": TODAY.isoformat()}, follow_redirects=True)
        assert "alert-danger" in response.get_data(as_text=True)
        conn = sqlite3.connect(db_path)
        assert conn.execute(
            "SELECT COUNT(*) FROM community_requests").fetchone()[0] == 0
        conn.close()

    def test_duplicate_form_token_rejected(self, client, db_path):
        token = get_token(client, "/donations")
        payload = {"form_token": token, "donor_name": "Repeat Donor",
                   "category": "Food", "item_name": "Rice", "quantity": "5",
                   "condition": "New"}
        first = client.post("/donations/new", data=payload,
                            follow_redirects=True)
        assert "added successfully" in first.get_data(as_text=True)
        second = client.post("/donations/new", data=payload,
                             follow_redirects=True)
        assert "already submitted" in second.get_data(as_text=True)
        conn = sqlite3.connect(db_path)
        assert conn.execute("SELECT COUNT(*) FROM donations").fetchone()[0] == 1
        conn.close()

    def test_invalid_date_rejected(self, client, db_path):
        token = get_token(client, "/donations")
        response = client.post("/donations/new", data={
            "form_token": token, "donor_name": "D", "category": "Food",
            "item_name": "Rice", "quantity": "5", "condition": "New",
            "expiry_date": "not-a-date"}, follow_redirects=True)
        assert "valid date" in response.get_data(as_text=True)
        conn = sqlite3.connect(db_path)
        assert conn.execute("SELECT COUNT(*) FROM donations").fetchone()[0] == 0
        conn.close()

    def test_invalid_category_rejected(self, client, db_path):
        token = get_token(client, "/donations")
        response = self._post_donation(client, token,
                                       category="Electronics")
        assert "must be one of" in response.get_data(as_text=True)
        conn = sqlite3.connect(db_path)
        assert conn.execute("SELECT COUNT(*) FROM donations").fetchone()[0] == 0
        conn.close()


# ---------------------------------------------------------------------------
# Scenario 12 - recommendations do not touch inventory until confirmation
# ---------------------------------------------------------------------------
class TestScenario12RecommendationsAreReadOnly:
    def test_viewing_matching_page_changes_nothing(
            self, client, db_path, seed):
        seed["donation"](db_path, id=1, quantity=10, expiry_date=days(40))
        seed["request"](db_path, id=1, quantity=5)
        before = snapshot(db_path)

        for _ in range(3):           # revisiting must also change nothing
            html = client.get("/matching").get_data(as_text=True)
        assert "recommendation" in html, "page should show recommendations"
        assert snapshot(db_path) == before, \
            "viewing recommendations must not modify inventory"

    def test_cancelling_changes_nothing(self, client, db_path, seed):
        seed["donation"](db_path, id=1, quantity=10, expiry_date=days(40))
        seed["request"](db_path, id=1, quantity=5)
        before = snapshot(db_path)

        response = client.get("/")   # admin navigates away (cancels)
        assert response.status_code == 200
        assert snapshot(db_path) == before

    def test_confirm_without_selection_changes_nothing(
            self, client, db_path, seed):
        seed["donation"](db_path, id=1, quantity=10, expiry_date=days(40))
        seed["request"](db_path, id=1, quantity=5)
        before = snapshot(db_path)
        response = client.post("/matching/confirm", data={},
                               follow_redirects=True)
        assert "No recommendations were selected" in \
            response.get_data(as_text=True)
        assert snapshot(db_path) == before


# ---------------------------------------------------------------------------
# Transaction consistency
# ---------------------------------------------------------------------------
class TestTransactions:
    def _confirm(self, client, selections):
        return client.post("/matching/confirm",
                           data={"recommendation": selections},
                           follow_redirects=True)

    def test_valid_confirmation_persists_and_updates_everything(
            self, client, db_path, seed):
        seed["donation"](db_path, id=1, quantity=10, expiry_date=days(40))
        seed["request"](db_path, id=1, quantity=4)
        response = self._confirm(client, ["1:1:4"])
        html = response.get_data(as_text=True)
        assert "Confirmed 1 allocation(s)" in html

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        donation = conn.execute("SELECT * FROM donations").fetchone()
        request = conn.execute("SELECT * FROM community_requests").fetchone()
        allocation = conn.execute("SELECT * FROM allocations").fetchone()
        assert donation["available_quantity"] == 6
        assert donation["status"] == "partially_allocated"
        assert request["quantity_remaining"] == 0
        assert request["status"] == "fulfilled"
        assert allocation["quantity"] == 4
        assert allocation["status"] == "confirmed"
        assert "Rule" in allocation["reasons"], "history keeps reasoning"
        conn.close()

    def test_over_allocation_rejected_and_no_partial_state(
            self, client, db_path, seed):
        """A pair beyond stock must not be silently applied."""
        seed["donation"](db_path, id=1, quantity=10, expiry_date=days(40))
        seed["request"](db_path, id=1, quantity=4)
        self._confirm(client, ["1:1:40"])          # 40 > 10 available
        conn = sqlite3.connect(db_path)
        assert conn.execute(
            "SELECT COUNT(*) FROM allocations").fetchone()[0] == 0
        row = conn.execute(
            "SELECT available_quantity, status FROM donations").fetchone()
        assert row[0] == 10 and row[1] == "available"
        conn.close()

    def test_fully_allocated_pair_cannot_be_confirmed_twice(
            self, client, db_path, seed):
        seed["donation"](db_path, id=1, quantity=10, expiry_date=days(40))
        seed["request"](db_path, id=1, quantity=10)
        first = self._confirm(client, ["1:1:10"])
        assert "Confirmed 1 allocation(s)" in first.get_data(as_text=True)

        second = self._confirm(client, ["1:1:10"])  # replay the old pair
        html = second.get_data(as_text=True)
        assert "Confirmed 0 allocation(s)" in html or "alert-danger" in html

        conn = sqlite3.connect(db_path)
        assert conn.execute(
            "SELECT COUNT(*) FROM allocations").fetchone()[0] == 1
        assert conn.execute(
            "SELECT available_quantity FROM donations").fetchone()[0] == 0
        conn.close()

    def test_malformed_selection_is_handled_gracefully(
            self, client, db_path, seed):
        seed["donation"](db_path, id=1, quantity=10, expiry_date=days(40))
        seed["request"](db_path, id=1, quantity=4)
        response = self._confirm(client, ["not-a-pair"])
        assert response.status_code == 200, "must not raise a 500 error"
        assert "alert-danger" in response.get_data(as_text=True)
        conn = sqlite3.connect(db_path)
        assert conn.execute(
            "SELECT COUNT(*) FROM allocations").fetchone()[0] == 0
        conn.close()

    def test_unknown_ids_are_handled_gracefully(
            self, client, db_path, seed):
        seed["donation"](db_path, id=1, quantity=10, expiry_date=days(40))
        seed["request"](db_path, id=1, quantity=4)
        response = self._confirm(client, ["999:999:5"])
        assert response.status_code == 200, "must not raise a 500 error"
        assert "alert-danger" in response.get_data(as_text=True)
        conn = sqlite3.connect(db_path)
        assert conn.execute(
            "SELECT COUNT(*) FROM allocations").fetchone()[0] == 0
        conn.close()

    def test_expired_pair_rejected_at_confirmation(
            self, client, db_path, seed):
        """Even if a stale page offers an expired pair, confirm refuses."""
        seed["donation"](db_path, id=1, quantity=10, expiry_date=days(-2))
        seed["request"](db_path, id=1, quantity=4)
        response = self._confirm(client, ["1:1:4"])
        assert "Confirmed 0 allocation(s)" in \
            response.get_data(as_text=True)
        conn = sqlite3.connect(db_path)
        assert conn.execute(
            "SELECT COUNT(*) FROM allocations").fetchone()[0] == 0
        conn.close()

    def test_mixed_batch_skips_ineligible_and_reports(
            self, client, db_path, seed):
        """Rule-blocked pairs are skipped and reported; valid pairs commit.

        The confirm route re-evaluates every pair with the engine; a pair
        that fails Rule 3 is counted as 'skipped by the rules' and the
        remaining valid pairs are saved - quantities stay consistent.
        """
        seed["donation"](db_path, id=1, quantity=10, expiry_date=days(40))
        seed["donation"](db_path, id=2, quantity=10, expiry_date=days(-5))
        seed["request"](db_path, id=1, quantity=4)
        seed["request"](db_path, id=2, urgency="Low", quantity=4)

        # Pair 1 is valid; pair 2 uses an expired donation (Rule 3).
        response = self._confirm(client, ["1:1:4", "2:2:4"])
        html = response.get_data(as_text=True)
        assert "Confirmed 1 allocation(s); 1 were skipped" in html, \
            "the blocked pair must be counted and reported"

        conn = sqlite3.connect(db_path)
        assert conn.execute(
            "SELECT COUNT(*) FROM allocations").fetchone()[0] == 1, \
            "only the eligible pair may be saved"
        row = conn.execute("SELECT available_quantity FROM donations "
                           "WHERE id = 1").fetchone()
        assert row[0] == 6, "good pair applied: 10 - 4 = 6 left"
        row = conn.execute("SELECT available_quantity FROM donations "
                           "WHERE id = 2").fetchone()
        assert row[0] == 10, "expired donation must be untouched"
        conn.close()


# ---------------------------------------------------------------------------
# Dashboard counts
# ---------------------------------------------------------------------------
class TestDashboardCounts:
    def test_empty_dashboard_shows_zeros(self, client):
        assert stat_values(client.get("/").get_data(as_text=True)) == \
            [0, 0, 0, 0]

    def test_counts_follow_donations_requests_allocations(
            self, client, db_path, seed):
        seed["donation"](db_path, id=1, quantity=10, expiry_date=days(40))
        seed["donation"](db_path, id=2, quantity=20, expiry_date=days(40))
        seed["request"](db_path, id=1, quantity=4)
        seed["request"](db_path, id=2, urgency="Low", quantity=100)

        html = client.get("/").get_data(as_text=True)
        assert stat_values(html) == [2, 2, 0, 2], \
            "2 donations, 2 open requests, 0 allocations, 2 unallocated"

        client.post("/matching/confirm", data={"recommendation": "1:1:4"})
        html = client.get("/").get_data(as_text=True)
        # donations=2 (4 units allocated), pending=1 (request 2 still open),
        # allocations=1, unallocated items=2 (donation 1 still has stock)
        assert stat_values(html) == [2, 1, 1, 2]

    def test_demo_data_counts_and_idempotency(self, client, db_path):
        client.post("/demo/load")
        conn = sqlite3.connect(db_path)
        assert conn.execute("SELECT COUNT(*) FROM donations").fetchone()[0] == 8
        assert conn.execute(
            "SELECT COUNT(*) FROM community_requests").fetchone()[0] == 8
        assert conn.execute(
            "SELECT SUM(is_demo) FROM donations").fetchone()[0] == 8, \
            "demo rows must be labelled"

        client.post("/demo/load")     # second click
        assert conn.execute("SELECT COUNT(*) FROM donations").fetchone()[0] == 8, \
            "loading twice must not duplicate"
        conn.close()

    def test_demo_clear_removes_only_demo_rows(self, client, db_path, seed):
        seed["donation"](db_path, id=500, donor_name="Real Donor",
                         quantity=7, expiry_date=days(40))
        client.post("/demo/load")
        client.post("/demo/clear")
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT donor_name FROM donations").fetchall()
        assert rows == [("Real Donor",)], "user data must survive clearing"
        conn.close()


# ---------------------------------------------------------------------------
# Persistence across an application restart
# ---------------------------------------------------------------------------
class TestPersistence:
    def test_data_survives_restart(self, db_path, seed, monkeypatch):
        """Simulate a full restart: drop Flask's g, reopen the app."""
        seed["donation"](db_path, id=1, donor_name="Persistent Donor",
                         quantity=12, expiry_date=days(40))
        seed["request"](db_path, id=1, quantity=6,
                        recipient_name="Persistent Home")

        import app as app_module         # fresh client = new connection
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as fresh_client:
            html = fresh_client.get("/donations").get_data(as_text=True)
            assert "Persistent Donor" in html
            html = fresh_client.get("/requests").get_data(as_text=True)
            assert "Persistent Home" in html
            html = fresh_client.get("/").get_data(as_text=True)
            assert stat_values(html)[0] == 1


# ---------------------------------------------------------------------------
# Forms, filters and navigation links
# ---------------------------------------------------------------------------
class TestFormsFiltersAndLinks:
    NAV_PATHS = ["/", "/donations", "/requests", "/matching", "/allocations"]

    @pytest.mark.parametrize("path", NAV_PATHS)
    def test_navigation_links_resolve(self, client, path):
        response = client.get(path)
        assert response.status_code == 200

    def test_every_sidebar_link_on_every_page(self, client):
        for page in self.NAV_PATHS:
            html = client.get(page).get_data(as_text=True)
            for target in self.NAV_PATHS:
                assert 'href="{0}"'.format(target) in html, \
                    "{0} missing link to {1}".format(page, target)

    def test_valid_donation_form_works(self, client, db_path):
        token = get_token(client, "/donations")
        response = client.post("/donations/new", data={
            "form_token": token, "donor_name": "Working Donor",
            "category": "Books", "item_name": "Story Books",
            "description": "Nice books", "quantity": "5",
            "condition": "New"}, follow_redirects=True)
        assert "Donation added successfully" in response.get_data(as_text=True)
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT * FROM donations").fetchone()
        assert row[1] == "Working Donor" and row[5] == 5 and row[6] == 5
        conn.close()

    def test_valid_request_form_works(self, client, db_path):
        token = get_token(client, "/requests")
        response = client.post("/requests/new", data={
            "form_token": token, "recipient_name": "Working Home",
            "category": "Clothes", "item_name": "Jackets", "quantity": "15",
            "urgency": "High", "beneficiaries": "42",
            "request_date": TODAY.isoformat()}, follow_redirects=True)
        assert "registered successfully" in response.get_data(as_text=True)
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT * FROM community_requests").fetchone()
        assert row[1] == "Working Home" and row[4] == 15 and row[6] == "High"
        conn.close()

    def test_search_filter(self, client, db_path, seed):
        seed["request"](db_path, id=1, recipient_name="Alpha Shelter",
                        item_name="Rice")
        seed["request"](db_path, id=2, recipient_name="Beta School",
                        item_name="Textbooks", urgency="Low")
        html = client.get("/requests?q=alpha").get_data(as_text=True)
        assert "Alpha Shelter" in html and "Beta School" not in html

    def test_category_filter(self, client, db_path, seed):
        seed["request"](db_path, id=1, category="Food", item_name="Rice")
        seed["request"](db_path, id=2, category="Books",
                        item_name="Textbooks")
        html = client.get("/requests?category=Books").get_data(as_text=True)
        assert "Textbooks" in html and "Rice" not in html

    def test_urgency_filter(self, client, db_path, seed):
        seed["request"](db_path, id=1, urgency="Critical",
                        recipient_name="Critical Home")
        seed["request"](db_path, id=2, urgency="Low",
                        recipient_name="Low Home")
        html = client.get("/requests?urgency=Critical").get_data(as_text=True)
        assert "Critical Home" in html and "Low Home" not in html

    def test_status_filter(self, client, db_path, seed):
        seed["request"](db_path, id=1, quantity=10,
                        recipient_name="Open Home")
        seed["request"](db_path, id=2, quantity=10,
                        recipient_name="Filled Home")
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE community_requests SET status='fulfilled', "
                     "quantity_remaining=0 WHERE id=2")
        conn.commit(); conn.close()
        html = client.get("/requests?status=fulfilled").get_data(as_text=True)
        assert "Filled Home" in html and "Open Home" not in html

    def test_empty_filter_result_is_helpful(self, client):
        html = client.get("/requests?q=zzz-nothing").get_data(as_text=True)
        assert "No requests match" in html

    def test_static_assets_resolve(self, client):
        response = client.get("/static/css/style.css")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Full demo journey (the exam walkthrough) in one test
# ---------------------------------------------------------------------------
class TestFullDemoJourney:
    def test_load_match_confirm_history(self, client, db_path):
        assert client.post("/demo/load").status_code in (200, 302)

        html = client.get("/matching").get_data(as_text=True)
        assert "recommendation" in html, "demo data must produce matches"

        # Confirm every non-verification recommendation the engine offers.
        import re
        pairs = re.findall(
            r'value="(\d+:\d+:\d+)"', html)
        assert pairs, "expected recommendation values in the page"
        response = client.post("/matching/confirm",
                               data={"recommendation": pairs},
                               follow_redirects=True)
        html = response.get_data(as_text=True)
        assert "Confirmed" in html

        conn = sqlite3.connect(db_path)
        allocations = conn.execute(
            "SELECT COUNT(*) FROM allocations").fetchone()[0]
        assert allocations == len(pairs), "every ticked pair must be saved"
        for row in conn.execute("SELECT available_quantity FROM donations"):
            assert row[0] >= 0
        for row in conn.execute(
                "SELECT quantity_remaining FROM community_requests"):
            assert row[0] >= 0
        conn.close()

        html = client.get("/allocations").get_data(as_text=True)
        assert "Rule" in html, "history must show the justifying rules"
        assert client.get("/allocations").status_code == 200
