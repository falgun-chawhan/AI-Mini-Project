# Smart Donation Distribution System

SPPU Third Year Computer Engineering — AI Laboratory Mini Project
(Domain: Social and Community Services)

A beginner-friendly Flask + SQLite web application that keeps donation
records, community requests and a **rule-based (IF–THEN) inference engine**
that recommends which donations should be allocated to which requests.

> The matching engine uses **no machine learning and no external AI API** —
> every decision comes from explicit IF–THEN rules written in
> `rule_engine.py`, so the whole logic can be explained line-by-line in a viva.

---

## 1. Prerequisites

- Python 3.9 or newer installed on your computer
- A web browser (Chrome / Edge / Firefox)
- A terminal (Command Prompt, PowerShell or a Linux shell)

No other software, account, cloud service or paid dependency is required.

---

## 2. Installation instructions

Open a terminal inside the project folder (`MINI_PROJECT_AI`) and follow
the next four steps in order.

---

## 3. How to create a virtual environment

macOS / Linux:

```bash
python3 -m venv venv
```

Windows (Command Prompt):

```bat
python -m venv venv
```

This creates a folder named `venv` that stores the project's own
Python packages, so your system Python stays clean.

Activate it before every run:

```bash
source venv/bin/activate        # macOS / Linux
```

```bat
venv\Scripts\activate           # Windows
```

You should see `(venv)` at the start of your terminal prompt.

---

## 4. How to install dependencies

```bash
pip install -r requirements.txt
```

The only dependency is **Flask**. SQLite ships with Python and Bootstrap is
loaded from a CDN, so nothing else needs to be installed.

---

## 5. How to initialize the database

The database (`donations.db`) is created automatically the first time you
start the application — `app.py` calls `db.init_db()`, which runs the
`CREATE TABLE IF NOT EXISTS` statements in `database.py`.

To recreate it manually (optional):

```bash
python -c "import database; database.init_db()"
```

Simply delete `donations.db` at any time to start from a clean slate.

---

## 6. How to run the application

```bash
python app.py
```

You should see output like:

```
 * Running on http://127.0.0.1:5000
 * Press CTRL+C to quit
```

---

## 7. Local browser URL

**http://127.0.0.1:5000**

Pages:

| Page | URL | Purpose |
|---|---|---|
| Dashboard | `/` | Statistics, recent records, demo data buttons |
| Donations | `/donations` | Add and view donations |
| Requests | `/requests` | Register, search and filter requests |
| Smart Matching | `/matching` | Run the rule engine and confirm allocations |
| Allocations | `/allocations` | Allocation history with the rules used |

Click **Load Demo Data** on the dashboard to insert 8 donations and 8
requests (labelled as demo records). The button never duplicates records,
and **Clear Demo Data** removes them again.

---

## 8. How the rule-based inference engine works

`rule_engine.py` evaluates every donation-request pair with these IF–THEN
rules:

| Rule | IF | THEN |
|---|---|---|
| **Rule 1 — Category Match** | a donation's category matches a request's category **and** the item names are compatible (shared word, containment, or a synonym from a small dictionary table) | the pair is eligible for matching |
| **Rule 2 — Urgency Priority** | a request is Critical or High | it is ranked ahead of compatible Medium/Low requests |
| **Rule 3 — Expiry Safety** | a donation has an expiry date in the past | allocation is blocked. Perishable food inside a 2-day safety window is also blocked. **Medicines are never auto-allocated** — they appear as "Needs verification" and the administrator must verify before confirming |
| **Rule 4 — Quantity Check** | available donation quantity > 0 and the request still has unfulfilled quantity | allocate `min(available, still required)` — partial fulfilment is allowed, over-allocation is impossible |
| **Rule 5 — Beneficiary Priority** | two eligible requests have the same urgency | the request serving more beneficiaries is served first |
| **Rule 6 — Explanation** | a pair is recommended | it carries readable reasons (category match, urgency, quantity, beneficiaries) shown on the matching page and stored with the allocation |

Engine guarantees:

- All eligible pairs are evaluated; requests are ranked
  **deterministically** (urgency → beneficiaries → request date → id).
- Nothing can be double-allocated or over-allocated: availability is tracked
  during ranking, and `allocate()` re-checks the database values.
- Expired items are **never** allocated.
- Several allocations are saved inside **one SQLite transaction** — if any
  allocation fails, nothing is committed and the quantities stay correct.
- A **recommendation** (shown on `/matching`) is not an **allocation** —
  nothing is written until the administrator ticks pairs and presses
  *Confirm Allocations*.
- Every confirmed allocation keeps an allocation history record with the
  rules that justified the match.

Suggested demo flow: **Load Demo Data → Run Smart Matching → tick the pairs →
Confirm Allocations → open Allocations.** You will see an expired bread batch
and a perishable milk batch rejected by Rule 3, medicines waiting for
verification, a rice donation split across two requests (Rule 4 partial
fulfilment), and the orphanage ranked first because it is Critical and serves
more beneficiaries (Rules 2 and 5).

---

## 9. Running the automated tests

The matching logic and every route are covered by a pytest suite in
`tests/` (89 tests: the six rules, determinism, transactions, dashboard
counts, persistence, forms and filters).

```bash
pip install pytest          # one-time, dev only - not required to run the app
python -m pytest tests/ -v
```

Each test uses its own temporary database, so running the suite never
touches your real `donations.db`.

---

## 10. Project structure

```
MINI_PROJECT_AI/
├── app.py               # Flask application: routes and input validation
├── rule_engine.py       # Rule-based IF–THEN inference engine
├── database.py          # SQLite schema, connections and helpers
├── requirements.txt     # Python dependencies (Flask)
├── README.md            # This file
├── templates/           # Jinja2 HTML templates
│   ├── base.html        #   layout, navbar, flash messages
│   ├── dashboard.html   #   statistics + recent records
│   ├── donations.html   #   donation form + table
│   ├── requests.html    #   request form + search/filter + table
│   ├── matching.html    #   recommendations, explanations, confirm
│   └── allocations.html #   allocation history
└── static/
    └── css/
        └── style.css    # Purple & white charity theme
```

---

## 11. Reliability and security

- All inputs are validated on the server (required fields, positive
  integers, valid `YYYY-MM-DD` dates, allowed categories/urgencies).
- Every query uses **parameterised SQL** (`?` placeholders) — no string
  concatenation, so SQL injection is not possible.
- Negative and zero quantities are rejected; expired dates are rejected by
  the engine.
- A one-time form token prevents accidental duplicate submissions.
- Sample data can only be loaded once, so repeated clicks never duplicate
  records.
- Failures inside the allocation transaction roll the database back.
- No real personal or medical data is used — everything is demo data.

---

## 12. Troubleshooting

| Problem | Solution |
|---|---|
| `ModuleNotFoundError: No module named 'flask'` | Activate the virtual environment, then run `pip install -r requirements.txt` |
| `Address already in use` | Another process is using port 5000 — close it or change `port=5000` in `app.py` |
| Pages look unstyled | The Bootstrap CDN needs an internet connection the first time; the app itself runs fully offline |
| Want a fresh database | Stop the app, delete `donations.db`, start the app again |
