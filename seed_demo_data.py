"""
Optional: populates the database with a few sample medicines, a supplier,
and ~120 days of synthetic sales history so you can try out the dashboard,
billing screen, and forecasting engine immediately, without waiting to
import your real dailysales.csv.

Run once with:  python seed_demo_data.py
Safe to skip entirely if you'd rather start from a clean, empty database.
"""
import random
from datetime import date, timedelta

from app import create_app
from app.extensions import db
from app.models import Inventory, Supplier, StockBatch, SalesHistory

app = create_app()

SAMPLE_ITEMS = [
    ("Paracetamol 500mg", "Analgesic", 15.0, 400),
    ("Amoxicillin 250mg", "Antibiotic", 42.0, 250),
    ("Cetirizine 10mg", "Antihistamine", 18.0, 300),
    ("Insulin Glargine", "Diabetes", 620.0, 40),
    ("Azithromycin 500mg", "Antibiotic", 85.0, 150),
    ("Metformin 500mg", "Diabetes", 22.0, 350),
    ("ORS Sachet", "Rehydration", 12.0, 500),
    ("Cough Syrup 100ml", "Cold & Flu", 65.0, 120),
]

with app.app_context():
    if Inventory.query.count() > 0:
        print("Inventory already has data — skipping demo seed to avoid duplicates.")
    else:
        supplier = Supplier(name="Sunrise Pharma Distributors", contact_person="R. Sunder",
                             phone="+91 90000 11122", lead_time_days=5)
        db.session.add(supplier)
        db.session.flush()

        today = date.today()
        for name, category, price, base_stock in SAMPLE_ITEMS:
            item = Inventory(
                item_name=name, category=category, unit="strip",
                current_stock=base_stock, reorder_level=int(base_stock * 0.15),
                safety_stock=int(base_stock * 0.05),
                unit_price=price, cost_price=round(price * 0.7, 2),
                supplier_id=supplier.id,
            )
            db.session.add(item)
            db.session.flush()

            db.session.add(StockBatch(
                item_id=item.id, batch_no=f"B{random.randint(10000,99999)}",
                quantity=base_stock, expiry_date=today + timedelta(days=random.randint(60, 540)),
                received_date=today - timedelta(days=random.randint(1, 30)),
            ))

            # 120 days of synthetic daily sales with weekly seasonality + noise
            for d in range(120, 0, -1):
                sale_date = today - timedelta(days=d)
                weekday_factor = 1.3 if sale_date.weekday() in (5, 6) else 1.0
                base = (base_stock / 120) * 0.6
                qty = max(0, int(random.gauss(base * weekday_factor, base * 0.35)))
                if qty > 0:
                    db.session.add(SalesHistory(
                        item_id=item.id, sale_date=sale_date,
                        quantity_sold=qty, source="import",
                    ))

        db.session.commit()
        print(f"Seeded {len(SAMPLE_ITEMS)} demo medicines with 120 days of sales history.")

    print("\nDefault login:")
    print(f"  Username: {app.config['DEFAULT_ADMIN_USERNAME']}")
    print(f"  Password: {app.config['DEFAULT_ADMIN_PASSWORD']}")
