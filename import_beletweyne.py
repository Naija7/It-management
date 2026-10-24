from io import StringIO, BytesIO

import pandas as pd
from sqlalchemy import select, func

from database import SessionLocal
from models import Device, Location


EXCEL_PATH = r"C:\Jordan-Py\Beletwyne.xlsx"


def load_dataframe() -> pd.DataFrame:
    """Load the Beletweyne Excel file into a single DataFrame.

    Mirrors the logic in app.import_devices for Excel files.
    """

    with open(EXCEL_PATH, "rb") as f:
        data = f.read()

    excel_sheets = pd.read_excel(BytesIO(data), sheet_name=None, header=1)
    frames = [sheet_df for sheet_df in excel_sheets.values() if sheet_df is not None and not sheet_df.empty]
    if not frames:
        raise SystemExit("Uploaded Excel file appears to be empty.")

    df = pd.concat(frames, ignore_index=True)

    # Fallback: first row contains headers but pandas created Unnamed columns
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

    return df


def main() -> None:
    df = load_dataframe()

    # Normal devices sheet has proper column headers; this Beletwyne-only
    # file is more primitive, so we massage its columns into a
    # Devices.xlsx-like shape.

    # If the first column is just a running index like "SL NO:", rename it.
    cols = list(df.columns)
    if cols and isinstance(cols[0], (int, float)):
        cols[0] = "SL NO:"

    # Second column should be the location name (e.g. "Beletwyene").
    if len(cols) > 1 and isinstance(cols[1], str) and cols[1]:
        cols[1] = "LOCATION"

    # Third column appears to be SUB LOCATION / ITEM CODE; keep as-is.

    # Fourth column is ITEM CODE in the screenshot, so rename accordingly
    if len(cols) > 3 and isinstance(cols[3], (int, float, str)):
        cols[3] = "ITEM CODE"

    # Fifth column is QUANTITY
    if len(cols) > 4:
        cols[4] = "QUANTITY"

    # Sixth column is BARCODE
    if len(cols) > 5:
        cols[5] = "BARCODE"

    # Seventh column is ITEM
    if len(cols) > 6:
        cols[6] = "ITEM"

    # Ninth column is CATEGORY
    if len(cols) > 8:
        cols[8] = "CATEGORY"

    # Tenth column is ORIGIN
    if len(cols) > 9:
        cols[9] = "ORIGIN"

    df.columns = cols

    lower_to_real = {str(c).lower(): str(c) for c in df.columns}

    def find_col(candidates):
        for cand in candidates:
            if cand in lower_to_real:
                return lower_to_real[cand]
        return None

    def cell_to_str(row, col_name: str | None) -> str:
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
    notes_col = find_col(["notes", "remarks", "comment", "comments"])
    current_loc_col = find_col(["current location", "current_location"])
    origin_col = find_col(["origin"])
    item_code_col = find_col(["item code", "item_code"])
    quantity_col = find_col(["quantity", "qty"])

    if not name_col or not type_col:
        raise SystemExit(
            "Could not find required columns for device name and type. "
            "Expected something like: 'ITEM' (name) and 'CATEGORY' (type)."
        )

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

            location_name = None
            if location_col:
                base_loc = cell_to_str(row, location_col)
                if base_loc:
                    location_name = base_loc

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

    print(f"Import completed: {created} created, {updated} updated.")


if __name__ == "__main__":
    main()
