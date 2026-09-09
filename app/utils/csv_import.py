"""
Imports a historical sales CSV (e.g. dailysales.csv) into the SalesHistory
table so the forecasting engine has data to train on.

Expected columns (case-insensitive, flexible naming):
  - item name:   "item_name", "item", "medicine", "product"
  - date:        "date", "sale_date"
  - quantity:    "quantity_sold", "quantity", "qty", "units_sold"

Rows whose item name doesn't match an existing Inventory item are skipped
(reported back so the user can add the item first and re-import).
"""
import pandas as pd
from datetime import datetime

from app.extensions import db
from app.models import Inventory, SalesHistory

COLUMN_ALIASES = {
    "item_name": ["item_name", "item", "medicine", "product", "medicine_name"],
    "date": ["date", "sale_date", "sold_on"],
    "quantity": ["quantity_sold", "quantity", "qty", "units_sold", "sold_qty"],
}


def _resolve_columns(columns):
    lower_cols = {c.lower().strip(): c for c in columns}
    resolved = {}
    for key, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower_cols:
                resolved[key] = lower_cols[alias]
                break
    missing = [k for k in COLUMN_ALIASES if k not in resolved]
    if missing:
        raise ValueError(
            f"CSV is missing required column(s): {', '.join(missing)}. "
            f"Expected something like item_name/date/quantity_sold."
        )
    return resolved


def import_sales_csv(filepath):
    df = pd.read_csv(filepath)
    cols = _resolve_columns(df.columns)

    df = df.rename(columns={
        cols["item_name"]: "item_name",
        cols["date"]: "date",
        cols["quantity"]: "quantity",
    })[["item_name", "date", "quantity"]].dropna()

    # Build a case-insensitive lookup of existing inventory items
    items = {i.item_name.strip().lower(): i for i in Inventory.query.all()}

    inserted = 0
    skipped = 0
    unmatched_names = set()

    for _, row in df.iterrows():
        name_key = str(row["item_name"]).strip().lower()
        item = items.get(name_key)
        if item is None:
            skipped += 1
            unmatched_names.add(str(row["item_name"]).strip())
            continue

        try:
            sale_date = pd.to_datetime(row["date"]).date()
            qty = int(float(row["quantity"]))
        except (ValueError, TypeError):
            skipped += 1
            continue

        db.session.add(SalesHistory(
            item_id=item.id,
            sale_date=sale_date,
            quantity_sold=qty,
            source="import",
        ))
        inserted += 1

        # commit in batches to keep memory sane on large files
        if inserted % 500 == 0:
            db.session.commit()

    db.session.commit()

    return {"inserted": inserted, "skipped": skipped, "unmatched_names": unmatched_names}
