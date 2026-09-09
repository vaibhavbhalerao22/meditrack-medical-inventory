import os
import uuid
from datetime import datetime

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, current_app, send_from_directory, abort)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import Customer, Prescription, Bill, Role
from app.decorators import roles_required

customers_bp = Blueprint("customers", __name__, template_folder="../templates/customers")

ALLOWED_ROLES = (Role.ADMIN, Role.CASHIER)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "pdf", "webp"}


def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@customers_bp.route("/")
@login_required
@roles_required(*ALLOWED_ROLES)
def list_customers():
    q = request.args.get("q", "").strip()
    query = Customer.query
    if q:
        query = query.filter(
            db.or_(Customer.name.ilike(f"%{q}%"), Customer.phone.ilike(f"%{q}%"))
        )
    customers = query.order_by(Customer.name.asc()).all()
    return render_template("customers/list.html", customers=customers, q=q)


@customers_bp.route("/new", methods=["GET", "POST"])
@login_required
@roles_required(*ALLOWED_ROLES)
def new_customer():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        notes = request.form.get("notes", "").strip()

        if not name or not phone:
            flash("Name and phone number are required.", "error")
            return redirect(url_for("customers.new_customer"))

        if Customer.query.filter_by(phone=phone).first():
            flash(f"A customer with phone {phone} already exists.", "error")
            return redirect(url_for("customers.new_customer"))

        customer = Customer(name=name, phone=phone, address=address or None, notes=notes or None)
        db.session.add(customer)
        db.session.commit()
        flash(f"Customer '{name}' added.", "success")
        return redirect(url_for("customers.profile", customer_id=customer.id))

    return render_template("customers/new.html")


@customers_bp.route("/<int:customer_id>")
@login_required
@roles_required(*ALLOWED_ROLES)
def profile(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    bills = (Bill.query.filter_by(customer_phone=customer.phone)
             .order_by(Bill.created_on.desc()).all())
    total_spent = sum(float(b.total) for b in bills)
    prescriptions = customer.prescriptions.order_by(Prescription.uploaded_on.desc()).all()
    return render_template("customers/profile.html", customer=customer, bills=bills,
                            total_spent=total_spent, prescriptions=prescriptions)


@customers_bp.route("/<int:customer_id>/prescription", methods=["POST"])
@login_required
@roles_required(*ALLOWED_ROLES)
def upload_prescription(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose an image or PDF to upload.", "error")
        return redirect(url_for("customers.profile", customer_id=customer.id))

    if not _allowed_file(file.filename):
        flash("Only PNG, JPG, WEBP, or PDF files are allowed.", "error")
        return redirect(url_for("customers.profile", customer_id=customer.id))

    ext = file.filename.rsplit(".", 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "prescriptions", str(customer.id))
    os.makedirs(folder, exist_ok=True)
    file.save(os.path.join(folder, unique_name))

    rel_path = os.path.join("prescriptions", str(customer.id), unique_name)
    db.session.add(Prescription(
        customer_id=customer.id,
        file_path=rel_path,
        original_filename=secure_filename(file.filename),
        notes=request.form.get("notes", "").strip() or None,
        uploaded_by=current_user.id,
    ))
    db.session.commit()
    flash("Prescription uploaded.", "success")
    return redirect(url_for("customers.profile", customer_id=customer.id))


@customers_bp.route("/<int:customer_id>/prescription/<int:prescription_id>/file")
@login_required
@roles_required(*ALLOWED_ROLES)
def prescription_file(customer_id, prescription_id):
    p = Prescription.query.get_or_404(prescription_id)
    if p.customer_id != customer_id:
        abort(404)
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], os.path.dirname(p.file_path))
    filename = os.path.basename(p.file_path)
    return send_from_directory(folder, filename)


@customers_bp.route("/<int:customer_id>/prescription/<int:prescription_id>/delete", methods=["POST"])
@login_required
@roles_required(*ALLOWED_ROLES)
def delete_prescription(customer_id, prescription_id):
    p = Prescription.query.get_or_404(prescription_id)
    if p.customer_id != customer_id:
        abort(404)
    full_path = os.path.join(current_app.config["UPLOAD_FOLDER"], p.file_path)
    if os.path.exists(full_path):
        os.remove(full_path)
    db.session.delete(p)
    db.session.commit()
    flash("Prescription deleted.", "info")
    return redirect(url_for("customers.profile", customer_id=customer_id))
