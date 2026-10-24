from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from io import StringIO, BytesIO
import csv
import pandas as pd
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

from database import Base, engine, SessionLocal
from models import Device, Location, User


app = Flask(__name__)
app.secret_key = "change-me"  # replace with env var in production


# Create tables if they do not exist
Base.metadata.create_all(bind=engine)


def ensure_default_users() -> None:
    """Create initial users if none exist.

    - it_manager (role=manager)
    - inventory_manager (role=manager)
    - inventory (role=viewer)
    """

    with SessionLocal() as db:
        existing = {
            u.username
            for u in db.scalars(select(User)).all()
        }

        desired = [
            ("manager@it.com", "IT Manager", "manager", "maurinho"),
            ("inventory@it.com", "Inventory Manager", "manager", "esko1"),
            ("user1@it.com", "Inventory Read-Only", "viewer", "esko1"),
        ]

        new_users = []
        for username, full_name, role, pw in desired:
            if username in existing:
                continue
            new_users.append(
                User(
                    username=username,
                    full_name=full_name,
                    role=role,
                    password_hash=generate_password_hash(pw),
                )
            )

        if new_users:
            db.add_all(new_users)
            db.commit()


ensure_default_users()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def manager_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session or session.get("role") != "manager":
            flash("You are not allowed to perform this action.", "danger")
            return redirect(url_for("list_devices"))
        return view(*args, **kwargs)

    return wrapped


def get_current_user(db):
    """Return the currently logged-in User instance or None."""

    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.get(User, user_id)


@app.context_processor
def inject_current_user():
    return {
        "current_username": session.get("username"),
        "current_user_role": session.get("role"),
    }


@app.route("/")
@app.route("/devices")
@login_required
def list_devices():
    device_type = request.args.get("type")
    status = request.args.get("status")
    location_id = request.args.get("location_id")
    search = request.args.get("search")

    with SessionLocal() as db:
        stmt = select(Device).options(selectinload(Device.location)).order_by(Device.name)

        if device_type:
            stmt = stmt.where(Device.device_type == device_type)
        if status:
            stmt = stmt.where(Device.status == status)
        if location_id:
            stmt = stmt.where(Device.location_id == int(location_id))
        if search:
            like_pattern = f"%{search}%"
            stmt = stmt.where(
                (Device.name.ilike(like_pattern))
                | (Device.serial_number.ilike(like_pattern))
                | (Device.barcode.ilike(like_pattern))
            )

        devices = db.scalars(stmt).all()
        locations = db.scalars(
            select(Location).order_by(Location.name)
        ).all()

    device_types = sorted({d.device_type for d in devices})
    statuses = ["Active", "In Repair", "Retired", "Loaned"]

    return render_template(
        "devices_list.html",
        devices=devices,
        locations=locations,
        device_types=device_types,
        statuses=statuses,
        current_filters={
            "device_type": device_type,
            "status": status,
            "location_id": location_id,
            "search": search,
        },
    )


@app.route("/devices/new", methods=["GET", "POST"])
@login_required
@manager_required
def create_device():
    with SessionLocal() as db:
        locations = db.scalars(select(Location).order_by(Location.name)).all()

        if request.method == "POST":
            name = request.form.get("name")
            device_type = request.form.get("device_type")
            serial_number = request.form.get("serial_number")
            barcode = request.form.get("barcode")
            status = request.form.get("status") or "Active"
            location_id = request.form.get("location_id") or None
            notes = request.form.get("notes")

            if not name or not device_type:
                flash("Name and type are required.", "danger")
                return render_template("device_form.html", locations=locations)

            device = Device(
                name=name,
                device_type=device_type,
                serial_number=serial_number or None,
                barcode=barcode or None,
                status=status,
                location_id=int(location_id) if location_id else None,
                notes=notes or None,
            )
            db.add(device)
            db.commit()
            flash("Device created successfully.", "success")
            return redirect(url_for("list_devices"))

    statuses = ["Active", "In Repair", "Retired", "Loaned"]
    return render_template("device_form.html", locations=locations, statuses=statuses)


@app.route("/devices/<int:device_id>/edit", methods=["GET", "POST"])
@login_required
@manager_required
def edit_device(device_id: int):
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        if not device:
            flash("Device not found.", "danger")
            return redirect(url_for("list_devices"))

        locations = db.scalars(select(Location).order_by(Location.name)).all()

        if request.method == "POST":
            device.name = request.form.get("name")
            device.device_type = request.form.get("device_type")
            device.serial_number = request.form.get("serial_number") or None
            device.barcode = request.form.get("barcode") or None
            device.status = request.form.get("status") or "Active"
            location_id = request.form.get("location_id") or None
            device.location_id = int(location_id) if location_id else None
            device.notes = request.form.get("notes") or None

            if not device.name or not device.device_type:
                flash("Name and type are required.", "danger")
                return render_template(
                    "device_form.html",
                    device=device,
                    locations=locations,
                    statuses=["Active", "In Repair", "Retired", "Loaned"],
                )

            db.commit()
            flash("Device updated successfully.", "success")
            return redirect(url_for("list_devices"))

        return render_template(
            "device_form.html",
            device=device,
            locations=locations,
            statuses=["Active", "In Repair", "Retired", "Loaned"],
        )


@app.route("/devices/<int:device_id>/delete", methods=["POST"])
@login_required
@manager_required
def delete_device(device_id: int):
    with SessionLocal() as db:
        device = db.get(Device, device_id)
        if device:
            db.delete(device)
            db.commit()
            flash("Device deleted.", "info")
        else:
            flash("Device not found.", "danger")
    return redirect(url_for("list_devices"))


@app.route("/locations", methods=["GET", "POST"])
@login_required
@manager_required
def manage_locations():
    with SessionLocal() as db:
        if request.method == "POST":
            name = request.form.get("name")
            category = request.form.get("category")
            if not name:
                flash("Location name is required.", "danger")
            else:
                loc = Location(name=name, category=category or None)
                db.add(loc)
                db.commit()
                flash("Location added.", "success")

        locations = db.scalars(
            select(Location).options(selectinload(Location.devices)).order_by(Location.name)
        ).all()

    return render_template("locations.html", locations=locations)


@app.route("/locations/<int:location_id>/delete", methods=["POST"])
@login_required
@manager_required
def delete_location(location_id: int):
    """Delete a location only if it has no devices assigned."""

    with SessionLocal() as db:
        location = db.get(Location, location_id)
        if not location:
            flash("Location not found.", "danger")
            return redirect(url_for("manage_locations"))

        # Check if there are any devices still linked to this location
        has_devices = db.scalar(
            select(func.count(Device.id)).where(Device.location_id == location_id)
        )

        if has_devices:
            flash(
                "Cannot delete location that still has devices. "
                "Please transfer or remove devices first.",
                "danger",
            )
            return redirect(url_for("manage_locations"))

        db.delete(location)
        db.commit()
        flash("Location deleted successfully.", "success")

    return redirect(url_for("manage_locations"))


@app.route("/locations/transfer", methods=["GET", "POST"])
@login_required
@manager_required
def transfer_devices_between_locations():
    """Move all devices from one location to another.

    The user selects a source location and a destination location.
    Every device currently assigned to the source will be updated to
    reference the destination location.
    """

    with SessionLocal() as db:
        locations = db.scalars(
            select(Location).order_by(Location.name)
        ).all()

        if request.method == "POST":
            from_location_id = request.form.get("from_location_id")
            to_location_id = request.form.get("to_location_id")

            try:
                from_location_id_int = int(from_location_id) if from_location_id else None
                to_location_id_int = int(to_location_id) if to_location_id else None
            except ValueError:
                flash("Invalid location selection.", "danger")
                return render_template("transfer_devices.html", locations=locations)

            if not from_location_id_int or not to_location_id_int:
                flash("Please select both source and destination locations.", "danger")
                return render_template("transfer_devices.html", locations=locations)

            if from_location_id_int == to_location_id_int:
                flash("Source and destination locations must be different.", "warning")
                return render_template("transfer_devices.html", locations=locations)

            from_location = db.get(Location, from_location_id_int)
            to_location = db.get(Location, to_location_id_int)

            if not from_location or not to_location:
                flash("One of the selected locations does not exist.", "danger")
                return render_template("transfer_devices.html", locations=locations)

            devices = db.scalars(
                select(Device).where(Device.location_id == from_location_id_int)
            ).all()

            for device in devices:
                device.location_id = to_location_id_int

            transferred_count = len(devices)
            db.commit()

            if transferred_count == 0:
                flash(
                    f"No devices found in location '{from_location.name}' to transfer.",
                    "info",
                )
            else:
                flash(
                    f"Transferred {transferred_count} device(s) from "
                    f"'{from_location.name}' to '{to_location.name}'.",
                    "success",
                )

            return redirect(url_for("manage_locations"))

    return render_template("transfer_devices.html", locations=locations)


@app.route("/reports")
@login_required
def reports():
    with SessionLocal() as db:
        # Devices per location
        stmt = (
            select(Location.name, func.count(Device.id))
            .join(Device, isouter=True)
            .group_by(Location.id)
            .order_by(Location.name)
        )
        devices_per_location = db.execute(stmt).all()

        # Total active laptops
        total_active_laptops = db.scalar(
            select(func.count(Device.id)).where(
                Device.device_type.ilike("%laptop%"), Device.status == "Active"
            )
        )

    return render_template(
        "reports.html",
        devices_per_location=devices_per_location,
        total_active_laptops=total_active_laptops or 0,
    )


@app.route("/reports/devices_per_location.csv")
@login_required
def export_devices_per_location_csv():
    with SessionLocal() as db:
        stmt = (
            select(Location.name, func.count(Device.id))
            .join(Device, isouter=True)
            .group_by(Location.id)
            .order_by(Location.name)
        )
        rows = db.execute(stmt).all()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Location", "Device Count"])
    for name, count in rows:
        writer.writerow([name or "(Unassigned)", count])

    # Flask/Werkzeug expects binary files or BytesIO; encode the
    # in-memory text CSV into bytes before sending.
    data = output.getvalue().encode("utf-8")
    buffer = BytesIO(data)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="text/csv",
        as_attachment=True,
        download_name="devices_per_location.csv",
    )


@app.route("/devices/export.csv")
@login_required
@manager_required
def export_all_devices_csv():
    """Download all devices with key fields as CSV."""

    with SessionLocal() as db:
        devices = db.scalars(
            select(Device).options(selectinload(Device.location)).order_by(Device.name)
        ).all()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Name",
        "Type",
        "Serial Number",
        "Barcode",
        "Status",
        "Location",
        "Notes",
    ])
    for d in devices:
        writer.writerow([
            d.name,
            d.device_type,
            d.serial_number or "",
            d.barcode or "",
            d.status,
            d.location.name if d.location else "",
            d.notes or "",
        ])

    data = output.getvalue().encode("utf-8")
    buffer = BytesIO(data)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="text/csv",
        as_attachment=True,
        download_name="devices_export.csv",
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with SessionLocal() as db:
            user = db.scalars(select(User).where(User.username == username)).first()

        if user and check_password_hash(user.password_hash, password):
            session["user_id"] = user.id
            session["username"] = user.username
            session["role"] = user.role
            flash("Logged in successfully.", "success")
            return redirect(url_for("list_devices"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/users")
@login_required
@manager_required
def list_users():
    with SessionLocal() as db:
        users = db.scalars(select(User).order_by(User.username)).all()
    return render_template("users_list.html", users=users)


@app.route("/users/new", methods=["GET", "POST"])
@login_required
@manager_required
def create_user():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        role = request.form.get("role", "viewer").strip() or "viewer"
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not username or not password:
            flash("Username and password are required.", "danger")
        elif password != confirm:
            flash("Passwords do not match.", "danger")
        else:
            with SessionLocal() as db:
                existing = db.scalars(select(User).where(User.username == username)).first()
                if existing:
                    flash("A user with that username already exists.", "danger")
                else:
                    user = User(
                        username=username,
                        full_name=full_name or None,
                        role=role if role in {"manager", "viewer"} else "viewer",
                        password_hash=generate_password_hash(password),
                    )
                    db.add(user)
                    db.commit()
                    flash("User created successfully.", "success")
                    return redirect(url_for("list_users"))

    return render_template("user_form.html", user=None)


@app.route("/users/<int:user_id>/password", methods=["GET", "POST"])
@login_required
def change_password(user_id: int):
    with SessionLocal() as db:
        current = get_current_user(db)
        target = db.get(User, user_id)
        if not target:
            flash("User not found.", "danger")
            return redirect(url_for("list_users"))

        # Only managers or the user themself can change this password
        if not current or (current.role != "manager" and current.id != target.id):
            flash("You are not allowed to change this password.", "danger")
            return redirect(url_for("list_devices"))

        if request.method == "POST":
            current_pw = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            confirm_pw = request.form.get("confirm_password", "")

            if current.role != "manager":
                # Non-managers must confirm their current password.
                if not check_password_hash(target.password_hash, current_pw):
                    flash("Current password is incorrect.", "danger")
                    return render_template("change_password.html", user=target)

            if not new_pw:
                flash("New password is required.", "danger")
            elif new_pw != confirm_pw:
                flash("New passwords do not match.", "danger")
            else:
                target.password_hash = generate_password_hash(new_pw)
                db.commit()
                flash("Password updated successfully.", "success")
                if current.role == "manager":
                    return redirect(url_for("list_users"))
                return redirect(url_for("list_devices"))

        return render_template("change_password.html", user=target)


@app.route("/devices/import", methods=["GET", "POST"])
@login_required
@manager_required
def import_devices():
    if request.method == "GET":
        return render_template("import_devices.html")

    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Please choose a CSV or Excel file to upload.", "danger")
        return redirect(url_for("import_devices"))

    filename = file.filename.lower()

    try:
        if filename.endswith(".csv"):
            data = file.read().decode("utf-8-sig")
            df = pd.read_csv(StringIO(data))
        elif filename.endswith(".xlsx") or filename.endswith(".xls"):
            # Many Excel layouts (including Devices.xlsx) use two header rows.
            # Reading with header=1 gives the real column names like
            # LOCATION, ITEM, CATEGORY, etc., instead of generic Unnamed: columns.
            data = file.read()
            excel_sheets = pd.read_excel(BytesIO(data), sheet_name=None, header=1)

            frames = [sheet_df for sheet_df in excel_sheets.values() if sheet_df is not None and not sheet_df.empty]
            if not frames:
                flash("Uploaded Excel file appears to be empty.", "danger")
                return redirect(url_for("import_devices"))
            df = pd.concat(frames, ignore_index=True)
        else:
            flash("Unsupported file type. Please upload CSV or Excel.", "danger")
            return redirect(url_for("import_devices"))
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not read file: {exc}", "danger")
        return redirect(url_for("import_devices"))

    # Special handling for layouts like Devices.xlsx where the first row
    # contains the real headers and pandas created Unnamed: columns.
    # (This block is now mainly a fallback for other tricky files; for
    # Devices.xlsx the header=1 logic above already yields clean columns.)
    if len(df.columns) > 0 and all(str(c).lower().startswith("unnamed") for c in df.columns) and not df.empty:
        header_row = df.iloc[0]
        new_columns = []
        for idx, value in enumerate(header_row):
            if pd.isna(value):
                new_columns.append(f"col_{idx}")
            else:
                new_columns.append(str(value).strip())
        df.columns = new_columns
        df = df[1:]

    # Build a flexible mapping from your file's columns to the app fields.
    lower_to_real = {str(c).lower(): str(c) for c in df.columns}

    def find_col(candidates):
        for cand in candidates:
            if cand in lower_to_real:
                return lower_to_real[cand]
        return None

    def cell_to_str(row, col_name: str | None) -> str:
        """Return a cleaned string for a given column in a row.

        Treats pandas NaN/None as empty string so we don't end up with
        literal 'nan' text values for missing cells.
        """

        if not col_name:
            return ""
        value = row.get(col_name)
        if value is None or (isinstance(value, float) and pd.isna(value)) or pd.isna(value):
            return ""
        return str(value).strip()

    name_col = find_col(["name", "item", "device", "device name", "asset name"])
    type_col = find_col(["device_type", "type", "category", "item type"])
    serial_col = find_col(["serial_number", "serial", "serial no", "serial no."])
    barcode_col = find_col(["barcode", "bar code", "bar-code"])
    status_col = find_col(["status", "state"])
    location_col = find_col(["location", "site", "branch", "office"])
    # SUB LOCATION currently only contains numeric codes that should live in a
    # different table in the future. For now we deliberately ignore it when
    # building locations so that sites like "Beletweyne" appear as plain
    # top-level locations.
    sublocation_col = None
    notes_col = find_col(["notes", "remarks", "comment", "comments"])
    current_loc_col = find_col(["current location", "current_location"])
    origin_col = find_col(["origin"])
    item_code_col = find_col(["item code", "item_code"])
    quantity_col = find_col(["quantity", "qty"])

    if not name_col or not type_col:
        flash(
            "Could not find required columns for device name and type. "
            "Expected something like: 'ITEM' (name) and 'CATEGORY' (type) as in Devices.xlsx.",
            "danger",
        )
        return redirect(url_for("import_devices"))

    created = 0
    updated = 0

    with SessionLocal() as db:
        for _, row in df.iterrows():
            name = cell_to_str(row, name_col)
            device_type = cell_to_str(row, type_col)
            if not name or not device_type:
                continue

            serial_number = cell_to_str(row, serial_col) or None
            raw_barcode = cell_to_str(row, barcode_col)
            barcode = raw_barcode or None
            status = "Active"
            if status_col:
                status_val = cell_to_str(row, status_col)
                if status_val:
                    status = status_val

            # Build location from LOCATION only (ignore SUB LOCATION codes)
            location_name = None
            if location_col:
                base_loc = cell_to_str(row, location_col)
                if base_loc:
                    location_name = base_loc

            # Compose notes from remarks, current location, origin, item code, quantity
            notes_parts = []
            if notes_col:
                note_val = cell_to_str(row, notes_col)
                if note_val:
                    notes_parts.append(f"Remarks: {note_val}")
            if current_loc_col:
                cur_val = cell_to_str(row, current_loc_col)
                if cur_val:
                    notes_parts.append(f"Current location: {cur_val}")
            if origin_col:
                origin_val = cell_to_str(row, origin_col)
                if origin_val:
                    notes_parts.append(f"Origin: {origin_val}")
            if item_code_col:
                code_val = cell_to_str(row, item_code_col)
                if code_val:
                    notes_parts.append(f"Item code: {code_val}")
            if quantity_col:
                qty_val = cell_to_str(row, quantity_col)
                if qty_val:
                    notes_parts.append(f"Quantity: {qty_val}")

            notes = "; ".join(notes_parts) or None

            location_id = None
            if location_name:
                # Match locations case-insensitively so that manually
                # created entries like "BELETWEYNE" still link up with
                # Excel values like "Beletweyne".
                existing_loc = db.scalars(
                    select(Location).where(func.lower(Location.name) == location_name.lower())
                ).first()
                if not existing_loc:
                    existing_loc = Location(name=location_name)
                    db.add(existing_loc)
                    db.flush()
                location_id = existing_loc.id

            device = None
            if barcode:
                device = db.scalars(
                    select(Device).where(Device.barcode == barcode)
                ).first()

            if device:
                device.name = name
                device.device_type = device_type
                device.serial_number = serial_number
                device.status = status
                device.location_id = location_id
                device.notes = notes
                updated += 1
            else:
                device = Device(
                    name=name,
                    device_type=device_type,
                    serial_number=serial_number,
                    barcode=barcode,
                    status=status,
                    location_id=location_id,
                    notes=notes,
                )
                db.add(device)
                created += 1

        db.commit()

    flash(f"Import completed: {created} created, {updated} updated.", "success")
    return redirect(url_for("list_devices"))


if __name__ == "__main__":
    app.run(debug=True)
