import os
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(BASE_DIR, ".env"))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    # Database: defaults to SQLite (zero setup). Point DATABASE_URL at a MySQL
    # instance for production and nothing else in the app needs to change.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "instance", "pharmacy.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Shop info shown on invoices / reports
    SHOP_NAME = os.environ.get("SHOP_NAME", "City Care Pharmacy")
    SHOP_ADDRESS = os.environ.get("SHOP_ADDRESS", "123 MG Road, Pune, Maharashtra - 411001")
    SHOP_PHONE = os.environ.get("SHOP_PHONE", "+91 98765 43210")
    SHOP_GSTIN = os.environ.get("SHOP_GSTIN", "")

    DEFAULT_ADMIN_USERNAME = os.environ.get("DEFAULT_ADMIN_USERNAME", "admin")
    DEFAULT_ADMIN_PASSWORD = os.environ.get("DEFAULT_ADMIN_PASSWORD", "Admin@12345")

    # AI Assistant (optional) — leave ANTHROPIC_API_KEY blank to disable it.
    # Get a key at https://console.anthropic.com — this is billed separately
    # from anything else in this project.
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB upload cap (for CSV imports)

    # Business defaults used by the Recommendation Engine (EOQ) when an item
    # doesn't specify its own ordering cost / holding cost.
    DEFAULT_ORDERING_COST = 50.0     # cost to place one order (currency units)
    DEFAULT_HOLDING_COST_RATE = 0.18  # 18% of unit price held per year
    LOW_STOCK_ALERT_DAYS = 7
    EXPIRY_ALERT_DAYS = 30
