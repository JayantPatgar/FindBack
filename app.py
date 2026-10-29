
import os
from datetime import date, datetime
from difflib import SequenceMatcher
from functools import wraps
from urllib.parse import quote_plus

from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, abort
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-only-change-me")

host = os.getenv("MYSQL_HOST")
port = os.getenv("MYSQL_PORT", "3306")
username = os.getenv("MYSQL_USER")
password = os.getenv("MYSQL_PASSWORD")
database = os.getenv("MYSQL_DATABASE")

if not all([host, username, password, database]):
    raise RuntimeError("Please configure MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD and MYSQL_DATABASE in .env")

app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"mysql+pymysql://{quote_plus(username)}:{quote_plus(password)}"
    f"@{host}:{port}/{database}?charset=utf8mb4"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "connect_args": {"ssl": {}}
}

db = SQLAlchemy(app)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    item_name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(60), nullable=False)
    description = db.Column(db.Text, nullable=False)
    location = db.Column(db.String(200), nullable=False)
    report_type = db.Column(db.String(10), nullable=False)
    found_date = db.Column(db.Date, nullable=False)
    image_url = db.Column(db.String(500))
    status = db.Column(db.String(20), default="open", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="items")


class Claim(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    claimant_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    item = db.relationship("Item", backref="claims")
    claimant = db.relationship("User")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def match_score(first, second):
    """Return a simple text similarity score between 0 and 100."""
    a = f"{first.item_name} {first.category} {first.description} {first.location}".lower()
    b = f"{second.item_name} {second.category} {second.description} {second.location}".lower()

    name_score = SequenceMatcher(
        None, first.item_name.lower(), second.item_name.lower()
    ).ratio()

    description_score = SequenceMatcher(None, a, b).ratio()
    category_score = 1 if first.category.lower() == second.category.lower() else 0
    location_score = 1 if first.location.lower() == second.location.lower() else 0

    return round(
        (name_score * 0.45 + description_score * 0.25
         + category_score * 0.20 + location_score * 0.10) * 100
    )


@app.route("/")
def home():
    keyword = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    report_type = request.args.get("type", "").strip()

    query = Item.query.filter_by(status="open")

    if keyword:
        pattern = f"%{keyword}%"
        query = query.filter(
            db.or_(
                Item.item_name.ilike(pattern),
                Item.description.ilike(pattern),
                Item.location.ilike(pattern)
            )
        )

    if category:
        query = query.filter_by(category=category)

    if report_type in ("lost", "found"):
        query = query.filter_by(report_type=report_type)

    items = query.order_by(Item.created_at.desc()).all()

    return render_template(
        "index.html",
        items=items,
        q=keyword,
        category=category,
        report_type=report_type
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or len(password) < 8:
            flash("Enter your name and email. Password must be at least 8 characters.", "danger")
        elif User.query.filter_by(email=email).first():
            flash("That email is already registered.", "danger")
        else:
            user = User(
                name=name,
                email=email,
                password_hash=generate_password_hash(password)
            )
            db.session.add(user)
            db.session.commit()
            flash("Account created. Please log in.", "success")
            return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password_hash, password):
            session.clear()
            session["user_id"] = user.id
            session["user_name"] = user.name
            flash("Welcome to FindBack!", "success")
            return redirect(url_for("home"))

        flash("Invalid email or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have logged out.", "info")
    return redirect(url_for("home"))


@app.route("/report", methods=["GET", "POST"])
@login_required
def report():
    if request.method == "POST":
        item_name = request.form.get("item_name", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        location = request.form.get("location", "").strip()
        report_type = request.form.get("report_type", "").strip()
        date_text = request.form.get("found_date", "").strip()
        image_url = request.form.get("image_url", "").strip() or None

        try:
            item_date = date.fromisoformat(date_text)
        except ValueError:
            item_date = None

        if (
            not all([item_name, category, description, location])
            or report_type not in ("lost", "found")
            or not item_date
        ):
            flash("Complete all required fields and enter a valid date.", "danger")
        elif image_url and not image_url.startswith(("https://", "http://")):
            flash("Image URL must start with http:// or https://.", "danger")
        else:
            item = Item(
                user_id=session["user_id"],
                item_name=item_name,
                category=category,
                description=description,
                location=location,
                report_type=report_type,
                found_date=item_date,
                image_url=image_url
            )
            db.session.add(item)
            db.session.commit()
            flash("Your report has been published.", "success")
            return redirect(url_for("item_detail", item_id=item.id))

    return render_template("report.html", today=date.today().isoformat())


@app.route("/item/<int:item_id>")
def item_detail(item_id):
    item = db.get_or_404(Item, item_id)
    matches = []

    if item.status == "open":
        candidates = Item.query.filter(
            Item.id != item.id,
            Item.status == "open",
            Item.report_type != item.report_type
        ).all()

        for candidate in candidates:
            score = match_score(item, candidate)
            if score >= 25:
                matches.append((candidate, score))

        matches.sort(key=lambda pair: pair[1], reverse=True)
        matches = matches[:5]

    return render_template(
        "item_detail.html", item=item, matches=matches
    )


@app.route("/claim/<int:item_id>", methods=["POST"])
@login_required
def claim_item(item_id):
    item = db.get_or_404(Item, item_id)
    message = request.form.get("message", "").strip()

    if item.status != "open":
        flash("This item is no longer open for claims.", "warning")
    elif item.user_id == session["user_id"]:
        flash("You cannot claim your own report.", "warning")
    elif not message:
        flash("Explain how you can verify ownership.", "danger")
    elif Claim.query.filter_by(
        item_id=item.id,
        claimant_id=session["user_id"],
        status="pending"
    ).first():
        flash("You already have a pending claim for this item.", "info")
    else:
        db.session.add(Claim(
            item_id=item.id,
            claimant_id=session["user_id"],
            message=message
        ))
        db.session.commit()
        flash("Your claim was submitted for review.", "success")

    return redirect(url_for("item_detail", item_id=item.id))


@app.route("/my-reports")
@login_required
def my_reports():
    items = Item.query.filter_by(
        user_id=session["user_id"]
    ).order_by(Item.created_at.desc()).all()

    claims = Claim.query.join(Item).filter(
        Item.user_id == session["user_id"]
    ).order_by(Claim.created_at.desc()).all()

    my_claims = Claim.query.filter_by(
        claimant_id=session["user_id"]
    ).order_by(Claim.created_at.desc()).all()

    return render_template(
        "my_reports.html",
        items=items,
        claims=claims,
        my_claims=my_claims
    )


@app.route("/claim/<int:claim_id>/<action>", methods=["POST"])
@login_required
def review_claim(claim_id, action):
    if action not in ("approve", "reject"):
        abort(404)

    claim = db.get_or_404(Claim, claim_id)

    if claim.item.user_id != session["user_id"]:
        abort(403)

    if claim.status != "pending" or claim.item.status != "open":
        flash("This claim has already been processed.", "warning")
    elif action == "approve":
        claim.status = "approved"
        claim.item.status = "returned"
        db.session.commit()
        flash("Claim approved and item marked as returned.", "success")
    else:
        claim.status = "rejected"
        db.session.commit()
        flash("Claim rejected.", "info")

    return redirect(url_for("my_reports"))


@app.route("/health")
def health():
    return {"status": "ok"}


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=True)
