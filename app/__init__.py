import os
from datetime import datetime, date, timedelta
from flask import Flask, render_template
from flask_login import current_user

from app.config import Config
from app.extensions import db, login_manager


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Make sure the folders SQLite / CSV uploads need actually exist
    db_uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if db_uri.startswith("sqlite:///"):
        db_path = db_uri.replace("sqlite:///", "", 1)
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # ---- Blueprints ----
    from app.auth.routes import auth_bp
    from app.admin.routes import admin_bp
    from app.inventory.routes import inventory_bp
    from app.cashier.routes import cashier_bp
    from app.ai.routes import ai_bp
    from app.customers.routes import customers_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(inventory_bp, url_prefix="/inventory")
    app.register_blueprint(cashier_bp, url_prefix="/cashier")
    app.register_blueprint(ai_bp, url_prefix="/admin/ai")
    app.register_blueprint(customers_bp, url_prefix="/customers")

    # ---- Template helpers ----
    @app.template_filter("currency")
    def currency_filter(value):
        try:
            return f"₹{float(value):,.2f}"
        except (TypeError, ValueError):
            return value

    @app.context_processor
    def inject_globals():
        nav_alerts = {"low_stock": [], "low_stock_count": 0, "expiring": [], "expiring_count": 0}
        if current_user.is_authenticated and current_user.role in ("admin", "inventory_manager"):
            from app.models import Inventory, StockBatch
            low_items = [i for i in Inventory.query.all() if i.is_low_stock]
            horizon = date.today() + timedelta(days=app.config["EXPIRY_ALERT_DAYS"])
            expiring = (StockBatch.query
                        .filter(StockBatch.quantity > 0, StockBatch.expiry_date <= horizon)
                        .order_by(StockBatch.expiry_date.asc()).all())
            nav_alerts = {
                "low_stock": low_items[:5], "low_stock_count": len(low_items),
                "expiring": expiring[:5], "expiring_count": len(expiring),
            }
        return {
            "shop_name": app.config["SHOP_NAME"],
            "shop_address": app.config["SHOP_ADDRESS"],
            "current_year": datetime.utcnow().year,
            "nav_alerts": nav_alerts,
        }

    # ---- Error handlers ----
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    # ---- Root route ----
    @app.route("/")
    def index():
        from flask import redirect, url_for
        if current_user.is_authenticated:
            if current_user.role == "admin":
                return redirect(url_for("admin.dashboard"))
            elif current_user.role == "inventory_manager":
                return redirect(url_for("inventory.list_items"))
            else:
                return redirect(url_for("cashier.billing"))
        return redirect(url_for("auth.login"))

    with app.app_context():
        db.create_all()
        _seed_default_admin(app)

    return app


def _seed_default_admin(app):
    from app.models import User, Role
    if User.query.filter_by(role=Role.ADMIN).first() is None:
        admin = User(
            username=app.config["DEFAULT_ADMIN_USERNAME"],
            full_name="System Administrator",
            role=Role.ADMIN,
        )
        admin.set_password(app.config["DEFAULT_ADMIN_PASSWORD"])
        db.session.add(admin)
        db.session.commit()
        app.logger.info(
            "Created default admin user '%s' — please change the password after logging in.",
            app.config["DEFAULT_ADMIN_USERNAME"],
        )
