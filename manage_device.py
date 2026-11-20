"""Manage Device module.

A standalone device register with:
- inventory CRUD, filtering, bulk actions and Excel import/export
- "Used In" tracking (a specific PC or a department)
- field-level change log exposed through the Modification Report tab
"""

import random
import re
import time
from datetime import date
from io import BytesIO
from urllib.parse import urlencode

import pandas as pd
import pymysql
from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from db import get_db_connection
from inventory_auto_check import calculate_health_and_risk
from utils.device_audit import ensure_device_schema, log_device_changes
from utils.permissions import check_permission

manage_device_bp = Blueprint(
    "manage_device_bp", __name__, template_folder="templates"
)

ALLOWED_PER_PAGE = {10, 25, 50, 100}
DEFAULT_PER_PAGE = 10

STATUS_OPTIONS = [
    "Available",
    "IN USE",
    "Needs Fixing",
    "Defective",
    "For Disposal",
    "Inactive",
    "Damaged",
    "Surrendered",
]
USAGE_SCOPE_OPTIONS = [
    ("UNASSIGNED", "Not assigned"),
    ("PC", "Assigned to a PC"),
    ("DEPARTMENT", "Assigned to a department"),
]

REPORT_ACTIONS = [
    "ADDED",
    "UPDATED",
    "REPLACED",
    "STATUS_CHANGED",
    "ASSIGNED",
    "CHECKED",
    "ARCHIVED",
    "IMPORTED",
    "BULK_UPDATE",
    "RISK_UPDATED",
]

DEVICE_TYPE_EXCLUDED = "consumable"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _normalize_page(value, default=1):
    try:
        page = int(value)
    except (TypeError, ValueError):
        page = default
    return max(1, page)


def _normalize_per_page(value):
    try:
        per_page = int(value)
    except (TypeError, ValueError):
        per_page = DEFAULT_PER_PAGE
    return per_page if per_page in ALLOWED_PER_PAGE else DEFAULT_PER_PAGE


def _clamp_page(page, total_pages):
    if total_pages <= 0:
        return 1
    return min(max(1, page), total_pages)


def _optional_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _actor():
    user = session.get("user") or {}
    return user.get("user_id"), user.get("username") or "Unknown"


def _generate_serial(prefix="SN"):
    return f"{prefix}{int(time.time() * 1000) % 1000000}{random.randint(10, 99)}"


def _effective_risk(status, risk_level):
    status_key = (status or "").strip().lower()
    if status_key in {"damaged", "damage", "unusable"}:
        return "High"
    return risk_level or "Low"


def _used_in_label(row):
    """Human readable 'Used In' value for a device row."""
    scope = (row.get("usage_scope") or "").strip().upper()
    pcid = row.get("used_on_pcid")
    pcname = row.get("pcname")
    pc_department = row.get("pc_department_name")
    department = row.get("department_name")
    status_key = (row.get("status") or "").strip().lower()

    if pcid or scope == "PC":
        label = f"PC: {pcname}" if pcname else "PC"
        if pcid:
            label += f" (#{pcid})"
        if pc_department:
            label += f" — {pc_department}"
        return label

    if scope == "DEPARTMENT" or (status_key in {"in use", "in used", "inuse"} and department):
        return f"Dept: {department}" if department else "Department"

    return "Unassigned"


def _device_select_parts(state):
    """Return (select_columns, join_sql) that adapt to the live schema."""
    used_pc_select = (
        "df.used_on_pcid AS used_on_pcid,"
        if state.get("used_on_pcid")
        else "NULL AS used_on_pcid,"
    )
    usage_scope_select = (
        "df.usage_scope AS usage_scope,"
        if state.get("usage_scope")
        else "'UNASSIGNED' AS usage_scope,"
    )

    columns = f"""
        df.accession_id,
        df.item_name,
        df.brand_model,
        df.serial_no,
        df.municipal_serial_no,
        df.quantity,
        df.device_type,
        df.acquisition_cost,
        df.date_acquired,
        df.accountable,
        df.department_id,
        df.status,
        df.maintenance_interval_days,
        df.last_checked,
        df.health_score,
        df.risk_level,
        {used_pc_select}
        {usage_scope_select}
        dep.department_name AS department_name,
        pc.pcname AS pcname,
        pcdep.department_name AS pc_department_name
    """

    joins = """
        LEFT JOIN departments dep ON dep.department_id = df.department_id
    """
    if state.get("used_on_pcid"):
        joins += """
        LEFT JOIN pcinfofull pc ON pc.pcid = df.used_on_pcid
        LEFT JOIN departments pcdep ON pcdep.department_id = pc.department_id
        """
    else:
        joins += """
        LEFT JOIN pcinfofull pc ON 1 = 0
        LEFT JOIN departments pcdep ON 1 = 0
        """

    return columns, joins


def _base_device_where():
    return (
        "COALESCE(df.is_archived, 0) = 0 "
        "AND LOWER(COALESCE(df.status, '')) NOT IN ('surrendered', 'archived') "
        f"AND LOWER(COALESCE(df.device_type, '')) <> '{DEVICE_TYPE_EXCLUDED}'"
    )


def _parse_inventory_filters():
    return {
        "search": (request.args.get("search") or "").strip(),
        "department_id": _optional_int(request.args.get("department_id")),
        "status": (request.args.get("status") or "").strip(),
        "risk_level": (request.args.get("risk_level") or "").strip(),
        "usage_scope": (request.args.get("usage_scope") or "").strip().upper(),
        "accountable": (request.args.get("accountable") or "").strip(),
        "date_from": (request.args.get("date_from") or "").strip(),
        "date_to": (request.args.get("date_to") or "").strip(),
    }


def _parse_report_filters():
    return {
        "search": (request.args.get("r_search") or "").strip(),
        "action": (request.args.get("r_action") or "").strip().upper(),
        "department_id": _optional_int(request.args.get("r_department_id")),
        "date_from": (request.args.get("r_date_from") or "").strip(),
        "date_to": (request.args.get("r_date_to") or "").strip(),
        "device_id": _optional_int(request.args.get("r_device_id")),
    }


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def _fetch_devices(state, filters, page, per_page):
    columns, joins = _device_select_parts(state)
    where = [_base_device_where()]
    params = []

    if filters["search"]:
        term = f"%{filters['search']}%"
        where.append(
            "("
            "df.item_name LIKE %s OR df.brand_model LIKE %s OR "
            "df.serial_no LIKE %s OR df.municipal_serial_no LIKE %s OR "
            "df.accountable LIKE %s OR CAST(df.accession_id AS CHAR) LIKE %s"
            ")"
        )
        params.extend([term] * 6)

    if filters["department_id"]:
        where.append("df.department_id = %s")
        params.append(filters["department_id"])

    if filters["status"]:
        where.append("LOWER(COALESCE(df.status, '')) = %s")
        params.append(filters["status"].lower())

    if filters["risk_level"]:
        where.append("LOWER(COALESCE(df.risk_level, '')) = %s")
        params.append(filters["risk_level"].lower())

    if filters["usage_scope"] and state.get("usage_scope"):
        where.append("UPPER(COALESCE(df.usage_scope, 'UNASSIGNED')) = %s")
        params.append(filters["usage_scope"])

    if filters["accountable"]:
        where.append("df.accountable LIKE %s")
        params.append(f"%{filters['accountable']}%")

    if filters["date_from"]:
        where.append("df.date_acquired >= %s")
        params.append(filters["date_from"])

    if filters["date_to"]:
        where.append("df.date_acquired <= %s")
        params.append(filters["date_to"])

    where_sql = " AND ".join(where)

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                f"SELECT COUNT(*) AS total FROM devices_full df WHERE {where_sql}",
                params,
            )
            total = cur.fetchone()["total"] or 0

            total_pages = (total + per_page - 1) // per_page if total else 1
            page = _clamp_page(page, total_pages)
            offset = (page - 1) * per_page

            cur.execute(
                f"""
                SELECT {columns}
                FROM devices_full df
                {joins}
                WHERE {where_sql}
                ORDER BY df.accession_id DESC
                LIMIT %s OFFSET %s
                """,
                params + [per_page, offset],
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    for row in rows:
        row["used_in"] = _used_in_label(row)
        row["effective_risk"] = _effective_risk(row.get("status"), row.get("risk_level"))

    return rows, total, total_pages, page


def _fetch_stats(state):
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN LOWER(COALESCE(status, '')) IN ('in use', 'in used', 'inuse') THEN 1 ELSE 0 END) AS in_use,
                    SUM(CASE WHEN LOWER(COALESCE(status, '')) = 'available' THEN 1 ELSE 0 END) AS available,
                    SUM(CASE WHEN LOWER(COALESCE(status, '')) IN ('damaged', 'damage', 'unusable') THEN 1 ELSE 0 END) AS damaged,
                    SUM(CASE WHEN LOWER(COALESCE(risk_level, '')) = 'high' THEN 1 ELSE 0 END) AS high_risk,
                    SUM(
                        CASE
                            WHEN LOWER(COALESCE(df.status, '')) IN ('damaged', 'damage', 'unusable') THEN 0
                            WHEN df.last_checked IS NULL THEN 1
                            WHEN DATEDIFF(CURDATE(), df.last_checked) >= GREATEST(1, CASE WHEN IFNULL(df.maintenance_interval_days, 30) < 365 THEN IFNULL(df.maintenance_interval_days, 30) * 365 ELSE IFNULL(df.maintenance_interval_days, 30) END) THEN 1
                            WHEN DATEDIFF(CURDATE(), df.last_checked) >= (GREATEST(1, CASE WHEN IFNULL(df.maintenance_interval_days, 30) < 365 THEN IFNULL(df.maintenance_interval_days, 30) * 365 ELSE IFNULL(df.maintenance_interval_days, 30) END) * 0.8) THEN 1
                            ELSE 0
                        END
                    ) AS maintenance_due
                FROM devices_full df
                WHERE {_base_device_where()}
                """
            )
            row = cur.fetchone() or {}

            cur.execute(
                f"""
                SELECT COUNT(*) AS archived
                FROM devices_full df
                WHERE (
                    COALESCE(df.is_archived, 0) = 1
                    OR LOWER(COALESCE(df.status, '')) IN ('archived', 'surrendered')
                )
                AND LOWER(COALESCE(df.device_type, '')) <> '{DEVICE_TYPE_EXCLUDED}'
                """
            )
            archived_row = cur.fetchone() or {}
    finally:
        conn.close()

    return {
        "total": row.get("total") or 0,
        "in_use": row.get("in_use") or 0,
        "available": row.get("available") or 0,
        "damaged": row.get("damaged") or 0,
        "high_risk": row.get("high_risk") or 0,
        "maintenance_due": row.get("maintenance_due") or 0,
        "archived": archived_row.get("archived") or 0,
    }


def _fetch_report(state, filters, page, per_page):
    if not state.get("change_logs"):
        return [], 0, 1, 1

    where = ["1 = 1"]
    params = []

    if filters["search"]:
        term = f"%{filters['search']}%"
        where.append(
            "("
            "l.item_name LIKE %s OR l.serial_no LIKE %s OR "
            "l.changed_by_name LIKE %s OR l.remarks LIKE %s OR "
            "CAST(l.device_id AS CHAR) LIKE %s"
            ")"
        )
        params.extend([term] * 5)

    if filters["action"]:
        where.append("UPPER(l.action) = %s")
        params.append(filters["action"])

    if filters["department_id"]:
        where.append("df.department_id = %s")
        params.append(filters["department_id"])

    if filters["date_from"]:
        where.append("DATE(l.changed_at) >= %s")
        params.append(filters["date_from"])

    if filters["date_to"]:
        where.append("DATE(l.changed_at) <= %s")
        params.append(filters["date_to"])

    if filters["device_id"]:
        where.append("l.device_id = %s")
        params.append(filters["device_id"])

    where_sql = " AND ".join(where)

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM device_change_logs l
                LEFT JOIN devices_full df ON df.accession_id = l.device_id
                WHERE {where_sql}
                """,
                params,
            )
            total = cur.fetchone()["total"] or 0

            total_pages = (total + per_page - 1) // per_page if total else 1
            page = _clamp_page(page, total_pages)
            offset = (page - 1) * per_page

            cur.execute(
                f"""
                SELECT
                    l.log_id,
                    l.device_id,
                    l.item_name,
                    l.serial_no,
                    l.action,
                    l.field_name,
                    l.old_value,
                    l.new_value,
                    l.remarks,
                    l.changed_by,
                    l.changed_by_name,
                    l.changed_at,
                    dep.department_name
                FROM device_change_logs l
                LEFT JOIN devices_full df ON df.accession_id = l.device_id
                LEFT JOIN departments dep ON dep.department_id = df.department_id
                WHERE {where_sql}
                ORDER BY l.changed_at DESC, l.log_id DESC
                LIMIT %s OFFSET %s
                """,
                params + [per_page, offset],
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    for row in rows:
        if row.get("changed_at") is not None:
            row["changed_at_display"] = row["changed_at"].strftime("%Y-%m-%d %I:%M %p")
        else:
            row["changed_at_display"] = ""

    return rows, total, total_pages, page


def _fetch_departments():
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT department_id, department_name FROM departments ORDER BY department_name"
            )
            return cur.fetchall()
    finally:
        conn.close()


def _fetch_pcs():
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """
                SELECT p.pcid, p.pcname, p.department_id,
                       dep.department_name
                FROM pcinfofull p
                LEFT JOIN departments dep ON dep.department_id = p.department_id
                WHERE COALESCE(p.is_archived, 0) = 0
                  AND LOWER(COALESCE(p.status, '')) <> 'surrendered'
                ORDER BY p.pcname
                """
            )
            return cur.fetchall()
    finally:
        conn.close()


def _resolve_department_names(conn, department_ids):
    ids = [d for d in department_ids if d]
    if not ids:
        return {}
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(
            f"SELECT department_id, department_name FROM departments WHERE department_id IN ({placeholders})",
            ids,
        )
        return {row["department_id"]: row["department_name"] for row in cur.fetchall()}


def _resolve_pc_names(conn, pcids):
    ids = [p for p in pcids if p]
    if not ids:
        return {}
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(
            f"SELECT pcid, pcname FROM pcinfofull WHERE pcid IN ({placeholders})",
            ids,
        )
        return {row["pcid"]: row["pcname"] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
@manage_device_bp.route("/manage_device")
def device_page():
    conn = get_db_connection()
    try:
        state = ensure_device_schema(conn)
    finally:
        conn.close()

    page = _normalize_page(request.args.get("page", 1, type=int))
    per_page = _normalize_per_page(request.args.get("per_page", DEFAULT_PER_PAGE, type=int))
    r_page = _normalize_page(request.args.get("r_page", 1, type=int))
    r_per_page = _normalize_per_page(
        request.args.get("r_per_page", DEFAULT_PER_PAGE, type=int)
    )

    filters = _parse_inventory_filters()
    report_filters = _parse_report_filters()

    devices, total, total_pages, page = _fetch_devices(
        state, filters, page, per_page
    )
    report_rows, report_total, report_pages, r_page = _fetch_report(
        state, report_filters, r_page, r_per_page
    )

    inventory_qs = urlencode(
        {
            key: value
            for key, value in {**filters, "per_page": per_page}.items()
            if value not in (None, "")
        }
    )
    report_qs = urlencode(
        {
            key: value
            for key, value in {**report_filters, "r_per_page": r_per_page}.items()
            if value not in (None, "")
        }
    )

    return render_template(
        "manage_device.html",
        devices=devices,
        total_items=total,
        total_pages=total_pages,
        page=page,
        per_page=per_page,
        filters=filters,
        stats=_fetch_stats(state),
        departments=_fetch_departments(),
        pcs=_fetch_pcs(),
        status_options=STATUS_OPTIONS,
        usage_scope_options=USAGE_SCOPE_OPTIONS,
        report_actions=REPORT_ACTIONS,
        report_rows=report_rows,
        report_total=report_total,
        report_pages=report_pages,
        r_page=r_page,
        r_per_page=r_per_page,
        report_filters=report_filters,
        active_tab=request.args.get("tab", "inventory"),
        schema_state=state,
        inventory_qs=inventory_qs,
        report_qs=report_qs,
    )


# ---------------------------------------------------------------------------
# JSON helpers for the modal
# ---------------------------------------------------------------------------
@manage_device_bp.route("/manage_device/departments")
def departments_json():
    return jsonify(_fetch_departments())


@manage_device_bp.route("/manage_device/pcs")
def pcs_json():
    return jsonify(_fetch_pcs())


@manage_device_bp.route("/manage_device/get/<int:device_id>")
def get_device_json(device_id):
    conn = get_db_connection()
    try:
        state = ensure_device_schema(conn)
        columns, joins = _device_select_parts(state)
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                f"""
                SELECT {columns}
                FROM devices_full df
                {joins}
                WHERE df.accession_id = %s
                """,
                (device_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "Device not found"}), 404

    row["used_in"] = _used_in_label(row)
    row["interval_years"] = _days_to_years(row.get("maintenance_interval_days"))
    row["date_acquired"] = _iso_date(row.get("date_acquired"))
    row["last_checked"] = _iso_date(row.get("last_checked"))
    return jsonify(row)


# ---------------------------------------------------------------------------
# Create / update / archive
# ---------------------------------------------------------------------------
def _iso_date(value):
    """Return ``YYYY-MM-DD`` for date/datetime values so HTML date inputs work."""
    if value in (None, ""):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _days_to_years(raw_value):
    try:
        value = int(raw_value or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return ""
    if value >= 365:
        return max(1, round(value / 365))
    return value


def _years_to_days(raw_value, default_days=365):
    try:
        years = int(raw_value)
    except (TypeError, ValueError):
        return default_days
    return max(1, years) * 365


def _form_payload(form):
    usage_scope = (form.get("usage_scope") or "UNASSIGNED").strip().upper()
    used_on_pcid = _optional_int(form.get("used_on_pcid"))

    if usage_scope == "PC" and not used_on_pcid:
        usage_scope = "UNASSIGNED"
    if usage_scope != "PC":
        used_on_pcid = None

    return {
        "item_name": (form.get("item_name") or "").strip(),
        "brand_model": (form.get("brand_model") or "").strip() or None,
        "serial_no": (form.get("serial_no") or "").strip(),
        "municipal_serial_no": (form.get("municipal_serial_no") or "").strip() or None,
        "device_type": (form.get("device_type") or "Device").strip() or "Device",
        "acquisition_cost": form.get("acquisition_cost") or None,
        "date_acquired": form.get("date_acquired") or None,
        "accountable": (form.get("accountable") or "").strip() or None,
        "department_id": _optional_int(form.get("department_id")),
        "status": (form.get("status") or "Available").strip() or "Available",
        "maintenance_interval_days": _years_to_days(
            form.get("maintenance_interval_years")
        ),
        "usage_scope": usage_scope,
        "used_on_pcid": used_on_pcid,
    }


def _risk_for_payload(payload, last_checked=None):
    score, risk = calculate_health_and_risk(
        {
            "status": payload["status"],
            "maintenance_interval_days": payload["maintenance_interval_days"],
            "last_checked": last_checked,
            "date_acquired": payload["date_acquired"],
        }
    )
    return score, risk


@manage_device_bp.route("/manage_device/add", methods=["POST"])
@check_permission("inventory", "edit")
def add_device():
    conn = get_db_connection()
    try:
        state = ensure_device_schema(conn)
        payload = _form_payload(request.form)

        if not payload["item_name"]:
            flash("Item name is required.", "danger")
            return redirect(url_for("manage_device_bp.device_page"))

        if not payload["serial_no"]:
            payload["serial_no"] = _generate_serial("SN")
        if not payload["municipal_serial_no"]:
            payload["municipal_serial_no"] = _generate_serial("MSN")

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """
                SELECT accession_id FROM devices_full
                WHERE serial_no = %s OR municipal_serial_no = %s
                LIMIT 1
                """,
                (payload["serial_no"], payload["municipal_serial_no"]),
            )
            if cur.fetchone():
                flash("Serial No or Municipal Serial No already exists.", "danger")
                return redirect(url_for("manage_device_bp.device_page"))

            score, risk = _risk_for_payload(payload)

            columns = [
                "item_name",
                "brand_model",
                "serial_no",
                "municipal_serial_no",
                "quantity",
                "device_type",
                "acquisition_cost",
                "date_acquired",
                "accountable",
                "department_id",
                "status",
                "maintenance_interval_days",
                "health_score",
                "risk_level",
                "last_checked",
            ]
            values = [
                payload["item_name"],
                payload["brand_model"],
                payload["serial_no"],
                payload["municipal_serial_no"],
                1,
                payload["device_type"],
                payload["acquisition_cost"],
                payload["date_acquired"],
                payload["accountable"],
                payload["department_id"],
                payload["status"],
                payload["maintenance_interval_days"],
                score,
                risk,
                date.today(),
            ]

            if state.get("used_on_pcid"):
                columns.append("used_on_pcid")
                values.append(payload["used_on_pcid"])
            if state.get("usage_scope"):
                columns.append("usage_scope")
                values.append(payload["usage_scope"])

            placeholders = ", ".join(["%s"] * len(values))
            cur.execute(
                f"INSERT INTO devices_full ({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )
            device_id = cur.lastrowid
        conn.commit()

        changed_by, changed_by_name = _actor()
        log_device_changes(
            conn,
            device_id=device_id,
            item_name=payload["item_name"],
            serial_no=payload["serial_no"],
            action="ADDED",
            remarks=f"Device added ({payload['status']})",
            changed_by=changed_by,
            changed_by_name=changed_by_name,
        )

        flash(f"{payload['item_name']} added successfully.", "success")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"MANAGE DEVICE ADD ERROR: {exc}")
        flash("Error adding device.", "danger")
    finally:
        conn.close()

    return redirect(url_for("manage_device_bp.device_page"))


@manage_device_bp.route("/manage_device/update", methods=["POST"])
@check_permission("inventory", "edit")
def update_device():
    conn = get_db_connection()
    try:
        state = ensure_device_schema(conn)
        device_id = _optional_int(request.form.get("accession_id"))
        if not device_id:
            flash("Missing device ID.", "danger")
            return redirect(url_for("manage_device_bp.device_page"))

        payload = _form_payload(request.form)
        if not payload["item_name"]:
            flash("Item name is required.", "danger")
            return redirect(url_for("manage_device_bp.device_page"))

        if not payload["serial_no"]:
            payload["serial_no"] = _generate_serial("SN")
        if not payload["municipal_serial_no"]:
            payload["municipal_serial_no"] = _generate_serial("MSN")

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM devices_full WHERE accession_id = %s",
                (device_id,),
            )
            old = cur.fetchone()
            if not old:
                flash("Device not found.", "danger")
                return redirect(url_for("manage_device_bp.device_page"))

            cur.execute(
                """
                SELECT accession_id FROM devices_full
                WHERE (serial_no = %s OR municipal_serial_no = %s)
                  AND accession_id <> %s
                LIMIT 1
                """,
                (payload["serial_no"], payload["municipal_serial_no"], device_id),
            )
            if cur.fetchone():
                flash("Serial No or Municipal Serial No already exists.", "danger")
                return redirect(url_for("manage_device_bp.device_page"))

            score, risk = _risk_for_payload(payload, old.get("last_checked"))

            set_parts = [
                "item_name = %s",
                "brand_model = %s",
                "serial_no = %s",
                "municipal_serial_no = %s",
                "device_type = %s",
                "acquisition_cost = %s",
                "date_acquired = %s",
                "accountable = %s",
                "department_id = %s",
                "status = %s",
                "maintenance_interval_days = %s",
                "health_score = %s",
                "risk_level = %s",
            ]
            values = [
                payload["item_name"],
                payload["brand_model"],
                payload["serial_no"],
                payload["municipal_serial_no"],
                payload["device_type"],
                payload["acquisition_cost"],
                payload["date_acquired"],
                payload["accountable"],
                payload["department_id"],
                payload["status"],
                payload["maintenance_interval_days"],
                score,
                risk,
            ]

            if state.get("used_on_pcid"):
                set_parts.append("used_on_pcid = %s")
                values.append(payload["used_on_pcid"])
            if state.get("usage_scope"):
                set_parts.append("usage_scope = %s")
                values.append(payload["usage_scope"])

            values.append(device_id)
            cur.execute(
                f"UPDATE devices_full SET {', '.join(set_parts)} WHERE accession_id = %s",
                values,
            )

            dept_names = _resolve_department_names(
                conn,
                [old.get("department_id"), payload["department_id"]],
            )
            pc_names = _resolve_pc_names(
                conn,
                [old.get("used_on_pcid"), payload["used_on_pcid"]]
                if state.get("used_on_pcid")
                else [],
            )

        conn.commit()

        # Build a field-level diff for the report.
        changes = []
        tracked = [
            ("item_name", old.get("item_name"), payload["item_name"]),
            ("brand_model", old.get("brand_model"), payload["brand_model"]),
            ("serial_no", old.get("serial_no"), payload["serial_no"]),
            (
                "municipal_serial_no",
                old.get("municipal_serial_no"),
                payload["municipal_serial_no"],
            ),
            ("acquisition_cost", old.get("acquisition_cost"), payload["acquisition_cost"]),
            (
                "date_acquired",
                _iso_date(old.get("date_acquired")),
                _iso_date(payload["date_acquired"]),
            ),
            ("accountable", old.get("accountable"), payload["accountable"]),
            (
                "department_id",
                dept_names.get(old.get("department_id")),
                dept_names.get(payload["department_id"]),
            ),
            ("status", old.get("status"), payload["status"]),
            (
                "maintenance_interval_days",
                _days_to_years(old.get("maintenance_interval_days")),
                _days_to_years(payload["maintenance_interval_days"]),
            ),
            ("risk_level", old.get("risk_level"), risk),
        ]

        if state.get("used_on_pcid"):
            tracked.append(
                (
                    "used_on_pcid",
                    pc_names.get(old.get("used_on_pcid")),
                    pc_names.get(payload["used_on_pcid"]),
                )
            )
        if state.get("usage_scope"):
            tracked.append(
                ("usage_scope", old.get("usage_scope"), payload["usage_scope"])
            )

        changes = [c for c in tracked if str(c[1]) != str(c[2])]

        if changes:
            changed_by, changed_by_name = _actor()
            log_device_changes(
                conn,
                device_id=device_id,
                item_name=payload["item_name"],
                serial_no=payload["serial_no"],
                changes=changes,
                changed_by=changed_by,
                changed_by_name=changed_by_name,
            )

        flash(f"{payload['item_name']} updated successfully.", "success")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"MANAGE DEVICE UPDATE ERROR: {exc}")
        flash("Error updating device.", "danger")
    finally:
        conn.close()

    return redirect(url_for("manage_device_bp.device_page"))


@manage_device_bp.route("/manage_device/archive/<int:device_id>", methods=["POST"])
@check_permission("inventory", "edit")
def archive_device(device_id):
    conn = get_db_connection()
    try:
        ensure_device_schema(conn)
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT item_name, serial_no FROM devices_full WHERE accession_id = %s",
                (device_id,),
            )
            row = cur.fetchone()
            if not row:
                flash("Device not found.", "danger")
                return redirect(url_for("manage_device_bp.device_page"))

            cur.execute(
                """
                UPDATE devices_full
                SET is_archived = 1, deleted_at = NOW()
                WHERE accession_id = %s
                """,
                (device_id,),
            )
        conn.commit()

        changed_by, changed_by_name = _actor()
        log_device_changes(
            conn,
            device_id=device_id,
            item_name=row.get("item_name"),
            serial_no=row.get("serial_no"),
            action="ARCHIVED",
            remarks="Device moved to archive",
            changed_by=changed_by,
            changed_by_name=changed_by_name,
        )
        flash(f"{row.get('item_name')} archived.", "success")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"MANAGE DEVICE ARCHIVE ERROR: {exc}")
        flash("Error archiving device.", "danger")
    finally:
        conn.close()

    return redirect(url_for("manage_device_bp.device_page"))


# ---------------------------------------------------------------------------
# Status checks and bulk actions
# ---------------------------------------------------------------------------
def _recalculate_and_log(conn, device_id, old_row, status=None, remarks="Marked as checked"):
    status = status or old_row.get("status") or "Available"
    score, risk = calculate_health_and_risk(
        {
            "status": status,
            "maintenance_interval_days": old_row.get("maintenance_interval_days"),
            "last_checked": date.today(),
            "date_acquired": old_row.get("date_acquired"),
        }
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE devices_full
            SET last_checked = CURDATE(), health_score = %s, risk_level = %s
            WHERE accession_id = %s
            """,
            (score, risk, device_id),
        )
    conn.commit()

    changed_by, changed_by_name = _actor()
    log_device_changes(
        conn,
        device_id=device_id,
        item_name=old_row.get("item_name"),
        serial_no=old_row.get("serial_no"),
        action="CHECKED",
        remarks=f"{remarks} (risk: {risk}, health: {score}%)",
        changed_by=changed_by,
        changed_by_name=changed_by_name,
    )


@manage_device_bp.route("/manage_device/check/<int:device_id>", methods=["POST"])
@check_permission("inventory", "edit")
def mark_checked(device_id):
    conn = get_db_connection()
    try:
        ensure_device_schema(conn)
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM devices_full WHERE accession_id = %s",
                (device_id,),
            )
            row = cur.fetchone()
        if not row:
            flash("Device not found.", "danger")
        else:
            _recalculate_and_log(conn, device_id, row)
            flash(f"{row.get('item_name')} marked as checked.", "success")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"MANAGE DEVICE CHECK ERROR: {exc}")
        flash("Error checking device.", "danger")
    finally:
        conn.close()

    return redirect(url_for("manage_device_bp.device_page"))


def _selected_ids():
    ids = request.form.getlist("device_ids")
    result = []
    for value in ids:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _bulk_rows(conn, device_ids):
    if not device_ids:
        return []
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        placeholders = ",".join(["%s"] * len(device_ids))
        cur.execute(
            f"SELECT * FROM devices_full WHERE accession_id IN ({placeholders})",
            device_ids,
        )
        return cur.fetchall()


@manage_device_bp.route("/manage_device/bulk-check", methods=["POST"])
@check_permission("inventory", "edit")
def bulk_check():
    device_ids = _selected_ids()
    if not device_ids:
        flash("Select at least one device.", "danger")
        return redirect(url_for("manage_device_bp.device_page"))

    conn = get_db_connection()
    try:
        ensure_device_schema(conn)
        rows = _bulk_rows(conn, device_ids)
        for row in rows:
            _recalculate_and_log(conn, row["accession_id"], row, remarks="Bulk check")
        flash(f"{len(rows)} device(s) marked as checked.", "success")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"MANAGE DEVICE BULK CHECK ERROR: {exc}")
        flash("Error checking selected devices.", "danger")
    finally:
        conn.close()

    return redirect(url_for("manage_device_bp.device_page"))


@manage_device_bp.route("/manage_device/bulk-surrender", methods=["POST"])
@check_permission("inventory", "edit")
def bulk_surrender():
    device_ids = _selected_ids()
    if not device_ids:
        flash("Select at least one device.", "danger")
        return redirect(url_for("manage_device_bp.device_page"))

    conn = get_db_connection()
    try:
        state = ensure_device_schema(conn)
        rows = _bulk_rows(conn, device_ids)
        changed_by, changed_by_name = _actor()

        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(device_ids))
            set_sql = (
                "status = 'Surrendered', is_archived = 1, deleted_at = NOW()"
            )
            if state.get("used_on_pcid"):
                set_sql += ", used_on_pcid = NULL"
            if state.get("usage_scope"):
                set_sql += ", usage_scope = 'UNASSIGNED'"
            cur.execute(
                f"UPDATE devices_full SET {set_sql} WHERE accession_id IN ({placeholders})",
                device_ids,
            )
        conn.commit()

        for row in rows:
            log_device_changes(
                conn,
                device_id=row["accession_id"],
                item_name=row.get("item_name"),
                serial_no=row.get("serial_no"),
                action="STATUS_CHANGED",
                changes=[("status", row.get("status"), "Surrendered")],
                remarks="Bulk surrender",
                changed_by=changed_by,
                changed_by_name=changed_by_name,
            )
        flash(f"{len(rows)} device(s) surrendered.", "success")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"MANAGE DEVICE BULK SURRENDER ERROR: {exc}")
        flash("Error surrendering selected devices.", "danger")
    finally:
        conn.close()

    return redirect(url_for("manage_device_bp.device_page"))


@manage_device_bp.route("/manage_device/bulk-damaged", methods=["POST"])
@check_permission("inventory", "edit")
def bulk_damaged():
    device_ids = _selected_ids()
    if not device_ids:
        flash("Select at least one device.", "danger")
        return redirect(url_for("manage_device_bp.device_page"))

    conn = get_db_connection()
    try:
        ensure_device_schema(conn)
        rows = _bulk_rows(conn, device_ids)
        changed_by, changed_by_name = _actor()

        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(device_ids))
            cur.execute(
                f"""
                UPDATE devices_full
                SET status = 'Damaged', risk_level = 'High', health_score = 0
                WHERE accession_id IN ({placeholders})
                """,
                device_ids,
            )
        conn.commit()

        for row in rows:
            log_device_changes(
                conn,
                device_id=row["accession_id"],
                item_name=row.get("item_name"),
                serial_no=row.get("serial_no"),
                action="STATUS_CHANGED",
                changes=[
                    ("status", row.get("status"), "Damaged"),
                    ("risk_level", row.get("risk_level"), "High"),
                ],
                remarks="Bulk mark as damaged",
                changed_by=changed_by,
                changed_by_name=changed_by_name,
            )
        flash(f"{len(rows)} device(s) marked as damaged.", "success")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"MANAGE DEVICE BULK DAMAGED ERROR: {exc}")
        flash("Error marking selected devices as damaged.", "danger")
    finally:
        conn.close()

    return redirect(url_for("manage_device_bp.device_page"))


@manage_device_bp.route("/manage_device/risk-update", methods=["POST"])
@check_permission("inventory", "edit")
def risk_update():
    conn = get_db_connection()
    try:
        ensure_device_schema(conn)
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                f"SELECT * FROM devices_full df WHERE {_base_device_where()}"
            )
            rows = cur.fetchall()

        changed_by, changed_by_name = _actor()
        updated = 0
        for row in rows:
            score, risk = calculate_health_and_risk(row)
            if score == row.get("health_score") and risk == row.get("risk_level"):
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE devices_full
                    SET health_score = %s, risk_level = %s
                    WHERE accession_id = %s
                    """,
                    (score, risk, row["accession_id"]),
                )
            conn.commit()
            updated += 1
            log_device_changes(
                conn,
                device_id=row["accession_id"],
                item_name=row.get("item_name"),
                serial_no=row.get("serial_no"),
                action="RISK_UPDATED",
                changes=[("risk_level", row.get("risk_level"), risk)],
                remarks=f"Health score recalculated to {score}%",
                changed_by=changed_by,
                changed_by_name=changed_by_name,
            )

        flash(f"Risk levels recalculated. {updated} device(s) updated.", "success")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"MANAGE DEVICE RISK UPDATE ERROR: {exc}")
        flash("Error updating risk levels.", "danger")
    finally:
        conn.close()

    return redirect(url_for("manage_device_bp.device_page"))


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------
@manage_device_bp.route("/manage_device/export", methods=["POST"])
def export_devices():
    data = request.get_json(silent=True) or {}
    device_ids = data.get("device_ids") or []
    try:
        device_ids = [int(value) for value in device_ids]
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid device selection"}), 400

    if not device_ids:
        return jsonify({"error": "No devices selected"}), 400

    conn = get_db_connection()
    try:
        state = ensure_device_schema(conn)
        columns, joins = _device_select_parts(state)
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            placeholders = ",".join(["%s"] * len(device_ids))
            cur.execute(
                f"""
                SELECT {columns}
                FROM devices_full df
                {joins}
                WHERE df.accession_id IN ({placeholders})
                ORDER BY df.accession_id
                """,
                device_ids,
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return jsonify({"error": "No matching devices found"}), 404

    export_rows = []
    for row in rows:
        export_rows.append(
            {
                "Item Number": row.get("accession_id"),
                "Item Name": row.get("item_name"),
                "Brand / Model": row.get("brand_model"),
                "Serial No": row.get("serial_no"),
                "Municipal Serial No": row.get("municipal_serial_no"),
                "Acquisition Cost": row.get("acquisition_cost"),
                "Date Acquired": row.get("date_acquired"),
                "Accountable": row.get("accountable"),
                "Department": row.get("department_name"),
                "Status": row.get("status"),
                "Interval (Years)": _days_to_years(
                    row.get("maintenance_interval_days")
                ),
                "Risk Level": row.get("effective_risk")
                or _effective_risk(row.get("status"), row.get("risk_level")),
                "Used In": _used_in_label(row),
            }
        )

    df = pd.DataFrame(export_rows)

    output_columns = [
        "Item Number",
        "Item Name",
        "Brand / Model",
        "Serial No",
        "Municipal Serial No",
        "Acquisition Cost",
        "Date Acquired",
        "Accountable",
        "Department",
        "Status",
        "Interval (Years)",
        "Risk Level",
        "Used In",
    ]
    df = df[output_columns]

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Manage Device")
        ws = writer.sheets.get("Manage Device")
        if ws:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            header_font = Font(bold=True, color="FFFFFF")
            header_fill = PatternFill("solid", fgColor="1D4ED8")
            header_alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True
            )
            for cell in ws[1]:
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_alignment
            for col_idx, column_cells in enumerate(ws.columns, start=1):
                letter = get_column_letter(col_idx)
                max_len = max(
                    (len(str(c.value)) for c in column_cells if c.value is not None),
                    default=10,
                )
                ws.column_dimensions[letter].width = min(max(max_len + 2, 12), 40)

    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="manage_device_export.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@manage_device_bp.route("/manage_device/import", methods=["POST"])
@check_permission("inventory", "edit")
def import_devices():
    file = request.files.get("file")
    duplicate_option = request.form.get("duplicate_option", "skip")
    if duplicate_option not in {"skip", "overwrite"}:
        duplicate_option = "skip"

    if not file:
        return jsonify({"success": False, "error": "No file uploaded"})

    conn = get_db_connection()
    try:
        state = ensure_device_schema(conn)
        df = pd.read_excel(file)

        def normalize_header(name):
            text = str(name).strip().lower()
            text = re.sub(r"[^a-z0-9]+", "_", text)
            return text.strip("_")

        df = df.rename(columns={col: normalize_header(col) for col in df.columns})

        alias_map = {
            "item_number": "accession_id",
            "device_name": "item_name",
            "department_name": "department",
            "used_in": "used_in",
            "risk_level": "risk_level",
        }
        for old_name, new_name in alias_map.items():
            if old_name in df.columns and new_name not in df.columns:
                df[new_name] = df[old_name]

        def clean(value):
            if pd.isna(value):
                return None
            if isinstance(value, str):
                value = value.strip()
                return value or None
            if isinstance(value, pd.Timestamp):
                return value.date()
            return value

        department_cache = {}
        pc_cache = {}
        added = updated = skipped = 0
        changed_by, changed_by_name = _actor()

        for _, row in df.iterrows():
            item_name = clean(row.get("item_name"))
            if not item_name:
                skipped += 1
                continue

            serial = clean(row.get("serial_no")) or _generate_serial("SN")
            municipal = clean(row.get("municipal_serial_no")) or _generate_serial("MSN")

            department_id = clean(row.get("department_id"))
            department_name = clean(row.get("department"))
            if not department_id and department_name:
                if department_name not in department_cache:
                    with conn.cursor(pymysql.cursors.DictCursor) as cur:
                        cur.execute(
                            "SELECT department_id FROM departments WHERE department_name = %s LIMIT 1",
                            (department_name,),
                        )
                        found = cur.fetchone()
                    department_cache[department_name] = (
                        found["department_id"] if found else None
                    )
                department_id = department_cache.get(department_name)

            used_in = clean(row.get("used_in"))
            used_on_pcid = clean(row.get("used_on_pcid"))
            usage_scope = clean(row.get("usage_scope"))

            if used_in and not used_on_pcid:
                match = re.match(r"PC:\s*([^()]+)", str(used_in), re.IGNORECASE)
                if match:
                    pc_name = match.group(1).strip()
                    if pc_name not in pc_cache:
                        with conn.cursor(pymysql.cursors.DictCursor) as cur:
                            cur.execute(
                                "SELECT pcid FROM pcinfofull WHERE pcname = %s LIMIT 1",
                                (pc_name,),
                            )
                            found = cur.fetchone()
                        pc_cache[pc_name] = found["pcid"] if found else None
                    used_on_pcid = pc_cache.get(pc_name)

            if used_on_pcid:
                usage_scope = "PC"
            elif department_id:
                usage_scope = usage_scope or "DEPARTMENT"
            else:
                usage_scope = "UNASSIGNED"

            payload = {
                "item_name": item_name,
                "brand_model": clean(row.get("brand_model")),
                "serial_no": serial,
                "municipal_serial_no": municipal,
                "device_type": clean(row.get("device_type")) or "Device",
                "acquisition_cost": clean(row.get("acquisition_cost")),
                "date_acquired": clean(row.get("date_acquired")),
                "accountable": clean(row.get("accountable")),
                "department_id": int(department_id) if department_id else None,
                "status": clean(row.get("status")) or "Available",
                "maintenance_interval_days": _years_to_days(
                    clean(row.get("interval_years"))
                    or clean(row.get("interval"))
                    or clean(row.get("maintenance_interval_days"))
                ),
                "usage_scope": usage_scope,
                "used_on_pcid": int(used_on_pcid) if used_on_pcid else None,
            }

            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    """
                    SELECT accession_id FROM devices_full
                    WHERE serial_no = %s OR municipal_serial_no = %s
                    LIMIT 1
                    """,
                    (serial, municipal),
                )
                existing = cur.fetchone()

            if existing and duplicate_option == "skip":
                skipped += 1
                continue

            score, risk = _risk_for_payload(payload)

            if existing:
                set_parts = [
                    "item_name = %s",
                    "brand_model = %s",
                    "acquisition_cost = %s",
                    "date_acquired = %s",
                    "accountable = %s",
                    "department_id = %s",
                    "status = %s",
                    "maintenance_interval_days = %s",
                    "health_score = %s",
                    "risk_level = %s",
                ]
                values = [
                    payload["item_name"],
                    payload["brand_model"],
                    payload["acquisition_cost"],
                    payload["date_acquired"],
                    payload["accountable"],
                    payload["department_id"],
                    payload["status"],
                    payload["maintenance_interval_days"],
                    score,
                    risk,
                ]
                if state.get("used_on_pcid"):
                    set_parts.append("used_on_pcid = %s")
                    values.append(payload["used_on_pcid"])
                if state.get("usage_scope"):
                    set_parts.append("usage_scope = %s")
                    values.append(payload["usage_scope"])
                values.append(existing["accession_id"])
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE devices_full SET {', '.join(set_parts)} WHERE accession_id = %s",
                        values,
                    )
                conn.commit()
                updated += 1
                log_device_changes(
                    conn,
                    device_id=existing["accession_id"],
                    item_name=payload["item_name"],
                    serial_no=serial,
                    action="IMPORTED",
                    remarks="Existing device overwritten by Excel import",
                    changed_by=changed_by,
                    changed_by_name=changed_by_name,
                )
            else:
                columns = [
                    "item_name",
                    "brand_model",
                    "serial_no",
                    "municipal_serial_no",
                    "quantity",
                    "device_type",
                    "acquisition_cost",
                    "date_acquired",
                    "accountable",
                    "department_id",
                    "status",
                    "maintenance_interval_days",
                    "health_score",
                    "risk_level",
                    "last_checked",
                ]
                values = [
                    payload["item_name"],
                    payload["brand_model"],
                    serial,
                    municipal,
                    1,
                    payload["device_type"],
                    payload["acquisition_cost"],
                    payload["date_acquired"],
                    payload["accountable"],
                    payload["department_id"],
                    payload["status"],
                    payload["maintenance_interval_days"],
                    score,
                    risk,
                    date.today(),
                ]
                if state.get("used_on_pcid"):
                    columns.append("used_on_pcid")
                    values.append(payload["used_on_pcid"])
                if state.get("usage_scope"):
                    columns.append("usage_scope")
                    values.append(payload["usage_scope"])

                placeholders = ", ".join(["%s"] * len(values))
                with conn.cursor() as cur:
                    cur.execute(
                        f"INSERT INTO devices_full ({', '.join(columns)}) VALUES ({placeholders})",
                        values,
                    )
                    new_id = cur.lastrowid
                conn.commit()
                added += 1
                log_device_changes(
                    conn,
                    device_id=new_id,
                    item_name=payload["item_name"],
                    serial_no=serial,
                    action="IMPORTED",
                    remarks="Device created by Excel import",
                    changed_by=changed_by,
                    changed_by_name=changed_by_name,
                )

        return jsonify({"success": True, "added": added, "updated": updated, "skipped": skipped})
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"MANAGE DEVICE IMPORT ERROR: {exc}")
        return jsonify({"success": False, "error": str(exc)})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# History + report export
# ---------------------------------------------------------------------------
@manage_device_bp.route("/manage_device/history/<int:device_id>")
def device_history(device_id):
    conn = get_db_connection()
    try:
        state = ensure_device_schema(conn)
        if not state.get("change_logs"):
            return jsonify({"device_id": device_id, "logs": []})
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """
                SELECT log_id, action, field_name, old_value, new_value,
                       remarks, changed_by_name, changed_at
                FROM device_change_logs
                WHERE device_id = %s
                ORDER BY changed_at DESC, log_id DESC
                LIMIT 200
                """,
                (device_id,),
            )
            logs = cur.fetchall()
    finally:
        conn.close()

    for log in logs:
        if log.get("changed_at") is not None:
            log["changed_at"] = log["changed_at"].strftime("%Y-%m-%d %I:%M %p")
        else:
            log["changed_at"] = ""

    return jsonify({"device_id": device_id, "logs": logs})


@manage_device_bp.route("/manage_device/report-export")
def export_report():
    conn = get_db_connection()
    try:
        state = ensure_device_schema(conn)
        if not state.get("change_logs"):
            rows = []
        else:
            where = ["1 = 1"]
            params = []
            search = (request.args.get("r_search") or "").strip()
            action = (request.args.get("r_action") or "").strip().upper()
            department_id = _optional_int(request.args.get("r_department_id"))
            date_from = (request.args.get("r_date_from") or "").strip()
            date_to = (request.args.get("r_date_to") or "").strip()

            if search:
                term = f"%{search}%"
                where.append(
                    "(l.item_name LIKE %s OR l.serial_no LIKE %s OR l.changed_by_name LIKE %s OR l.remarks LIKE %s)"
                )
                params.extend([term] * 4)
            if action:
                where.append("UPPER(l.action) = %s")
                params.append(action)
            if department_id:
                where.append("df.department_id = %s")
                params.append(department_id)
            if date_from:
                where.append("DATE(l.changed_at) >= %s")
                params.append(date_from)
            if date_to:
                where.append("DATE(l.changed_at) <= %s")
                params.append(date_to)

            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT
                        l.changed_at AS `Date / Time`,
                        l.device_id AS `Item Number`,
                        l.item_name AS `Item Name`,
                        l.serial_no AS `Serial No`,
                        l.action AS `Action`,
                        l.field_name AS `Field`,
                        l.old_value AS `Old Value`,
                        l.new_value AS `New Value`,
                        l.remarks AS `Remarks`,
                        l.changed_by_name AS `Changed By`
                    FROM device_change_logs l
                    LEFT JOIN devices_full df ON df.accession_id = l.device_id
                    WHERE {' AND '.join(where)}
                    ORDER BY l.changed_at DESC, l.log_id DESC
                    """,
                    params,
                )
                rows = cur.fetchall()
    finally:
        conn.close()

    df = pd.DataFrame(rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        sheet_name = "Device Modifications"
        if df.empty:
            df = pd.DataFrame(
                columns=[
                    "Date / Time",
                    "Item Number",
                    "Item Name",
                    "Serial No",
                    "Action",
                    "Field",
                    "Old Value",
                    "New Value",
                    "Remarks",
                    "Changed By",
                ]
            )
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        ws = writer.sheets.get(sheet_name)
        if ws:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            header_font = Font(bold=True, color="FFFFFF")
            header_fill = PatternFill("solid", fgColor="1D4ED8")
            for cell in ws[1]:
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            for col_idx, column_cells in enumerate(ws.columns, start=1):
                letter = get_column_letter(col_idx)
                max_len = max(
                    (len(str(c.value)) for c in column_cells if c.value is not None),
                    default=10,
                )
                ws.column_dimensions[letter].width = min(max(max_len + 2, 12), 45)

    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name="device_modification_report.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
