"""
The admin dashboard's sales graphs are driven by actual Bills (real POS
transactions), not by the raw SalesHistory rows used for forecasting — those
are two different tables on purpose (one feeds forecasting, one feeds
reporting). If you only imported a CSV and haven't rung up any real bills
yet, the dashboard will look empty/flat even though forecasting works fine.

This script backfills one Bill (with a matching BillItem) for every
CSV-imported SalesHistory row, dated on the original sale date, so the
dashboard's daily/monthly graphs and "top selling items" show your real
historical revenue immediately.

Run once with:  python backfill_bills_from_history.py

Safe to re-run — it skips import rows that already have a matching bill.
It does NOT touch current_stock (that's already set correctly by
seed_real_data.py / your manual entry) — this only adds reporting records.
"""
from datetime import datetime, time as dtime

from app import create_app
from app.extensions import db
from app.models import User, Role, SalesHistory, Bill, BillItem, Inventory

app = create_app()

with app.app_context():
    # Bills need a cashier_id — use (or create) a system account for imported history
    system_user = User.query.filter_by(username="import_history").first()
    if system_user is None:
        system_user = User(username="import_history", full_name="Historical Import",
                            role=Role.CASHIER, is_active_user=False)
        system_user.set_password("not-a-real-login-" + str(datetime.utcnow().timestamp()))
        db.session.add(system_user)
        db.session.flush()

    already_done = {
        inv for (inv,) in db.session.query(Bill.invoice_no)
        .filter(Bill.invoice_no.like("HIST-%"))
    }

    rows = SalesHistory.query.filter_by(source="import").order_by(SalesHistory.sale_date).all()
    print(f"Found {len(rows)} imported sales rows to backfill.")

    created = 0
    for i, row in enumerate(rows):
        invoice_no = f"HIST-{row.item_id}-{row.sale_date.isoformat()}"
        if invoice_no in already_done:
            continue

        item = db.session.get(Inventory, row.item_id)
        if item is None:
            continue

        unit_price = float(item.unit_price)
        line_total = round(unit_price * row.quantity_sold, 2)

        bill = Bill(
            invoice_no=invoice_no,
            cashier_id=system_user.id,
            customer_name="Historical Import",
            subtotal=line_total,
            discount=0,
            tax=0,
            total=line_total,
            payment_mode="Import",
            created_on=datetime.combine(row.sale_date, dtime(12, 0)),
        )
        db.session.add(bill)
        db.session.flush()

        db.session.add(BillItem(
            bill_id=bill.id,
            item_id=item.id,
            item_name_snapshot=item.item_name,
            quantity=row.quantity_sold,
            unit_price=unit_price,
            line_total=line_total,
        ))
        created += 1

        if created % 1000 == 0:
            db.session.commit()
            print(f"  ...{created} bills created so far")

    db.session.commit()
    print(f"\nDone. Created {created} historical bill records.")
    print("Refresh the admin dashboard — the graphs should now show real revenue.")
