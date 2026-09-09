from datetime import datetime, date
from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, jsonify, send_file, abort, current_app)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Inventory, Bill, BillItem, SalesHistory, StockBatch, Return, Role
from app.decorators import roles_required
from app.utils.pdf_invoice import generate_invoice_pdf
from app.utils.excel_export import build_workbook

cashier_bp = Blueprint("cashier", __name__, template_folder="../templates/cashier")


@cashier_bp.route("/billing")
@login_required
@roles_required(Role.CASHIER, Role.ADMIN)
def billing():
    return render_template("cashier/billing.html")


@cashier_bp.route("/api/search-items")
@login_required
@roles_required(Role.CASHIER, Role.ADMIN)
def search_items():
    q = request.args.get("q", "").strip()
    query = Inventory.query
    if q:
        query = query.filter(Inventory.item_name.ilike(f"%{q}%"))
    items = query.order_by(Inventory.item_name).limit(20).all()
    return jsonify([
        {
            "id": i.id,
            "name": i.item_name,
            "category": i.category,
            "unit": i.unit,
            "price": float(i.unit_price),
            "stock": i.current_stock,
            "generic_name": i.generic_name,
        }
        for i in items
    ])


@cashier_bp.route("/api/substitutes/<int:item_id>")
@login_required
@roles_required(Role.CASHIER, Role.ADMIN)
def find_substitutes(item_id):
    item = Inventory.query.get_or_404(item_id)
    if not item.generic_name:
        return jsonify({"substitutes": [], "message":
                         "No generic/salt composition on file for this medicine — "
                         "add one in Inventory \u2192 Manage to enable substitute suggestions."})

    alternatives = (Inventory.query
                    .filter(Inventory.generic_name.ilike(item.generic_name),
                            Inventory.id != item.id,
                            Inventory.current_stock > 0)
                    .order_by(Inventory.item_name)
                    .all())
    return jsonify({
        "substitutes": [
            {"id": a.id, "name": a.item_name, "strength": a.strength,
             "price": float(a.unit_price), "stock": a.current_stock, "unit": a.unit}
            for a in alternatives
        ],
        "message": None,
    })


def _next_invoice_no():
    today_str = datetime.utcnow().strftime("%Y%m%d")
    count_today = Bill.query.filter(Bill.invoice_no.like(f"INV-{today_str}-%")).count()
    return f"INV-{today_str}-{count_today + 1:04d}"


def _deduct_stock_fefo(item, quantity):
    """Deduct `quantity` from the item's batches, oldest-expiry first."""
    remaining = quantity
    batches = (item.batches
               .filter(StockBatch.quantity > 0)
               .order_by(StockBatch.expiry_date.asc())
               .all())
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity, remaining)
        batch.quantity -= take
        remaining -= take
    item.current_stock = max(0, item.current_stock - quantity)


@cashier_bp.route("/bill/create", methods=["POST"])
@login_required
@roles_required(Role.CASHIER, Role.ADMIN)
def create_bill():
    payload = request.get_json()
    cart = payload.get("cart", [])
    if not cart:
        return jsonify({"error": "Cart is empty."}), 400

    discount = float(payload.get("discount", 0) or 0)
    tax = float(payload.get("tax", 0) or 0)
    customer_name = payload.get("customer_name") or "Walk-in Customer"
    customer_phone = payload.get("customer_phone", "")
    payment_mode = payload.get("payment_mode", "Cash")

    bill = Bill(
        invoice_no=_next_invoice_no(),
        cashier_id=current_user.id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        payment_mode=payment_mode,
        discount=discount,
        tax=tax,
    )
    db.session.add(bill)
    db.session.flush()

    subtotal = 0.0
    for entry in cart:
        item = Inventory.query.get(entry["id"])
        if item is None:
            continue
        qty = int(entry["qty"])
        if qty <= 0:
            continue
        if qty > item.current_stock:
            db.session.rollback()
            return jsonify({"error": f"Not enough stock for '{item.item_name}'. "
                                      f"Only {item.current_stock} left."}), 400

        line_total = qty * float(item.unit_price)
        subtotal += line_total

        db.session.add(BillItem(
            bill_id=bill.id,
            item_id=item.id,
            item_name_snapshot=item.item_name,
            quantity=qty,
            unit_price=item.unit_price,
            line_total=line_total,
        ))

        _deduct_stock_fefo(item, qty)

        db.session.add(SalesHistory(
            item_id=item.id,
            sale_date=date.today(),
            quantity_sold=qty,
            remaining_stock=item.current_stock,
            source="pos",
        ))

    bill.subtotal = subtotal
    bill.total = max(0.0, subtotal - discount + tax)
    db.session.commit()

    return jsonify({"success": True, "bill_id": bill.id, "invoice_no": bill.invoice_no,
                     "redirect": url_for("cashier.view_bill", bill_id=bill.id)})


@cashier_bp.route("/bill/<int:bill_id>")
@login_required
@roles_required(Role.CASHIER, Role.ADMIN)
def view_bill(bill_id):
    bill = Bill.query.get_or_404(bill_id)
    from app.models import Customer
    matched_customer = Customer.query.filter_by(phone=bill.customer_phone).first() if bill.customer_phone else None
    return render_template("cashier/invoice.html", bill=bill, matched_customer=matched_customer)


@cashier_bp.route("/bill-item/<int:bill_item_id>/return", methods=["POST"])
@login_required
@roles_required(Role.CASHIER, Role.ADMIN)
def process_return(bill_item_id):
    line = BillItem.query.get_or_404(bill_item_id)
    bill = line.bill

    if current_user.role == Role.CASHIER and bill.cashier_id != current_user.id:
        abort(403)

    try:
        qty = int(request.form.get("quantity", 0))
    except (TypeError, ValueError):
        qty = 0
    reason = request.form.get("reason", "Customer return")

    if qty <= 0:
        flash("Enter a valid quantity to return.", "error")
        return redirect(url_for("cashier.view_bill", bill_id=bill.id))
    if qty > line.returnable_quantity:
        flash(f"Only {line.returnable_quantity} unit(s) of '{line.item_name_snapshot}' "
              f"can still be returned from this bill.", "error")
        return redirect(url_for("cashier.view_bill", bill_id=bill.id))

    refund_amount = qty * float(line.unit_price)

    # Put the returned stock back. We don't know which original batch it came
    # from (billing deducts FEFO across possibly multiple batches), so — as a
    # simplification — it's added back to the item's overall stock count rather
    # than to a specific batch/expiry.
    item = line.item
    item.current_stock += qty

    line.returned_quantity += qty
    db.session.add(Return(
        bill_item_id=line.id,
        quantity=qty,
        reason=reason,
        refund_amount=refund_amount,
        processed_by=current_user.id,
    ))
    db.session.commit()

    flash(f"Returned {qty} unit(s) of '{line.item_name_snapshot}' — "
          f"refund of ₹{refund_amount:,.2f} recorded.", "success")
    return redirect(url_for("cashier.view_bill", bill_id=bill.id))


@cashier_bp.route("/returns")
@login_required
@roles_required(Role.CASHIER, Role.ADMIN)
def returns_history():
    query = Return.query.join(BillItem).join(Bill)
    if current_user.role == Role.CASHIER:
        query = query.filter(Bill.cashier_id == current_user.id)
    returns = query.order_by(Return.processed_on.desc()).limit(200).all()
    total_refunded = sum(float(r.refund_amount) for r in returns)
    return render_template("cashier/returns_history.html", returns=returns, total_refunded=total_refunded)


@cashier_bp.route("/bill/<int:bill_id>/pdf")
@login_required
@roles_required(Role.CASHIER, Role.ADMIN)
def download_invoice(bill_id):
    bill = Bill.query.get_or_404(bill_id)
    buffer = generate_invoice_pdf(
        bill,
        shop_name=current_app.config["SHOP_NAME"],
        shop_address=current_app.config["SHOP_ADDRESS"],
        shop_phone=current_app.config["SHOP_PHONE"],
        shop_gstin=current_app.config["SHOP_GSTIN"],
    )
    return send_file(buffer, mimetype="application/pdf",
                      as_attachment=True,
                      download_name=f"{bill.invoice_no}.pdf")


@cashier_bp.route("/bills")
@login_required
@roles_required(Role.CASHIER, Role.ADMIN)
def bill_history():
    query = Bill.query
    if current_user.role == Role.CASHIER:
        query = query.filter_by(cashier_id=current_user.id)

    date_filter = request.args.get("date")
    if date_filter:
        try:
            d = datetime.strptime(date_filter, "%Y-%m-%d").date()
            query = query.filter(db.func.date(Bill.created_on) == d)
        except ValueError:
            pass

    bills = query.order_by(Bill.created_on.desc()).limit(200).all()
    return render_template("cashier/bill_history.html", bills=bills, date_filter=date_filter or "")


@cashier_bp.route("/bills/export.xlsx")
@login_required
@roles_required(Role.CASHIER, Role.ADMIN)
def export_bills_xlsx():
    query = Bill.query
    if current_user.role == Role.CASHIER:
        query = query.filter_by(cashier_id=current_user.id)

    date_filter = request.args.get("date")
    if date_filter:
        try:
            d = datetime.strptime(date_filter, "%Y-%m-%d").date()
            query = query.filter(db.func.date(Bill.created_on) == d)
        except ValueError:
            pass

    bills = query.order_by(Bill.created_on.desc()).limit(200).all()
    headers = ["Invoice No.", "Date", "Customer", "Phone", "Cashier", "Payment Mode",
               "Subtotal", "Discount", "Tax", "Total", "Refunded"]
    rows = [
        (b.invoice_no, b.created_on.strftime("%Y-%m-%d %H:%M"), b.customer_name,
         b.customer_phone or "", b.cashier.full_name, b.payment_mode,
         float(b.subtotal), float(b.discount), float(b.tax), float(b.total), b.total_refunded)
        for b in bills
    ]
    buffer = build_workbook("Bills", headers, rows)
    return send_file(buffer, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                      as_attachment=True, download_name="bill_history_report.xlsx")
