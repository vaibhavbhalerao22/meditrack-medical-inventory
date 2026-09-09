import os
from datetime import datetime, date, timedelta
from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, jsonify, current_app, send_file)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import (Inventory, StockBatch, Supplier, SalesHistory, ForecastResult,
                         ForecastValidation, WriteOff, PurchaseOrder, Role)
from app.decorators import roles_required
from app.forecasting.engine import run_hybrid_forecast
from app.forecasting.recommend import calculate_recommendation
from app.utils.csv_import import import_sales_csv
from app.utils.excel_export import build_workbook

inventory_bp = Blueprint("inventory", __name__, template_folder="../templates/inventory")

MANAGER_ROLES = (Role.ADMIN, Role.INVENTORY_MANAGER)


# ---------------------------------------------------------------------------
# Inventory CRUD
# ---------------------------------------------------------------------------

@inventory_bp.route("/")
@login_required
@roles_required(*MANAGER_ROLES)
def list_items():
    q = request.args.get("q", "").strip()
    query = Inventory.query
    if q:
        query = query.filter(Inventory.item_name.ilike(f"%{q}%"))
    items = query.order_by(Inventory.item_name.asc()).all()

    today = date.today()
    expiry_horizon = today + timedelta(days=current_app.config["EXPIRY_ALERT_DAYS"])

    low_stock_count = sum(1 for i in items if i.is_low_stock)
    expiring_count = (StockBatch.query
                       .filter(StockBatch.quantity > 0,
                               StockBatch.expiry_date <= expiry_horizon)
                       .count())

    return render_template("inventory/list.html", items=items, q=q,
                            low_stock_count=low_stock_count,
                            expiring_count=expiring_count)


@inventory_bp.route("/export.xlsx")
@login_required
@roles_required(*MANAGER_ROLES)
def export_inventory_xlsx():
    items = Inventory.query.order_by(Inventory.item_name.asc()).all()
    headers = ["Medicine", "Generic Name", "Category", "Unit", "Current Stock",
               "Reorder Level", "Safety Stock", "Unit Price", "Cost Price",
               "Supplier", "Nearest Expiry"]
    rows = [
        (i.item_name, i.generic_name or "", i.category, i.unit, i.current_stock,
         i.reorder_level, i.safety_stock, float(i.unit_price), float(i.cost_price),
         i.supplier.name if i.supplier else "",
         i.nearest_expiry.isoformat() if i.nearest_expiry else "")
        for i in items
    ]
    buffer = build_workbook("Inventory", headers, rows)
    return send_file(buffer, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                      as_attachment=True, download_name="inventory_stock_report.xlsx")


@inventory_bp.route("/item/new", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGER_ROLES)
def add_item():
    suppliers = Supplier.query.order_by(Supplier.name).all()
    if request.method == "POST":
        item = Inventory(
            item_name=request.form["item_name"].strip(),
            generic_name=request.form.get("generic_name", "").strip() or None,
            strength=request.form.get("strength", "").strip() or None,
            category=request.form["category"].strip(),
            unit=request.form.get("unit", "strip"),
            current_stock=int(request.form.get("current_stock", 0)),
            reorder_level=int(request.form.get("reorder_level", 10)),
            safety_stock=int(request.form.get("safety_stock", 5)),
            unit_price=float(request.form.get("unit_price", 0)),
            cost_price=float(request.form.get("cost_price", 0)),
            supplier_id=request.form.get("supplier_id") or None,
        )
        db.session.add(item)
        db.session.flush()

        # optional first batch
        batch_no = request.form.get("batch_no")
        expiry_date = request.form.get("expiry_date")
        if batch_no and expiry_date:
            batch = StockBatch(
                item_id=item.id,
                batch_no=batch_no,
                quantity=item.current_stock,
                expiry_date=datetime.strptime(expiry_date, "%Y-%m-%d").date(),
            )
            db.session.add(batch)

        db.session.commit()
        flash(f"'{item.item_name}' added to inventory.", "success")
        return redirect(url_for("inventory.list_items"))

    return render_template("inventory/add_edit.html", item=None, suppliers=suppliers)


@inventory_bp.route("/item/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGER_ROLES)
def edit_item(item_id):
    item = Inventory.query.get_or_404(item_id)
    suppliers = Supplier.query.order_by(Supplier.name).all()

    if request.method == "POST":
        item.item_name = request.form["item_name"].strip()
        item.generic_name = request.form.get("generic_name", "").strip() or None
        item.strength = request.form.get("strength", "").strip() or None
        item.category = request.form["category"].strip()
        item.unit = request.form.get("unit", item.unit)
        item.current_stock = int(request.form.get("current_stock", item.current_stock))
        item.reorder_level = int(request.form.get("reorder_level", item.reorder_level))
        item.safety_stock = int(request.form.get("safety_stock", item.safety_stock))
        item.unit_price = float(request.form.get("unit_price", item.unit_price))
        item.cost_price = float(request.form.get("cost_price", item.cost_price))
        item.supplier_id = request.form.get("supplier_id") or None
        db.session.commit()
        flash(f"'{item.item_name}' updated.", "success")
        return redirect(url_for("inventory.list_items"))

    return render_template("inventory/add_edit.html", item=item, suppliers=suppliers)


@inventory_bp.route("/item/<int:item_id>/delete", methods=["POST"])
@login_required
@roles_required(Role.ADMIN)
def delete_item(item_id):
    item = Inventory.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash(f"'{item.item_name}' removed from inventory.", "info")
    return redirect(url_for("inventory.list_items"))


# ---------------------------------------------------------------------------
# Batches / expiry
# ---------------------------------------------------------------------------

@inventory_bp.route("/item/<int:item_id>/batch/add", methods=["POST"])
@login_required
@roles_required(*MANAGER_ROLES)
def add_batch(item_id):
    item = Inventory.query.get_or_404(item_id)
    qty = int(request.form["quantity"])
    batch = StockBatch(
        item_id=item.id,
        batch_no=request.form["batch_no"].strip(),
        quantity=qty,
        expiry_date=datetime.strptime(request.form["expiry_date"], "%Y-%m-%d").date(),
    )
    item.current_stock += qty
    db.session.add(batch)
    db.session.commit()
    flash(f"Batch {batch.batch_no} added ({qty} {item.unit}s).", "success")
    return redirect(url_for("inventory.edit_item", item_id=item.id))


@inventory_bp.route("/expiring")
@login_required
@roles_required(*MANAGER_ROLES)
def expiring_batches():
    horizon = date.today() + timedelta(days=current_app.config["EXPIRY_ALERT_DAYS"])
    batches = (StockBatch.query
               .filter(StockBatch.quantity > 0, StockBatch.expiry_date <= horizon)
               .order_by(StockBatch.expiry_date.asc())
               .all())
    return render_template("inventory/expiring.html", batches=batches)


@inventory_bp.route("/batch/<int:batch_id>/writeoff", methods=["POST"])
@login_required
@roles_required(*MANAGER_ROLES)
def writeoff_batch(batch_id):
    batch = StockBatch.query.get_or_404(batch_id)
    item = batch.item
    reason = request.form.get("reason", "Expired")
    notes = request.form.get("notes", "")
    qty = int(request.form.get("quantity", batch.quantity))
    qty = min(qty, batch.quantity)

    if qty <= 0:
        flash("Nothing to write off for that batch.", "error")
        return redirect(request.referrer or url_for("inventory.expiring_batches"))

    unit_cost = float(item.cost_price) if item.cost_price else float(item.unit_price)
    total_loss = round(unit_cost * qty, 2)

    db.session.add(WriteOff(
        item_id=item.id,
        batch_id=batch.id,
        quantity=qty,
        reason=reason,
        unit_cost=unit_cost,
        total_loss=total_loss,
        notes=notes,
        written_off_by=current_user.id,
    ))
    batch.quantity -= qty
    item.current_stock = max(0, item.current_stock - qty)
    db.session.commit()

    flash(f"Wrote off {qty} {item.unit}(s) of '{item.item_name}' — loss of {total_loss:.2f}.", "success")
    return redirect(request.referrer or url_for("inventory.expiring_batches"))


@inventory_bp.route("/wastage-report")
@login_required
@roles_required(*MANAGER_ROLES)
def wastage_report():
    days = int(request.args.get("days", 90))
    since = datetime.utcnow() - timedelta(days=days)

    write_offs = (WriteOff.query
                  .filter(WriteOff.written_off_on >= since)
                  .order_by(WriteOff.written_off_on.desc())
                  .all())

    total_loss = sum(float(w.total_loss) for w in write_offs)
    total_units = sum(w.quantity for w in write_offs)

    by_item = {}
    for w in write_offs:
        key = w.item.item_name
        by_item.setdefault(key, {"qty": 0, "loss": 0.0})
        by_item[key]["qty"] += w.quantity
        by_item[key]["loss"] += float(w.total_loss)
    by_item_sorted = sorted(by_item.items(), key=lambda kv: kv[1]["loss"], reverse=True)

    return render_template("inventory/wastage_report.html",
                            write_offs=write_offs, total_loss=total_loss,
                            total_units=total_units, by_item=by_item_sorted, days=days)


@inventory_bp.route("/wastage-report/export.xlsx")
@login_required
@roles_required(*MANAGER_ROLES)
def export_wastage_xlsx():
    days = int(request.args.get("days", 90))
    since = datetime.utcnow() - timedelta(days=days)
    write_offs = (WriteOff.query
                  .filter(WriteOff.written_off_on >= since)
                  .order_by(WriteOff.written_off_on.desc())
                  .all())
    headers = ["Date", "Medicine", "Batch No.", "Quantity", "Reason", "Unit Cost", "Total Loss", "Written Off By"]
    rows = [
        (w.written_off_on.strftime("%Y-%m-%d %H:%M"), w.item.item_name,
         w.batch.batch_no if w.batch else "", w.quantity, w.reason,
         float(w.unit_cost), float(w.total_loss), w.user.full_name)
        for w in write_offs
    ]
    buffer = build_workbook("Wastage", headers, rows)
    return send_file(buffer, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                      as_attachment=True, download_name=f"wastage_report_last_{days}_days.xlsx")


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------

@inventory_bp.route("/suppliers", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGER_ROLES)
def suppliers():
    if request.method == "POST":
        supplier = Supplier(
            name=request.form["name"].strip(),
            contact_person=request.form.get("contact_person"),
            phone=request.form.get("phone"),
            email=request.form.get("email"),
            address=request.form.get("address"),
            lead_time_days=int(request.form.get("lead_time_days", 7)),
        )
        db.session.add(supplier)
        db.session.commit()
        flash(f"Supplier '{supplier.name}' added.", "success")
        return redirect(url_for("inventory.suppliers"))

    all_suppliers = Supplier.query.order_by(Supplier.name).all()
    return render_template("inventory/suppliers.html", suppliers=all_suppliers)


# ---------------------------------------------------------------------------
# Historical sales CSV import (e.g. dailysales.csv) -> feeds the forecaster
# ---------------------------------------------------------------------------

@inventory_bp.route("/sales/import", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGER_ROLES)
def import_sales():
    if request.method == "POST":
        file = request.files.get("csv_file")
        if not file or file.filename == "":
            flash("Please choose a CSV file to upload.", "error")
            return redirect(url_for("inventory.import_sales"))

        filename = secure_filename(file.filename)
        save_path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
        file.save(save_path)

        result = import_sales_csv(save_path)
        flash(
            f"Import complete: {result['inserted']} rows added, "
            f"{result['skipped']} skipped (unmatched item names).", "success"
        )
        if result["unmatched_names"]:
            flash(
                "Unmatched item names (add these to inventory first): "
                + ", ".join(sorted(result["unmatched_names"])[:15]), "info"
            )
        return redirect(url_for("inventory.import_sales"))

    recent = (SalesHistory.query.filter_by(source="import")
              .order_by(SalesHistory.id.desc()).limit(10).all())
    total_records = SalesHistory.query.count()
    return render_template("inventory/import_sales.html", recent=recent,
                            total_records=total_records)


# ---------------------------------------------------------------------------
# Forecasting (ARIMA + LSTM hybrid) & recommendations
# ---------------------------------------------------------------------------

@inventory_bp.route("/forecast")
@login_required
@roles_required(*MANAGER_ROLES)
def forecast_home():
    items = Inventory.query.order_by(Inventory.item_name).all()
    return render_template("inventory/forecast.html", items=items, result=None)


@inventory_bp.route("/forecast/<int:item_id>/run", methods=["POST"])
@login_required
@roles_required(*MANAGER_ROLES)
def run_forecast(item_id):
    item = Inventory.query.get_or_404(item_id)
    horizon = int(request.form.get("horizon", 14))

    history_count = SalesHistory.query.filter_by(item_id=item.id).count()
    if history_count < 30:
        flash(
            f"'{item.item_name}' only has {history_count} days of sales history. "
            "At least ~30 days are recommended for a meaningful forecast. "
            "Import more history via Sales Import.", "error"
        )
        return redirect(url_for("inventory.forecast_home"))

    try:
        forecast_rows, metrics, validation_rows = run_hybrid_forecast(item.id, horizon_days=horizon)
    except Exception as exc:  # keep the app usable even if a model fails to converge
        current_app.logger.exception("Forecast failed for item %s", item.id)
        flash(f"Forecast could not be generated: {exc}", "error")
        return redirect(url_for("inventory.forecast_home"))

    # persist future forecast
    ForecastResult.query.filter_by(item_id=item.id).delete()
    for row in forecast_rows:
        db.session.add(ForecastResult(
            item_id=item.id,
            forecast_date=row["date"],
            predicted_demand=row["demand"],
            model_used="Hybrid (ARIMA+LSTM)",
            rmse=metrics.get("rmse"),
            mae=metrics.get("mae"),
        ))

    # persist held-out validation (actual vs predicted) for the accuracy dashboard
    ForecastValidation.query.filter_by(item_id=item.id).delete()
    for row in validation_rows:
        db.session.add(ForecastValidation(
            item_id=item.id,
            eval_date=row["date"],
            actual_demand=row["actual"],
            predicted_demand=row["predicted"],
            model_used="Hybrid (ARIMA+LSTM)",
        ))
    db.session.commit()

    recommendation = calculate_recommendation(item, forecast_rows)

    items = Inventory.query.order_by(Inventory.item_name).all()
    return render_template("inventory/forecast.html", items=items,
                            selected_item=item, forecast_rows=forecast_rows,
                            metrics=metrics, recommendation=recommendation)


@inventory_bp.route("/forecast/<int:item_id>/data")
@login_required
@roles_required(*MANAGER_ROLES)
def forecast_data(item_id):
    """JSON endpoint used by the chart on the forecast page."""
    rows = (ForecastResult.query.filter_by(item_id=item_id)
            .order_by(ForecastResult.forecast_date.asc()).all())
    return jsonify([
        {"date": r.forecast_date.isoformat(), "demand": float(r.predicted_demand)}
        for r in rows
    ])


# ---------------------------------------------------------------------------
# Forecast accuracy dashboard
# ---------------------------------------------------------------------------

def _accuracy_metrics(rows):
    """rows: list of ForecastValidation for one item. Returns MAE/RMSE/MAPE."""
    n = len(rows)
    if n == 0:
        return None
    errors = [r.predicted_demand - r.actual_demand for r in rows]
    mae = sum(abs(e) for e in errors) / n
    rmse = (sum(e ** 2 for e in errors) / n) ** 0.5
    pct_errors = [abs(r.predicted_demand - r.actual_demand) / r.actual_demand
                  for r in rows if r.actual_demand]  # skip zero-actual days (undefined %)
    mape = (sum(pct_errors) / len(pct_errors) * 100) if pct_errors else None
    return {
        "n": n,
        "mae": round(mae, 2),
        "rmse": round(rmse, 2),
        "mape": round(mape, 1) if mape is not None else None,
        "generated_on": rows[0].generated_on,
        "model_used": rows[0].model_used,
    }


@inventory_bp.route("/forecast/accuracy")
@login_required
@roles_required(*MANAGER_ROLES)
def forecast_accuracy():
    items = Inventory.query.order_by(Inventory.item_name).all()
    rows_by_item = {}
    for item in items:
        rows = (ForecastValidation.query.filter_by(item_id=item.id)
                .order_by(ForecastValidation.eval_date.asc()).all())
        metrics = _accuracy_metrics(rows)
        if metrics:
            rows_by_item[item.id] = {"item": item, **metrics}

    scored_items = sorted(rows_by_item.values(),
                           key=lambda r: (r["mape"] is None, r["mape"]))
    unscored_items = [i for i in items if i.id not in rows_by_item]

    overall = None
    if scored_items:
        mapes = [r["mape"] for r in scored_items if r["mape"] is not None]
        overall = {
            "avg_mae": round(sum(r["mae"] for r in scored_items) / len(scored_items), 2),
            "avg_rmse": round(sum(r["rmse"] for r in scored_items) / len(scored_items), 2),
            "avg_mape": round(sum(mapes) / len(mapes), 1) if mapes else None,
            "items_validated": len(scored_items),
        }

    selected_id = request.args.get("item_id", type=int)
    if selected_id is None and scored_items:
        selected_id = scored_items[0]["item"].id

    return render_template(
        "inventory/forecast_accuracy.html",
        scored_items=scored_items,
        unscored_items=unscored_items,
        overall=overall,
        selected_id=selected_id,
    )


@inventory_bp.route("/forecast/accuracy/<int:item_id>/data")
@login_required
@roles_required(*MANAGER_ROLES)
def forecast_accuracy_data(item_id):
    """JSON endpoint used by the actual-vs-predicted chart on the accuracy page."""
    rows = (ForecastValidation.query.filter_by(item_id=item_id)
            .order_by(ForecastValidation.eval_date.asc()).all())
    return jsonify({
        "labels": [r.eval_date.isoformat() for r in rows],
        "actual": [r.actual_demand for r in rows],
        "predicted": [r.predicted_demand for r in rows],
    })


# ---------------------------------------------------------------------------
# Auto-generated reorder suggestions (aggregated across all items)
# ---------------------------------------------------------------------------

@inventory_bp.route("/forecast/reorder-suggestions")
@login_required
@roles_required(*MANAGER_ROLES)
def reorder_suggestions():
    items = Inventory.query.order_by(Inventory.item_name).all()
    urgent, ok, unforecasted = [], [], []

    for item in items:
        forecast_rows = [
            {"date": r.forecast_date, "demand": float(r.predicted_demand)}
            for r in item.forecasts.order_by(ForecastResult.forecast_date.asc()).all()
        ]
        if not forecast_rows:
            unforecasted.append(item)
            continue

        try:
            rec = calculate_recommendation(item, forecast_rows)
        except Exception:
            unforecasted.append(item)
            continue

        # Estimate the date stock will actually hit the reorder point, so the
        # suggestion reads as "reorder by <date>" rather than just "reorder now".
        avg_demand = rec["avg_daily_demand"]
        if avg_demand > 0:
            days_until_reorder_point = max(
                0, (item.current_stock - rec["reorder_point"]) / avg_demand
            )
            reorder_by = date.today() + timedelta(days=round(days_until_reorder_point))
        else:
            reorder_by = None

        entry = {"item": item, **rec, "reorder_by": reorder_by}
        if rec["should_reorder_now"]:
            urgent.append(entry)
        else:
            ok.append(entry)

    # Most urgent first: fewest days of stock left, treating None (infinite) as last
    urgent.sort(key=lambda e: (e["days_of_stock_left"] is None, e["days_of_stock_left"]))
    ok.sort(key=lambda e: e["item"].item_name)

    return render_template(
        "inventory/reorder_suggestions.html",
        urgent=urgent, ok=ok, unforecasted=unforecasted,
    )


# ---------------------------------------------------------------------------
# Purchase orders
# ---------------------------------------------------------------------------

@inventory_bp.route("/purchase-orders")
@login_required
@roles_required(*MANAGER_ROLES)
def purchase_orders():
    pending = (PurchaseOrder.query.filter_by(status=PurchaseOrder.STATUS_PENDING)
               .order_by(PurchaseOrder.created_on.desc()).all())
    past = (PurchaseOrder.query.filter(PurchaseOrder.status != PurchaseOrder.STATUS_PENDING)
            .order_by(PurchaseOrder.created_on.desc()).limit(50).all())
    items = Inventory.query.order_by(Inventory.item_name.asc()).all()
    today = date.today().isoformat()
    return render_template("inventory/purchase_orders.html",
                            pending=pending, past=past, items=items, today=today)


@inventory_bp.route("/purchase-orders/create", methods=["POST"])
@login_required
@roles_required(*MANAGER_ROLES)
def create_purchase_order():
    item = Inventory.query.get_or_404(request.form.get("item_id", type=int))
    try:
        qty = int(request.form.get("quantity", 0))
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        flash("Enter a valid quantity to order.", "error")
        return redirect(url_for("inventory.purchase_orders"))

    po = PurchaseOrder(
        item_id=item.id,
        supplier_id=item.supplier_id,
        quantity=qty,
        notes=request.form.get("notes", "").strip() or None,
        created_by=current_user.id,
        created_via="manual",
    )
    db.session.add(po)
    db.session.commit()
    flash(f"Purchase order created: {qty} {item.unit}s of '{item.item_name}'.", "success")
    return redirect(url_for("inventory.purchase_orders"))


@inventory_bp.route("/purchase-orders/<int:po_id>/receive", methods=["POST"])
@login_required
@roles_required(*MANAGER_ROLES)
def receive_purchase_order(po_id):
    po = PurchaseOrder.query.get_or_404(po_id)
    if po.status != PurchaseOrder.STATUS_PENDING:
        flash("This purchase order has already been processed.", "error")
        return redirect(url_for("inventory.purchase_orders"))

    batch_no = request.form.get("batch_no", "").strip()
    expiry_raw = request.form.get("expiry_date", "").strip()
    if not batch_no or not expiry_raw:
        flash("Batch number and expiry date are required to receive stock.", "error")
        return redirect(url_for("inventory.purchase_orders"))

    batch = StockBatch(
        item_id=po.item_id,
        batch_no=batch_no,
        quantity=po.quantity,
        expiry_date=datetime.strptime(expiry_raw, "%Y-%m-%d").date(),
    )
    po.item.current_stock += po.quantity
    po.status = PurchaseOrder.STATUS_RECEIVED
    po.received_on = datetime.utcnow()
    db.session.add(batch)
    db.session.commit()
    flash(f"Received {po.quantity} {po.item.unit}s of '{po.item.item_name}' "
          f"(batch {batch_no}) — stock updated.", "success")
    return redirect(url_for("inventory.purchase_orders"))


@inventory_bp.route("/purchase-orders/<int:po_id>/cancel", methods=["POST"])
@login_required
@roles_required(*MANAGER_ROLES)
def cancel_purchase_order(po_id):
    po = PurchaseOrder.query.get_or_404(po_id)
    if po.status != PurchaseOrder.STATUS_PENDING:
        flash("This purchase order has already been processed.", "error")
        return redirect(url_for("inventory.purchase_orders"))
    po.status = PurchaseOrder.STATUS_CANCELLED
    db.session.commit()
    flash("Purchase order cancelled.", "info")
    return redirect(url_for("inventory.purchase_orders"))
