# MediTrack — Medical Inventory, Forecasting & Billing System

A real, working implementation of the "Medical Inventory Demand Forecasting System"
project — extended for actual pharmacy/medical-store use: batch & expiry tracking,
a Cashier billing/POS module with PDF invoices, and admin sales analytics, on top of
the original hybrid **ARIMA + LSTM** demand forecasting engine and **EOQ** restocking
recommendations.

## Roles

| Role | Access |
|---|---|
| **Administrator** | Everything — dashboard & sales graphs, staff accounts, all inventory & billing screens |
| **Inventory Manager** | Stock, batches/expiry, suppliers, sales CSV import, demand forecasting |
| **Cashier** | New Bill (POS), bill history, PDF invoice download |

*(The original report's "Data Analyst" role has been removed and replaced with Cashier, per your request.)*

## Features

- Full inventory CRUD with **batch numbers & expiry dates** (FEFO stock deduction on sale)
- **Generic name / salt composition** field per medicine, powering **substitute suggestions** at billing time when an item is out of stock
- Supplier directory with per-supplier lead time (feeds the Reorder Point calculation)
- **Cashier billing screen**: live item search, cart, discount/tax, payment mode, auto stock deduction, and **keyboard shortcuts** (F2 search, Enter to add first result, Ctrl+Enter to generate bill, Esc to clear search)
- **PDF invoice generation** (shop name, address, date, itemized total) — ReportLab
- **Hybrid ARIMA + LSTM** demand forecasting per medicine, with a held-out validation split (RMSE/MAE)
- **EOQ + Reorder Point** recommendation engine
- Low-stock and expiry alerts (dashboard + dedicated pages)
- **Write-off / wastage tracking** — mark expired or damaged batches as written off (deducts stock, logs the loss), with a dedicated **Wastage Report** showing total ₹ loss by period and by medicine
- **Staff performance report** — bills generated and average bill value per cashier, per day and overall
- **AI Assistant** (admin-only, optional) — an agentic chat assistant that can look up your live stock, sales, wastage, and forecast data to answer questions like "what's expiring soon" or "how did we do this month" — see setup below
- **Admin-only** daily (30-day) and monthly (12-month) sales graphs, top-selling items — anchored to your most recent bill so it works with historical/backfilled data too
- **Dark mode** toggle (top bar), preference remembered per browser
- CSV import for historical sales data (e.g. your `dailysales.csv`) to train the forecaster
- Role-based access control end to end (Admin / Inventory Manager / Cashier)

## Project Structure

```
pharmacy-system/
├── app/
│   ├── __init__.py          # App factory
│   ├── config.py            # Env-driven configuration
│   ├── extensions.py        # db, login_manager
│   ├── models.py            # SQLAlchemy models
│   ├── decorators.py        # @roles_required
│   ├── auth/                # Login / logout
│   ├── admin/                # Dashboard, sales graph APIs, user management
│   ├── inventory/           # Stock CRUD, batches, suppliers, CSV import, forecast routes
│   ├── cashier/              # POS billing, invoice PDF, bill history
│   ├── forecasting/
│   │   ├── engine.py        # ARIMA + LSTM hybrid pipeline
│   │   └── recommend.py     # EOQ / Reorder Point
│   ├── utils/
│   │   ├── csv_import.py
│   │   └── pdf_invoice.py
│   ├── static/css/style.css
│   └── templates/
├── seed_demo_data.py        # Optional: sample medicines + 120 days of synthetic sales
├── requirements.txt
├── .env.example
└── run.py
```

## Upgrading an existing installation

If you already had this project running before the substitute-suggestion /
wastage-tracking update, run this once against your existing database (safe,
additive only, never touches existing data):

```bash
python migrate_add_generic_name.py
```

## Setting up the AI Assistant (optional)

The AI Assistant is an **agentic** admin-only chat feature — it can call read-only
tools that query your live database (low stock, expiring batches, sales summary,
top sellers, wastage, saved forecasts) and answers based on what it finds, not
guesses. It never modifies data.

1. Get an API key at [console.anthropic.com](https://console.anthropic.com) —
   this is billed separately, based on your own Anthropic account usage.
2. Add it to `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```
3. Restart the app. You'll see "AI Assistant" in the sidebar under Administrator.

Leave `ANTHROPIC_API_KEY` blank to keep this feature off — everything else in
the app works fine without it.

## Setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # then edit shop name/address/admin password inside it
python run.py
```

Visit `http://localhost:5000`. Log in with the default admin account printed in your
`.env` (`admin` / `Admin@12345` unless you changed it) — **change this password**
via Admin → Users after first login (create a new admin account, then deactivate the
default one).

### Try it with demo data (optional)

```bash
python seed_demo_data.py
```

This adds 8 sample medicines with realistic 120-day sales history so you can try
billing, the dashboard graphs, and forecasting immediately.

### Show a pharmacist a full live demo (2 months of real-looking bills)

```bash
python seed_two_month_demo.py
```

**This resets your database** and seeds a realistic, self-contained demo: 20
common medicines (including 3 brand-name pairs sharing the same generic, to
demo substitute suggestions), 2 suppliers, stock batches, and **60 days of
real Bill/BillItem records ending today** — not just a CSV import, so they
show up properly in Cashier → Bill History, the admin dashboard graphs, and
Staff Performance, exactly as if two cashiers had been ringing up sales for
two months. It also includes a deliberate "flu week" demand spike layered on
top of steady weekly seasonality, so when you run a forecast (Inventory →
Demand Forecast → try "Crocin 500mg" or "Cetzine 10mg") the ARIMA+LSTM hybrid
has both a steady pattern and a real irregular spike to demonstrate.

Login accounts created:
| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `Admin@12345` |
| Inventory Manager | `manager` | `Manager@123` |
| Cashier | `priya` | `Cashier@123` |
| Cashier | `rahul` | `Cashier@123` |

The script prints exactly which items ended up low on stock (for the low-stock
alert demo) and confirms the substitute pair (Crocin → Calpol) is ready to
show. Safe to re-run any time you want a fresh, reproducible demo dataset —
it always regenerates the same data (seeded random), just with today's date
as the end point.

### Try it with your real sales data (already included)

Your uploaded `salesdaily.csv` (the 8-drug-category dataset — ATC codes M01AB,
M01AE, N02BA, N02BE, N05B, N05C, R03, R06, spanning 2014–2019) has already been
converted into `data/dailysales_import.csv` and mapped to 8 representative
medicines. Load it with:

```bash
python seed_real_data.py
```

This creates the 8 medicines (edit their price/stock in Inventory → Manage to
match your real shop — those numbers were placeholders) and imports all
~14,000 real sales rows. Then log in and go to **Inventory → Demand Forecast**
to run a real ARIMA+LSTM forecast immediately — no waiting on CSV uploads.

**Note on speed:** with ~2,000+ days of history per item (like this dataset),
a single forecast run can take a few minutes (ARIMA order search + two LSTM
training passes). That's expected — it's real model fitting, not a bug. For
your day-to-day sales going forward, per-item history will be far shorter and
forecasts will run in seconds.


### Importing your real `dailysales.csv`

1. Add your medicines to Inventory first (names must match the CSV).
2. Go to **Inventory → Import Sales CSV** and upload the file.
3. Expected columns (flexible naming — e.g. `item_name`/`item`/`medicine`,
   `date`/`sale_date`, `quantity_sold`/`quantity`/`qty` all work):

   ```csv
   item_name,date,quantity_sold
   Paracetamol 500mg,2026-01-01,12
   Paracetamol 500mg,2026-01-02,9
   ```
4. Unmatched item names are reported back so you can add them and re-import.
5. Go to **Inventory → Demand Forecast**, pick the medicine, and run the forecast
   (needs ~30+ days of history per item for a meaningful result).

## Database: SQLite vs MySQL

The app defaults to **SQLite** (zero setup — just works). For production, since a
POS + multiple concurrent staff logins need proper concurrent writes, switch to
**MySQL** by setting one environment variable — nothing else in the code changes:

```
# .env
DATABASE_URL=mysql+pymysql://db_user:db_password@db_host:3306/db_name
```

Then `pip install pymysql` (already in requirements.txt) and restart the app —
tables are created automatically on first run either way.

## Deployment notes

- Set `SECRET_KEY` in `.env` to a long random string in production.
- Turn off `debug=True` in `run.py` for production, and run behind a proper WSGI
  server (e.g. `gunicorn run:app`).
- TensorFlow (used for the LSTM half of forecasting) is a heavy dependency — on
  small/free hosting tiers, forecasting may be slow or need more RAM than the free
  tier offers. Inventory, billing, and reporting all work independently of it.
- The `SHOP_NAME` / `SHOP_ADDRESS` / `SHOP_PHONE` / `SHOP_GSTIN` in `.env` are what
  print on every PDF invoice.

## Suggested next steps

- SMS/email delivery for low-stock and expiry alerts (currently in-app only)
- Multi-branch support (the schema is already structured to extend to this)
- Barcode scanning in the billing screen (the search box is scanner-friendly as-is
  if your barcode scanner types + Enter)
