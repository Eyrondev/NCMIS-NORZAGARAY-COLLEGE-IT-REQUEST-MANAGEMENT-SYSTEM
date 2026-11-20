from flask import request, render_template, redirect, url_for, flash, Blueprint,jsonify,session,send_file
from db import get_db_connection
from io import BytesIO
import pandas as pd
import pymysql, re
from collections import defaultdict
import uuid
from datetime import date
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from utils.inventory_audit import log_inventory_action
from utils.maintenance_audit import (
    current_technician,
    ensure_maintenance_history_schema,
    is_repair_transition,
    log_maintenance_event,
)
from notification import add_notification

manage_pc_bp = Blueprint('manage_pc_bp', __name__)


# Parts that can be replaced from the PC inventory screen. The keyword list is
# used to match available inventory rows to the selected part column.
PC_PART_DEFINITIONS = {
    'monitor': {'label': 'Monitor', 'keywords': ['monitor', 'display']},
    'motherboard': {'label': 'Motherboard', 'keywords': ['motherboard', 'mainboard']},
    'ram': {'label': 'RAM', 'keywords': ['ram', 'memory']},
    'storage': {'label': 'Storage', 'keywords': ['storage', 'ssd', 'hdd', 'hard drive', 'hard disk', 'nvme']},
    'gpu': {'label': 'GPU', 'keywords': ['gpu', 'graphics', 'video card']},
    'psu': {'label': 'PSU', 'keywords': ['psu', 'power supply']},
    'casing': {'label': 'Casing', 'keywords': ['casing', 'chassis']},
    'mouse': {'label': 'Mouse', 'keywords': ['mouse']},
    'keyboard': {'label': 'Keyboard', 'keywords': ['keyboard']},
    'other_parts': {'label': 'Other Parts', 'keywords': None},
}
PC_PART_ALLOWED = tuple(PC_PART_DEFINITIONS.keys())

# Post-removal dispositions offered by the Replace Part dialog.
REMOVED_PART_STATUS_OPTIONS = ('Available', 'Needs Fixing', 'Defective', 'For Disposal')

# Inventory states that mean "this physical unit is already spoken for".
_ASSIGNED_DEVICE_STATUSES = (
    'in use', 'in used', 'in-used', 'inuse', 'assigned', 'used',
    'damaged', 'damage', 'unusable', 'defective', 'for disposal',
    'needs fixing', 'surrendered', 'archived', 'inactive',
)


class _ReplacementError(Exception):
    """Raised for validation failures in the replacement workflow."""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _existing_pc_part_columns(cur):
    """Return list of existing part column names on `pcinfofull` preserving canonical order.

    Uses the provided cursor to query information_schema; returns an ordered
    subset of the known part columns that actually exist in the table.
    """
    parts = ['monitor', 'motherboard', 'ram', 'storage', 'gpu', 'psu', 'casing', 'mouse', 'keyboard', 'other_parts']
    try:
        placeholders = ','.join(['%s'] * len(parts))
        cur.execute(f"SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pcinfofull' AND COLUMN_NAME IN ({placeholders})", parts)
        rows = cur.fetchall()
        found = set()
        for r in rows:
            if isinstance(r, dict):
                found.add(r.get('COLUMN_NAME'))
            else:
                found.add(r[0])
        return [p for p in parts if p in found]
    except Exception:
        # If anything goes wrong, conservatively assume common part columns
        # except `mouse` which may be missing on older schemas.
        return [p for p in parts if p != 'mouse']

def _parse_tokens(value):
    if not value:
        return set()
    return set(t.strip() for t in re.split(r"[,;\n]+", str(value)) if t and t.strip())


def _split_part_label(value):
    """Split a catalog suggestion label (``Item — Brand``) into its parts.

    The `/part-suggestions` endpoint stores labels in the PC row using the
    format ``item_name — brand_model``.  Looking that whole string up against
    `devices_full` never matches, so replacement tracking needs to split it.
    """
    text = str(value or '').strip()
    for sep in (' — ', ' – ', ' - '):
        if sep in text:
            item_name, brand_model = text.split(sep, 1)
            item_name, brand_model = item_name.strip(), brand_model.strip()
            if item_name and brand_model:
                return item_name, brand_model
    return None, None


def _token_is_unique_device(cur, token):
    """True when `token` identifies one physical row (accession id or serial).

    Generic labels/names can be shared by several units and must not be
    treated as "referenced elsewhere" just because another PC uses the same
    display text.
    """
    token = str(token or '').strip()
    if not token:
        return False
    try:
        int(token)
        return True
    except (TypeError, ValueError):
        pass
    try:
        cur.execute(
            "SELECT 1 FROM devices_full WHERE serial_no = %s OR municipal_serial_no = %s LIMIT 1",
            (token, token),
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def _apply_returned_status(cur, accession_id, old_status, old_quantity, new_status, performed_by=None):
    """Return one replaced part to Manage Item with ``new_status``.

    Non-consumable units are stored with ``quantity = 1`` (0 once assigned),
    so the old code's ``quantity IS NULL`` check never matched and the row
    stayed ``IN USE``.  Treat quantity ``0``/``1`` as a single unit and set
    its status; only genuine stock rows (quantity > 1) get a unit added back.

    Returns a dict describing the change, or ``None`` when nothing was done.
    """
    if accession_id is None:
        return None

    try:
        quantity = int(old_quantity) if old_quantity is not None else None
    except (TypeError, ValueError):
        quantity = None

    tag = 'mark_damaged' if new_status == 'Damaged' else 'release'
    detail = {
        'accession_id': accession_id,
        'old_status': old_status,
        'new_status': new_status,
        'old_quantity': quantity,
    }

    if quantity is None or quantity <= 1:
        if quantity is not None and quantity < 1:
            cur.execute(
                "UPDATE devices_full SET quantity = %s WHERE accession_id = %s",
                (1, accession_id),
            )
            try:
                log_inventory_action('DEVICE', accession_id, 'UPDATE', 'quantity', quantity, 1, performed_by)
            except Exception:
                pass
            try:
                print(f"[{tag}] accession_id={accession_id} quantity {quantity} -> 1")
            except Exception:
                pass
        detail['new_quantity'] = 1 if quantity is None or quantity < 1 else quantity

        if str(old_status or '').strip().lower() != new_status.lower():
            cur.execute(
                "UPDATE devices_full SET status = %s WHERE accession_id = %s",
                (new_status, accession_id),
            )
            try:
                log_inventory_action('DEVICE', accession_id, 'UPDATE', 'status', old_status, new_status, performed_by)
            except Exception:
                pass
            try:
                print(f"[{tag}] accession_id={accession_id} {old_status!r} -> {new_status!r}")
            except Exception:
                pass
        return detail

    # Genuine stock row: put one returned unit back into the row.
    new_quantity = quantity + 1
    cur.execute(
        "UPDATE devices_full SET quantity = %s WHERE accession_id = %s",
        (new_quantity, accession_id),
    )
    try:
        log_inventory_action('DEVICE', accession_id, 'UPDATE', 'quantity', quantity, new_quantity, performed_by)
    except Exception:
        pass
    try:
        print(f"[{tag}] accession_id={accession_id} quantity {quantity} -> {new_quantity}")
    except Exception:
        pass
    detail['new_quantity'] = new_quantity

    # Only a row that was fully in use changes status; a stock row that was
    # already Available stays Available even when the unit came back damaged.
    if str(old_status or '').strip().lower() == 'in use':
        cur.execute(
            "UPDATE devices_full SET status = %s WHERE accession_id = %s",
            (new_status, accession_id),
        )
        try:
            log_inventory_action('DEVICE', accession_id, 'UPDATE', 'status', old_status, new_status, performed_by)
        except Exception:
            pass
        try:
            print(f"[{tag}] accession_id={accession_id} {old_status!r} -> {new_status!r}")
        except Exception:
            pass

    return detail


def _resolve_replaced_device_rows(cur, token, prefer_pcid=None, exclude_accession_id=None):
    """Return device rows referenced by a PC part token.

    Handles accession ids, exact serials, catalog labels (``Item — Brand``)
    and finally partial name/brand matches.  When the parent PC is known, a
    device already linked through ``used_on_pcid`` wins so same-model units do
    not get mixed up.  ``exclude_accession_id`` keeps the incoming replacement
    out of the "removed part" lookup.
    """
    token = str(token or '').strip()
    if not token:
        return []

    def _allowed(row):
        if not row:
            return False
        aid = row.get('accession_id') if isinstance(row, dict) else row[0]
        if exclude_accession_id is not None and str(aid) == str(exclude_accession_id):
            return False
        return True

    try:
        aid = int(token)
    except (TypeError, ValueError):
        aid = None

    if aid is not None:
        cur.execute(
            "SELECT accession_id, status, quantity, used_on_pcid FROM devices_full "
            "WHERE accession_id = %s",
            (aid,),
        )
        row = cur.fetchone()
        return [row] if _allowed(row) else []

    cur.execute(
        "SELECT accession_id, status, quantity, used_on_pcid FROM devices_full "
        "WHERE serial_no = %s OR municipal_serial_no = %s",
        (token, token),
    )
    rows = [row for row in (cur.fetchall() or []) if _allowed(row)]
    if rows:
        return rows

    item_name, brand_model = _split_part_label(token)
    if item_name and brand_model:
        # Prefer the unit that is explicitly linked to this PC.
        if prefer_pcid:
            cur.execute(
                "SELECT accession_id, status, quantity, used_on_pcid FROM devices_full "
                "WHERE used_on_pcid = %s "
                "AND LOWER(TRIM(item_name)) = %s "
                "AND LOWER(TRIM(COALESCE(brand_model, ''))) = %s "
                "ORDER BY accession_id LIMIT 1",
                (prefer_pcid, item_name.lower(), brand_model.lower()),
            )
            row = cur.fetchone()
            if _allowed(row):
                return [row]

        cur.execute(
            "SELECT accession_id, status, quantity, used_on_pcid FROM devices_full "
            "WHERE LOWER(TRIM(item_name)) = %s "
            "AND LOWER(TRIM(COALESCE(brand_model, ''))) = %s "
            "ORDER BY CASE WHEN LOWER(COALESCE(status, '')) = 'in use' THEN 0 "
            "WHEN LOWER(COALESCE(status, '')) LIKE 'damaged%%' THEN 2 ELSE 1 END, "
            "accession_id LIMIT 5",
            (item_name.lower(), brand_model.lower()),
        )
        rows = [row for row in (cur.fetchall() or []) if _allowed(row)]
        if rows:
            return rows

    like = f"%{token.lower()}%"
    cur.execute(
        "SELECT accession_id, status, quantity, used_on_pcid FROM devices_full "
        "WHERE LOWER(item_name) LIKE %s OR LOWER(COALESCE(brand_model, '')) LIKE %s "
        "ORDER BY CASE WHEN LOWER(COALESCE(status, '')) = 'in use' THEN 0 "
        "WHEN LOWER(COALESCE(status, '')) LIKE 'damaged%%' THEN 2 ELSE 1 END, "
        "accession_id LIMIT 5",
        (like, like),
    )
    return [row for row in (cur.fetchall() or []) if _allowed(row)]


def _return_replaced_part(cur, value, mark_damaged=False, exclude_pcid=None, performed_by=None, collected=None):
    """Return part(s) removed from a PC to Manage Item and report the changes.

    ``mark_damaged=False`` returns the old part as Available; ``True`` marks
    it Damaged.  Returns a list of change details (also appended to
    ``collected`` when supplied).
    """
    if collected is None:
        collected = []
    if not value:
        return collected

    target_status = 'Damaged' if mark_damaged else 'Available'
    for token in _parse_tokens(value):
        try:
            print(f"[return_part] token={token!r} -> {target_status}")
        except Exception:
            pass

        token_is_unique = _token_is_unique_device(cur, token)
        for row in _resolve_replaced_device_rows(cur, token):
            aid = row.get('accession_id') if isinstance(row, dict) else row[0]
            old_status = row.get('status') if isinstance(row, dict) else row[1]
            old_qty = row.get('quantity') if isinstance(row, dict) else row[2]

            if token_is_unique and _is_token_referenced_elsewhere(cur, token, exclude_pcid):
                try:
                    print(f"[return_part] accession_id={aid} still referenced elsewhere; skipping")
                except Exception:
                    pass
                continue

            detail = _apply_returned_status(cur, aid, old_status, old_qty, target_status, performed_by)
            if detail:
                detail['token'] = token
                collected.append(detail)
    return collected


def _ensure_part_replacement_table(cur):
    """Create the part-replacement report table on installs that lack it."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pc_part_replacements (
            replacement_id INT AUTO_INCREMENT PRIMARY KEY,
            pcid INT NOT NULL,
            part VARCHAR(64) NOT NULL,
            old_value TEXT,
            new_value TEXT,
            removed_status VARCHAR(32) DEFAULT NULL,
            accession_id INT DEFAULT NULL,
            new_accession_id INT DEFAULT NULL,
            performed_by INT DEFAULT NULL,
            remarks TEXT DEFAULT NULL,
            performed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_pc_part_replacements_pcid (pcid)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
        """
    )

    # Older installations already have the report table; add the column that
    # records which inventory unit was installed without recreating the table.
    try:
        cur.execute("SHOW COLUMNS FROM pc_part_replacements LIKE 'new_accession_id'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE pc_part_replacements ADD COLUMN new_accession_id INT DEFAULT NULL")
    except Exception as exc:
        try:
            print(f"[replace_pc_part] Could not ensure new_accession_id column: {exc}")
        except Exception:
            pass


def _part_display_label(item_name, brand_model):
    """Return the label stored in the PC part column for one inventory unit."""
    name = str(item_name or '').strip()
    brand = str(brand_model or '').strip()
    if name and brand:
        return f"{name} — {brand}"
    return name or brand


def _device_matches_part(row, part):
    """True when an inventory row is classified as the selected PC part."""
    definition = PC_PART_DEFINITIONS.get(part)
    keywords = definition.get('keywords') if definition else None
    if not keywords:
        return True
    haystack = ' '.join(
        str(row.get(field) or '')
        for field in ('item_name', 'brand_model', 'device_type')
    ).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def _device_referenced_by_another_pc(cur, accession_id, serial_no=None, exclude_pcid=None):
    """True when another active PC already references this inventory unit."""
    parts = _existing_pc_part_columns(cur)
    if not parts:
        return False

    tokens = []
    for token in (accession_id, serial_no):
        token = str(token or '').strip()
        if token and token not in tokens:
            tokens.append(token)
    if not tokens:
        return False

    unit_clauses = []
    params = []
    for part in parts:
        for token in tokens:
            unit_clauses.append(
                f"(`{part}` = %s OR FIND_IN_SET(%s, REPLACE(COALESCE(`{part}`, ''), ' ', '')) > 0)"
            )
            params.extend([token, token])

    sql = (
        "SELECT COUNT(*) AS cnt FROM pcinfofull "
        "WHERE pcid != %s AND COALESCE(is_archived, 0) = 0 "
        "AND (" + " OR ".join(unit_clauses) + ")"
    )
    cur.execute(sql, [exclude_pcid or 0] + params)
    row = cur.fetchone()
    cnt = row.get('cnt') if isinstance(row, dict) else (row[0] if row else 0)
    return int(cnt or 0) > 0


def _apply_removed_part_status(cur, pcid, old_value, new_status, performed_by=None,
                               exclude_accession_id=None):
    """Apply the chosen disposition to the removed part's inventory row(s)."""
    details = []
    for token in _parse_tokens(old_value):
        token_is_unique = _token_is_unique_device(cur, token)
        for row in _resolve_replaced_device_rows(
            cur,
            token,
            prefer_pcid=pcid,
            exclude_accession_id=exclude_accession_id,
        ):
            aid = row.get('accession_id') if isinstance(row, dict) else row[0]
            old_status = row.get('status') if isinstance(row, dict) else row[1]
            old_qty = row.get('quantity') if isinstance(row, dict) else row[2]
            used_on_pcid = row.get('used_on_pcid') if isinstance(row, dict) else None

            if exclude_accession_id and str(aid) == str(exclude_accession_id):
                continue
            # A shared display label must never pull a unit that is explicitly
            # assigned to a different PC into this replacement.
            if (
                not token_is_unique
                and used_on_pcid
                and str(used_on_pcid) != str(pcid)
            ):
                try:
                    print(f"[removed_part] accession_id={aid} is assigned to PC {used_on_pcid}; skipping")
                except Exception:
                    pass
                continue
            if token_is_unique and _is_token_referenced_elsewhere(cur, token, pcid):
                try:
                    print(f"[removed_part] accession_id={aid} still referenced elsewhere; skipping")
                except Exception:
                    pass
                continue

            try:
                quantity = int(old_qty) if old_qty is not None else 0
            except (TypeError, ValueError):
                quantity = 0

            # A tracked unit is normally stored with quantity 0/1. Stock rows
            # (quantity > 1) were never tied to this PC, so leave them alone.
            if quantity > 1 and not used_on_pcid:
                continue

            new_quantity = 1 if quantity < 1 else quantity
            if new_quantity != old_qty:
                cur.execute(
                    "UPDATE devices_full SET quantity = %s WHERE accession_id = %s",
                    (new_quantity, aid),
                )
                try:
                    log_inventory_action(
                        'DEVICE', aid, 'UPDATE', 'quantity', old_qty, new_quantity, performed_by
                    )
                except Exception:
                    pass

            if str(old_status or '').strip() != new_status:
                cur.execute(
                    "UPDATE devices_full SET status = %s WHERE accession_id = %s",
                    (new_status, aid),
                )
                try:
                    log_inventory_action(
                        'DEVICE', aid, 'UPDATE', 'status', old_status, new_status, performed_by
                    )
                except Exception:
                    pass

            # The unit is no longer installed in the PC even when the chosen
            # disposition is "For Disposal" or "Defective".
            try:
                cur.execute(
                    "UPDATE devices_full "
                    "SET used_on_pcid = NULL, usage_scope = 'UNASSIGNED' "
                    "WHERE accession_id = %s",
                    (aid,),
                )
            except Exception:
                # Older schemas may not have these columns; status/quantity
                # updates above are still valid.
                pass

            details.append({
                'accession_id': aid,
                'token': token,
                'old_status': old_status,
                'new_status': new_status,
                'quantity': new_quantity,
            })
            break  # one physical unit per token

    return details


def _claim_replacement_device(cur, accession_id, pcid, performed_by=None):
    """Lock and validate one available inventory unit for assignment."""
    cur.execute(
        "SELECT accession_id, item_name, brand_model, serial_no, municipal_serial_no, "
        "quantity, device_type, status, is_archived, used_on_pcid, usage_scope "
        "FROM devices_full WHERE accession_id = %s FOR UPDATE",
        (accession_id,),
    )
    row = cur.fetchone()
    if not row:
        raise _ReplacementError('The selected replacement item was not found.', 404)

    serial_no = row.get('serial_no')
    status_key = str(row.get('status') or '').strip().lower()
    used_on_pcid = row.get('used_on_pcid')
    usage_scope = str(row.get('usage_scope') or '').strip().upper()

    if int(row.get('is_archived') or 0) == 1:
        raise _ReplacementError('The selected item is archived and cannot be assigned.', 409)

    if not used_on_pcid and usage_scope not in ('', 'UNASSIGNED'):
        raise _ReplacementError(
            'That inventory item is already assigned elsewhere. Choose a different unit.', 409
        )

    if used_on_pcid and int(used_on_pcid) == int(pcid):
        raise _ReplacementError(
            'That inventory item is already assigned to this PC. Choose a different unit.', 409
        )

    if used_on_pcid:
        raise _ReplacementError(
            'That inventory item is already assigned to another PC. Choose a different unit.', 409
        )

    if status_key in _ASSIGNED_DEVICE_STATUSES and status_key != 'available':
        raise _ReplacementError(
            f'That inventory item is not available (current status: {row.get("status") or "unknown"}).',
            409,
        )

    try:
        quantity = int(row.get('quantity') if row.get('quantity') is not None else 1)
    except (TypeError, ValueError):
        quantity = 1

    if quantity < 1:
        raise _ReplacementError('That inventory item has no available stock.', 409)
    if quantity > 1:
        raise _ReplacementError(
            'That inventory row contains more than one unit. Add individually tracked items '
            'before assigning it to a PC.',
            409,
        )

    if _device_referenced_by_another_pc(cur, accession_id, serial_no, exclude_pcid=pcid):
        raise _ReplacementError(
            'That inventory item is already linked to another PC. Choose a different unit.', 409
        )

    old_status = row.get('status')
    cur.execute(
        "UPDATE devices_full "
        "SET status = 'IN USE', quantity = 0, used_on_pcid = %s, usage_scope = 'PC' "
        "WHERE accession_id = %s",
        (pcid, accession_id),
    )
    try:
        log_inventory_action(
            'DEVICE', accession_id, 'UPDATE', 'status', old_status, 'IN USE', performed_by
        )
        log_inventory_action(
            'DEVICE', accession_id, 'UPDATE', 'quantity', quantity, 0, performed_by
        )
    except Exception:
        pass

    row['status'] = 'IN USE'
    row['quantity'] = 0
    row['used_on_pcid'] = pcid
    row['usage_scope'] = 'PC'
    return row


def _available_part_rows(cur, part, pcid=None):
    """Return available, individually tracked inventory units for one part."""
    definition = PC_PART_DEFINITIONS.get(part)
    if not definition:
        raise _ReplacementError(f'Unknown part: {part}')

    where = [
        "COALESCE(df.is_archived, 0) = 0",
        "LOWER(TRIM(COALESCE(df.status, ''))) IN ('available', '')",
        "COALESCE(df.used_on_pcid, 0) = 0",
        "UPPER(TRIM(COALESCE(df.usage_scope, 'UNASSIGNED'))) IN ('', 'UNASSIGNED')",
        "COALESCE(df.quantity, 1) >= 1",
        "COALESCE(df.quantity, 1) <= 1",
        "LOWER(TRIM(COALESCE(df.device_type, ''))) NOT IN ('consumable')",
        "TRIM(COALESCE(df.device_type, '')) NOT REGEXP '^[0-9]+$'",
    ]
    params = []

    keywords = definition.get('keywords')
    if keywords:
        keyword_clauses = []
        for keyword in keywords:
            keyword_clauses.append(
                "(LOWER(COALESCE(df.item_name, '')) LIKE %s "
                "OR LOWER(COALESCE(df.brand_model, '')) LIKE %s "
                "OR LOWER(COALESCE(df.device_type, '')) LIKE %s)"
            )
            like = f"%{keyword.lower()}%"
            params.extend([like, like, like])
        where.append("(" + " OR ".join(keyword_clauses) + ")")

    sql = (
        "SELECT df.accession_id, df.item_name, df.brand_model, df.serial_no, "
        "df.municipal_serial_no, df.quantity, df.device_type, df.status, "
        "df.acquisition_cost, df.date_acquired "
        "FROM devices_full df "
        "WHERE " + " AND ".join(where) + " "
        "ORDER BY df.item_name, df.brand_model, df.accession_id "
        "LIMIT 200"
    )
    cur.execute(sql, params)
    rows = cur.fetchall() or []

    results = []
    for row in rows:
        label = _part_display_label(row.get('item_name'), row.get('brand_model'))
        results.append({
            'accession_id': row.get('accession_id'),
            'item_name': row.get('item_name'),
            'brand_model': row.get('brand_model') or '',
            'label': label,
            'serial_no': row.get('serial_no') or '',
            'municipal_serial_no': row.get('municipal_serial_no') or '',
            'quantity': 1,
            'device_type': row.get('device_type') or '',
            'status': row.get('status') or 'Available',
            'acquisition_cost': float(row.get('acquisition_cost') or 0),
            'date_acquired': str(row.get('date_acquired') or ''),
        })
    return results


def _replace_pc_part_from_inventory(data):
    """Replace one PC part with an inventory unit and update both sides atomically."""
    if 'user' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    part = str(data.get('part') or '').strip().lower()
    if part not in PC_PART_DEFINITIONS:
        return jsonify({"success": False, "error": "Invalid part selected"}), 400

    try:
        pcid = int(data.get('pcid'))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid PC selected"}), 400

    try:
        replacement_accession_id = int(data.get('replacement_accession_id'))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Choose a replacement item from inventory"}), 400

    removed_status = str(data.get('removed_status') or 'Available').strip()
    if removed_status not in REMOVED_PART_STATUS_OPTIONS:
        return jsonify({"success": False, "error": "Invalid status for the removed part"}), 400

    remarks = str(data.get('remarks') or '').strip() or None
    performed_by = None
    try:
        performed_by = session.get('user', {}).get('user_id')
    except Exception:
        performed_by = None

    conn = get_db_connection()
    try:
        # DDL (CREATE/ALTER/TRIGGER) commits implicitly, so prepare the audit
        # schema before the replacement transaction starts.
        try:
            ensure_maintenance_history_schema(conn)
        except Exception as schema_error:
            print(f"[replace_pc_part] maintenance schema error: {schema_error}")

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # DDL (CREATE/ALTER) commits implicitly, so run it before changes.
            try:
                _ensure_part_replacement_table(cur)
            except Exception as table_error:
                try:
                    print(f"[replace_pc_part] Could not ensure report table: {table_error}")
                except Exception:
                    pass

            existing_parts = _existing_pc_part_columns(cur)
            if part not in existing_parts:
                if part not in PC_PART_ALLOWED:
                    return jsonify({"success": False, "error": f"Part not supported: {part}"}), 400
                try:
                    cur.execute(f"ALTER TABLE pcinfofull ADD COLUMN `{part}` TEXT")
                    existing_parts = _existing_pc_part_columns(cur)
                except Exception as alter_error:
                    try:
                        print(f"[replace_pc_part] Could not add column `{part}`: {alter_error}")
                    except Exception:
                        pass

            if part not in existing_parts:
                return jsonify({
                    "success": False,
                    "error": f"The current database schema cannot store a replacement for {part}."
                }), 400

            select_cols = ', '.join(existing_parts)
            cur.execute(
                f"SELECT pcid, pcname, serial_no, status, {select_cols} "
                "FROM pcinfofull WHERE pcid = %s AND COALESCE(is_archived, 0) = 0 FOR UPDATE",
                (pcid,),
            )
            pc_row = cur.fetchone()
            if not pc_row:
                return jsonify({"success": False, "error": "PC not found"}), 404

            old_value = pc_row.get(part)
            old_tokens = _parse_tokens(old_value)

            # Lock and validate the unit first so duplicate assignments get a
            # precise message instead of a generic "not available" response.
            replacement = _claim_replacement_device(
                cur, replacement_accession_id, pcid, performed_by
            )
            if not _device_matches_part(replacement, part):
                raise _ReplacementError(
                    'The selected inventory item is not categorized as this part. Choose a matching item.',
                    409,
                )

            new_value = replacement.get('label') or _part_display_label(
                replacement.get('item_name'), replacement.get('brand_model')
            )
            if not new_value:
                raise _ReplacementError('The selected replacement item has no display name.')

            # Apply the removed part's disposition to the old inventory unit(s).
            removed_details = _apply_removed_part_status(
                cur,
                pcid,
                old_value,
                removed_status,
                performed_by,
                exclude_accession_id=replacement_accession_id,
            )

            # Update the PC part column only after the replacement is locked in.
            cur.execute(
                f"UPDATE pcinfofull SET `{part}` = %s WHERE pcid = %s",
                (new_value, pcid),
            )
            try:
                log_inventory_action('PC', pcid, 'UPDATE', part, old_value, new_value, performed_by)
            except Exception:
                pass

            # Record the replacement for the per-PC history page.
            removed_accession_id = removed_details[0]['accession_id'] if removed_details else None
            try:
                cur.execute(
                    "INSERT INTO pc_part_replacements "
                    "(pcid, part, old_value, new_value, removed_status, accession_id, "
                    "new_accession_id, performed_by, remarks) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        pcid,
                        part,
                        old_value,
                        new_value,
                        removed_status,
                        removed_accession_id,
                        replacement_accession_id,
                        performed_by,
                        remarks,
                    ),
                )
            except Exception as report_error:
                try:
                    print(f"[replace_pc_part] Could not record replacement history: {report_error}")
                except Exception:
                    pass

            # Section 4: every replacement also writes one immutable
            # maintenance-history record with the full part/status detail.
            technician = current_technician()
            removed_status_note = f"Removed part recorded as {removed_status}."
            if not removed_details:
                removed_status_note += " No linked inventory record was found."
            history_remarks = ' '.join(
                part for part in (remarks, removed_status_note) if part
            ) or removed_status_note
            old_part_status = (
                (removed_details[0].get('old_status') if removed_details else None)
                or 'IN USE'
            )
            log_maintenance_event(
                cur,
                asset_type='PC',
                asset_id=pcid,
                pcid=pcid,
                action='Part Replacement',
                maintenance_type='Part Replacement',
                replaced_part=PC_PART_DEFINITIONS[part]['label'],
                previous_part=old_value or '—',
                new_part=new_value,
                previous_part_status=removed_status,
                new_part_status='IN USE',
                old_status=old_part_status,
                new_status='IN USE',
                computer_name=pc_row.get('pcname'),
                asset_serial=pc_row.get('serial_no'),
                technician=technician,
                performed_by=technician,
                remarks=history_remarks,
            )

            conn.commit()

        message = f"{PC_PART_DEFINITIONS[part]['label']} replaced successfully."
        if old_tokens and not removed_details:
            message += " The old part was not linked to an inventory record, so only the new part was updated."

        return jsonify({
            "success": True,
            "message": message,
            "part": part,
            "part_label": PC_PART_DEFINITIONS[part]['label'],
            "pcid": pcid,
            "new_value": new_value,
            "replacement": {
                "accession_id": replacement_accession_id,
                "label": new_value,
                "serial_no": replacement.get('serial_no'),
                "status": 'IN USE',
                "quantity": 0,
            },
            "removed": {
                "old_value": old_value,
                "status": removed_status,
                "tracked": bool(removed_details),
                "items": removed_details,
            },
        })

    except _ReplacementError as exc:
        conn.rollback()
        return jsonify({"success": False, "error": exc.message}), exc.status_code
    except Exception as exc:
        conn.rollback()
        try:
            print(f"[replace_pc_part] inventory replacement failed: {exc}")
        except Exception:
            pass
        return jsonify({"success": False, "error": "Could not complete the replacement."}), 500
    finally:
        conn.close()


def _mark_device_values_in_use(cur, value, performed_by=None):
    """Mark devices referenced by `value` as IN USE.
    Supports accession_id, exact serials, and partial case-insensitive name/brand matches.
    """
    if not value:
        return

    tokens = _parse_tokens(value)
    for token in tokens:
        try:
            print(f"[mark_in_use] token={token!r}")
        except Exception:
            pass
        # try accession id (exact)
        try:
            acc_id = int(token)
        except Exception:
            acc_id = None

        if acc_id is not None:
            cur.execute("SELECT accession_id, status, quantity, device_type FROM devices_full WHERE accession_id = %s", (acc_id,))
            row = cur.fetchone()
            if row:
                aid = row.get('accession_id') if isinstance(row, dict) else row[0]
                old_status = row.get('status') if isinstance(row, dict) else row[1]
                old_qty = row.get('quantity') if isinstance(row, dict) else row[2]
                try:
                    cur_qty = int(old_qty) if old_qty is not None else None
                except Exception:
                    cur_qty = None

                # If quantity is known, decrement by 1; otherwise mark as IN USE
                if cur_qty is None:
                    if old_status != 'IN USE':
                        cur.execute("UPDATE devices_full SET status = %s WHERE accession_id = %s", ('IN USE', aid))
                        try:
                            log_inventory_action('DEVICE', aid, 'UPDATE', 'status', old_status, 'IN USE', performed_by)
                        except Exception:
                            pass
                        try:
                            print(f"[mark_in_use] updated accession_id={aid} {old_status!r} -> 'IN USE'")
                        except Exception:
                            pass
                else:
                    if cur_qty > 0:
                        new_qty = max(cur_qty - 1, 0)
                        cur.execute("UPDATE devices_full SET quantity = %s WHERE accession_id = %s", (new_qty, aid))
                        try:
                            log_inventory_action('DEVICE', aid, 'UPDATE', 'quantity', cur_qty, new_qty, performed_by)
                        except Exception:
                            pass
                        if new_qty == 0 and old_status != 'IN USE':
                            cur.execute("UPDATE devices_full SET status = %s WHERE accession_id = %s", ('IN USE', aid))
                            try:
                                log_inventory_action('DEVICE', aid, 'UPDATE', 'status', old_status, 'IN USE', performed_by)
                            except Exception:
                                pass
                        try:
                            print(f"[mark_in_use] accession_id={aid} quantity {cur_qty} -> {new_qty}")
                        except Exception:
                            pass
                    else:
                        if old_status != 'IN USE':
                            cur.execute("UPDATE devices_full SET status = %s WHERE accession_id = %s", ('IN USE', aid))
                            try:
                                log_inventory_action('DEVICE', aid, 'UPDATE', 'status', old_status, 'IN USE', performed_by)
                            except Exception:
                                pass
                            try:
                                print(f"[mark_in_use] accession_id={aid} set to 'IN USE' (quantity already 0)")
                            except Exception:
                                pass
            else:
                try:
                    print(f"[mark_in_use] accession_id {acc_id} not found")
                except Exception:
                    pass
            continue

        # exact serial / municipal serial (may match multiple rows)
        cur.execute("SELECT accession_id, status, quantity, device_type FROM devices_full WHERE serial_no = %s OR municipal_serial_no = %s", (token, token))
        rows = cur.fetchall()
        if rows:
            try:
                matched = [(r.get('accession_id') if isinstance(r, dict) else r[0]) for r in rows]
                print(f"[mark_in_use] serial match token={token!r} matched_accession_ids={matched}")
            except Exception:
                pass
            for row in rows:
                aid = row.get('accession_id') if isinstance(row, dict) else row[0]
                old_status = row.get('status') if isinstance(row, dict) else row[1]
                old_qty = row.get('quantity') if isinstance(row, dict) else row[2]
                try:
                    cur_qty = int(old_qty) if old_qty is not None else None
                except Exception:
                    cur_qty = None

                if cur_qty is None:
                    if old_status != 'IN USE':
                        cur.execute("UPDATE devices_full SET status = %s WHERE accession_id = %s", ('IN USE', aid))
                        try:
                            log_inventory_action('DEVICE', aid, 'UPDATE', 'status', old_status, 'IN USE', performed_by)
                        except Exception:
                            pass
                        try:
                            print(f"[mark_in_use] updated accession_id={aid} {old_status!r} -> 'IN USE'")
                        except Exception:
                            pass
                else:
                    if cur_qty > 0:
                        new_qty = max(cur_qty - 1, 0)
                        cur.execute("UPDATE devices_full SET quantity = %s WHERE accession_id = %s", (new_qty, aid))
                        try:
                            log_inventory_action('DEVICE', aid, 'UPDATE', 'quantity', cur_qty, new_qty, performed_by)
                        except Exception:
                            pass
                        if new_qty == 0 and old_status != 'IN USE':
                            cur.execute("UPDATE devices_full SET status = %s WHERE accession_id = %s", ('IN USE', aid))
                            try:
                                log_inventory_action('DEVICE', aid, 'UPDATE', 'status', old_status, 'IN USE', performed_by)
                            except Exception:
                                pass
                        try:
                            print(f"[mark_in_use] accession_id={aid} quantity {cur_qty} -> {new_qty}")
                        except Exception:
                            pass
                    else:
                        if old_status != 'IN USE':
                            cur.execute("UPDATE devices_full SET status = %s WHERE accession_id = %s", ('IN USE', aid))
                            try:
                                log_inventory_action('DEVICE', aid, 'UPDATE', 'status', old_status, 'IN USE', performed_by)
                            except Exception:
                                pass
                            try:
                                print(f"[mark_in_use] accession_id={aid} set to 'IN USE' (quantity already 0)")
                            except Exception:
                                pass
            continue

        # Catalog labels are stored as "Item — Brand"; match both halves first.
        item_name, brand_model = _split_part_label(token)
        row = None
        matched_by = None
        if item_name and brand_model:
            cur.execute(
                "SELECT accession_id, status, quantity, device_type FROM devices_full "
                "WHERE LOWER(TRIM(item_name)) = %s "
                "AND LOWER(TRIM(COALESCE(brand_model, ''))) = %s "
                "ORDER BY CASE WHEN LOWER(COALESCE(status, '')) = 'in use' THEN 2 "
                "WHEN LOWER(COALESCE(status, '')) LIKE 'damaged%%' THEN 2 ELSE 0 END, "
                "accession_id LIMIT 1",
                (item_name.lower(), brand_model.lower()),
            )
            row = cur.fetchone()
            if row:
                matched_by = 'label'

        if not row:
            # partial, case-insensitive match on item_name or brand_model — pick one representative row
            like = f"%{token.lower()}%"
            cur.execute("SELECT accession_id, status, quantity, device_type FROM devices_full WHERE LOWER(item_name) LIKE %s OR LOWER(brand_model) LIKE %s ORDER BY accession_id LIMIT 1", (like, like))
            row = cur.fetchone()
            if row:
                matched_by = 'partial-name'
        if row:
            try:
                aid = row.get('accession_id') if isinstance(row, dict) else row[0]
                print(f"[mark_in_use] {matched_by} match token={token!r} matched_accession_id={aid}")
            except Exception:
                aid = row.get('accession_id') if isinstance(row, dict) else row[0]
            old_status = row.get('status') if isinstance(row, dict) else row[1]
            old_qty = row.get('quantity') if isinstance(row, dict) else row[2]
            try:
                cur_qty = int(old_qty) if old_qty is not None else None
            except Exception:
                cur_qty = None

            if cur_qty is None:
                if old_status != 'IN USE':
                    cur.execute("UPDATE devices_full SET status = %s WHERE accession_id = %s", ('IN USE', aid))
                    try:
                        log_inventory_action('DEVICE', aid, 'UPDATE', 'status', old_status, 'IN USE', performed_by)
                    except Exception:
                        pass
                    try:
                        print(f"[mark_in_use] updated accession_id={aid} {old_status!r} -> 'IN USE'")
                    except Exception:
                        pass
            else:
                if cur_qty > 0:
                    new_qty = max(cur_qty - 1, 0)
                    cur.execute("UPDATE devices_full SET quantity = %s WHERE accession_id = %s", (new_qty, aid))
                    try:
                        log_inventory_action('DEVICE', aid, 'UPDATE', 'quantity', cur_qty, new_qty, performed_by)
                    except Exception:
                        pass
                    if new_qty == 0 and old_status != 'IN USE':
                        cur.execute("UPDATE devices_full SET status = %s WHERE accession_id = %s", ('IN USE', aid))
                        try:
                            log_inventory_action('DEVICE', aid, 'UPDATE', 'status', old_status, 'IN USE', performed_by)
                        except Exception:
                            pass
                    try:
                        print(f"[mark_in_use] accession_id={aid} quantity {cur_qty} -> {new_qty}")
                    except Exception:
                        pass
                else:
                    if old_status != 'IN USE':
                        cur.execute("UPDATE devices_full SET status = %s WHERE accession_id = %s", ('IN USE', aid))
                        try:
                            log_inventory_action('DEVICE', aid, 'UPDATE', 'status', old_status, 'IN USE', performed_by)
                        except Exception:
                            pass
                        try:
                            print(f"[mark_in_use] accession_id={aid} set to 'IN USE' (quantity already 0)")
                        except Exception:
                            pass
        continue


def _is_token_referenced_elsewhere(cur, token, exclude_pcid=None):
    parts = _existing_pc_part_columns(cur)
    if not parts:
        return False
    ex = exclude_pcid or 0
    placeholders = ' OR '.join([f"{p} LIKE %s" for p in parts])
    like = f"%{token}%"
    params = [ex] + [like] * len(parts)
    # Try including is_archived guard if available, fall back if DB lacks the column
    try:
        cur.execute(f"SELECT COUNT(*) AS cnt FROM pcinfofull WHERE pcid != %s AND COALESCE(is_archived, 0) = 0 AND ({placeholders})", params)
    except Exception:
        cur.execute(f"SELECT COUNT(*) AS cnt FROM pcinfofull WHERE pcid != %s AND ({placeholders})", params)
    row = cur.fetchone()
    cnt = row.get('cnt') if isinstance(row, dict) else (row[0] if row else 0)
    return int(cnt or 0) > 0


def _release_device_values_if_unreferenced(cur, value, exclude_pcid=None, performed_by=None):
    """Release devices referenced by `value` back to Available if no other active PC references them."""
    if not value:
        return

    tokens = _parse_tokens(value)
    for token in tokens:
        try:
            print(f"[release] token={token!r}")
        except Exception:
            pass

        # try accession id
        try:
            aid = int(token)
        except Exception:
            aid = None

        if aid is not None:
            cur.execute("SELECT accession_id, status, quantity, device_type FROM devices_full WHERE accession_id = %s", (aid,))
            row = cur.fetchone()
            if not row:
                try:
                    print(f"[release] accession_id {aid} not found")
                except Exception:
                    pass
                continue
            old_status = row.get('status') if isinstance(row, dict) else row[1]
            old_qty = row.get('quantity') if isinstance(row, dict) else row[2]
            try:
                cur_qty = int(old_qty) if old_qty is not None else None
            except Exception:
                cur_qty = None

            if _is_token_referenced_elsewhere(cur, token, exclude_pcid):
                try:
                    print(f"[release] accession_id={aid} still referenced elsewhere; skipping")
                except Exception:
                    pass
                continue

            if cur_qty is None:
                if old_status != 'Available':
                    cur.execute("UPDATE devices_full SET status = %s WHERE accession_id = %s", ('Available', aid))
                    try:
                        log_inventory_action('DEVICE', aid, 'UPDATE', 'status', old_status, 'Available', performed_by)
                    except Exception:
                        pass
                    try:
                        print(f"[release] updated accession_id={aid} {old_status!r} -> 'Available'")
                    except Exception:
                        pass
            else:
                new_qty = cur_qty + 1
                cur.execute("UPDATE devices_full SET quantity = %s WHERE accession_id = %s", (new_qty, aid))
                try:
                    log_inventory_action('DEVICE', aid, 'UPDATE', 'quantity', cur_qty, new_qty, performed_by)
                except Exception:
                    pass
                if old_status != 'Available':
                    cur.execute("UPDATE devices_full SET status = %s WHERE accession_id = %s", ('Available', aid))
                    try:
                        log_inventory_action('DEVICE', aid, 'UPDATE', 'status', old_status, 'Available', performed_by)
                    except Exception:
                        pass
                try:
                    print(f"[release] accession_id={aid} quantity {cur_qty} -> {new_qty} and status -> 'Available'")
                except Exception:
                    pass
            continue


def _mark_device_values_damaged(cur, value, exclude_pcid=None, performed_by=None, collected=None):
    """Mark devices referenced by `value` as Damaged and return them to Manage Item list.

    Behavior:
      - Single-unit items (quantity is NULL, 0, or 1) are stored one row per
        physical device. Restore quantity to 1 and set `status = 'Damaged'`.
      - Genuine stock rows (quantity > 1) get one unit added back and become
        Damaged when the row was fully in use.
      - Skip tokens that are still referenced by other active PCs, but only
        when the token uniquely identifies a device (accession id/serial).
        Display labels such as "Motherboard — Asus B650M" can be shared by
        many units and must not be skipped for that reason.
    """
    return _return_replaced_part(
        cur,
        value,
        mark_damaged=True,
        exclude_pcid=exclude_pcid,
        performed_by=performed_by,
        collected=collected,
    )


@manage_pc_bp.route("/delete-pc/<int:pcid>", methods=["POST"])
def delete_pc(pcid):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Fetch only existing part columns so we can release referenced devices after archiving
            parts = _existing_pc_part_columns(cur)
            if parts:
                cols = ', '.join(parts)
                cur.execute(f"SELECT {cols} FROM pcinfofull WHERE pcid = %s", (pcid,))
                pc_row = cur.fetchone()
            else:
                pc_row = {}

            # Soft-archive the PC first so subsequent reference checks exclude it
            cur.execute("UPDATE pcinfofull SET is_archived = 1, deleted_at = NOW() WHERE pcid = %s", (pcid,))

            # Release referenced device items if they are not referenced by any other active PC
            performed_by = None
            try:
                performed_by = session.get('user', {}).get('user_id')
            except Exception:
                performed_by = None

            parts_to_check = parts
            if pc_row:
                for field in parts_to_check:
                    try:
                        _release_device_values_if_unreferenced(cur, pc_row.get(field), exclude_pcid=pcid, performed_by=performed_by)
                    except Exception:
                        pass

            conn.commit()
        if is_ajax:
            return jsonify(success=True, message="PC archived successfully.")
        flash("PC archived successfully.", "success")
    except Exception as e:
        conn.rollback()
        if is_ajax:
            return jsonify(success=False, error=str(e)), 500
        flash(str(e), "error")
    finally:
        conn.close()
    return redirect(url_for('manage_inventory.inventory_load'))


@manage_pc_bp.route('/filter-pcs', methods=['GET'])
def filter_pcs():
    conn = get_db_connection()

    try:
        args = request.args

        department_id = args.get('department_id')
        status = args.get('status')
        location = args.get('location')
        accountable = args.get('accountable')
        serial_no = args.get('serial_no')

        date_from = args.get('date_from')
        date_to = args.get('date_to')

        risk_level = args.get('risk_level')
        health_min = args.get('health_min', type=int)
        health_max = args.get('health_max', type=int)

        last_checked_from = args.get('last_checked_from')
        last_checked_to = args.get('last_checked_to')

        overdue_only = args.get('overdue') == '1'
        needs_checking = args.get('needs_checking') == '1'

        search = args.get('search')

        # Determine whether the table has `is_archived` to avoid SQL errors on older schemas
        try:
            with conn.cursor() as _cur:
                _cur.execute("SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pcinfofull' AND COLUMN_NAME = 'is_archived'")
                _row = _cur.fetchone()
                has_is_arch = bool(_row and (int(_row.get('cnt', 0)) if isinstance(_row, dict) else _row[0] > 0))
        except Exception:
            has_is_arch = False

        # Build list of existing part columns and construct the SELECT dynamically
        try:
            with conn.cursor() as _cur:
                parts = _existing_pc_part_columns(_cur)
        except Exception:
            parts = []

        parts_select = ''
        if parts:
            parts_select = ',\n                ' + ',\n                '.join([f"p.{p}" for p in parts])

        query = f"""
            SELECT 
                p.pcid,
                p.pcname,
                p.department_id,
                d.department_name,
                p.location,
                p.quantity,
                p.acquisition_cost,
                p.date_acquired,
                p.accountable,
                p.serial_no,
                p.municipal_serial_no,
                p.status,
                p.note,

                -- health & maintenance
                p.health_score,
                CASE WHEN LOWER(TRIM(p.status)) IN ('damaged', 'damage', 'unusable') THEN 'High' ELSE p.risk_level END AS risk_level,
                p.last_checked,
                p.maintenance_interval_days
                {parts_select}

            FROM pcinfofull p
            LEFT JOIN departments d ON p.department_id = d.department_id
            WHERE 1=1
        """

        if has_is_arch:
            query += " AND COALESCE(p.is_archived, 0) = 0"

        params = []
        # Normalize status parameter and default to excluding surrendered PCs
        status_norm = status.strip().lower() if isinstance(status, str) and status.strip() else None
        if status_norm != 'surrendered':
            query += " AND LOWER(COALESCE(p.status, '')) != 'surrendered'"

        if department_id:
            query += " AND p.department_id = %s"
            params.append(department_id)

        if status:
            query += " AND LOWER(COALESCE(p.status, '')) = %s"
            params.append(status_norm)

        if location:
            query += " AND p.location LIKE %s"
            params.append(f"%{location}%")

        if accountable:
            query += " AND p.accountable LIKE %s"
            params.append(f"%{accountable}%")

        if serial_no:
            query += """
                AND (
                    p.serial_no LIKE %s
                    OR p.municipal_serial_no LIKE %s
                )
            """
            params.extend([f"%{serial_no}%", f"%{serial_no}%"])

        if date_from and date_to:
            query += " AND p.date_acquired BETWEEN %s AND %s"
            params.extend([date_from, date_to])

        # 🔹 NEW FILTERS

        if risk_level:
            query += " AND (CASE WHEN LOWER(TRIM(p.status)) IN ('damaged', 'damage', 'unusable') THEN 'High' ELSE p.risk_level END) = %s"
            params.append(risk_level)

        if health_min is not None:
            query += " AND p.health_score >= %s"
            params.append(health_min)

        if health_max is not None:
            query += " AND p.health_score <= %s"
            params.append(health_max)

        if last_checked_from and last_checked_to:
            query += " AND p.last_checked BETWEEN %s AND %s"
            params.extend([last_checked_from, last_checked_to])

        if overdue_only:
            query += """
                AND (
                    p.last_checked IS NULL
                    OR DATE_ADD(
                        p.last_checked,
                        INTERVAL GREATEST(
                            1,
                            CASE
                                WHEN IFNULL(p.maintenance_interval_days, 30) < 365
                                    THEN IFNULL(p.maintenance_interval_days, 30) * 365
                                ELSE IFNULL(p.maintenance_interval_days, 30)
                            END
                        ) DAY
                    ) < CURDATE()
                )
            """

        if needs_checking:
            query += " AND p.status = 'Needs Checking'"

        if search:
            search_cols = ['p.pcname'] + ([f"p.{p}" for p in parts] if parts else [])
            query += " AND (\n" + "\n OR ".join([f"{col} LIKE %s" for col in search_cols]) + "\n)\n"
            params.extend([f"%{search}%"] * len(search_cols))

        query += " ORDER BY p.pcid DESC"

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(query, params)
            pcs = cur.fetchall()

        return jsonify(pcs)

    except Exception as e:
        print(f"❌ Error filtering PCs: {e}")
        return jsonify({"error": "Error filtering PCs"}), 500

    finally:
        conn.close()

@manage_pc_bp.route('/add-pcinfofull', methods=['POST'])
def add_pcinfofull():
    conn = get_db_connection()
    data = request.form.to_dict()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # 🔹 Check inputs: require at least one identifier (serial_no or municipal_serial_no)
            serial = (data.get('serial_no') or '').strip() or None
            municipal = (data.get('municipal_serial_no') or '').strip() or None

            if not serial and not municipal:
                return jsonify({"success": False, "error": "Provide at least Serial No or Municipal Serial No."}), 400

            # 🔹 Conditional duplicate check depending on which identifiers were provided
            if serial and municipal:
                cur.execute("SELECT COUNT(*) AS count FROM pcinfofull WHERE serial_no = %s OR municipal_serial_no = %s", (serial, municipal))
            elif serial:
                cur.execute("SELECT COUNT(*) AS count FROM pcinfofull WHERE serial_no = %s", (serial,))
            else:
                cur.execute("SELECT COUNT(*) AS count FROM pcinfofull WHERE municipal_serial_no = %s", (municipal,))

            duplicate = cur.fetchone()['count']
            if duplicate > 0:
                return jsonify({"success": False, "error": "Duplicate entry: Serial No or Municipal Serial No already exists."}), 400

            # Default new PCs to Available (unless explicitly set) and initialize check/health values
            if not data.get('status'):
                data['status'] = 'Available'

            # Reuse surrendered/damaged/archived numbered slot first for single add.
            department_id = data.get('department_id')
            if department_id:
                # Fetch department info
                cur.execute(
                    "SELECT department_name, department_code, COALESCE(max_pc_allowed, 0) AS max_pc_allowed FROM departments WHERE department_id = %s",
                    (department_id,)
                )
                dept_row = cur.fetchone()

                if dept_row:
                    dept_code = (dept_row.get('department_code') or dept_row.get('department_name') or '').strip().lower().replace(' ', '-')
                    base_pcname = f"pc-{dept_code}"

                    # Check if pcinfofull has is_archived column
                    try:
                        cur.execute("SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pcinfofull' AND COLUMN_NAME = 'is_archived'")
                        _row = cur.fetchone()
                        has_is_arch = bool(_row and (int(_row.get('cnt', 0)) if isinstance(_row, dict) else _row[0] > 0))
                    except Exception:
                        has_is_arch = False

                    # Compute current active PC total (exclude surrendered, damaged/unusable, and archived if column exists)
                    # Compute current active PC total (exclude surrendered, damaged/unusable, and archived if column exists)
                    pc_active_arch_cond = 'AND COALESCE(p.is_archived, 0) = 0' if has_is_arch else ''
                    cur.execute(f"""
                        SELECT COUNT(DISTINCT p.pcid) AS active_total
                        FROM pcinfofull p
                        WHERE p.department_id = %s {pc_active_arch_cond}
                          AND LOWER(COALESCE(p.status, '')) NOT IN ('surrendered', 'damaged', 'damage', 'unusable')
                    """, (department_id,))
                    active_row = cur.fetchone()
                    active_total = int(active_row.get('active_total', 0) if active_row else 0)

                    # Respect max_pc_allowed (0 = no limit)
                    try:
                        max_pc_allowed = int(dept_row.get('max_pc_allowed') or 0)
                    except Exception:
                        max_pc_allowed = 0

                    # Single add counts as one PC
                    new_quantity = 1

                    if max_pc_allowed > 0 and (active_total + new_quantity) > max_pc_allowed:
                        return jsonify({
                            "success": False,
                            "error": f"Cannot add PC: department has reached its PC limit ({max_pc_allowed}). Active PCs: {active_total}."
                        }), 400

                    # Get existing PC names/status for that department (include is_archived if available)
                    if has_is_arch:
                        cur.execute("""
                            SELECT pcname, status, COALESCE(is_archived, 0) AS is_archived
                            FROM pcinfofull
                            WHERE department_id = %s AND pcname LIKE %s
                        """, (department_id, f"{base_pcname}-%"))
                    else:
                        cur.execute("""
                            SELECT pcname, status, 0 AS is_archived
                            FROM pcinfofull
                            WHERE department_id = %s AND pcname LIKE %s
                        """, (department_id, f"{base_pcname}-%"))
                    existing_rows = cur.fetchall()

                    pattern = re.compile(rf"^{re.escape(base_pcname)}-(\d+)$", re.IGNORECASE)
                    active_numbers = set()
                    available_numbers = set()

                    for row in existing_rows:
                        pc_name = (row.get('pcname') or '').strip()
                        match = pattern.match(pc_name)
                        if not match:
                            continue

                        seq_number = int(match.group(1))
                        row_status = str(row.get('status') or '').strip().lower()
                        row_archived = int(row.get('is_archived') or 0)

                        # Treat surrendered, damaged/unusable, or archived rows as available for reuse
                        if row_archived == 1 or row_status in ('surrendered', 'damaged', 'damage', 'unusable'):
                            available_numbers.add(seq_number)
                        else:
                            active_numbers.add(seq_number)

                    reusable_numbers = sorted(num for num in available_numbers if num not in active_numbers)
                    if reusable_numbers:
                        data['pcname'] = f"{base_pcname}-{reusable_numbers[0]:02d}"

            try:
                dept_assigned = bool(department_id)
            except Exception:
                dept_assigned = False

            # Do not override explicit status choices from the form.


            # 🔹 Insert new PC (only include existing part columns)
            parts = _existing_pc_part_columns(cur)
            base_cols = [
                'pcname', 'department_id', 'location', 'quantity', 'acquisition_cost',
                'date_acquired', 'accountable', 'serial_no', 'municipal_serial_no', 'status',
                'note', 'maintenance_interval_days'
            ]
            cols = base_cols + parts + ['last_checked', 'health_score', 'risk_level']
            placeholders = ','.join(['%s'] * (len(base_cols) + len(parts))) + ", CURDATE(), 100, 'Low'"
            sql = f"INSERT INTO pcinfofull ({', '.join(cols)}) VALUES ({placeholders})"
            params = [
                data.get('pcname'), data.get('department_id'), data.get('location'), data.get('quantity'),
                data.get('acquisition_cost'), data.get('date_acquired'), data.get('accountable'),
                serial, municipal, data.get('status'), data.get('note'), data.get('maintenance_interval_days')
            ] + [data.get(p) for p in parts]

            cur.execute(sql, params)

            # Ensure DB defaults/triggers can't leave the newly created PC in an incorrect state
            new_pcid = cur.lastrowid

            # Decide final status for the newly inserted PC (respect explicit choices)
            status_to_set = data.get('status')
            if not status_to_set or str(status_to_set).strip() == '':
                status_to_set = 'IN USE' if dept_assigned else 'Available'

            # Persist canonical defaults for last_checked/health/risk and chosen status
            cur.execute("""
                UPDATE pcinfofull
                SET
                    status = %s,
                    last_checked = CURDATE(),
                    health_score = 100,
                    risk_level = 'Low'
                WHERE pcid = %s
            """, (status_to_set, new_pcid))
            # Reflect canonical status back into data for downstream logic
            data['status'] = status_to_set
            # After committing the new PC, mark any referenced device items as IN USE
            performed_by = None
            try:
                performed_by = session.get('user', {}).get('user_id')
            except Exception:
                performed_by = None

            parts_to_check = parts
            for part_field in parts_to_check:
                _mark_device_values_in_use(cur, data.get(part_field), performed_by)

            conn.commit()

        return jsonify({"success": True, "message": "PC added successfully!"})

    except Exception as e:
        conn.rollback()
        print(f"❌ Error adding PC: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    finally:
        conn.close()
    manage_inventory.risk




@manage_pc_bp.route('/update-pcinfofull', methods=['POST'])
def update_pcinfofull():
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_db_connection()
    data = request.form
    user_id = session['user']['user_id']
    # Ensure status_param is defined early to avoid UnboundLocalError
    status_param = (data.get('status') or '').strip()

    try:
        # Prepare the immutable audit schema before the write transaction.
        try:
            ensure_maintenance_history_schema(conn)
        except Exception as schema_error:
            print(f"[update_pc] maintenance schema error: {schema_error}")

        with conn.cursor(pymysql.cursors.DictCursor) as cur:

            # 🔹 1. Duplicate check (require at least one identifier)
            serial = (data.get('serial_no') or '').strip() or None
            municipal = (data.get('municipal_serial_no') or '').strip() or None
            pcid = data.get('pcid')

            # Debug: log incoming identifiers and requested status
            try:
                print(f"[update_pc] incoming pcid={pcid!r} status_raw={repr(data.get('status'))} department_id={repr(data.get('department_id'))}")
            except Exception:
                pass

            if not serial and not municipal:
                return jsonify({"success": False, "error": "Provide at least Serial No or Municipal Serial No."}), 400

            if serial and municipal:
                cur.execute("SELECT COUNT(*) AS count FROM pcinfofull WHERE (serial_no = %s OR municipal_serial_no = %s) AND pcid != %s", (serial, municipal, pcid))
            elif serial:
                cur.execute("SELECT COUNT(*) AS count FROM pcinfofull WHERE serial_no = %s AND pcid != %s", (serial, pcid))
            else:
                cur.execute("SELECT COUNT(*) AS count FROM pcinfofull WHERE municipal_serial_no = %s AND pcid != %s", (municipal, pcid))

            if cur.fetchone()['count'] > 0:
                return jsonify({"success": False, "error": "Duplicate entry: Serial No or Municipal Serial No already exists."}), 400

            # 🔹 2. Fetch OLD values
            cur.execute("SELECT * FROM pcinfofull WHERE pcid = %s", (data['pcid'],))
            old_pc = cur.fetchone()

            if not old_pc:
                return jsonify({"success": False, "error": "PC not found"}), 404

            # 🔹 3. Perform UPDATE
            # Determine effective status: only derive when status is not provided.
            status_param = (data.get('status') or '').strip()
            dept_param = data.get('department_id')
            try:
                print(f"[update_pc] before derive status_raw={repr(data.get('status'))} dept_param={repr(dept_param)}")
            except Exception:
                pass
            if not status_param:
                status_param = 'IN USE' if dept_param else 'Available'
            try:
                print(f"[update_pc] derived status_param={repr(status_param)}")
            except Exception:
                pass

            # Build dynamic update including only existing part columns
            parts = _existing_pc_part_columns(cur)
            update_fields = [
                "pcname=%s",
                "department_id=%s",
                "location=%s",
                "quantity=%s",
                "acquisition_cost=%s",
                "date_acquired=%s",
                "accountable=%s",
                "serial_no=%s",
                "municipal_serial_no=%s",
                "status=%s",
                "risk_level=CASE WHEN LOWER(TRIM(%s)) IN ('damaged', 'damage', 'unusable') THEN 'High' ELSE risk_level END",
                "note=%s",
                "maintenance_interval_days=%s",
            ]
            params = [
                data.get('pcname'),
                data.get('department_id'),
                data.get('location'),
                data.get('quantity'),
                data.get('acquisition_cost'),
                data.get('date_acquired'),
                data.get('accountable'),
                serial,
                municipal,
                status_param,
                status_param,
                data.get('note'),
                data.get('maintenance_interval_days')
            ]

            for p in parts:
                update_fields.append(f"{p}=%s")
                params.append(data.get(p))

            params.append(data.get('pcid'))
            sql = "UPDATE pcinfofull SET\n                " + ",\n                ".join(update_fields) + "\n            WHERE pcid=%s"
            cur.execute(sql, params)

            # 🔹 4. AUDIT LOGGING
            tracked_fields = [
                'pcname', 'department_id', 'location', 'quantity',
                'acquisition_cost', 'date_acquired', 'accountable',
                'serial_no', 'municipal_serial_no', 'status', 'note',
                'maintenance_interval_days'
            ] + parts

            for field in tracked_fields:
                old_value = old_pc.get(field)
                # Use derived status_param for accurate audit if status was inferred
                if field == 'status':
                    new_value = status_param
                else:
                    new_value = data.get(field)

                # Normalize dates
                if isinstance(old_value, date):
                    old_value = old_value.strftime('%Y-%m-%d')

                # Skip if both empty
                if (old_value is None or old_value == '') and (new_value is None or new_value == ''):
                    continue

                if str(old_value) != str(new_value):
                    log_inventory_action(
                        entity_type='PC',
                        entity_id=data['pcid'],
                        action='UPDATE',
                        field_name=field,
                        old_value=old_value,
                        new_value=new_value,
                        performed_by=user_id
                    )

            # Release any previously assigned parts that are no longer present, then mark new ones as IN USE
            parts_to_check = parts

            # Build token sets
            old_tokens = set()
            for f in parts_to_check:
                try:
                    old_tokens.update(_parse_tokens(old_pc.get(f)))
                except Exception:
                    pass

            new_tokens = set()
            for f in parts_to_check:
                try:
                    new_tokens.update(_parse_tokens(data.get(f)))
                except Exception:
                    pass

            # Tokens removed in the update -> consider releasing them
            tokens_to_release = old_tokens - new_tokens
            for token in tokens_to_release:
                try:
                    _release_device_values_if_unreferenced(cur, token, exclude_pcid=pcid, performed_by=user_id)
                except Exception:
                    pass

            # Mark new/assigned tokens as IN USE
            for part_field in parts_to_check:
                try:
                    _mark_device_values_in_use(cur, data.get(part_field), user_id)
                except Exception:
                    pass

            # Section 4: log repairs, status changes, and part replacements made
            # through the PC edit form.  The maintenance record is immutable.
            technician = current_technician()
            old_status_value = old_pc.get('status')
            pcid_value = data.get('pcid')
            if is_repair_transition(old_status_value, status_param):
                log_maintenance_event(
                    cur,
                    asset_type='PC',
                    asset_id=pcid_value,
                    pcid=pcid_value,
                    action='Repair Completed',
                    maintenance_type='Repair',
                    old_status=old_status_value,
                    new_status=status_param,
                    computer_name=data.get('pcname') or old_pc.get('pcname'),
                    asset_serial=serial or old_pc.get('serial_no'),
                    technician=technician,
                    performed_by=technician,
                    remarks=(
                        f"Repair completed: status changed from "
                        f"{old_status_value or '—'} to {status_param or '—'}."
                    ),
                )
            elif str(old_status_value or '').strip().lower() != str(status_param or '').strip().lower():
                log_maintenance_event(
                    cur,
                    asset_type='PC',
                    asset_id=pcid_value,
                    pcid=pcid_value,
                    action='Status Updated',
                    maintenance_type='Status Change',
                    old_status=old_status_value,
                    new_status=status_param,
                    computer_name=data.get('pcname') or old_pc.get('pcname'),
                    asset_serial=serial or old_pc.get('serial_no'),
                    technician=technician,
                    performed_by=technician,
                    remarks=(
                        f"Status changed from {old_status_value or '—'} "
                        f"to {status_param or '—'}."
                    ),
                )

            for part_field in parts:
                old_part_value = old_pc.get(part_field)
                new_part_value = data.get(part_field)
                if str(old_part_value or '').strip() == str(new_part_value or '').strip():
                    continue
                if not str(new_part_value or '').strip():
                    # A removed part is an inventory release, not a replacement.
                    continue
                log_maintenance_event(
                    cur,
                    asset_type='PC',
                    asset_id=pcid_value,
                    pcid=pcid_value,
                    action='Part Replacement',
                    maintenance_type='Part Replacement',
                    replaced_part=PC_PART_DEFINITIONS.get(part_field, {}).get(
                        'label', part_field.replace('_', ' ').title()
                    ),
                    previous_part=old_part_value or '—',
                    new_part=new_part_value,
                    previous_part_status='IN USE' if old_part_value else '—',
                    new_part_status='IN USE',
                    old_status=old_status_value,
                    new_status=status_param,
                    computer_name=data.get('pcname') or old_pc.get('pcname'),
                    asset_serial=serial or old_pc.get('serial_no'),
                    technician=technician,
                    performed_by=technician,
                    remarks='Part updated from the PC edit form.',
                )

            # After performing updates, fetch current DB status for logging/verification
            try:
                cur.execute("SELECT status FROM pcinfofull WHERE pcid = %s", (data.get('pcid'),))
                _new = cur.fetchone()
                try:
                    print(f"[update_pc] DB status after update: {(_new.get('status') if _new else None)!r}")
                except Exception:
                    pass
            except Exception:
                pass

            conn.commit()

        return jsonify({"success": True, "message": "PC updated successfully!"})

    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

    finally:
        conn.close()
@manage_pc_bp.route('/pc-parts/available', methods=['GET'])
def available_pc_parts():
    """Return available inventory units matching a PC part (Section 3 dialog)."""
    if 'user' not in session:
        return jsonify({"success": False, "error": "Unauthorized", "results": []}), 401

    part = str(request.args.get('part') or '').strip().lower()
    if part not in PC_PART_DEFINITIONS:
        return jsonify({"success": False, "error": "Invalid part selected", "results": []}), 400

    try:
        pcid = int(request.args.get('pcid')) if request.args.get('pcid') else None
    except (TypeError, ValueError):
        pcid = None

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            current_value = ''
            pc_info = None
            if pcid:
                existing_parts = _existing_pc_part_columns(cur)
                if part in existing_parts:
                    cur.execute(
                        f"SELECT pcid, pcname, serial_no, `{part}` AS current_value "
                        "FROM pcinfofull WHERE pcid = %s",
                        (pcid,),
                    )
                else:
                    cur.execute(
                        "SELECT pcid, pcname, serial_no, NULL AS current_value "
                        "FROM pcinfofull WHERE pcid = %s",
                        (pcid,),
                    )
                pc_row = cur.fetchone()
                if pc_row:
                    current_value = pc_row.get('current_value') or ''
                    pc_info = {
                        'pcid': pc_row.get('pcid'),
                        'pcname': pc_row.get('pcname'),
                        'serial_no': pc_row.get('serial_no'),
                    }

            results = _available_part_rows(cur, part, pcid)

        return jsonify({
            "success": True,
            "part": part,
            "part_label": PC_PART_DEFINITIONS[part]['label'],
            "current_value": current_value,
            "pc": pc_info,
            "removed_status_options": list(REMOVED_PART_STATUS_OPTIONS),
            "results": results,
        })
    except _ReplacementError as exc:
        return jsonify({"success": False, "error": exc.message, "results": []}), exc.status_code
    except Exception as exc:
        try:
            print(f"[pc-parts/available] {exc}")
        except Exception:
            pass
        return jsonify({"success": False, "error": "Could not load available inventory.", "results": []}), 500
    finally:
        conn.close()


@manage_pc_bp.route('/replace-pc-part', methods=['POST'])
def replace_pc_part():
    if 'user' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    # Accept JSON or form data; support single-part legacy or multiple replacements
    data = request.get_json(silent=True) or request.form

    # New Section 3 flow: choose one part and one available inventory unit.
    if isinstance(data, dict) and data.get('replacement_accession_id') and data.get('part'):
        return _replace_pc_part_from_inventory(data)

    pcid = data.get('pcid')
    replacements = {}

    # If JSON provided with 'replacements' map
    if isinstance(data, dict) and data.get('replacements'):
        replacements = data.get('replacements') or {}
    else:
        # Fall back to single-part legacy parameters
        part = data.get('part')
        new_value = data.get('new_value') if data.get('new_value') is not None else ''
        if part:
            replacements = {part: new_value}

    allowed_parts = list(PC_PART_ALLOWED)
    if not pcid or not replacements:
        return jsonify({"success": False, "error": "Invalid request parameters"}), 400

    # Optional flag from the Replace Part modal: mark the removed part as
    # Damaged instead of returning it to inventory as Available.
    raw_mark_damaged = data.get('mark_damaged')
    if isinstance(raw_mark_damaged, str):
        mark_damaged = raw_mark_damaged.strip().lower() in ('1', 'true', 'yes', 'on', 'damaged')
    else:
        mark_damaged = bool(raw_mark_damaged)

    # Validate replacement keys
    for p in list(replacements.keys()):
        if p not in allowed_parts:
            return jsonify({"success": False, "error": f"Invalid part: {p}"}), 400

    conn = get_db_connection()
    try:
        # Prepare the immutable audit schema before the write transaction.
        try:
            ensure_maintenance_history_schema(conn)
        except Exception as schema_error:
            print(f"[replace_pc_part] maintenance schema error: {schema_error}")

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Determine which part columns actually exist in the DB for this install
            existing_parts = _existing_pc_part_columns(cur)

            # Make sure the replacement report table exists before any data is
            # changed (DDL commits implicitly, so keep it at the start).
            try:
                _ensure_part_replacement_table(cur)
            except Exception as table_error:
                try:
                    print(f"[replace_pc_part] Could not create replacement report table: {table_error}")
                except Exception:
                    pass

            # Determine requested parts that are not present in the DB schema.
            # Attempt to create missing columns automatically (only for
            # allowed part names) so replacements persist in dedicated cols.
            missing_parts = [p for p in list(replacements.keys()) if p not in existing_parts]
            if missing_parts:
                for p in list(missing_parts):
                    # Only add columns for known/allowed part names
                    if p not in allowed_parts:
                        continue
                    try:
                        cur.execute(f"ALTER TABLE pcinfofull ADD COLUMN `{p}` TEXT")
                        try:
                            print(f"[replace_pc_part] Added missing column `{p}` to pcinfofull")
                        except Exception:
                            pass
                    except Exception as e:
                        try:
                            print(f"[replace_pc_part] Could not add column `{p}`: {e}")
                        except Exception:
                            pass

                # Refresh list of existing parts after attempted ALTERs
                existing_parts = _existing_pc_part_columns(cur)

            # Recompute missing after potential schema changes; if still
            # missing and there's no `other_parts` column to fall back to,
            # return an error for the first unsupported part.
            missing_parts = [p for p in list(replacements.keys()) if p not in existing_parts]
            if missing_parts and 'other_parts' not in existing_parts:
                return jsonify({"success": False, "error": f"Part not supported by current schema: {missing_parts[0]}"}), 400

            # Fetch current values for only the existing parts
            if existing_parts:
                cols = ', '.join(existing_parts)
                cur.execute(
                    f"SELECT pcid, pcname, serial_no, status, {cols} "
                    "FROM pcinfofull WHERE pcid = %s",
                    (pcid,),
                )
                row = cur.fetchone()
            else:
                cur.execute(
                    "SELECT pcid, pcname, serial_no, status "
                    "FROM pcinfofull WHERE pcid = %s",
                    (pcid,),
                )
                row = cur.fetchone()

            if not row:
                return jsonify({"success": False, "error": "PC not found"}), 404

            performed_by = None
            try:
                performed_by = session.get('user', {}).get('user_id')
            except Exception:
                performed_by = None

            # If the caller requested replacements for part columns that do
            # not exist, merge their new-values into `other_parts` (if present)
            if missing_parts:
                tokens_to_add = []
                for p in missing_parts:
                    v = replacements.pop(p, '')
                    if v:
                        tokens_to_add.append(str(v).strip())

                base_other = ''
                try:
                    base_other = (replacements.get('other_parts') or row.get('other_parts') or '')
                    base_other = str(base_other).strip()
                except Exception:
                    base_other = ''

                if tokens_to_add:
                    if base_other:
                        combined = base_other + ', ' + ', '.join(tokens_to_add)
                    else:
                        combined = ', '.join(tokens_to_add)
                    replacements['other_parts'] = combined

            # Build sets of tokens to release: only release tokens that are
            # present in the old values but not present in the new values.
            parts_to_check = [p for p in list(replacements.keys()) if p in existing_parts]
            old_tokens = set()
            for f in parts_to_check:
                try:
                    old_tokens.update(_parse_tokens(row.get(f)))
                except Exception:
                    pass

            new_tokens = set()
            for f in parts_to_check:
                try:
                    new_tokens.update(_parse_tokens(replacements.get(f)))
                except Exception:
                    pass

            tokens_to_release = old_tokens - new_tokens
            released_details = []
            for token in tokens_to_release:
                try:
                    _return_replaced_part(
                        cur,
                        token,
                        mark_damaged=mark_damaged,
                        exclude_pcid=pcid,
                        performed_by=performed_by,
                        collected=released_details,
                    )
                except Exception:
                    pass

            # Build UPDATE statement for all replacements
            set_clauses = []
            params = []
            for part, new_value in replacements.items():
                # Skip if new value equals current value to avoid no-op
                old_value = row.get(part)
                if (old_value is None and (new_value is None or new_value == '')) or str(old_value) == str(new_value):
                    continue
                set_clauses.append(f"{part} = %s")
                params.append(new_value)

            if set_clauses:
                sql = "UPDATE pcinfofull SET " + ", ".join(set_clauses) + " WHERE pcid = %s"
                params.append(pcid)
                cur.execute(sql, params)

            # Record one report row per actually changed part.  The report is
            # what the per-PC "Replacements" button opens.
            replacement_events = []
            try:
                for f in parts_to_check:
                    old_value = row.get(f)
                    new_value = replacements.get(f)
                    try:
                        unchanged = str(old_value) == str(new_value)
                    except Exception:
                        unchanged = old_value == new_value
                    if unchanged:
                        continue

                    part_tokens = _parse_tokens(old_value) - _parse_tokens(new_value)
                    matched = [d for d in released_details if d.get('token') in part_tokens]
                    detail = matched[0] if matched else {}
                    removed_status = detail.get('new_status') or (
                        'Damaged' if mark_damaged else 'Available'
                    )
                    cur.execute(
                        "INSERT INTO pc_part_replacements "
                        "(pcid, part, old_value, new_value, removed_status, accession_id, performed_by) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (
                            pcid,
                            f,
                            old_value,
                            new_value,
                            removed_status,
                            detail.get('accession_id'),
                            performed_by,
                        ),
                    )
                    replacement_events.append(
                        {
                            'part': f,
                            'old_value': old_value,
                            'new_value': new_value,
                            'old_part_status': detail.get('old_status') or 'IN USE',
                            'removed_status': removed_status,
                        }
                    )
            except Exception as report_error:
                try:
                    print(f"[replace_pc_part] Could not record replacement history: {report_error}")
                except Exception:
                    pass

            # Section 4: one immutable maintenance record per changed part.
            technician = current_technician()
            for event in replacement_events:
                log_maintenance_event(
                    cur,
                    asset_type='PC',
                    asset_id=pcid,
                    pcid=pcid,
                    action='Part Replacement',
                    maintenance_type='Part Replacement',
                    replaced_part=PC_PART_DEFINITIONS.get(
                        event['part'], {}
                    ).get('label', event['part'].replace('_', ' ').title()),
                    previous_part=event['old_value'] or '—',
                    new_part=event['new_value'] or '—',
                    previous_part_status=event['removed_status'],
                    new_part_status='IN USE',
                    old_status=event['old_part_status'],
                    new_status='IN USE',
                    computer_name=row.get('pcname'),
                    asset_serial=row.get('serial_no'),
                    technician=technician,
                    performed_by=technician,
                    remarks=f"Removed part recorded as {event['removed_status']}.",
                )

            # Audit and mark new values as IN USE
            for part, new_value in replacements.items():
                old_value = row.get(part)
                try:
                    if str(old_value) != str(new_value):
                        try:
                            log_inventory_action('PC', pcid, 'UPDATE', part, old_value, new_value, performed_by)
                        except Exception:
                            pass
                except Exception:
                    pass

                try:
                    _mark_device_values_in_use(cur, new_value, performed_by)
                except Exception:
                    pass

            conn.commit()
        return jsonify({"success": True, "message": "Part(s) replaced successfully."})

    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

    finally:
        conn.close()


@manage_pc_bp.route('/pc-replacement-history')
def pc_replacement_history():
    """Part replacement report for one PC (opened from the row button)."""
    pcid = request.args.get('pcid', type=int)
    pc = None
    logs = []
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            _ensure_part_replacement_table(cur)
            if pcid:
                cur.execute("SELECT pcid, pcname, serial_no FROM pcinfofull WHERE pcid = %s", (pcid,))
                pc = cur.fetchone()
                cur.execute(
                    "SELECT r.replacement_id, r.pcid, r.part, r.old_value, r.new_value, "
                    "r.removed_status, r.accession_id, r.new_accession_id, r.performed_at, "
                    "COALESCE(u.username, 'System') AS performed_by_name "
                    "FROM pc_part_replacements r "
                    "LEFT JOIN users u ON u.user_id = r.performed_by "
                    "WHERE r.pcid = %s "
                    "ORDER BY r.performed_at DESC, r.replacement_id DESC",
                    (pcid,),
                )
                logs = cur.fetchall()
    finally:
        conn.close()
    return render_template('pc_replacement_history.html', pc=pc, logs=logs, pcid=pcid)


@manage_pc_bp.route('/batch_add_pcinfofull', methods=['POST'])
def batch_add_pcinfofull():
    data = request.get_json()
    if not data or not isinstance(data, list):
        return jsonify({'success': False, 'error': 'Invalid data format'}), 400

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            inserted = 0

            # 🔹 Get department info from first entry
            first_pc = data[0]
            department_id = first_pc.get('department_id')
            if not department_id:
                return jsonify({'success': False, 'error': 'Missing department ID'}), 400

            # 🔹 Get department_code and max_pc_allowed
            cur.execute("SELECT department_name, department_code, COALESCE(max_pc_allowed, 0) AS max_pc_allowed FROM departments WHERE department_id = %s", (department_id,))
            dept_row = cur.fetchone()
            if not dept_row:
                return jsonify({'success': False, 'error': 'Department not found'}), 404

            dept_code = (dept_row.get('department_code') or dept_row.get('department_name')).strip().lower().replace(' ', '-')
            base_pcname = f"pc-{dept_code}"

            # Check if pcinfofull has is_archived column
            try:
                cur.execute("SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pcinfofull' AND COLUMN_NAME = 'is_archived'")
                _row = cur.fetchone()
                has_is_arch = bool(_row and (int(_row.get('cnt', 0)) if isinstance(_row, dict) else _row[0] > 0))
            except Exception:
                has_is_arch = False

            # Compute current active PC total (exclude surrendered, damaged/unusable, and archived if column exists)
            pc_active_arch_cond = 'AND COALESCE(p.is_archived, 0) = 0' if has_is_arch else ''
            cur.execute(f"""
                SELECT COUNT(DISTINCT p.pcid) AS active_total
                FROM pcinfofull p
                WHERE p.department_id = %s {pc_active_arch_cond}
                  AND LOWER(COALESCE(p.status, '')) NOT IN ('surrendered', 'damaged', 'damage', 'unusable')
            """, (department_id,))
            active_row = cur.fetchone()
            active_total = int(active_row.get('active_total', 0) if active_row else 0)

            # Determine total number of new PCs (skip invalid rows and duplicates)
            total_new = 0
            for pc in data:
                serial = pc.get('serial_no') or None
                municipal = pc.get('municipal_serial_no') or None
                if not serial and not municipal:
                    continue
                cur.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM pcinfofull
                    WHERE serial_no = %s OR municipal_serial_no = %s
                    """, (serial, municipal)
                )
                if cur.fetchone()['count'] > 0:
                    # duplicate/skipped
                    continue
                total_new += 1

            try:
                max_pc_allowed = int(dept_row.get('max_pc_allowed') or 0)
            except Exception:
                max_pc_allowed = 0

            if max_pc_allowed > 0 and (active_total + total_new) > max_pc_allowed:
                return jsonify({'success': False, 'error': f'Cannot add PCs: would exceed department PC limit ({max_pc_allowed}). Active: {active_total}, adding: {total_new}'}), 400

            # 🔹 Get existing PC names/status for that department (include is_archived if available)
            if has_is_arch:
                cur.execute("""
                    SELECT pcname, status, COALESCE(is_archived, 0) AS is_archived FROM pcinfofull
                    WHERE department_id = %s AND pcname LIKE %s
                """, (department_id, f"{base_pcname}-%"))
            else:
                cur.execute("""
                    SELECT pcname, status, 0 AS is_archived FROM pcinfofull
                    WHERE department_id = %s AND pcname LIKE %s
                """, (department_id, f"{base_pcname}-%"))
            existing_rows = cur.fetchall()

            # Reuse surrendered/damaged/archived slots first (e.g., if ...-21 is surrendered, next add gets ...-21).
            pattern = re.compile(rf"^{re.escape(base_pcname)}-(\d+)$", re.IGNORECASE)
            active_numbers = set()
            available_numbers = set()

            for row in existing_rows:
                pc_name = (row.get('pcname') or '').strip()
                match = pattern.match(pc_name)
                if not match:
                    continue

                seq_number = int(match.group(1))
                row_status = str(row.get('status') or '').strip().lower()
                row_archived = int(row.get('is_archived') or 0)

                if row_archived == 1 or row_status in ('surrendered', 'damaged', 'damage', 'unusable'):
                    available_numbers.add(seq_number)
                else:
                    active_numbers.add(seq_number)

            reusable_numbers = sorted(num for num in available_numbers if num not in active_numbers)
            all_seen_numbers = active_numbers.union(available_numbers)
            next_number = (max(all_seen_numbers) + 1) if all_seen_numbers else 1


            # 🔹 Pre-validate batch: require at least one identifier per row and detect in-file duplicates
            missing_rows = []
            serial_map = defaultdict(list)
            municipal_map = defaultdict(list)

            for idx, pc in enumerate(data):
                row_no = idx + 1
                s = (pc.get('serial_no') or '').strip() or None
                m = (pc.get('municipal_serial_no') or '').strip() or None
                if not s and not m:
                    missing_rows.append(row_no)
                if s:
                    serial_map[s].append(row_no)
                if m:
                    municipal_map[m].append(row_no)

            duplicate_serials = {s: rows for s, rows in serial_map.items() if len(rows) > 1}
            duplicate_municipals = {m: rows for m, rows in municipal_map.items() if len(rows) > 1}

            if missing_rows or duplicate_serials or duplicate_municipals:
                issues = []
                if missing_rows:
                    issues.append(f"Rows missing both Serial No and Municipal Serial No: {', '.join(map(str, missing_rows))}")
                if duplicate_serials:
                    items = [f"'{s}' (rows {', '.join(map(str, rows))})" for s, rows in duplicate_serials.items()]
                    issues.append(f"Duplicate Serial No in batch: {', '.join(items)}")
                if duplicate_municipals:
                    items = [f"'{m}' (rows {', '.join(map(str, rows))})" for m, rows in duplicate_municipals.items()]
                    issues.append(f"Duplicate Municipal Serial No in batch: {', '.join(items)}")

                return jsonify({'success': False, 'error': '; '.join(issues)}), 400

            # 🔹 Check database for existing identifiers referenced in batch
            db_conflicts = []
            if serial_map:
                placeholders = ','.join(['%s'] * len(serial_map))
                cur.execute(f"SELECT serial_no FROM pcinfofull WHERE serial_no IN ({placeholders})", list(serial_map.keys()))
                for r in cur.fetchall():
                    db_conflicts.append(f"Serial '{r.get('serial_no')}'")
            if municipal_map:
                placeholders = ','.join(['%s'] * len(municipal_map))
                cur.execute(f"SELECT municipal_serial_no FROM pcinfofull WHERE municipal_serial_no IN ({placeholders})", list(municipal_map.keys()))
                for r in cur.fetchall():
                    db_conflicts.append(f"Municipal '{r.get('municipal_serial_no')}'")

            if db_conflicts:
                return jsonify({'success': False, 'error': 'Duplicate in database: ' + ', '.join(db_conflicts)}), 400

            # 🔹 Insert each PC
            for pc in data:
                if not pc.get('status'):
                    pc['status'] = 'Available'

                if reusable_numbers:
                    assigned_number = reusable_numbers.pop(0)
                else:
                    while next_number in active_numbers:
                        next_number += 1
                    assigned_number = next_number
                    next_number += 1

                active_numbers.add(assigned_number)
                pc['pcname'] = f"{base_pcname}-{assigned_number:02d}"

                # Insert using only existing part columns
                parts = _existing_pc_part_columns(cur)
                base_cols = [
                    'pcname', 'department_id', 'location', 'quantity', 'acquisition_cost',
                    'date_acquired', 'accountable', 'serial_no', 'municipal_serial_no', 'status',
                    'note', 'maintenance_interval_days'
                ]
                cols = base_cols + parts + ['last_checked', 'health_score', 'risk_level', 'created_at', 'updated_at']
                placeholders = ','.join(['%s'] * (len(base_cols) + len(parts))) + ", CURDATE(), 100, 'Low', NOW(), NOW()"
                sql = f"INSERT INTO pcinfofull ({', '.join(cols)}) VALUES ({placeholders})"
                params = [pc.get(c) for c in base_cols] + [pc.get(p) for p in parts]
                cur.execute(sql, params)
                # mark referenced device items as IN USE for this pc
                performed_by = None
                try:
                    performed_by = session.get('user', {}).get('user_id')
                except Exception:
                    performed_by = None

                for part_field in parts:
                    _mark_device_values_in_use(cur, pc.get(part_field), performed_by)

                inserted += 1

        conn.commit()
        return jsonify({
            'success': True,
        #     'message': f'Successfully added {inserted} PCs under {dept_row['department_name']} ({dept_code.upper()})'
        # 
        })

    except Exception as e:
        print("❌ Batch insert error:", e)
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

@manage_pc_bp.route('/manage_pc/export-selected-pcs', methods=['POST'])
def export_selected_pcs():

    data = request.get_json(silent=True) or {}
    pcids = data.get("pcids") or []

    try:
        pcids = [int(pid) for pid in pcids]
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid PC selection"}), 400

    if not pcids:
        return jsonify({"error": "No PCs selected"}), 400

    conn = get_db_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:

            format_strings = ','.join(['%s'] * len(pcids))

            cur.execute(f"""
                SELECT
                    p.pcid,
                    p.pcname,
                    COALESCE(d.department_name, '') AS department,
                    p.location,
                    p.acquisition_cost,
                    p.date_acquired,
                    p.accountable,
                    p.serial_no,
                    p.municipal_serial_no,
                    p.status
                FROM pcinfofull p
                LEFT JOIN departments d ON p.department_id = d.department_id
                WHERE p.pcid IN ({format_strings})
                ORDER BY p.pcid
            """, pcids)

            rows = cur.fetchall()

        if not rows:
            return jsonify({"error": "No matching PCs found for export"}), 404

        df = pd.DataFrame(rows).rename(columns={
            "pcid": "PC ID",
            "pcname": "PC Name",
            "department": "Department",
            "location": "Location",
            "acquisition_cost": "Acquisition Cost",
            "date_acquired": "Date Acquired",
            "accountable": "Accountable",
            "serial_no": "Serial No.",
            "municipal_serial_no": "Municipal Serial No.",
            "status": "Status",
        })

        output = BytesIO()

        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            sheet_name = 'Selected PCs'
            df.to_excel(writer, index=False, sheet_name=sheet_name)

            ws = writer.sheets[sheet_name]

            header_fill = PatternFill(fill_type='solid', fgColor='4F46E5')
            header_font = Font(color='FFFFFF', bold=True)
            thin_side = Side(style='thin', color='D1D5DB')
            cell_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

            for col_idx, col_name in enumerate(df.columns, start=1):
                header_cell = ws.cell(row=1, column=col_idx)
                header_cell.fill = header_fill
                header_cell.font = header_font
                header_cell.alignment = Alignment(horizontal='center', vertical='center')
                header_cell.border = cell_border

                values = [str(col_name)]
                for value in df.iloc[:, col_idx - 1].tolist():
                    values.append('' if value is None else str(value))
                max_len = max(len(v) for v in values)
                ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 3, 36)

            for row_idx in range(2, ws.max_row + 1):
                for col_idx in range(1, ws.max_column + 1):
                    cell = ws.cell(row=row_idx, column=col_idx)
                    cell.alignment = Alignment(horizontal='left', vertical='center')
                    cell.border = cell_border

            column_map = {name: idx + 1 for idx, name in enumerate(df.columns)}

            if 'Acquisition Cost' in column_map:
                col = column_map['Acquisition Cost']
                for row_idx in range(2, ws.max_row + 1):
                    ws.cell(row=row_idx, column=col).number_format = '#,##0.00'

            if 'Date Acquired' in column_map:
                col = column_map['Date Acquired']
                for row_idx in range(2, ws.max_row + 1):
                    date_cell = ws.cell(row=row_idx, column=col)
                    date_cell.number_format = 'yyyy-mm-dd'
                    date_cell.alignment = Alignment(horizontal='center', vertical='center')

            for name in ('PC ID', 'Status'):
                if name in column_map:
                    col = column_map[name]
                    for row_idx in range(2, ws.max_row + 1):
                        ws.cell(row=row_idx, column=col).alignment = Alignment(horizontal='center', vertical='center')

            ws.freeze_panes = 'A2'
            ws.auto_filter.ref = ws.dimensions

        output.seek(0)

        return send_file(
            output,
            as_attachment=True,
            download_name="selected_pcs.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    except Exception as e:
        print("❌ Export error:", e)
        return jsonify({"error": str(e)}), 500

    finally:
        conn.close()

@manage_pc_bp.route('/manage_pc/import-pcs-excel', methods=['POST'])
def import_pcs_excel():
    file = request.files.get("file")
    duplicate_option = request.form.get("duplicate_option", "skip")

    if not file:
        return jsonify({"success": False, "error": "No file uploaded"})

    if duplicate_option not in {"skip", "ignore", "overwrite"}:
        duplicate_option = "skip"

    conn = None

    try:
        df = pd.read_excel(file)

        # Normalize incoming headers so both template exports and raw imports are supported.
        def normalize_header(name):
            text = str(name).strip().lower()
            text = re.sub(r"[^a-z0-9]+", "_", text)
            return text.strip("_")

        df = df.rename(columns={col: normalize_header(col) for col in df.columns})

        alias_map = {
            "pc_name": "pcname",
            "department_name": "department",
        }
        for old_name, new_name in alias_map.items():
            if old_name in df.columns and new_name not in df.columns:
                df[new_name] = df[old_name]

        # Lightweight cleaning helper used for pre-validation and per-row processing
        def clean_value(value):
            if pd.isna(value):
                return None
            if isinstance(value, str):
                value = value.strip()
                return value or None
            if isinstance(value, pd.Timestamp):
                return value.date()
            return value

        # Pre-validate uploaded file for duplicate serial / municipal numbers
        serial_rows = defaultdict(list)
        municipal_rows = defaultdict(list)
        missing_both_rows = []

        for idx, row in df.iterrows():
            row_number = int(idx) + 2  # Excel row (header at row 1)
            serial = clean_value(row.get("serial_no"))
            municipal = clean_value(row.get("municipal_serial_no"))

            if not serial and not municipal:
                missing_both_rows.append(row_number)

            if serial:
                serial_rows[serial].append(row_number)

            if municipal:
                municipal_rows[municipal].append(row_number)

        duplicate_serials = {s: rows for s, rows in serial_rows.items() if len(rows) > 1}
        duplicate_municipals = {m: rows for m, rows in municipal_rows.items() if len(rows) > 1}

        # If the uploaded file contains duplicates within itself, fail fast and return details
        if duplicate_serials or duplicate_municipals or missing_both_rows:
            issues = []
            if duplicate_serials:
                items = [f"'{s}' (rows {', '.join(map(str, rows))})" for s, rows in duplicate_serials.items()]
                issues.append(f"Duplicate Serial No in file: {', '.join(items)}")
            if duplicate_municipals:
                items = [f"'{m}' (rows {', '.join(map(str, rows))})" for m, rows in duplicate_municipals.items()]
                issues.append(f"Duplicate Municipal Serial No in file: {', '.join(items)}")
            if missing_both_rows:
                issues.append(f"Rows missing both Serial No and Municipal Serial No: {', '.join(map(str, missing_both_rows))}")

            return jsonify({
                "success": False,
                "error": "; ".join(issues)
            }), 400

        conn = get_db_connection()
        cur = conn.cursor(pymysql.cursors.DictCursor)

        added = 0
        updated = 0
        skipped = 0

        department_cache = {}

        for _, row in df.iterrows():

            pcname = clean_value(row.get("pcname"))
            serial = clean_value(row.get("serial_no"))
            municipal = clean_value(row.get("municipal_serial_no"))
            location = clean_value(row.get("location"))
            acquisition_cost = clean_value(row.get("acquisition_cost"))
            date_acquired = clean_value(row.get("date_acquired"))
            accountable = clean_value(row.get("accountable"))
            status = clean_value(row.get("status")) or "Available"
            status_text = str(status).strip()
            normalized_status_text = status_text.lower()
            imported_risk = "High" if normalized_status_text in {"damaged", "damage", "unusable"} else "Low"

            department_id = clean_value(row.get("department_id"))
            department_name = clean_value(row.get("department"))

            if not department_id and department_name:
                if department_name not in department_cache:
                    cur.execute(
                        "SELECT department_id FROM departments WHERE department_name = %s LIMIT 1",
                        (department_name,)
                    )
                    dept_row = cur.fetchone()
                    department_cache[department_name] = dept_row["department_id"] if dept_row else None
                department_id = department_cache.get(department_name)

            if not serial and not municipal:
                skipped += 1
                continue

            existing = None
            if serial and municipal:
                cur.execute(
                    "SELECT pcid FROM pcinfofull WHERE serial_no = %s OR municipal_serial_no = %s LIMIT 1",
                    (serial, municipal)
                )
                existing = cur.fetchone()
            elif serial:
                cur.execute(
                    "SELECT pcid FROM pcinfofull WHERE serial_no = %s LIMIT 1",
                    (serial,)
                )
                existing = cur.fetchone()
            elif municipal:
                cur.execute(
                    "SELECT pcid FROM pcinfofull WHERE municipal_serial_no = %s LIMIT 1",
                    (municipal,)
                )
                existing = cur.fetchone()

            if existing:
                if duplicate_option == "skip":
                    skipped += 1
                    continue

                if duplicate_option == "ignore":
                    skipped += 1
                    continue

                if duplicate_option == "overwrite":
                    cur.execute("""
                        UPDATE pcinfofull SET
                        pcname = COALESCE(%s, pcname),
                        department_id = COALESCE(%s, department_id),
                        location = COALESCE(%s, location),
                        acquisition_cost = COALESCE(%s, acquisition_cost),
                        date_acquired = COALESCE(%s, date_acquired),
                        accountable = COALESCE(%s, accountable),
                        status = COALESCE(%s, status),
                        risk_level = CASE WHEN LOWER(TRIM(COALESCE(%s, status))) IN ('damaged', 'damage', 'unusable') THEN 'High' ELSE risk_level END
                        WHERE pcid=%s
                    """, (
                        pcname,
                        department_id,
                        location,
                        acquisition_cost,
                        date_acquired,
                        accountable,
                        status,
                        status,
                        existing["pcid"]
                    ))

                    updated += 1
                    continue

            if not pcname:
                skipped += 1
                continue

            cur.execute("""
                INSERT INTO pcinfofull
                (pcname,department_id,location,acquisition_cost,date_acquired,
                 accountable,serial_no,municipal_serial_no,status,
                 last_checked,health_score,risk_level)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, CURDATE(), 100, %s)
            """, (
                pcname,
                department_id,
                location,
                acquisition_cost,
                date_acquired,
                accountable,
                serial,
                municipal,
                status,
                imported_risk
            ))

            added += 1

        conn.commit()

        return jsonify({
            "success": True,
            "added": added,
            "updated": updated,
            "skipped": skipped
        })

    except Exception as e:
        print("Import error:", e)
        return jsonify({"success": False, "error": str(e)})

    finally:
        if conn:
            conn.close()