"""
Adds the new columns/tables introduced for: substitute/generic suggestions,
wastage write-offs. Safe to run on your existing database — it only adds
what's missing and never touches existing data. Safe to re-run too.

New tables (WriteOff) are created automatically by db.create_all() on app
startup. This script handles the one thing create_all() can't do: adding a
new COLUMN to an EXISTING table (generic_name/strength on Inventory).

Uses SQLAlchemy's own engine/inspector rather than guessing a file path, so
this works correctly regardless of whether your DATABASE_URL is a relative
path, an absolute path, or MySQL.

Run once with:  python migrate_add_generic_name.py
"""
from sqlalchemy import inspect, text
from app import create_app
from app.extensions import db

app = create_app()

with app.app_context():
    inspector = inspect(db.engine)
    existing_cols = {col["name"] for col in inspector.get_columns("inventory")}

    added = []
    with db.engine.connect() as conn:
        if "generic_name" not in existing_cols:
            conn.execute(text("ALTER TABLE inventory ADD COLUMN generic_name VARCHAR(200)"))
            added.append("generic_name")
        if "strength" not in existing_cols:
            conn.execute(text("ALTER TABLE inventory ADD COLUMN strength VARCHAR(50)"))
            added.append("strength")
        conn.commit()

    if added:
        print(f"Added column(s) to inventory table: {', '.join(added)}")
    else:
        print("inventory table already up to date — nothing to do.")

    print("write_offs table is created automatically on app startup if missing (already handled).")
