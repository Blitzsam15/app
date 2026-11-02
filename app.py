import json
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path

import click
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
ALLOWED_PRODUCT_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
ALLOWED_RECEIPT_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
PRODUCT_CATEGORIES = ("Tops", "Trousers", "Underwear", "Others")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "replace-this-before-deployment"),
        DATABASE=os.environ.get(
            "DATABASE_PATH", str(Path(app.instance_path) / "shop.sqlite3")
        ),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "static" / "uploads" / "products").mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "instance" / "uploads" / "receipts").mkdir(parents=True, exist_ok=True)
    # Create the schema at startup; subsequent starts preserve existing shop data.
    initialize_database(app)

    @app.before_request
    def ensure_csrf_token():
        # Protect both HTML forms and the storefront's fetch-based order form.
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_urlsafe(32)
        if request.method == "POST":
            supplied_token = request.form.get("csrf_token") or request.headers.get(
                "X-CSRF-Token"
            )
            if not supplied_token or not secrets.compare_digest(
                supplied_token, session["_csrf_token"]
            ):
                abort(400, description="Your session expired. Please refresh and try again.")

    @app.teardown_appcontext
    def close_database(_error=None):
        database = g.pop("database", None)
        if database is not None:
            database.close()

    def admin_required(view):
        @wraps(view)
        def wrapped_view(**kwargs):
            if not session.get("admin_id"):
                return redirect(url_for("admin_login"))
            return view(**kwargs)

        return wrapped_view

    @app.template_filter("naira")
    def naira(value):
        return f"₦{int(value):,}"

    @app.context_processor
    def inject_csrf_token():
        return {
            "csrf_token": session.get("_csrf_token", ""),
            "current_year": datetime.now().year,
        }

    @app.get("/")
    def index():
        products = get_db().execute(
            "SELECT * FROM products ORDER BY created_at DESC, id DESC"
        ).fetchall()
        settings = get_settings()
        return render_template(
            "index.html",
            products=products,
            categories=PRODUCT_CATEGORIES,
            settings=settings,
        )

    @app.post("/orders")
    def create_order():
        buyer_name = request.form.get("buyer_name", "").strip()
        phone = request.form.get("phone", "").strip()
        item_type = request.form.get("item_type", "")
        quantity_raw = request.form.get("quantity", "")
        try:
            quantity = int(quantity_raw)
        except (TypeError, ValueError):
            return jsonify(error="Enter a valid quantity."), 400

        if not buyer_name or len(buyer_name) > 100:
            return jsonify(error="Enter your name (up to 100 characters)."), 400
        if not phone or len(phone) > 30 or not re.fullmatch(r"[+()\d\s-]+", phone):
            return jsonify(error="Enter a valid phone number."), 400
        if not 1 <= quantity <= 100:
            return jsonify(error="Quantity must be between 1 and 100."), 400

        settings = get_settings()
        if not all(
            settings[key].strip()
            for key in ("bank_name", "account_number", "account_name")
        ):
            return jsonify(
                error="This shop is still setting up its payment details. Please try again later."
            ), 503

        database = get_db()
        if item_type == "eggs":
            item_name = "Fresh farm eggs"
            unit_price = int(settings["egg_price"])
        elif item_type == "product":
            try:
                product_id = int(request.form.get("product_id", ""))
            except (TypeError, ValueError):
                return jsonify(error="That product could not be found."), 404
            product = database.execute(
                "SELECT id, name, price FROM products WHERE id = ?", (product_id,)
            ).fetchone()
            if product is None:
                return jsonify(error="That product is no longer available."), 404
            item_name = product["name"]
            unit_price = product["price"]
        else:
            return jsonify(error="Choose an item before ordering."), 400

        total = unit_price * quantity
        items = [{"name": item_name, "quantity": quantity, "unit_price": unit_price}]
        cursor = database.execute(
            """INSERT INTO orders
               (buyer_name, phone, items, total, status, created_at)
               VALUES (?, ?, ?, ?, 'Pending', ?)""",
            (
                buyer_name,
                phone,
                json.dumps(items, ensure_ascii=False),
                total,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        database.commit()
        return jsonify(
            order_id=cursor.lastrowid,
            buyer_name=buyer_name,
            phone=phone,
            item_name=item_name,
            quantity=quantity,
            total=total,
            bank_name=settings["bank_name"],
            account_number=settings["account_number"],
            account_name=settings["account_name"],
            whatsapp=settings["whatsapp"],
        ), 201

    @app.post("/orders/<int:order_id>/receipt")
    def upload_receipt(order_id):
        order = get_db().execute(
            "SELECT id FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if order is None:
            abort(404)
        uploaded_file = request.files.get("receipt")
        if not uploaded_file or not uploaded_file.filename:
            flash("Choose a receipt image to upload.", "error")
            return redirect(url_for("index"))
        extension = uploaded_file.filename.rsplit(".", 1)[-1].lower()
        if extension not in ALLOWED_RECEIPT_EXTENSIONS:
            flash("Receipts must be JPG, PNG, or WebP images.", "error")
            return redirect(url_for("index"))
        filename = f"{uuid.uuid4().hex}.{extension}"
        # Keep payment screenshots outside /static so only admins can view them.
        destination = BASE_DIR / "instance" / "uploads" / "receipts" / filename
        uploaded_file.save(destination)
        get_db().execute(
            "UPDATE orders SET receipt_image = ? WHERE id = ?",
            (filename, order_id),
        )
        get_db().commit()
        flash("Receipt received. Your order is pending confirmation.", "success")
        return redirect(url_for("index"))

    @app.route("/admin/login", methods=("GET", "POST"))
    def admin_login():
        if session.get("admin_id"):
            return redirect(url_for("admin_dashboard"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            admin = get_db().execute(
                "SELECT id, username, password_hash FROM admins WHERE username = ?",
                (username,),
            ).fetchone()
            if admin and check_password_hash(admin["password_hash"], password):
                session.clear()
                session["admin_id"] = admin["id"]
                session["_csrf_token"] = secrets.token_urlsafe(32)
                return redirect(url_for("admin_dashboard"))
            flash("Incorrect username or password.", "error")
        return render_template("admin_login.html")

    @app.post("/admin/logout")
    @admin_required
    def admin_logout():
        session.clear()
        return redirect(url_for("admin_login"))

    @app.post("/admin/password")
    @admin_required
    def change_admin_password():
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        admin = get_db().execute(
            "SELECT password_hash FROM admins WHERE id = ?",
            (session["admin_id"],),
        ).fetchone()
        if admin is None:
            session.clear()
            flash("Please sign in again before changing your password.", "error")
            return redirect(url_for("admin_login"))
        if not check_password_hash(admin["password_hash"], current_password):
            flash("Your current password is incorrect.", "error")
            return redirect(url_for("admin_dashboard"))
        if not 12 <= len(new_password) <= 256:
            flash("Your new password must be between 12 and 256 characters.", "error")
            return redirect(url_for("admin_dashboard"))
        if new_password != confirm_password:
            flash("The new password and confirmation do not match.", "error")
            return redirect(url_for("admin_dashboard"))

        database = get_db()
        database.execute(
            "UPDATE admins SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), session["admin_id"]),
        )
        database.commit()
        flash("Your password has been changed.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.get("/admin/receipts/<path:filename>")
    @admin_required
    def admin_receipt(filename):
        receipt_directory = BASE_DIR / "instance" / "uploads" / "receipts"
        return send_from_directory(receipt_directory, filename)

    @app.get("/admin/receipt-notifications")
    @admin_required
    def receipt_notifications():
        receipts = get_db().execute(
            """SELECT id AS order_id, buyer_name, receipt_image
               FROM orders
               WHERE receipt_image IS NOT NULL
               ORDER BY id DESC"""
        ).fetchall()
        return jsonify(
            receipts=[
                {
                    "order_id": row["order_id"],
                    "buyer_name": row["buyer_name"],
                    "receipt_image": row["receipt_image"],
                }
                for row in receipts
            ]
        )

    @app.get("/admin")
    @admin_required
    def admin_dashboard():
        database = get_db()
        products = database.execute(
            "SELECT * FROM products ORDER BY created_at DESC, id DESC"
        ).fetchall()
        order_rows = database.execute(
            "SELECT * FROM orders ORDER BY created_at DESC, id DESC"
        ).fetchall()
        orders = []
        for row in order_rows:
            order = dict(row)
            order["items"] = json.loads(order["items"])
            orders.append(order)
        return render_template(
            "admin_dashboard.html",
            products=products,
            categories=PRODUCT_CATEGORIES,
            orders=orders,
            settings=get_settings(),
        )

    @app.post("/admin/products")
    @admin_required
    def add_product():
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "")
        try:
            price = int(request.form.get("price", ""))
        except (TypeError, ValueError):
            flash("Enter a valid whole-naira price.", "error")
            return redirect(url_for("admin_dashboard"))
        photo = request.files.get("photo")
        if category not in PRODUCT_CATEGORIES:
            flash("Choose a valid product category.", "error")
            return redirect(url_for("admin_dashboard"))
        if not name or len(name) > 100 or price < 1:
            flash("Enter a product name and a price greater than zero.", "error")
            return redirect(url_for("admin_dashboard"))
        if not photo or not photo.filename:
            flash("Choose a product photo.", "error")
            return redirect(url_for("admin_dashboard"))
        extension = photo.filename.rsplit(".", 1)[-1].lower()
        if extension not in ALLOWED_PRODUCT_EXTENSIONS:
            flash("Product photos must be JPG, PNG, or WebP images.", "error")
            return redirect(url_for("admin_dashboard"))
        filename = f"{uuid.uuid4().hex}.{extension}"
        photo.save(BASE_DIR / "static" / "uploads" / "products" / filename)
        get_db().execute(
            """INSERT INTO products (name, price, photo, created_at, category)
               VALUES (?, ?, ?, ?, ?)""",
            (
                name,
                price,
                f"uploads/products/{filename}",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                category,
            ),
        )
        get_db().commit()
        flash(f"{name} has been added to the shop.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/products/<int:product_id>/edit")
    @admin_required
    def edit_product(product_id):
        database = get_db()
        product = database.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if product is None:
            abort(404)
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "")
        try:
            price = int(request.form.get("price", ""))
        except (TypeError, ValueError):
            flash("Enter a valid whole-naira price.", "error")
            return redirect(url_for("admin_dashboard"))
        if category not in PRODUCT_CATEGORIES:
            flash("Choose a valid product category.", "error")
            return redirect(url_for("admin_dashboard"))
        if not name or len(name) > 100 or price < 1:
            flash("Enter a product name and a price greater than zero.", "error")
            return redirect(url_for("admin_dashboard"))
        database.execute(
            "UPDATE products SET name = ?, price = ?, category = ? WHERE id = ?",
            (name, price, category, product_id),
        )
        database.commit()
        flash("Product details updated.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/products/<int:product_id>/delete")
    @admin_required
    def delete_product(product_id):
        database = get_db()
        product = database.execute(
            "SELECT photo FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if product is None:
            abort(404)
        database.execute("DELETE FROM products WHERE id = ?", (product_id,))
        database.commit()
        photo_path = BASE_DIR / "static" / product["photo"]
        if photo_path.is_file():
            photo_path.unlink()
        flash("Product deleted.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/settings")
    @admin_required
    def update_settings():
        try:
            egg_price = int(request.form.get("egg_price", ""))
        except (TypeError, ValueError):
            flash("Enter a valid whole-naira egg price.", "error")
            return redirect(url_for("admin_dashboard"))
        bank_name = request.form.get("bank_name", "").strip()[:100]
        account_number = request.form.get("account_number", "").strip()[:40]
        account_name = request.form.get("account_name", "").strip()[:100]
        whatsapp = re.sub(r"\D", "", request.form.get("whatsapp", ""))[:20]
        if egg_price < 1 or not bank_name or not account_number or not account_name:
            flash("Set the egg price and complete all bank account fields.", "error")
            return redirect(url_for("admin_dashboard"))
        database = get_db()
        for key, value in {
            "egg_price": str(egg_price),
            "bank_name": bank_name,
            "account_number": account_number,
            "account_name": account_name,
            "whatsapp": whatsapp,
        }.items():
            database.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        database.commit()
        flash("Shop and payment details saved.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.post("/admin/orders/<int:order_id>/paid")
    @admin_required
    def mark_order_paid(order_id):
        cursor = get_db().execute(
            "UPDATE orders SET status = 'Paid' WHERE id = ?", (order_id,)
        )
        get_db().commit()
        if cursor.rowcount == 0:
            abort(404)
        flash("Order marked as paid.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.cli.command("create-admin")
    @click.argument("username")
    @click.password_option()
    def create_admin(username, password):
        """Create or update an administrator login."""
        username = username.strip()
        if not username or len(username) > 80:
            raise click.ClickException("Username must be between 1 and 80 characters.")
        if len(password) < 12:
            raise click.ClickException("Use a password with at least 12 characters.")
        database = get_db()
        database.execute(
            "INSERT INTO admins (username, password_hash) VALUES (?, ?) "
            "ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash",
            (username, generate_password_hash(password)),
        )
        database.commit()
        click.echo(f"Administrator '{username}' is ready.")

    return app


def get_db():
    if "database" not in g:
        database_path = Path(current_app.config["DATABASE"])
        database_path.parent.mkdir(parents=True, exist_ok=True)
        g.database = sqlite3.connect(database_path)
        g.database.row_factory = sqlite3.Row
        g.database.execute("PRAGMA foreign_keys = ON")
    return g.database


def get_settings():
    defaults = {
        "egg_price": "3500",
        "bank_name": "",
        "account_number": "",
        "account_name": "",
        "whatsapp": "",
    }
    rows = get_db().execute("SELECT key, value FROM settings").fetchall()
    defaults.update({row["key"]: row["value"] for row in rows})
    return defaults


def initialize_database(app):
    database_path = Path(app.config["DATABASE"])
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as database:
        database.executescript(
            """
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price INTEGER NOT NULL CHECK (price > 0),
                photo TEXT NOT NULL,
                created_at TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'Others'
                    CHECK (category IN ('Tops', 'Trousers', 'Underwear', 'Others'))
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                buyer_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                items TEXT NOT NULL,
                total INTEGER NOT NULL CHECK (total > 0),
                receipt_image TEXT,
                status TEXT NOT NULL DEFAULT 'Pending'
                    CHECK (status IN ('Pending', 'Paid')),
                created_at TEXT NOT NULL
            );
            """
        )
        product_columns = {
            row[1] for row in database.execute("PRAGMA table_info(products)")
        }
        if "category" not in product_columns:
            database.execute(
                """ALTER TABLE products
                   ADD COLUMN category TEXT NOT NULL DEFAULT 'Others'
                   CHECK (category IN ('Tops', 'Trousers', 'Underwear', 'Others'))"""
            )
        database.executemany(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            [
                ("egg_price", "3500"),
                ("bank_name", ""),
                ("account_number", ""),
                ("account_name", ""),
                ("whatsapp", ""),
            ],
        )


app = create_app()


if __name__ == "__main__":
    app.run()
