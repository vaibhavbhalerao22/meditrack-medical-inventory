from datetime import datetime, date
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db

# ---------------------------------------------------------------------------
# Users & roles
# ---------------------------------------------------------------------------

class Role:
    ADMIN = "admin"
    INVENTORY_MANAGER = "inventory_manager"
    CASHIER = "cashier"

    LABELS = {
        ADMIN: "Administrator",
        INVENTORY_MANAGER: "Inventory Manager",
        CASHIER: "Cashier",
    }


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default=Role.CASHIER)
    is_active_user = db.Column(db.Boolean, default=True)
    created_on = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    bills = db.relationship("Bill", backref="cashier", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def role_label(self):
        return Role.LABELS.get(self.role, self.role)

    # Flask-Login expects `is_active`
    @property
    def is_active(self):
        return self.is_active_user

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------

class Supplier(db.Model):
    __tablename__ = "suppliers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    contact_person = db.Column(db.String(150))
    phone = db.Column(db.String(30))
    email = db.Column(db.String(150))
    address = db.Column(db.String(300))
    lead_time_days = db.Column(db.Integer, default=7)  # avg days to deliver

    items = db.relationship("Inventory", backref="supplier", lazy="dynamic")

    def __repr__(self):
        return f"<Supplier {self.name}>"


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

class Inventory(db.Model):
    __tablename__ = "inventory"

    id = db.Column(db.Integer, primary_key=True)
    item_name = db.Column(db.String(200), nullable=False, index=True)
    generic_name = db.Column(db.String(200), nullable=True, index=True)  # salt/composition, e.g. "Paracetamol"
    strength = db.Column(db.String(50), nullable=True)  # e.g. "500mg"
    category = db.Column(db.String(100), nullable=False)
    unit = db.Column(db.String(30), default="strip")  # strip, bottle, box, tablet...
    current_stock = db.Column(db.Integer, nullable=False, default=0)
    reorder_level = db.Column(db.Integer, nullable=False, default=10)
    safety_stock = db.Column(db.Integer, nullable=False, default=5)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    cost_price = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    ordering_cost = db.Column(db.Numeric(10, 2), nullable=True)   # overrides default if set
    holding_cost_rate = db.Column(db.Float, nullable=True)        # overrides default if set
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=True)
    created_on = db.Column(db.DateTime, default=datetime.utcnow)
    updated_on = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    batches = db.relationship("StockBatch", backref="item", lazy="dynamic",
                               cascade="all, delete-orphan")
    sales = db.relationship("SalesHistory", backref="item", lazy="dynamic")
    forecasts = db.relationship("ForecastResult", backref="item", lazy="dynamic")

    @property
    def is_low_stock(self):
        return self.current_stock <= self.reorder_level

    @property
    def nearest_expiry(self):
        batch = (self.batches
                 .filter(StockBatch.quantity > 0)
                 .order_by(StockBatch.expiry_date.asc())
                 .first())
        return batch.expiry_date if batch else None

    def __repr__(self):
        return f"<Inventory {self.item_name}>"


class StockBatch(db.Model):
    """A received batch of a medicine — lets us track expiry (FEFO)."""
    __tablename__ = "stock_batches"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("inventory.id"), nullable=False)
    batch_no = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    expiry_date = db.Column(db.Date, nullable=False)
    received_date = db.Column(db.Date, default=date.today)

    @property
    def days_to_expiry(self):
        return (self.expiry_date - date.today()).days

    def __repr__(self):
        return f"<Batch {self.batch_no} exp {self.expiry_date}>"


class WriteOff(db.Model):
    """A formal record of stock removed as a loss (expired/damaged), so the
    wastage report reflects real deductions rather than just a snapshot."""
    __tablename__ = "write_offs"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("inventory.id"), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey("stock_batches.id"), nullable=True)
    quantity = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(50), nullable=False, default="Expired")  # Expired / Damaged / Other
    unit_cost = db.Column(db.Numeric(10, 2), nullable=False)  # cost price at time of write-off
    total_loss = db.Column(db.Numeric(10, 2), nullable=False)
    notes = db.Column(db.String(300), nullable=True)
    written_off_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    written_off_on = db.Column(db.DateTime, default=datetime.utcnow)

    item = db.relationship("Inventory")
    batch = db.relationship("StockBatch")
    user = db.relationship("User")

    def __repr__(self):
        return f"<WriteOff item={self.item_id} qty={self.quantity} loss={self.total_loss}>"


# ---------------------------------------------------------------------------
# Sales history (feeds the forecasting engine)
# ---------------------------------------------------------------------------

class SalesHistory(db.Model):
    __tablename__ = "sales_history"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("inventory.id"), nullable=False)
    sale_date = db.Column(db.Date, nullable=False, index=True)
    quantity_sold = db.Column(db.Integer, nullable=False)
    remaining_stock = db.Column(db.Integer, nullable=True)
    source = db.Column(db.String(20), default="pos")  # 'pos' (live bill) or 'import' (csv)

    def __repr__(self):
        return f"<Sale item={self.item_id} {self.sale_date} qty={self.quantity_sold}>"


# ---------------------------------------------------------------------------
# Forecast results (written by the ARIMA + LSTM hybrid pipeline)
# ---------------------------------------------------------------------------

class ForecastResult(db.Model):
    __tablename__ = "forecast_results"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("inventory.id"), nullable=False)
    forecast_date = db.Column(db.Date, nullable=False)
    predicted_demand = db.Column(db.Numeric(10, 2), nullable=False)
    model_used = db.Column(db.String(50), nullable=False, default="Hybrid")  # Hybrid/ARIMA/LSTM
    rmse = db.Column(db.Float, nullable=True)
    mae = db.Column(db.Float, nullable=True)
    generated_on = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Forecast item={self.item_id} {self.forecast_date} qty={self.predicted_demand}>"


class ForecastValidation(db.Model):
    """Predicted-vs-actual pairs from the held-out validation split computed each
    time a forecast is (re)run for an item. This is what the Forecast Accuracy
    dashboard charts and computes MAE/RMSE/MAPE from — separate from
    ForecastResult, which only holds the future-looking forecast."""
    __tablename__ = "forecast_validations"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("inventory.id"), nullable=False)
    eval_date = db.Column(db.Date, nullable=False)
    actual_demand = db.Column(db.Float, nullable=False)
    predicted_demand = db.Column(db.Float, nullable=False)
    model_used = db.Column(db.String(50), nullable=False, default="Hybrid")
    generated_on = db.Column(db.DateTime, default=datetime.utcnow)

    item = db.relationship("Inventory")

    def __repr__(self):
        return f"<ForecastValidation item={self.item_id} {self.eval_date}>"


# ---------------------------------------------------------------------------
# Billing (Cashier module)
# ---------------------------------------------------------------------------

class Bill(db.Model):
    __tablename__ = "bills"

    id = db.Column(db.Integer, primary_key=True)
    invoice_no = db.Column(db.String(40), unique=True, nullable=False)
    cashier_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    customer_name = db.Column(db.String(150), default="Walk-in Customer")
    customer_phone = db.Column(db.String(30))
    subtotal = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    discount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    tax = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    payment_mode = db.Column(db.String(20), default="Cash")  # Cash / UPI / Card
    created_on = db.Column(db.DateTime, default=datetime.utcnow)

    line_items = db.relationship("BillItem", backref="bill", lazy="dynamic",
                                  cascade="all, delete-orphan")

    @property
    def total_refunded(self):
        total = 0
        for li in self.line_items:
            for r in li.returns:
                total += float(r.refund_amount)
        return total

    def __repr__(self):
        return f"<Bill {self.invoice_no} total={self.total}>"


class BillItem(db.Model):
    __tablename__ = "bill_items"

    id = db.Column(db.Integer, primary_key=True)
    bill_id = db.Column(db.Integer, db.ForeignKey("bills.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("inventory.id"), nullable=False)
    item_name_snapshot = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    line_total = db.Column(db.Numeric(10, 2), nullable=False)
    returned_quantity = db.Column(db.Integer, nullable=False, default=0)

    item = db.relationship("Inventory")

    @property
    def returnable_quantity(self):
        return self.quantity - self.returned_quantity

    def __repr__(self):
        return f"<BillItem {self.item_name_snapshot} qty={self.quantity}>"


# ---------------------------------------------------------------------------
# Returns / refunds
# ---------------------------------------------------------------------------

class Return(db.Model):
    """A partial or full return against one bill line item. Puts the returned
    quantity back into inventory stock and records a refund amount. Kept as a
    separate audit trail rather than mutating the original Bill, so the bill
    always reflects what was actually sold at the time."""
    __tablename__ = "returns"

    id = db.Column(db.Integer, primary_key=True)
    bill_item_id = db.Column(db.Integer, db.ForeignKey("bill_items.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.String(50), nullable=False, default="Customer return")
    refund_amount = db.Column(db.Numeric(10, 2), nullable=False)
    processed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    processed_on = db.Column(db.DateTime, default=datetime.utcnow)

    bill_item = db.relationship("BillItem", backref=db.backref("returns", lazy="dynamic"))
    user = db.relationship("User")

    def __repr__(self):
        return f"<Return bill_item={self.bill_item_id} qty={self.quantity} refund={self.refund_amount}>"


# ---------------------------------------------------------------------------
# Purchase orders (restocking workflow — can be created manually or by the
# AI assistant)
# ---------------------------------------------------------------------------

class PurchaseOrder(db.Model):
    __tablename__ = "purchase_orders"

    STATUS_PENDING = "Pending"
    STATUS_RECEIVED = "Received"
    STATUS_CANCELLED = "Cancelled"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("inventory.id"), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=True)
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    notes = db.Column(db.String(300), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_on = db.Column(db.DateTime, default=datetime.utcnow)
    created_via = db.Column(db.String(20), nullable=False, default="manual")  # 'manual' or 'ai_assistant'
    received_on = db.Column(db.DateTime, nullable=True)

    item = db.relationship("Inventory")
    supplier = db.relationship("Supplier")
    creator = db.relationship("User")

    def __repr__(self):
        return f"<PurchaseOrder item={self.item_id} qty={self.quantity} status={self.status}>"


# ---------------------------------------------------------------------------
# Customer accounts (purchase history is matched by phone number against
# Bill.customer_phone rather than a hard FK, so this doesn't require any
# change to the existing billing flow)
# ---------------------------------------------------------------------------

class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(30), unique=True, nullable=False, index=True)
    address = db.Column(db.String(300), nullable=True)
    notes = db.Column(db.String(300), nullable=True)
    created_on = db.Column(db.DateTime, default=datetime.utcnow)

    prescriptions = db.relationship("Prescription", backref="customer", lazy="dynamic",
                                     cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Customer {self.name} {self.phone}>"


class Prescription(db.Model):
    __tablename__ = "prescriptions"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    file_path = db.Column(db.String(400), nullable=False)  # relative path under UPLOAD_FOLDER
    original_filename = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.String(300), nullable=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    uploaded_on = db.Column(db.DateTime, default=datetime.utcnow)

    uploader = db.relationship("User")

    def __repr__(self):
        return f"<Prescription customer={self.customer_id} file={self.file_path}>"
