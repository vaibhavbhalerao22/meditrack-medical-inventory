"""
Resets the database to a clean slate and seeds it with a realistic TWO-MONTH
demo dataset -- ending today -- so you can show a pharmacist real-looking
dashboard graphs, bill history, and demand forecasting without waiting on
real-world data to accumulate.

This creates:
  - 4 staff accounts (1 admin, 1 inventory manager, 2 cashiers)
  - 2 suppliers
  - 20 realistic medicines (incl. 3 brand-name pairs sharing the same
    generic/salt, to demo the substitute-suggestion feature)
  - Stock batches with a realistic mix of expiry dates
  - ~60 days of REAL Bill + BillItem + SalesHistory records (not just a CSV
    import) -- so they show up in Cashier > Bill History exactly like real
    transactions, AND feed the forecasting engine
  - Weekly seasonality (busier weekends) + a deliberate "flu week" demand
    spike, so the ARIMA (steady pattern) + LSTM (irregular spike) hybrid has
    something real to demonstrate

WARNING: this WIPES your existing database. Only run this for a demo/reset.

Run once with:  python seed_two_month_demo.py
"""
import random
from datetime import date, datetime, time as dtime, timedelta

from app import create_app
from app.extensions import db
from app.models import User, Role, Supplier, Inventory, StockBatch, SalesHistory, Bill, BillItem

app = create_app()
random.seed(42)  # reproducible demo data

TODAY = date.today()
START_DATE = TODAY - timedelta(days=59)  # 60 days inclusive, ending today

# ---------------------------------------------------------------------------
# Medicine catalog -- (name, generic_name, strength, category, unit,
#                      price, cost, popularity_weight, qty_range)
# popularity_weight: relative chance of appearing in a bill (higher = more common)
# qty_range: (min, max) units per line when it IS in a bill
# ---------------------------------------------------------------------------
MEDICINES = [
    ("Crocin 500mg",      "Paracetamol",   "500mg", "Analgesic",      "strip", 18.0, 12.0, 10, (1, 3)),
    ("Calpol 500mg",      "Paracetamol",   "500mg", "Analgesic",      "strip", 17.0, 11.5, 6,  (1, 3)),
    ("Combiflam",         "Ibuprofen + Paracetamol", "400mg/325mg", "Analgesic", "strip", 22.0, 15.0, 7, (1, 2)),
    ("Voveran 50mg",      "Diclofenac",    "50mg",  "NSAID",          "strip", 25.0, 17.0, 5,  (1, 2)),
    ("Dicloflex 50mg",    "Diclofenac",    "50mg",  "NSAID",          "strip", 23.0, 16.0, 3,  (1, 2)),
    ("Amoxyclav 625mg",   "Amoxicillin + Clavulanate", "625mg", "Antibiotic", "strip", 95.0, 68.0, 6, (1, 2)),
    ("Azithral 500mg",    "Azithromycin",  "500mg", "Antibiotic",     "strip", 85.0, 60.0, 4,  (1, 1)),
    ("Cetzine 10mg",      "Cetirizine",    "10mg",  "Antihistamine",  "strip", 15.0, 10.0, 9,  (1, 2)),
    ("Levocet 5mg",       "Levocetirizine","5mg",   "Antihistamine",  "strip", 20.0, 13.5, 4,  (1, 2)),
    ("Pantop 40mg",       "Pantoprazole",  "40mg",  "Antacid",        "strip", 45.0, 31.0, 6,  (1, 2)),
    ("Omez 20mg",         "Omeprazole",    "20mg",  "Antacid",        "strip", 40.0, 27.0, 5,  (1, 2)),
    ("Metformin 500mg",   "Metformin",     "500mg", "Diabetes",       "strip", 22.0, 15.0, 7,  (1, 2)),
    ("Glycomet 500mg",    "Metformin",     "500mg", "Diabetes",       "strip", 21.0, 14.5, 4,  (1, 2)),
    ("Amlodipine 5mg",    "Amlodipine",    "5mg",   "Cardiac/BP",     "strip", 18.0, 12.5, 5,  (1, 2)),
    ("Telma 40mg",        "Telmisartan",   "40mg",  "Cardiac/BP",     "strip", 55.0, 38.0, 3,  (1, 1)),
    ("Limcee 500mg",      "Ascorbic Acid", "500mg", "Supplement",     "strip", 30.0, 20.0, 4,  (1, 2)),
    ("Shelcal 500",       "Calcium + Vitamin D3", "500mg", "Supplement", "strip", 95.0, 65.0, 3, (1, 1)),
    ("ORS Sachet",        "Oral Rehydration Salts", "-", "Rehydration", "sachet", 12.0, 8.0, 6, (1, 3)),
    ("Benadryl Syrup 100ml", "Diphenhydramine", "100ml", "Cold & Flu", "bottle", 65.0, 45.0, 5, (1, 1)),
    ("Digene Gel 200ml",  "Antacid Combination", "200ml", "Antacid",  "bottle", 85.0, 58.0, 3, (1, 1)),
]

# Categories boosted during the simulated "flu week" (irregular spike for LSTM to catch)
FLU_BOOST_CATEGORIES = {"Cold & Flu", "Antihistamine", "Analgesic"}
FLU_WEEK_START_DAY = 22  # days after START_DATE
FLU_WEEK_LEN = 7

CASHIER_NAMES = [("priya", "Priya Sharma"), ("rahul", "Rahul Verma")]
CUSTOMER_NAMES = [
    "Walk-in Customer", "Walk-in Customer", "Walk-in Customer",  # weighted common
    "Anil Kulkarni", "Sunita Patil", "Rajesh Deshmukh", "Meena Joshi",
    "Suresh Pawar", "Kavita Shinde", "Ramesh Gaikwad", "Pooja Kale",
]


def deduct_stock_fefo(item, quantity):
    remaining = quantity
    batches = sorted([b for b in item.batches if b.quantity > 0], key=lambda b: b.expiry_date)
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity, remaining)
        batch.quantity -= take
        remaining -= take
    item.current_stock = max(0, item.current_stock - quantity)


with app.app_context():
    print("Resetting database...")
    db.drop_all()
    db.create_all()

    # --- Users ---
    admin = User(username="admin", full_name="System Administrator", role=Role.ADMIN)
    admin.set_password("Admin@12345")
    manager = User(username="manager", full_name="Anjali Deshmukh", role=Role.INVENTORY_MANAGER)
    manager.set_password("Manager@123")
    db.session.add_all([admin, manager])

    cashier_users = []
    for username, full_name in CASHIER_NAMES:
        u = User(username=username, full_name=full_name, role=Role.CASHIER)
        u.set_password("Cashier@123")
        db.session.add(u)
        cashier_users.append(u)
    db.session.commit()
    print("Created users: admin, manager, " + ", ".join(u.username for u in cashier_users))

    # --- Suppliers ---
    sup1 = Supplier(name="Sunrise Pharma Distributors", contact_person="R. Sunder",
                     phone="+91 90000 11122", lead_time_days=5)
    sup2 = Supplier(name="MedPlus Wholesale", contact_person="K. Iyer",
                     phone="+91 90000 33344", lead_time_days=8)
    db.session.add_all([sup1, sup2])
    db.session.commit()

    # --- Inventory + batches ---
    items_by_name = {}
    for i, (name, generic, strength, category, unit, price, cost, weight, qty_range) in enumerate(MEDICINES):
        supplier = sup1 if i % 2 == 0 else sup2
        avg_daily_qty = weight * 0.35  # rough expected units/day across all bills
        total_demand_60d = avg_daily_qty * 60
        # Most items get a healthy buffer. A couple get a tight buffer so
        # low-stock alerts have something real to show. Calpol gets extra
        # buffer so it stays in stock as a substitute when Crocin (same
        # generic, higher demand) runs out -- a clean demo of the feature.
        if name in ("Azithral 500mg", "Telma 40mg"):
            buffer = 0.9
        elif name == "Calpol 500mg":
            buffer = 1.9
        elif name == "Crocin 500mg":
            buffer = 1.1
        else:
            buffer = 1.35
        initial_stock = max(20, round(total_demand_60d * buffer))
        reorder_level = max(5, round(avg_daily_qty * 7))  # ~1 week of stock

        item = Inventory(
            item_name=name, generic_name=generic, strength=strength, category=category,
            unit=unit, current_stock=0, reorder_level=reorder_level,
            safety_stock=max(3, round(avg_daily_qty * 3)),
            unit_price=price, cost_price=cost, supplier_id=supplier.id,
        )
        db.session.add(item)
        db.session.flush()

        # 1-2 batches per item with a realistic expiry spread
        batch1_qty = round(initial_stock * 0.6)
        batch2_qty = initial_stock - batch1_qty
        b1 = StockBatch(item_id=item.id, batch_no="B" + str(random.randint(10000, 99999)),
                         quantity=batch1_qty,
                         expiry_date=TODAY + timedelta(days=random.randint(45, 400)),
                         received_date=START_DATE - timedelta(days=10))
        b2 = StockBatch(item_id=item.id, batch_no="B" + str(random.randint(10000, 99999)),
                         quantity=batch2_qty,
                         expiry_date=TODAY + timedelta(days=random.randint(20, 90)),
                         received_date=START_DATE + timedelta(days=20))
        item.current_stock = initial_stock
        db.session.add_all([b1, b2])
        items_by_name[name] = item

    db.session.commit()
    print("Added " + str(len(MEDICINES)) + " medicines with stock batches.")

    # --- 60 days of real bills ---
    total_bills = 0
    total_sales_rows = 0

    for d in range(60):
        day = START_DATE + timedelta(days=d)
        is_weekend = day.weekday() in (5, 6)
        is_flu_week = FLU_WEEK_START_DAY <= d < FLU_WEEK_START_DAY + FLU_WEEK_LEN

        # mild upward trend + weekend boost + flu-week boost
        base = 11 + (d * 0.05)
        if is_weekend:
            base *= 1.4
        if is_flu_week:
            base *= 1.3
        num_bills = max(4, int(random.gauss(base, base * 0.15)))

        invoice_seq = 0
        for _ in range(num_bills):
            cashier = random.choice(cashier_users)
            n_lines = random.choices([1, 2, 3, 4], weights=[35, 35, 20, 10])[0]

            # weighted pick of medicines, boosted for flu categories during flu week
            live_weights = []
            for m in MEDICINES:
                w = m[7]
                if is_flu_week and m[3] in FLU_BOOST_CATEGORIES:
                    w *= 2.2
                live_weights.append(w)
            chosen = random.choices(MEDICINES, weights=live_weights, k=min(n_lines, len(MEDICINES)))
            chosen_names = list(dict.fromkeys(m[0] for m in chosen))  # dedupe, keep order

            # Compute valid lines BEFORE creating any DB rows, so an empty
            # result (everything picked happened to be out of stock) never
            # needs a bill to be created-then-deleted (which risks reusing
            # an invoice number within the same uncommitted transaction).
            lines = []
            for name in chosen_names:
                item = items_by_name[name]
                spec = next(m for m in MEDICINES if m[0] == name)
                qty = random.randint(*spec[8])
                qty = min(qty, item.current_stock)
                if qty > 0:
                    lines.append((item, qty))
            if not lines:
                continue

            invoice_seq += 1
            hour = random.randint(9, 20)
            minute = random.randint(0, 59)
            created_on = datetime.combine(day, dtime(hour, minute))

            invoice_no = "INV-" + day.strftime("%Y%m%d") + "-" + str(invoice_seq).zfill(4)
            bill = Bill(
                invoice_no=invoice_no, cashier_id=cashier.id,
                customer_name=random.choice(CUSTOMER_NAMES),
                payment_mode=random.choices(["Cash", "UPI", "Card"], weights=[45, 40, 15])[0],
                discount=0, tax=0,
                created_on=created_on,
            )
            db.session.add(bill)
            db.session.flush()

            subtotal = 0.0
            for item, qty in lines:
                unit_price = float(item.unit_price)
                line_total = round(unit_price * qty, 2)
                subtotal += line_total

                db.session.add(BillItem(
                    bill_id=bill.id, item_id=item.id, item_name_snapshot=item.item_name,
                    quantity=qty, unit_price=unit_price, line_total=line_total,
                ))
                deduct_stock_fefo(item, qty)
                db.session.add(SalesHistory(
                    item_id=item.id, sale_date=day, quantity_sold=qty,
                    remaining_stock=item.current_stock, source="pos",
                ))
                total_sales_rows += 1

            bill.subtotal = round(subtotal, 2)
            bill.total = round(subtotal, 2)
            total_bills += 1

        db.session.commit()

    print("")
    print("Generated " + str(total_bills) + " bills across " + str(START_DATE) + " to " + str(TODAY) +
          " (" + str(total_sales_rows) + " sales line records).")

    low_stock = [i.item_name for i in Inventory.query.all() if i.current_stock <= i.reorder_level]
    print("Items currently at/below reorder level (for demo): " + (", ".join(low_stock) or "none"))

    print("")
    print("Done. Login accounts (all demo passwords -- change before real use):")
    print("  Admin:             admin / Admin@12345")
    print("  Inventory Manager: manager / Manager@123")
    for username, full_name in CASHIER_NAMES:
        print("  Cashier:           " + username + " / Cashier@123   (" + full_name + ")")
    print("")
    print("Next steps:")
    print("  - Admin dashboard graphs and Bill History are populated immediately.")
    print("  - For forecasting, go to Inventory -> Demand Forecast and run it per item")
    print("    (e.g. try 'Crocin 500mg' or 'Cetzine 10mg' -- both have strong volume + the flu-week spike).")
