"""
Recommendation Engine — turns a demand forecast into a concrete restocking
plan using the Economic Order Quantity (EOQ) and Reorder Point formulas
described in the project report.
"""
import math
from flask import current_app


def calculate_recommendation(item, forecast_rows):
    """
    item: Inventory model instance
    forecast_rows: list of {"date": date, "demand": float} from the forecaster

    Returns a dict with eoq, reorder_point, avg_daily_demand, days_of_stock_left,
    should_reorder_now, and a plain-language recommendation string.
    """
    if not forecast_rows:
        raise ValueError("No forecast data to build a recommendation from.")

    avg_daily_demand = sum(r["demand"] for r in forecast_rows) / len(forecast_rows)
    annual_demand = avg_daily_demand * 365

    ordering_cost = float(item.ordering_cost) if item.ordering_cost else \
        current_app.config["DEFAULT_ORDERING_COST"]
    holding_rate = item.holding_cost_rate if item.holding_cost_rate else \
        current_app.config["DEFAULT_HOLDING_COST_RATE"]
    holding_cost_per_unit = float(item.unit_price) * holding_rate

    if holding_cost_per_unit <= 0 or annual_demand <= 0:
        eoq = 0
    else:
        eoq = math.sqrt((2 * annual_demand * ordering_cost) / holding_cost_per_unit)

    lead_time_days = item.supplier.lead_time_days if item.supplier else 7
    reorder_point = (avg_daily_demand * lead_time_days) + item.safety_stock

    days_of_stock_left = (item.current_stock / avg_daily_demand) if avg_daily_demand > 0 else float("inf")
    should_reorder_now = item.current_stock <= reorder_point

    if should_reorder_now:
        message = (
            f"Reorder now: order about {round(eoq)} {item.unit}s. "
            f"At the current pace ({avg_daily_demand:.1f}/day), stock runs out in "
            f"~{days_of_stock_left:.0f} days, and this supplier needs {lead_time_days} days to deliver."
        )
    else:
        message = (
            f"No action needed yet. Stock will stay above the reorder point of "
            f"{round(reorder_point)} {item.unit}s for now. Recommended order size when "
            f"you do reorder: {round(eoq)} {item.unit}s."
        )

    return {
        "avg_daily_demand": round(avg_daily_demand, 2),
        "annual_demand_est": round(annual_demand, 1),
        "eoq": round(eoq),
        "reorder_point": round(reorder_point),
        "lead_time_days": lead_time_days,
        "days_of_stock_left": (round(days_of_stock_left) if days_of_stock_left != float("inf") else None),
        "should_reorder_now": should_reorder_now,
        "message": message,
    }
