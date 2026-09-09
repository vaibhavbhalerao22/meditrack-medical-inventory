"""
Loads YOUR real historical sales data into MediTrack so forecasting and the
dashboard have real numbers to work with instead of an empty inventory.

Source: data/dailysales_import.csv — this is your uploaded salesdaily.csv
(the classic 8-drug-category pharma sales dataset, ATC codes M01AB, M01AE,
N02BA, N02BE, N05B, N05C, R03, R06) reshaped into item_name/date/quantity_sold
rows and mapped to representative medicine names for each category.

Run once with:  python seed_real_data.py

This creates the 8 medicines (if they don't already exist) with starter
stock/pricing you should adjust to your actual shop, then imports every row
of sales history so you can immediately go to Inventory -> Demand Forecast
and get a real ARIMA+LSTM forecast.
"""
import os
from app import create_app
from app.extensions import db
from app.models import Inventory, Supplier
from app.utils.csv_import import import_sales_csv

app = create_app()

# Starter catalog entries for the 8 drug categories in your CSV.
# Prices/stock levels are placeholders — edit them in Inventory -> Manage
# once you're live, they don't affect the forecast (only sales history does).
STARTER_ITEMS = [
    ("Diclofenac 50mg (M01AB)",   "NSAID",           18.0, 300),
    ("Ibuprofen 400mg (M01AE)",   "NSAID",           14.0, 300),
    ("Aspirin 325mg (N02BA)",     "Analgesic",        8.0, 400),
    ("Paracetamol 500mg (N02BE)", "Analgesic",       15.0, 500),
    ("Diazepam 5mg (N05B)",       "Anxiolytic",      22.0, 200),
    ("Zolpidem 10mg (N05C)",      "Hypnotic/Sedative", 35.0, 100),
    ("Salbutamol Inhaler (R03)",  "Respiratory",     95.0, 150),
    ("Cetirizine 10mg (R06)",     "Antihistamine",   18.0, 300),
]

CSV_PATH = os.path.join(os.path.dirname(__file__), "data", "dailysales_import.csv")

with app.app_context():
    supplier = Supplier.query.first()
    if supplier is None:
        supplier = Supplier(name="Default Supplier", lead_time_days=7)
        db.session.add(supplier)
        db.session.flush()

    created = 0
    for name, category, price, stock in STARTER_ITEMS:
        if Inventory.query.filter_by(item_name=name).first() is None:
            db.session.add(Inventory(
                item_name=name, category=category, unit="strip",
                current_stock=stock, reorder_level=int(stock * 0.15),
                safety_stock=int(stock * 0.05),
                unit_price=price, cost_price=round(price * 0.7, 2),
                supplier_id=supplier.id,
            ))
            created += 1
    db.session.commit()
    print(f"Added {created} new medicine(s) to inventory (skipped any that already existed).")

    if not os.path.exists(CSV_PATH):
        print(f"Could not find {CSV_PATH} — nothing imported.")
    else:
        result = import_sales_csv(CSV_PATH)
        print(f"Imported {result['inserted']} sales records "
              f"({result['skipped']} skipped).")
        if result["unmatched_names"]:
            print("Unmatched names:", ", ".join(result["unmatched_names"]))

    print("\nDone. Log in and go to Inventory -> Demand Forecast to run a real forecast.")
