from datetime import datetime, date, timedelta
from collections import OrderedDict
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models import User, Role, Inventory, StockBatch, Bill, BillItem, SalesHistory, Return
from app.decorators import roles_required

admin_bp = Blueprint("admin", __name__, template_folder="../templates/admin")


@admin_bp.route("/dashboard")
@login_required
@roles_required(Role.ADMIN)
def dashboard():
    total_items = Inventory.query.count()
    low_stock_items = [i for i in Inventory.query.all() if i.is_low_stock]

    horizon = date.today() + timedelta(days=30)
    expiring_batches = (StockBatch.query
                         .filter(StockBatch.quantity > 0, StockBatch.expiry_date <= horizon)
                         .count())

    today = date.today()
    latest_bill_date = db.session.query(func.max(Bill.created_on)).scalar()
    reference_day = latest_bill_date.date() if latest_bill_date else today

    today_sales_gross = (db.session.query(func.coalesce(func.sum(Bill.total), 0))
                          .filter(func.date(Bill.created_on) == reference_day).scalar())
    today_bill_count = Bill.query.filter(func.date(Bill.created_on) == reference_day).count()
    today_refunds = float(db.session.query(func.coalesce(func.sum(Return.refund_amount), 0))
                          .filter(func.date(Return.processed_on) == reference_day).scalar())
    today_sales = float(today_sales_gross) - today_refunds

    month_start = reference_day.replace(day=1)
    month_sales_gross = (db.session.query(func.coalesce(func.sum(Bill.total), 0))
                         .filter(func.date(Bill.created_on) >= month_start,
                                 func.date(Bill.created_on) <= reference_day).scalar())
    month_refunds = float(db.session.query(func.coalesce(func.sum(Return.refund_amount), 0))
                          .filter(func.date(Return.processed_on) >= month_start,
                                  func.date(Return.processed_on) <= reference_day).scalar())
    month_sales = float(month_sales_gross) - month_refunds

    total_users = User.query.count()

    # Net top-selling items: gross quantity/revenue per medicine, minus whatever
    # of that same line item has since been returned/refunded.
    gross_rows = db.session.query(
        BillItem.item_name_snapshot, BillItem.quantity, BillItem.line_total
    ).all()
    return_rows = (db.session.query(
                        BillItem.item_name_snapshot, Return.quantity, Return.refund_amount)
                    .join(BillItem, Return.bill_item_id == BillItem.id).all())

    qty_by_name, revenue_by_name = {}, {}
    for name, qty, line_total in gross_rows:
        qty_by_name[name] = qty_by_name.get(name, 0) + qty
        revenue_by_name[name] = revenue_by_name.get(name, 0.0) + float(line_total)
    for name, r_qty, r_amount in return_rows:
        qty_by_name[name] = qty_by_name.get(name, 0) - r_qty
        revenue_by_name[name] = revenue_by_name.get(name, 0.0) - float(r_amount)

    top_items = sorted(qty_by_name.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_items = [(name, qty, round(revenue_by_name[name], 2)) for name, qty in top_items]

    return render_template(
        "admin/dashboard.html",
        total_items=total_items,
        low_stock_items=low_stock_items,
        expiring_batches=expiring_batches,
        today_sales=today_sales,
        today_bill_count=today_bill_count,
        today_refunds=today_refunds,
        month_sales=month_sales,
        month_refunds=month_refunds,
        total_users=total_users,
        top_items=top_items,
    )


@admin_bp.route("/staff-performance")
@login_required
@roles_required(Role.ADMIN)
def staff_performance():
    days = int(request.args.get("days", 30))
    since = datetime.utcnow() - timedelta(days=days)

    rows = (db.session.query(Bill.cashier_id, Bill.created_on, Bill.total)
            .filter(Bill.created_on >= since)
            .all())

    # Aggregate per cashier (overall) and per cashier-per-day
    by_cashier = {}
    by_cashier_day = {}
    for cashier_id, created_on, total in rows:
        by_cashier.setdefault(cashier_id, {"bills": 0, "revenue": 0.0})
        by_cashier[cashier_id]["bills"] += 1
        by_cashier[cashier_id]["revenue"] += float(total)

        day_key = (cashier_id, created_on.date())
        by_cashier_day.setdefault(day_key, {"bills": 0, "revenue": 0.0})
        by_cashier_day[day_key]["bills"] += 1
        by_cashier_day[day_key]["revenue"] += float(total)

    users_by_id = {u.id: u for u in User.query.all()}

    summary = []
    for cashier_id, stats in by_cashier.items():
        user = users_by_id.get(cashier_id)
        if user is None:
            continue
        avg_bill = stats["revenue"] / stats["bills"] if stats["bills"] else 0
        summary.append({
            "name": user.full_name, "username": user.username,
            "bills": stats["bills"], "revenue": round(stats["revenue"], 2),
            "avg_bill": round(avg_bill, 2),
        })
    summary.sort(key=lambda r: r["revenue"], reverse=True)

    daily_rows = []
    for (cashier_id, day), stats in by_cashier_day.items():
        user = users_by_id.get(cashier_id)
        if user is None:
            continue
        avg_bill = stats["revenue"] / stats["bills"] if stats["bills"] else 0
        daily_rows.append({
            "date": day, "name": user.full_name,
            "bills": stats["bills"], "revenue": round(stats["revenue"], 2),
            "avg_bill": round(avg_bill, 2),
        })
    daily_rows.sort(key=lambda r: r["date"], reverse=True)

    return render_template("admin/staff_performance.html", summary=summary,
                            daily_rows=daily_rows, days=days)



@admin_bp.route("/api/sales/daily")
@login_required
@roles_required(Role.ADMIN)
def sales_daily():
    """Last N days of revenue, anchored to the most recent bill on record
    (so this works whether you're live today or viewing backfilled/historical
    data), for the admin dashboard line chart."""
    days = int(request.args.get("days", 30))
    latest_bill_date = db.session.query(func.max(Bill.created_on)).scalar()
    end_date = latest_bill_date.date() if latest_bill_date else date.today()
    start = end_date - timedelta(days=days - 1)

    rows = (db.session.query(Bill.created_on, Bill.total)
            .filter(Bill.created_on >= datetime.combine(start, datetime.min.time()))
            .filter(Bill.created_on <= datetime.combine(end_date, datetime.max.time()))
            .all())
    totals_by_day = {}
    for created_on, total in rows:
        key = str(created_on.date())
        totals_by_day[key] = totals_by_day.get(key, 0.0) + float(total)

    refund_rows = (db.session.query(Return.processed_on, Return.refund_amount)
                   .filter(Return.processed_on >= datetime.combine(start, datetime.min.time()))
                   .filter(Return.processed_on <= datetime.combine(end_date, datetime.max.time()))
                   .all())
    for processed_on, refund_amount in refund_rows:
        key = str(processed_on.date())
        totals_by_day[key] = totals_by_day.get(key, 0.0) - float(refund_amount)

    labels, values = [], []
    for i in range(days):
        d = start + timedelta(days=i)
        labels.append(d.strftime("%d %b %Y"))
        values.append(round(totals_by_day.get(str(d), 0.0), 2))

    return jsonify({"labels": labels, "values": values})


@admin_bp.route("/api/sales/monthly")
@login_required
@roles_required(Role.ADMIN)
def sales_monthly():
    """Last N months of revenue, anchored to the most recent bill on record."""
    months = int(request.args.get("months", 12))
    latest_bill_date = db.session.query(func.max(Bill.created_on)).scalar()
    anchor = latest_bill_date.date() if latest_bill_date else date.today()

    keys = []
    y, m = anchor.year, anchor.month
    for _ in range(months):
        keys.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    keys.reverse()

    # Aggregate in Python rather than with a DB-specific date-formatting
    # function, so this works unmodified on both SQLite and MySQL.
    earliest_needed = date(keys[0][0], keys[0][1], 1)
    rows = (db.session.query(Bill.created_on, Bill.total)
            .filter(Bill.created_on >= datetime.combine(earliest_needed, datetime.min.time()))
            .all())
    totals_by_month = {}
    for created_on, total in rows:
        key = f"{created_on.year:04d}-{created_on.month:02d}"
        totals_by_month[key] = totals_by_month.get(key, 0.0) + float(total)

    refund_rows = (db.session.query(Return.processed_on, Return.refund_amount)
                   .filter(Return.processed_on >= datetime.combine(earliest_needed, datetime.min.time()))
                   .all())
    for processed_on, refund_amount in refund_rows:
        key = f"{processed_on.year:04d}-{processed_on.month:02d}"
        totals_by_month[key] = totals_by_month.get(key, 0.0) - float(refund_amount)

    labels, values = [], []
    for (yy, mm) in keys:
        key = f"{yy:04d}-{mm:02d}"
        labels.append(date(yy, mm, 1).strftime("%b %Y"))
        values.append(round(totals_by_month.get(key, 0.0), 2))

    return jsonify({"labels": labels, "values": values})


# ---------------------------------------------------------------------------
# User management (Admin, Inventory Manager, Cashier)
# ---------------------------------------------------------------------------

@admin_bp.route("/users")
@login_required
@roles_required(Role.ADMIN)
def users():
    all_users = User.query.order_by(User.created_on.desc()).all()
    return render_template("admin/users.html", users=all_users, roles=Role)


@admin_bp.route("/users/new", methods=["POST"])
@login_required
@roles_required(Role.ADMIN)
def create_user():
    username = request.form["username"].strip()
    if User.query.filter_by(username=username).first():
        flash("That username is already taken.", "error")
        return redirect(url_for("admin.users"))

    user = User(
        username=username,
        full_name=request.form["full_name"].strip(),
        role=request.form["role"],
    )
    user.set_password(request.form["password"])
    db.session.add(user)
    db.session.commit()
    flash(f"User '{username}' created as {user.role_label}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@roles_required(Role.ADMIN)
def toggle_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You can't deactivate your own account.", "error")
        return redirect(url_for("admin.users"))
    user.is_active_user = not user.is_active_user
    db.session.commit()
    flash(f"'{user.username}' is now {'active' if user.is_active_user else 'deactivated'}.", "info")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@roles_required(Role.ADMIN)
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You can't delete your own account.", "error")
        return redirect(url_for("admin.users"))
    db.session.delete(user)
    db.session.commit()
    flash(f"User '{user.username}' deleted.", "info")
    return redirect(url_for("admin.users"))
