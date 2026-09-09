"""
Adds what's needed for: Returns/Refunds on bills, and the Forecast Accuracy
dashboard. Safe to run on your existing database — it only adds what's
missing and never touches existing data. Safe to re-run too.

New tables (Return, ForecastValidation) are created automatically by
db.create_all() on app startup. This script handles the one thing
create_all() can't do: adding a new COLUMN to an EXISTING table
(returned_quantity on BillItem).

Run once with:  python migrate_add_returns_and_accuracy.py
"""
from sqlalchemy import inspect, text
from app import create_app
from app.extensions import db

app = create_app()

with app.app_context():
    inspector = inspect(db.engine)
    existing_cols = {col["name"] for col in inspector.get_columns("bill_items")}

    added = []
    with db.engine.connect() as conn:
        if "returned_quantity" not in existing_cols:
            conn.execute(text(
                "ALTER TABLE bill_items ADD COLUMN returned_quantity INTEGER NOT NULL DEFAULT 0"
            ))
            added.append("returned_quantity")
        conn.commit()

    if added:
        print(f"Added column(s) to bill_items table: {', '.join(added)}")
    else:
        print("bill_items table already up to date — nothing to do.")

    print("returns and forecast_validations tables are created automatically "
          "on app startup if missing (already handled).")
