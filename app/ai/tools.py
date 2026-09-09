"""
Tools the AI assistant can call to answer questions about the live pharmacy
data — this is what makes it "agentic" rather than a plain chatbot: the
model decides which of these to call (zero, one, or several) based on the
question, we execute them against the real database, and feed the results
back so the model's answer reflects what's actually in your system today.

Most tools here are read-only lookups. One tool — create_purchase_order —
is NOT read-only: it writes a real PurchaseOrder row to the database. It
still doesn't touch stock levels directly; a created PO stays "Pending"
until someone receives it (with a batch number + expiry date) on the
Purchase Orders page, same as a manually created one.
"""
from datetime import date, datetime, timedelta
from sqlalchemy import func
from flask_login import current_user

from app.extensions import db
from app.models import Inventory, StockBatch, Bill, BillItem, WriteOff, ForecastResult, PurchaseOrder, Return
from app.forecasting.recommend import calculate_recommendation

TOOL_SCHEMAS = [
    {
        "name": "get_low_stock_items",
        "description": "Get medicines currently at or below their reorder level (need restocking soon).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_expiring_batches",
        "description": "Get stock batches expiring within a given number of days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Look-ahead window in days. Default 30."}
            },
        },
    },
    {
        "name": "get_sales_summary",
        "description": "Get gross revenue, refunds, and net revenue (gross minus refunds) plus bill count over the last N days, anchored to the most recent bill on record.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "How many days back to summarize. Default 30."}
            },
        },
    },
    {
        "name": "get_top_selling_items",
        "description": "Get the top-selling medicines by net quantity sold (gross sales minus returns), all-time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many items to return. Default 5."}
            },
        },
    },
    {
        "name": "get_wastage_summary",
        "description": "Get total write-off/wastage loss value and units over the last N days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "How many days back to summarize. Default 90."}
            },
        },
    },
    {
        "name": "get_item_forecast",
        "description": "Get the latest saved demand forecast for a medicine by name (partial match ok). "
                        "Only returns data if a forecast has already been run for that item on the Demand Forecast page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item_name": {"type": "string", "description": "Medicine name or partial name to search for."}
            },
            "required": ["item_name"],
        },
    },
    {
        "name": "search_inventory",
        "description": "Search the current inventory by name, and get stock level, price, and category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Medicine name or partial name to search for."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_purchase_orders",
        "description": "List recent purchase orders and their status (Pending / Received / Cancelled).",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Optional filter: 'Pending', 'Received', or 'Cancelled'."}
            },
        },
    },
    {
        "name": "create_purchase_order",
        "description": (
            "Create a new purchase order (restock request) for a medicine. This WRITES to the "
            "database — only call it after the user has clearly asked you to place/create the "
            "order (not just asked whether they should reorder something). If quantity is not "
            "given, it's calculated from the medicine's latest demand forecast (EOQ), or falls "
            "back to twice the reorder level if no forecast exists. The PO is created as "
            "'Pending' — it does not add stock until a staff member receives it (with a batch "
            "number and expiry date) on the Purchase Orders page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_name": {"type": "string", "description": "Medicine name or partial name."},
                "quantity": {"type": "integer", "description": "Units to order. Omit to auto-calculate."},
                "notes": {"type": "string", "description": "Optional short note to attach to the PO."},
            },
            "required": ["item_name"],
        },
    },
]


def get_low_stock_items(**_):
    items = Inventory.query.all()
    low = [i for i in items if i.current_stock <= i.reorder_level]
    return [
        {"name": i.item_name, "current_stock": i.current_stock,
         "reorder_level": i.reorder_level, "unit": i.unit}
        for i in low
    ]


def get_expiring_batches(days=30, **_):
    horizon = date.today() + timedelta(days=int(days))
    batches = (StockBatch.query
               .filter(StockBatch.quantity > 0, StockBatch.expiry_date <= horizon)
               .order_by(StockBatch.expiry_date.asc())
               .all())
    return [
        {"item": b.item.item_name, "batch_no": b.batch_no, "quantity": b.quantity,
         "expiry_date": b.expiry_date.isoformat(), "days_to_expiry": b.days_to_expiry}
        for b in batches
    ]


def get_sales_summary(days=30, **_):
    days = int(days)
    latest_bill_date = db.session.query(func.max(Bill.created_on)).scalar()
    end_date = latest_bill_date.date() if latest_bill_date else date.today()
    start = end_date - timedelta(days=days - 1)

    rows = (db.session.query(Bill.total)
            .filter(Bill.created_on >= datetime.combine(start, datetime.min.time()))
            .filter(Bill.created_on <= datetime.combine(end_date, datetime.max.time()))
            .all())
    gross_revenue = sum(float(r[0]) for r in rows)

    refunds = float(db.session.query(func.coalesce(func.sum(Return.refund_amount), 0))
                     .filter(Return.processed_on >= datetime.combine(start, datetime.min.time()))
                     .filter(Return.processed_on <= datetime.combine(end_date, datetime.max.time()))
                     .scalar())

    return {
        "period_days": days,
        "period_start": start.isoformat(),
        "period_end": end_date.isoformat(),
        "gross_revenue": round(gross_revenue, 2),
        "refunds": round(refunds, 2),
        "net_revenue": round(gross_revenue - refunds, 2),
        "bill_count": len(rows),
    }


def get_top_selling_items(limit=5, **_):
    gross_rows = (db.session.query(
                BillItem.item_name_snapshot,
                func.sum(BillItem.quantity).label("qty"),
                func.sum(BillItem.line_total).label("revenue"))
            .group_by(BillItem.item_name_snapshot).all())
    return_rows = (db.session.query(
                BillItem.item_name_snapshot,
                func.sum(Return.quantity), func.sum(Return.refund_amount))
            .join(BillItem, Return.bill_item_id == BillItem.id)
            .group_by(BillItem.item_name_snapshot).all())
    returns_by_name = {name: (r_qty or 0, float(r_amt or 0)) for name, r_qty, r_amt in return_rows}

    combined = []
    for name, qty, revenue in gross_rows:
        r_qty, r_amt = returns_by_name.get(name, (0, 0.0))
        combined.append({"name": name, "units_sold": int(qty) - r_qty,
                          "revenue": round(float(revenue) - r_amt, 2)})
    combined.sort(key=lambda x: x["units_sold"], reverse=True)
    return combined[:int(limit)]


def get_wastage_summary(days=90, **_):
    since = datetime.utcnow() - timedelta(days=int(days))
    write_offs = WriteOff.query.filter(WriteOff.written_off_on >= since).all()
    total_loss = sum(float(w.total_loss) for w in write_offs)
    total_units = sum(w.quantity for w in write_offs)
    return {"period_days": int(days), "total_loss": round(total_loss, 2),
            "total_units_written_off": total_units, "events": len(write_offs)}


def get_item_forecast(item_name, **_):
    item = Inventory.query.filter(Inventory.item_name.ilike(f"%{item_name}%")).first()
    if item is None:
        return {"error": f"No inventory item matching '{item_name}'."}
    rows = (ForecastResult.query.filter_by(item_id=item.id)
            .order_by(ForecastResult.forecast_date.asc()).all())
    if not rows:
        return {"item": item.item_name, "message": "No forecast has been run yet for this item."}
    return {
        "item": item.item_name,
        "current_stock": item.current_stock,
        "forecast": [{"date": r.forecast_date.isoformat(), "predicted_demand": float(r.predicted_demand)} for r in rows],
        "rmse": rows[0].rmse, "mae": rows[0].mae,
    }


def search_inventory(query, **_):
    items = (Inventory.query
             .filter(Inventory.item_name.ilike(f"%{query}%"))
             .limit(15).all())
    return [
        {"name": i.item_name, "category": i.category, "current_stock": i.current_stock,
         "unit_price": float(i.unit_price), "reorder_level": i.reorder_level}
        for i in items
    ]


def list_purchase_orders(status=None, **_):
    query = PurchaseOrder.query
    if status:
        query = query.filter(PurchaseOrder.status.ilike(status))
    rows = query.order_by(PurchaseOrder.created_on.desc()).limit(20).all()
    return [
        {"id": po.id, "item": po.item.item_name, "quantity": po.quantity,
         "status": po.status, "created_on": po.created_on.date().isoformat(),
         "created_via": po.created_via}
        for po in rows
    ]


def create_purchase_order(item_name, quantity=None, notes=None, **_):
    item = Inventory.query.filter(Inventory.item_name.ilike(f"%{item_name}%")).first()
    if item is None:
        return {"error": f"No inventory item matching '{item_name}'."}

    if quantity is None:
        forecast_rows = [
            {"date": r.forecast_date, "demand": float(r.predicted_demand)}
            for r in ForecastResult.query.filter_by(item_id=item.id)
            .order_by(ForecastResult.forecast_date.asc()).all()
        ]
        if forecast_rows:
            try:
                quantity = calculate_recommendation(item, forecast_rows)["eoq"]
            except Exception:
                quantity = None
        if quantity is None:
            quantity = (item.reorder_level * 2) if item.reorder_level else 50
    else:
        quantity = int(quantity)

    if quantity <= 0:
        return {"error": "Could not determine a valid quantity to order."}

    po = PurchaseOrder(
        item_id=item.id,
        supplier_id=item.supplier_id,
        quantity=quantity,
        notes=(notes or None),
        created_by=current_user.id,
        created_via="ai_assistant",
    )
    db.session.add(po)
    db.session.commit()
    return {
        "success": True,
        "purchase_order_id": po.id,
        "item": item.item_name,
        "quantity": quantity,
        "status": po.status,
        "note": "Created as Pending. It won't add to stock until a staff member receives it "
                "with a batch number and expiry date on the Purchase Orders page.",
    }


TOOL_FUNCTIONS = {
    "get_low_stock_items": get_low_stock_items,
    "get_expiring_batches": get_expiring_batches,
    "get_sales_summary": get_sales_summary,
    "get_top_selling_items": get_top_selling_items,
    "get_wastage_summary": get_wastage_summary,
    "get_item_forecast": get_item_forecast,
    "search_inventory": search_inventory,
    "list_purchase_orders": list_purchase_orders,
    "create_purchase_order": create_purchase_order,
}


def execute_tool(name, tool_input):
    func_ = TOOL_FUNCTIONS.get(name)
    if func_ is None:
        return {"error": f"Unknown tool '{name}'."}
    try:
        return func_(**(tool_input or {}))
    except Exception as exc:
        return {"error": str(exc)}
