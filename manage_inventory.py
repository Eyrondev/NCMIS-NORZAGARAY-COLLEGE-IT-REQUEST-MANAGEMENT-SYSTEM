from flask import Blueprint, render_template, flash, jsonify, request, redirect, url_for
from db import get_db_connection
import pymysql
from datetime import date, datetime
from inventory_auto_check import calculate_health_and_risk, normalize_interval_days, parse_date
from utils.maintenance_audit import (
    current_technician,
    ensure_maintenance_history_schema,
    log_maintenance_event,
)

 

 

manage_inventory_bp = Blueprint('manage_inventory', __name__, template_folder='templates')

ALLOWED_PER_PAGE = {5, 10, 25, 50}
DEFAULT_PER_PAGE = 10


INVENTORY_CATEGORY_DEFINITIONS = {
    "pcs": {
        "title": "Manage PCs",
        "singular": "PC",
        "kind": "pc",
        "icon": "cpu",
        "endpoint": "manage_inventory.inventory_pcs",
        "description": "Workstation records, assignment, health checks, and acquisition details.",
        "empty_title": "No PCs found",
        "empty_message": "Add a PC or adjust the current filters to see workstation records here.",
        "default_device_type": "",
        "status_options": ["Available", "IN USE", "Damaged", "Inactive", "Needs Checking"],
    },
    "components": {
        "title": "Manage Components",
        "singular": "Component",
        "kind": "device",
        "icon": "circuit-board",
        "endpoint": "manage_inventory.inventory_components",
        "description": "Internal hardware and replacement parts used to build, repair, and upgrade PCs.",
        "empty_title": "No components found",
        "empty_message": "Add a component, or classify a device by setting its item type or name to a component.",
        "default_device_type": "Component",
        "status_options": ["Available", "IN USE", "Damaged", "Needs Fixing", "Defective", "For Disposal", "Inactive", "Needs Checking"],
    },
    "peripherals": {
        "title": "Manage Peripherals",
        "singular": "Peripheral",
        "kind": "device",
        "icon": "keyboard",
        "endpoint": "manage_inventory.inventory_peripherals",
        "description": "External devices connected to workstations such as monitors, keyboards, printers, and network equipment.",
        "empty_title": "No peripherals found",
        "empty_message": "Add a peripheral, or classify a device by setting its item type or name to a peripheral.",
        "default_device_type": "Peripheral",
        "status_options": ["Available", "IN USE", "Damaged", "Needs Fixing", "Defective", "For Disposal", "Inactive", "Needs Checking"],
    },
    "consumables": {
        "title": "Manage Consumables",
        "singular": "Consumable",
        "kind": "consumable",
        "icon": "package",
        "endpoint": "manage_inventory.inventory_consumables",
        "description": "Stock items consumed during daily operations, with quantity and stock-level tracking.",
        "empty_title": "No consumables found",
        "empty_message": "Add a consumable or adjust the current filters to see stock records here.",
        "default_device_type": "",
        "status_options": ["Available", "Low Stock", "Out of Stock", "Damaged"],
    },
    "accessories": {
        "title": "Manage Accessories",
        "singular": "Accessory",
        "kind": "device",
        "icon": "cable",
        "endpoint": "manage_inventory.inventory_accessories",
        "description": "Cables, adapters, mounts, and other supporting items that complement devices.",
        "empty_title": "No accessories found",
        "empty_message": "Add an accessory, or classify a device by setting its item type or name to an accessory.",
        "default_device_type": "Accessory",
        "status_options": ["Available", "IN USE", "Damaged", "Needs Fixing", "Defective", "For Disposal", "Inactive", "Needs Checking"],
    },
}

_DEVICE_CATEGORY_TYPE_VALUES = {
    "components": ("component", "components", "part", "parts"),
    "peripherals": ("peripheral", "peripherals"),
    "accessories": ("accessory", "accessories"),
}

# Keyword order matters: the first matching group wins. Accessories are checked
# first so compound names such as "Monitor Stand" or "USB-C Adapter" land in the
# supporting-items page instead of the peripheral page.
_DEVICE_CATEGORY_KEYWORD_GROUPS = (
    ("accessories", (
        "cable", "cord", "adapter", "adaptor", "dongle", "hub", "stand", "mount",
        "bracket", "bag", "cover", "sleeve", "strap", "lock", "stylus", "pen",
        "cleaning kit", "dust cover", "screen protector", "mousepad", "mouse pad",
        "wrist rest", "extension", "connector", "splitter", "memory card", "sd card",
    )),
    ("peripherals", (
        "monitor", "display", "keyboard", "mouse", "webcam", "camera", "speaker",
        "headset", "headphone", "microphone", "printer", "scanner", "projector",
        "ups", "avr", "voltage regulator", "external drive", "external ssd",
        "external hdd", "drawing tablet", "tablet", "joystick", "gamepad",
        "barcode scanner", "label printer", "kvm", "docking station", "dock",
        "router", "switch", "access point", "modem", "firewall",
    )),
    ("components", (
        "motherboard", "mainboard", "processor", "cpu", "ram", "memory", "ssd",
        "hdd", "hard drive", "hard disk", "nvme", "graphics card", "gpu",
        "video card", "power supply", "psu", "casing", "cpu cooler", "cooler",
        "heatsink", "fan", "network card", "lan card", "sound card", "wifi card",
        "wireless card", "battery", "cmos", "expansion card", "riser card",
        "capture card",
    )),
)


def _device_category_case_sql():
    """Return a SQL CASE expression that assigns each device to one inventory page."""
    clauses = []
    for category_key, type_values in _DEVICE_CATEGORY_TYPE_VALUES.items():
        quoted = ", ".join("'%s'" % value for value in type_values)
        clauses.append(
            f"WHEN LOWER(TRIM(COALESCE(device_type, ''))) IN ({quoted}) THEN '{category_key}'"
        )

    clauses.extend([
        "WHEN LOWER(TRIM(COALESCE(device_type, ''))) IN ('pc', 'computer', 'desktop', 'laptop') THEN 'devices'",
        "WHEN LOWER(TRIM(COALESCE(device_type, ''))) = 'consumable' THEN 'consumables'",
        "WHEN TRIM(COALESCE(device_type, '')) REGEXP '^[0-9]+$' THEN 'consumables'",
    ])

    for category_key, keywords in _DEVICE_CATEGORY_KEYWORD_GROUPS:
        keyword_sql = " OR ".join(
            "LOCATE('%s', LOWER(COALESCE(item_name, ''))) > 0" % keyword
            for keyword in keywords
        )
        clauses.append(f"WHEN {keyword_sql} THEN '{category_key}'")

    return "CASE " + " ".join(clauses) + " ELSE 'devices' END"


_DEVICE_CATEGORY_CASE_SQL = _device_category_case_sql()
_DEVICE_ACTIVE_WHERE = (
    "COALESCE(df.is_archived, 0) = 0 "
    "AND LOWER(COALESCE(df.status, '')) NOT IN ('surrendered', 'archived')"
)
_CONSUMABLE_BASE_WHERE = "COALESCE(c.is_archived, 0) = 0"
_PC_ACTIVE_WHERE = (
    "COALESCE(pc.is_archived, 0) = 0 "
    "AND LOWER(COALESCE(pc.status, '')) != 'surrendered'"
)



def _get_inventory_stats():
    """Aggregate the counts displayed in the Inventory mini-dashboard."""
    stats = {
        "pc_count": 0,
        "item_count": 0,
        "available": 0,
        "damaged": 0,
        "consumables": 0,
        "surrendered": 0,
        "total_value": 0.0,
    }

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN LOWER(COALESCE(status, '')) = 'available' THEN 1 ELSE 0 END) AS available,
                    SUM(CASE WHEN LOWER(COALESCE(status, '')) IN ('damaged', 'damage', 'unusable') THEN 1 ELSE 0 END) AS damaged,
                    COALESCE(SUM(acquisition_cost), 0) AS asset_value
                FROM pcinfofull
                WHERE COALESCE(is_archived, 0) = 0
                  AND LOWER(COALESCE(status, '')) != 'surrendered'
            """)
            pc_stats = cur.fetchone() or {}

            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN LOWER(COALESCE(status, '')) = 'available' THEN 1 ELSE 0 END) AS available,
                    SUM(CASE WHEN LOWER(COALESCE(status, '')) IN ('damaged', 'damage', 'unusable') THEN 1 ELSE 0 END) AS damaged,
                    COALESCE(SUM(acquisition_cost), 0) AS asset_value
                FROM devices_full
                WHERE COALESCE(is_archived, 0) = 0
                  AND LOWER(COALESCE(status, '')) NOT IN ('surrendered', 'archived')
            """)
            item_stats = cur.fetchone() or {}

            cur.execute("""
                SELECT COUNT(*) AS total
                FROM pcinfofull
                WHERE COALESCE(is_archived, 0) = 1
                   OR LOWER(COALESCE(status, '')) = 'surrendered'
            """)
            surrendered_pcs = int((cur.fetchone() or {}).get("total") or 0)

            cur.execute("""
                SELECT COUNT(*) AS total
                FROM devices_full
                WHERE COALESCE(is_archived, 0) = 1
                   OR LOWER(COALESCE(status, '')) IN ('surrendered', 'archived')
            """)
            surrendered_items = int((cur.fetchone() or {}).get("total") or 0)

            cur.execute("""
                SELECT COUNT(*) AS cnt
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'consumables'
                  AND COLUMN_NAME = 'is_archived'
            """)
            has_consumable_archive = int((cur.fetchone() or {}).get("cnt") or 0) > 0
            consumable_where = (
                "WHERE COALESCE(is_archived, 0) = 0" if has_consumable_archive else ""
            )
            cur.execute(f"SELECT COUNT(*) AS total FROM consumables {consumable_where}")
            consumable_total = int((cur.fetchone() or {}).get("total") or 0)

            stats["pc_count"] = int(pc_stats.get("total") or 0)
            stats["item_count"] = int(item_stats.get("total") or 0)
            stats["available"] = int(pc_stats.get("available") or 0) + int(item_stats.get("available") or 0)
            stats["damaged"] = int(pc_stats.get("damaged") or 0) + int(item_stats.get("damaged") or 0)
            stats["consumables"] = consumable_total
            stats["surrendered"] = surrendered_pcs + surrendered_items
            stats["total_value"] = float(pc_stats.get("asset_value") or 0) + float(item_stats.get("asset_value") or 0)
    except Exception as exc:
        print(f"INVENTORY STATS ERROR: {exc}")
    finally:
        conn.close()

    return stats


def _inventory_category_meta(category_key):
    definition = INVENTORY_CATEGORY_DEFINITIONS.get(category_key)
    if not definition:
        return None

    meta = dict(definition)
    meta["key"] = category_key
    meta["add_action"] = {
        "pc": "openPcModal()",
        "device": "openItemModal()",
        "consumable": "openConsumableModal()",
    }.get(meta["kind"], "openItemModal()")
    return meta


def _empty_category_stats():
    return {
        "total": 0,
        "total_units": 0,
        "available": 0,
        "in_use": 0,
        "damaged": 0,
        "inactive": 0,
        "needs_checking": 0,
        "low_stock": 0,
        "out_of_stock": 0,
        "total_value": 0.0,
    }


def _apply_category_stats_row(stats, row):
    stats["total"] = int(row.get("total") or 0)
    stats["total_units"] = int(row.get("total_units") or 0)
    stats["available"] = int(row.get("available") or 0)
    stats["in_use"] = int(row.get("in_use") or 0)
    stats["damaged"] = int(row.get("damaged") or 0)
    stats["inactive"] = int(row.get("inactive") or 0)
    stats["needs_checking"] = int(row.get("needs_checking") or 0)
    stats["low_stock"] = int(row.get("low_stock") or 0)
    stats["out_of_stock"] = int(row.get("out_of_stock") or 0)
    stats["total_value"] = float(row.get("total_value") or 0)


def _get_category_stats(category_key):
    """Return aggregate metrics for one inventory category page."""
    stats = _empty_category_stats()
    definition = INVENTORY_CATEGORY_DEFINITIONS.get(category_key)
    if not definition:
        return stats

    kind = definition["kind"]
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            if kind == "pc":
                cur.execute(f"""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) AS total_units,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(pc.status, ''))) = 'available' THEN 1 ELSE 0 END) AS available,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(pc.status, ''))) IN ('in use', 'in used', 'in-used', 'inuse') THEN 1 ELSE 0 END) AS in_use,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(pc.status, ''))) IN ('damaged', 'damage', 'unusable') THEN 1 ELSE 0 END) AS damaged,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(pc.status, ''))) = 'inactive' THEN 1 ELSE 0 END) AS inactive,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(pc.status, ''))) = 'needs checking' THEN 1 ELSE 0 END) AS needs_checking,
                        COALESCE(SUM(pc.acquisition_cost), 0) AS total_value
                    FROM pcinfofull pc
                    WHERE {_PC_ACTIVE_WHERE}
                """)
                _apply_category_stats_row(stats, cur.fetchone() or {})
                stats["total_units"] = stats["total"]

            elif kind == "device":
                cur.execute(f"""
                    SELECT
                        COUNT(*) AS total,
                        COALESCE(SUM(CASE WHEN COALESCE(df.quantity, 0) < 1 THEN 1 ELSE df.quantity END), 0) AS total_units,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(df.status, ''))) = 'available' THEN 1 ELSE 0 END) AS available,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(df.status, ''))) IN ('in use', 'in used', 'in-used', 'inuse') THEN 1 ELSE 0 END) AS in_use,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(df.status, ''))) IN ('damaged', 'damage', 'unusable') THEN 1 ELSE 0 END) AS damaged,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(df.status, ''))) = 'inactive' THEN 1 ELSE 0 END) AS inactive,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(df.status, ''))) = 'needs checking' THEN 1 ELSE 0 END) AS needs_checking,
                        COALESCE(SUM(df.acquisition_cost), 0) AS total_value
                    FROM devices_full df
                    WHERE {_DEVICE_ACTIVE_WHERE}
                      AND ({_DEVICE_CATEGORY_CASE_SQL}) = %s
                """, (category_key,))
                _apply_category_stats_row(stats, cur.fetchone() or {})

            else:
                cur.execute("""
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'consumables'
                      AND COLUMN_NAME = 'is_archived'
                """)
                has_archive = int((cur.fetchone() or {}).get("cnt") or 0) > 0
                archive_where = f"WHERE {_CONSUMABLE_BASE_WHERE}" if has_archive else ""

                cur.execute(f"""
                    SELECT
                        COUNT(*) AS total,
                        COALESCE(SUM(COALESCE(c.quantity, 0)), 0) AS total_units,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(c.status, ''))) = 'available' THEN 1 ELSE 0 END) AS available,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(c.status, ''))) = 'low stock' THEN 1 ELSE 0 END) AS low_stock,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(c.status, ''))) = 'out of stock' THEN 1 ELSE 0 END) AS out_of_stock,
                        SUM(CASE WHEN LOWER(TRIM(COALESCE(c.status, ''))) = 'damaged' THEN 1 ELSE 0 END) AS damaged
                    FROM consumables c
                    {archive_where}
                """)
                _apply_category_stats_row(stats, cur.fetchone() or {})

    except Exception as exc:
        print(f"INVENTORY CATEGORY STATS ERROR [{category_key}]: {exc}")
    finally:
        conn.close()

    return stats


def _finalize_breakdown_rows(rows, total):
    finalized = []
    for row in rows:
        count = int(row.get("count") or 0)
        units = int(row.get("units") or 0) or count
        value = float(row.get("value") or 0)
        finalized.append({
            "label": row.get("label") or "Unspecified",
            "count": count,
            "units": units,
            "value": value,
            "percent": round((count / total) * 100, 1) if total else 0.0,
        })
    return finalized


def _get_category_report(category_key, stats):
    """Build the category-only report shown under the table."""
    definition = INVENTORY_CATEGORY_DEFINITIONS.get(category_key)
    if not definition:
        return {"generated_at": "", "summary": [], "breakdowns": []}

    kind = definition["kind"]
    total = stats["total"]
    breakdowns = []
    conn = get_db_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            if kind == "pc":
                cur.execute(f"""
                    SELECT
                        COALESCE(NULLIF(TRIM(pc.status), ''), 'Unspecified') AS label,
                        COUNT(*) AS count,
                        COUNT(*) AS units,
                        COALESCE(SUM(pc.acquisition_cost), 0) AS value
                    FROM pcinfofull pc
                    WHERE {_PC_ACTIVE_WHERE}
                    GROUP BY COALESCE(NULLIF(TRIM(pc.status), ''), 'Unspecified')
                    ORDER BY count DESC
                """)
                status_rows = _finalize_breakdown_rows(cur.fetchall(), total)

                cur.execute(f"""
                    SELECT
                        COALESCE(NULLIF(TRIM(dep.department_name), ''), 'Unassigned') AS label,
                        COUNT(*) AS count,
                        COUNT(*) AS units,
                        COALESCE(SUM(pc.acquisition_cost), 0) AS value
                    FROM pcinfofull pc
                    LEFT JOIN departments dep ON pc.department_id = dep.department_id
                    WHERE {_PC_ACTIVE_WHERE}
                    GROUP BY COALESCE(NULLIF(TRIM(dep.department_name), ''), 'Unassigned')
                    ORDER BY count DESC
                    LIMIT 10
                """)
                department_rows = _finalize_breakdown_rows(cur.fetchall(), total)

                cur.execute(f"""
                    SELECT
                        CASE
                            WHEN LOWER(TRIM(COALESCE(pc.status, ''))) IN ('damaged', 'damage', 'unusable') THEN 'High'
                            WHEN COALESCE(TRIM(pc.risk_level), '') = '' THEN 'Unspecified'
                            ELSE pc.risk_level
                        END AS label,
                        COUNT(*) AS count,
                        COUNT(*) AS units,
                        COALESCE(SUM(pc.acquisition_cost), 0) AS value
                    FROM pcinfofull pc
                    WHERE {_PC_ACTIVE_WHERE}
                    GROUP BY label
                    ORDER BY count DESC
                """)
                risk_rows = _finalize_breakdown_rows(cur.fetchall(), total)

                breakdowns = [
                    {"title": "By status", "rows": status_rows, "show_units": False, "show_value": True},
                    {"title": "By office / facility", "rows": department_rows, "show_units": False, "show_value": True},
                    {"title": "By risk level", "rows": risk_rows, "show_units": False, "show_value": True},
                ]

            elif kind == "device":
                base_from = f"""
                    FROM devices_full df
                    LEFT JOIN departments dep ON df.department_id = dep.department_id
                    WHERE {_DEVICE_ACTIVE_WHERE}
                      AND ({_DEVICE_CATEGORY_CASE_SQL}) = %s
                """

                cur.execute(f"""
                    SELECT
                        COALESCE(NULLIF(TRIM(df.status), ''), 'Unspecified') AS label,
                        COUNT(*) AS count,
                        COALESCE(SUM(CASE WHEN COALESCE(df.quantity, 0) < 1 THEN 1 ELSE df.quantity END), 0) AS units,
                        COALESCE(SUM(df.acquisition_cost), 0) AS value
                    {base_from}
                    GROUP BY COALESCE(NULLIF(TRIM(df.status), ''), 'Unspecified')
                    ORDER BY count DESC
                """, (category_key,))
                status_rows = _finalize_breakdown_rows(cur.fetchall(), total)

                cur.execute(f"""
                    SELECT
                        COALESCE(NULLIF(TRIM(df.item_name), ''), 'Unspecified') AS label,
                        COUNT(*) AS count,
                        COALESCE(SUM(CASE WHEN COALESCE(df.quantity, 0) < 1 THEN 1 ELSE df.quantity END), 0) AS units,
                        COALESCE(SUM(df.acquisition_cost), 0) AS value
                    {base_from}
                    GROUP BY COALESCE(NULLIF(TRIM(df.item_name), ''), 'Unspecified')
                    ORDER BY count DESC, units DESC
                    LIMIT 10
                """, (category_key,))
                item_rows = _finalize_breakdown_rows(cur.fetchall(), total)

                cur.execute(f"""
                    SELECT
                        COALESCE(NULLIF(TRIM(dep.department_name), ''), 'Unassigned') AS label,
                        COUNT(*) AS count,
                        COALESCE(SUM(CASE WHEN COALESCE(df.quantity, 0) < 1 THEN 1 ELSE df.quantity END), 0) AS units,
                        COALESCE(SUM(df.acquisition_cost), 0) AS value
                    {base_from}
                    GROUP BY COALESCE(NULLIF(TRIM(dep.department_name), ''), 'Unassigned')
                    ORDER BY count DESC
                    LIMIT 10
                """, (category_key,))
                department_rows = _finalize_breakdown_rows(cur.fetchall(), total)

                breakdowns = [
                    {"title": "By status", "rows": status_rows, "show_units": True, "show_value": True},
                    {"title": "By item", "rows": item_rows, "show_units": True, "show_value": True},
                    {"title": "By office / facility", "rows": department_rows, "show_units": True, "show_value": True},
                ]

            else:
                cur.execute("""
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'consumables'
                      AND COLUMN_NAME = 'is_archived'
                """)
                has_archive = int((cur.fetchone() or {}).get("cnt") or 0) > 0
                archive_where = f"WHERE {_CONSUMABLE_BASE_WHERE}" if has_archive else ""

                cur.execute(f"""
                    SELECT
                        COALESCE(NULLIF(TRIM(c.status), ''), 'Unspecified') AS label,
                        COUNT(*) AS count,
                        COALESCE(SUM(COALESCE(c.quantity, 0)), 0) AS units,
                        0 AS value
                    FROM consumables c
                    {archive_where}
                    GROUP BY COALESCE(NULLIF(TRIM(c.status), ''), 'Unspecified')
                    ORDER BY count DESC
                """)
                status_rows = _finalize_breakdown_rows(cur.fetchall(), total)

                cur.execute(f"""
                    SELECT
                        COALESCE(NULLIF(TRIM(c.item_name), ''), 'Unspecified') AS label,
                        COUNT(*) AS count,
                        COALESCE(SUM(COALESCE(c.quantity, 0)), 0) AS units,
                        0 AS value
                    FROM consumables c
                    {archive_where}
                    GROUP BY COALESCE(NULLIF(TRIM(c.item_name), ''), 'Unspecified')
                    ORDER BY units DESC, count DESC
                    LIMIT 10
                """)
                item_rows = _finalize_breakdown_rows(cur.fetchall(), total)

                cur.execute(f"""
                    SELECT
                        COALESCE(NULLIF(TRIM(dep.department_name), ''), 'Unassigned') AS label,
                        COUNT(*) AS count,
                        COALESCE(SUM(COALESCE(c.quantity, 0)), 0) AS units,
                        0 AS value
                    FROM consumables c
                    LEFT JOIN departments dep ON c.department_id = dep.department_id
                    {archive_where}
                    GROUP BY COALESCE(NULLIF(TRIM(dep.department_name), ''), 'Unassigned')
                    ORDER BY units DESC, count DESC
                    LIMIT 10
                """)
                department_rows = _finalize_breakdown_rows(cur.fetchall(), total)

                breakdowns = [
                    {"title": "By status", "rows": status_rows, "show_units": True, "show_value": False},
                    {"title": "By item", "rows": item_rows, "show_units": True, "show_value": False},
                    {"title": "By office / facility", "rows": department_rows, "show_units": True, "show_value": False},
                ]

    except Exception as exc:
        print(f"INVENTORY CATEGORY REPORT ERROR [{category_key}]: {exc}")
    finally:
        conn.close()

    summary = [
        {"label": "Total records", "value": stats["total"]},
        {"label": "Total units", "value": stats["total_units"]},
        {"label": "Available", "value": stats["available"]},
    ]
    if kind == "consumable":
        summary.extend([
            {"label": "Low stock", "value": stats["low_stock"]},
            {"label": "Out of stock", "value": stats["out_of_stock"]},
            {"label": "Damaged", "value": stats["damaged"]},
        ])
    else:
        summary.extend([
            {"label": "In use", "value": stats["in_use"]},
            {"label": "Damaged", "value": stats["damaged"]},
            {"label": "Acquisition value", "value": round(stats["total_value"], 2), "currency": True},
        ])

    return {
        "generated_at": datetime.now().strftime("%b %d, %Y %I:%M %p"),
        "summary": summary,
        "breakdowns": breakdowns,
    }


def _build_category_stat_cards(category_key, stats):
    kind = INVENTORY_CATEGORY_DEFINITIONS[category_key]["kind"]

    if kind == "pc":
        return [
            {"label": "Total PCs", "value": f"{stats['total']:,}", "icon": "cpu", "tone": "blue"},
            {"label": "Available", "value": f"{stats['available']:,}", "icon": "check-circle-2", "tone": "emerald"},
            {"label": "In use", "value": f"{stats['in_use']:,}", "icon": "monitor", "tone": "sky"},
            {"label": "Damaged", "value": f"{stats['damaged']:,}", "icon": "alert-triangle", "tone": "rose"},
            {"label": "Asset value", "value": f"₱{stats['total_value']:,.0f}", "icon": "wallet", "tone": "amber"},
        ]

    if kind == "device":
        return [
            {"label": "Records", "value": f"{stats['total']:,}", "icon": "boxes", "tone": "indigo"},
            {"label": "Units", "value": f"{stats['total_units']:,}", "icon": "layers", "tone": "violet"},
            {"label": "Available", "value": f"{stats['available']:,}", "icon": "check-circle-2", "tone": "emerald"},
            {"label": "In use", "value": f"{stats['in_use']:,}", "icon": "monitor", "tone": "sky"},
            {"label": "Damaged", "value": f"{stats['damaged']:,}", "icon": "alert-triangle", "tone": "rose"},
            {"label": "Asset value", "value": f"₱{stats['total_value']:,.0f}", "icon": "wallet", "tone": "amber"},
        ]

    return [
        {"label": "Item types", "value": f"{stats['total']:,}", "icon": "package", "tone": "green"},
        {"label": "Total quantity", "value": f"{stats['total_units']:,}", "icon": "boxes", "tone": "teal"},
        {"label": "Available", "value": f"{stats['available']:,}", "icon": "check-circle-2", "tone": "emerald"},
        {"label": "Low stock", "value": f"{stats['low_stock']:,}", "icon": "alert-triangle", "tone": "amber"},
        {"label": "Out of stock", "value": f"{stats['out_of_stock']:,}", "icon": "package-x", "tone": "rose"},
        {"label": "Damaged", "value": f"{stats['damaged']:,}", "icon": "shield-alert", "tone": "red"},
    ]


def _build_category_pagination(endpoint, page, per_page, total_items, total_pages, filter_args):
    total_pages = max(1, int(total_pages or 0))
    page = _clamp_page(page, total_pages)
    total_items = int(total_items or 0)

    window_start = max(1, page - 2)
    window_end = min(total_pages, page + 2)
    if window_end - window_start < 4:
        if window_start == 1:
            window_end = min(total_pages, window_start + 4)
        elif window_end == total_pages:
            window_start = max(1, window_end - 4)

    def page_url(target_page):
        args = dict(filter_args)
        args["page"] = target_page
        args["per_page"] = per_page
        return url_for(endpoint, **args)

    pages = [
        {"number": number, "url": page_url(number), "active": number == page}
        for number in range(window_start, window_end + 1)
    ]

    return {
        "page": page,
        "per_page": per_page,
        "total_items": total_items,
        "total_pages": total_pages,
        "pages": pages,
        "prev_url": page_url(page - 1) if page > 1 else None,
        "next_url": page_url(page + 1) if page < total_pages else None,
        "first_index": ((page - 1) * per_page + 1) if total_items else 0,
        "last_index": min(page * per_page, total_items) if total_items else 0,
    }


def get_device_category_list_paginated(category_key, page=1, per_page=10, filters=None):
    """Fetch devices assigned to one category page, with filters and pagination."""
    page = _normalize_page(page)
    per_page = _normalize_per_page(per_page)
    filters = filters or {}

    where_clauses = [
        _DEVICE_ACTIVE_WHERE,
        f"({_DEVICE_CATEGORY_CASE_SQL}) = %s",
    ]
    params = [category_key]

    department_id = filters.get("department_id")
    status = filters.get("status")
    item_name = filters.get("item_name")
    brand_model = filters.get("brand_model")
    accountable = filters.get("accountable")
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    search = filters.get("search")

    if department_id:
        where_clauses.append("df.department_id = %s")
        params.append(department_id)
    if status:
        where_clauses.append("df.status = %s")
        params.append(status)
    if item_name:
        where_clauses.append("df.item_name LIKE %s")
        params.append(f"%{item_name}%")
    if brand_model:
        where_clauses.append("df.brand_model LIKE %s")
        params.append(f"%{brand_model}%")
    if accountable:
        where_clauses.append("df.accountable LIKE %s")
        params.append(f"%{accountable}%")
    if date_from and date_to:
        where_clauses.append("df.date_acquired BETWEEN %s AND %s")
        params.extend([date_from, date_to])
    elif date_from:
        where_clauses.append("df.date_acquired >= %s")
        params.append(date_from)
    elif date_to:
        where_clauses.append("df.date_acquired <= %s")
        params.append(date_to)

    if search:
        where_clauses.append(
            "(df.accession_id LIKE %s OR df.item_name LIKE %s OR df.brand_model LIKE %s "
            "OR df.serial_no LIKE %s OR df.municipal_serial_no LIKE %s OR df.accountable LIKE %s "
            "OR dep.department_name LIKE %s)"
        )
        params.extend([f"%{search}%"] * 7)

    where_sql = " WHERE " + " AND ".join(where_clauses)
    conn = get_db_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM devices_full df
                LEFT JOIN departments dep ON df.department_id = dep.department_id
                {where_sql}
                """,
                params,
            )
            total_items = int((cur.fetchone() or {}).get("total") or 0)

            total_pages = (total_items + per_page - 1) // per_page
            page = _clamp_page(page, total_pages)
            offset = (page - 1) * per_page

            cur.execute(
                f"""
                SELECT
                    df.accession_id AS accession_id,
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
                    dep.department_name,
                    df.status,
                    df.updated_at,
                    CASE WHEN LOWER(TRIM(df.status)) IN ('damaged', 'damage', 'unusable') THEN 'High' ELSE df.risk_level END AS risk_level,
                    df.health_score,
                    df.last_checked,
                    df.maintenance_interval_days
                FROM devices_full df
                LEFT JOIN departments dep ON df.department_id = dep.department_id
                {where_sql}
                ORDER BY df.updated_at DESC, df.accession_id DESC
                LIMIT %s OFFSET %s
                """,
                params + [per_page, offset],
            )
            items = cur.fetchall()

        _apply_precise_health_scores(items)
        return items, total_items, total_pages, page

    except Exception as exc:
        print(f"INVENTORY DEVICE CATEGORY ERROR [{category_key}]: {exc}")
        return [], 0, 0, 1

    finally:
        conn.close()


def _render_inventory_category(category_key):
    meta = _inventory_category_meta(category_key)
    if not meta:
        flash("Unknown inventory category.", "danger")
        return redirect(url_for("manage_inventory.inventory_pcs"))

    kind = meta["kind"]
    page = _normalize_page(request.args.get("page", 1, type=int))
    per_page = _normalize_per_page(request.args.get("per_page", DEFAULT_PER_PAGE, type=int))

    filters = {
        "search": (request.args.get("search") or "").strip(),
        "status": (request.args.get("status") or "").strip(),
        "department_id": (request.args.get("department_id") or "").strip(),
        "item_name": (request.args.get("item_name") or "").strip(),
        "brand_model": (request.args.get("brand_model") or "").strip(),
        "accountable": (request.args.get("accountable") or "").strip(),
        "date_from": (request.args.get("date_from") or "").strip(),
        "date_to": (request.args.get("date_to") or "").strip(),
    }

    departments = get_departments_list()
    stats = _get_category_stats(category_key)
    report = _get_category_report(category_key, stats)

    seen_statuses = set()
    status_options = []
    reported_statuses = []
    if report.get("breakdowns"):
        reported_statuses = [row.get("label") for row in report["breakdowns"][0].get("rows", [])]
    for option in list(meta["status_options"]) + reported_statuses:
        if not option:
            continue
        key = str(option).strip().lower()
        if not key or key in ("surrendered", "archived") or key in seen_statuses:
            continue
        seen_statuses.add(key)
        status_options.append(option)

    pc_list = []
    item_list = []
    consumable_list = []

    if kind == "pc":
        pc_filters = {
            "department_id": filters["department_id"] or None,
            "status": filters["status"] or None,
            "accountable": filters["accountable"] or None,
            "search": filters["search"] or None,
            "date_from": filters["date_from"] or None,
            "date_to": filters["date_to"] or None,
        }
        pc_list, total_items, total_pages, page = get_pc_list_paginated(page, per_page, pc_filters)

    elif kind == "device":
        item_list, total_items, total_pages, page = get_device_category_list_paginated(
            category_key,
            page,
            per_page,
            filters,
        )

    else:
        consumable_list, total_items, total_pages, page = get_consumables_list_paginated(
            page=page,
            per_page=per_page,
            department_id=filters["department_id"] or None,
            status=filters["status"] or None,
            accountable=filters["accountable"] or None,
            item_name=filters["item_name"] or None,
            brand_model=filters["brand_model"] or None,
            date_from=filters["date_from"] or None,
            date_to=filters["date_to"] or None,
            search=filters["search"] or None,
        )

    filter_hidden = {key: value for key, value in filters.items() if value}
    pagination = _build_category_pagination(
        request.endpoint,
        page,
        per_page,
        total_items,
        total_pages,
        filter_hidden,
    )

    return render_template(
        "inventory_category.html",
        category=meta,
        stat_cards=_build_category_stat_cards(category_key, stats),
        stats=stats,
        report=report,
        pc_list=pc_list,
        item_list=item_list,
        consumable_list=consumable_list,
        departments=departments,
        filters=filters,
        filter_hidden=filter_hidden,
        status_options=status_options,
        pagination=pagination,
        per_page=per_page,
        per_page_options=sorted(ALLOWED_PER_PAGE),
        total_items=total_items,
        total_pages=pagination["total_pages"],
        reset_url=url_for(request.endpoint),
    )


@manage_inventory_bp.route("/manage_inventory")
def inventory_load():
    """Redirect legacy inventory links to the dedicated category pages."""
    section = (request.args.get("section") or "").strip().lower()
    preserved = request.args.to_dict(flat=True)
    preserved.pop("section", None)

    if section in ("item", "items", "device", "devices"):
        device_args = {
            key: value
            for key, value in preserved.items()
            if key not in ("item_page", "consumable_page", "page")
        }
        item_page = preserved.get("item_page") or preserved.get("page")
        if item_page:
            device_args["page"] = item_page
        device_args["tab"] = "inventory"
        return redirect(url_for("manage_device_bp.device_page", **device_args))

    if section in ("consumable", "consumables"):
        consumable_args = {
            key: value
            for key, value in preserved.items()
            if key not in ("consumable_page", "item_page", "page")
        }
        consumable_page = preserved.get("consumable_page") or preserved.get("page")
        if consumable_page:
            consumable_args["page"] = consumable_page
        return redirect(url_for("manage_inventory.inventory_consumables", **consumable_args))

    return redirect(url_for("manage_inventory.inventory_pcs", **preserved))


@manage_inventory_bp.route("/inventory")
def inventory_home():
    return redirect(url_for("manage_inventory.inventory_pcs"))


@manage_inventory_bp.route("/inventory/pcs")
def inventory_pcs():
    return _render_inventory_category("pcs")


@manage_inventory_bp.route("/inventory/components")
def inventory_components():
    return _render_inventory_category("components")


@manage_inventory_bp.route("/inventory/peripherals")
def inventory_peripherals():
    return _render_inventory_category("peripherals")


@manage_inventory_bp.route("/inventory/consumables")
def inventory_consumables():
    return _render_inventory_category("consumables")


@manage_inventory_bp.route("/inventory/accessories")
def inventory_accessories():
    return _render_inventory_category("accessories")

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


def _to_optional_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

@manage_inventory_bp.route('/manage_inventory/pcs-paged')
def pcs_paged():
    page = _normalize_page(request.args.get("page", 1, type=int))
    per_page = _normalize_per_page(request.args.get("per_page", DEFAULT_PER_PAGE, type=int))
    pc_filters = _get_pc_filters(request.args)

    pcs, total_items, total_pages, page = get_pc_list_paginated(page, per_page, pc_filters)
    request_source = request.headers.get('X-Request-Source', 'none')
    request_key = request.headers.get('X-Page-Request', 'none')
    print(f"[pcs_paged] source={request_source} request_key={request_key} section={request.args.get('section')} page={page} per_page={per_page} total_items={total_items} total_pages={total_pages} filters={pc_filters}")

    return jsonify({
        "pcs": pcs,
        "page": page,
        "per_page": per_page,
        "total_items": total_items,
        "total_pages": total_pages
    })

def _get_pc_filters(args):
    return {
        "department_id": args.get("department_id"),
        "status": args.get("status"),
        "location": args.get("location"),
        "accountable": args.get("accountable"),
        "serial_no": args.get("serial_no"),
        "date_from": args.get("date_from"),
        "date_to": args.get("date_to"),
        "risk_level": args.get("risk_level"),
        "health_min": _to_optional_int(args.get("health_min")),
        "health_max": _to_optional_int(args.get("health_max")),
        "last_checked_from": args.get("last_checked_from"),
        "last_checked_to": args.get("last_checked_to"),
        "overdue": args.get("overdue"),
        "needs_checking": args.get("needs_checking"),
        "search": args.get("search"),
    }


def _compute_remaining_health_percent(asset_row, today=None):
    today = today or date.today()

    status_key = str(asset_row.get("status") or "").strip().lower()
    if status_key in {"damaged", "damage", "unusable"}:
        return 0.0

    interval_days = normalize_interval_days(asset_row.get("maintenance_interval_days"), default_days=30)
    last_checked = parse_date(asset_row.get("last_checked"))
    date_acquired = parse_date(asset_row.get("date_acquired"))

    # Only use a real baseline date; do not implicitly reset by defaulting to "today".
    baseline_date = last_checked or date_acquired
    if baseline_date is None:
        current_health = asset_row.get("health_score")
        try:
            current_health = float(current_health)
            return round(max(0.0, min(100.0, current_health)), 2)
        except (TypeError, ValueError):
            return 100.0

    elapsed_days = max(0, (today - baseline_date).days)
    remaining_percent = max(0.0, 100.0 - ((elapsed_days / interval_days) * 100.0))
    return round(remaining_percent, 2)
def get_pc_list_paginated(page=1, per_page=10, filters=None):
    """
    Fetch paginated PCs from pcinfofull excluding surrendered.
    Returns:
        pcs, total_items, total_pages, current_page
    """
    page = _normalize_page(page)
    per_page = _normalize_per_page(per_page)
    filters = filters or {}
    conn = get_db_connection()

    where_clauses = ["LOWER(COALESCE(pc.status, '')) != 'surrendered'", "COALESCE(pc.is_archived, 0) = 0"]
    query_params = []

    department_id = filters.get("department_id")
    status = filters.get("status")
    location = filters.get("location")
    accountable = filters.get("accountable")
    serial_no = filters.get("serial_no")
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    risk_level = filters.get("risk_level")
    health_min = filters.get("health_min")
    health_max = filters.get("health_max")
    last_checked_from = filters.get("last_checked_from")
    last_checked_to = filters.get("last_checked_to")
    overdue_only = str(filters.get("overdue") or "") == "1"
    needs_checking = str(filters.get("needs_checking") or "") == "1"
    search = filters.get("search")

    if department_id:
        where_clauses.append("pc.department_id = %s")
        query_params.append(department_id)

    if status:
        where_clauses.append("pc.status = %s")
        query_params.append(status)

    if location:
        where_clauses.append("pc.location LIKE %s")
        query_params.append(f"%{location}%")

    if accountable:
        where_clauses.append("pc.accountable LIKE %s")
        query_params.append(f"%{accountable}%")

    if serial_no:
        where_clauses.append("(pc.serial_no LIKE %s OR pc.municipal_serial_no LIKE %s)")
        query_params.extend([f"%{serial_no}%", f"%{serial_no}%"])

    if date_from and date_to:
        where_clauses.append("pc.date_acquired BETWEEN %s AND %s")
        query_params.extend([date_from, date_to])
    elif date_from:
        where_clauses.append("pc.date_acquired >= %s")
        query_params.append(date_from)
    elif date_to:
        where_clauses.append("pc.date_acquired <= %s")
        query_params.append(date_to)

    if risk_level:
        where_clauses.append("(CASE WHEN LOWER(TRIM(pc.status)) IN ('damaged', 'damage', 'unusable') THEN 'High' ELSE pc.risk_level END) = %s")
        query_params.append(risk_level)

    if health_min is not None:
        where_clauses.append("pc.health_score >= %s")
        query_params.append(health_min)

    if health_max is not None:
        where_clauses.append("pc.health_score <= %s")
        query_params.append(health_max)

    if last_checked_from and last_checked_to:
        where_clauses.append("pc.last_checked BETWEEN %s AND %s")
        query_params.extend([last_checked_from, last_checked_to])
    elif last_checked_from:
        where_clauses.append("pc.last_checked >= %s")
        query_params.append(last_checked_from)
    elif last_checked_to:
        where_clauses.append("pc.last_checked <= %s")
        query_params.append(last_checked_to)

    if overdue_only:
        where_clauses.append("""(
            pc.last_checked IS NULL
            OR DATE_ADD(
                pc.last_checked,
                INTERVAL GREATEST(
                    1,
                    CASE
                        WHEN IFNULL(pc.maintenance_interval_days, 30) < 365
                            THEN IFNULL(pc.maintenance_interval_days, 30) * 365
                        ELSE IFNULL(pc.maintenance_interval_days, 30)
                    END
                ) DAY
            ) < CURDATE()
        )""")

    if needs_checking:
        where_clauses.append("pc.status = 'Needs Checking'")

    if search:
        where_clauses.append("(pc.pcname LIKE %s OR pc.motherboard LIKE %s OR pc.ram LIKE %s OR pc.storage LIKE %s OR pc.gpu LIKE %s)")
        query_params.extend([f"%{search}%"] * 5)

    where_sql = " WHERE " + " AND ".join(where_clauses)

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:

            # Total count
            cur.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM pcinfofull pc
                {where_sql}
                """,
                query_params,
            )
            total_items = cur.fetchone()["total"]

            total_pages = (total_items + per_page - 1) // per_page
            page = _clamp_page(page, total_pages)
            offset = (page - 1) * per_page

            # Paginated PC data
            cur.execute(f"""
                SELECT
                    pc.pcid,
                    pc.pcname,
                    pc.department_id,
                    dep.department_name,
                    pc.location,
                    pc.acquisition_cost,
                    pc.date_acquired,
                    pc.accountable,
                    pc.serial_no,
                    pc.municipal_serial_no,
                    pc.note,
                    pc.maintenance_interval_days,
                    pc.status,
                    pc.last_checked,
                    pc.health_score,
                    CASE WHEN LOWER(TRIM(pc.status)) IN ('damaged', 'damage', 'unusable') THEN 'High' ELSE pc.risk_level END AS risk_level,

                    -- ⭐ maintenance calculation
                    DATEDIFF(CURDATE(), pc.last_checked) AS days_since_check

                FROM pcinfofull pc
                LEFT JOIN departments dep
                    ON pc.department_id = dep.department_id
                {where_sql}
                ORDER BY pc.pcid DESC
                LIMIT %s OFFSET %s
            """, query_params + [per_page, offset])

            pcs = cur.fetchall()

            # Keep risk consistent with status even if legacy rows still carry stale risk values.
            for pc in pcs:
                status_key = str(pc.get("status") or "").strip().lower()
                if status_key in {"damaged", "damage", "unusable"}:
                    pc["risk_level"] = "High"
                elif not pc.get("risk_level"):
                    pc["risk_level"] = "--"

            _apply_precise_health_scores(pcs)
            

        return pcs, total_items, total_pages, page
    
    

    except Exception as e:
        print(f"❌ Error fetching paginated PCs: {e}")
        return [], 0, 0, 1

    finally:
        conn.close()

def get_pc_list():
    """Fetch all PCs with department and part details from pcinfofull."""
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
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
                    p.maintenance_interval_days,
                    p.motherboard,
                    p.ram,
                    p.storage,
                    p.gpu,
                    p.psu,
                    p.casing,
                    p.other_parts,
                    p.risk_level,
                    p.health_score
                   
                
                FROM pcinfofull p
                LEFT JOIN departments d ON p.department_id = d.department_id
                ORDER BY p.pcid
            """)
            return cur.fetchall()
    except Exception as e:
        print(f"❌ Error fetching PC list: {e}")
        return []
    finally:
        conn.close()

 
def _apply_precise_health_scores(rows):
    if not rows:
        return

    today = date.today()
    for row in rows:
        row["health_score"] = _compute_remaining_health_percent(row, today=today)



@manage_inventory_bp.route('/manage_inventory/items-paged')
def items_paged():
    page = _normalize_page(request.args.get("page", 1, type=int))
    per_page = _normalize_per_page(request.args.get("per_page", DEFAULT_PER_PAGE, type=int))

    department_id = request.args.get('department_id')
    status = request.args.get('status')
    accountable = request.args.get('accountable')
    serial_no = request.args.get('serial_no')
    item_name = request.args.get('item_name')
    brand_model = request.args.get('brand_model')
    device_type = request.args.get('device_type')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    request_source = request.headers.get('X-Request-Source', 'none')
    request_key = request.headers.get('X-Page-Request', 'none')

    search = request.args.get('search')

    # If any filter is present, or a free-text search term was provided,
    # use the filtered paginated loader which supports search across key
    # device columns. Otherwise fall back to the unfiltered paged loader.
    if any([department_id, status, accountable, serial_no, item_name, brand_model, device_type, date_from, date_to]) or search:
        items, total_items, total_pages, page = get_filtered_item_list_paginated(
            page, per_page, department_id, status, accountable, serial_no, 
            item_name, brand_model, device_type, date_from, date_to, search
        )
    else:
        items, total_items, total_pages, page = get_item_list_paginated(page, per_page)

    print(
        f"[items_paged] source={request_source} request_key={request_key} section={request.args.get('section')} "
        f"page={page} per_page={per_page} total_items={total_items} total_pages={total_pages} "
        f"filters={{'department_id': {department_id}, 'status': {status}, 'accountable': {accountable}, 'serial_no': {serial_no}, 'item_name': {item_name}, 'brand_model': {brand_model}, 'device_type': {device_type}, 'date_from': {date_from}, 'date_to': {date_to}}}"
    )

    return jsonify({
        "items": items,
        "page": page,
        "per_page": per_page,
        "total_items": total_items,
        "total_pages": total_pages
    })
 


@manage_inventory_bp.route('/manage_inventory/surrendered-pcs-paged')
def surrendered_pcs_paged():
    page = _normalize_page(request.args.get("page", 1, type=int))
    per_page = _normalize_per_page(request.args.get("per_page", DEFAULT_PER_PAGE, type=int))

    pcs, total_items, total_pages, page = get_surrendered_pc_list_paginated(page, per_page)

    return jsonify({
        "pcs": pcs,
        "page": page,
        "per_page": per_page,
        "total_items": total_items,
        "total_pages": total_pages
    })


@manage_inventory_bp.route('/manage_inventory/surrendered-items-paged')
def surrendered_items_paged():
    page = _normalize_page(request.args.get("page", 1, type=int))
    per_page = _normalize_per_page(request.args.get("per_page", DEFAULT_PER_PAGE, type=int))

    items, total_items, total_pages, page = get_surrendered_device_list_paginated(page, per_page)

    return jsonify({
        "items": items,
        "page": page,
        "per_page": per_page,
        "total_items": total_items,
        "total_pages": total_pages
    })


@manage_inventory_bp.route('/manage_inventory/consumables-paged')
def consumables_paged():
    page = _normalize_page(request.args.get("page", 1, type=int))
    per_page = _normalize_per_page(request.args.get("per_page", DEFAULT_PER_PAGE, type=int))

    department_id = request.args.get('department_id')
    status = request.args.get('status')
    accountable = request.args.get('accountable')
    item_name = request.args.get('item_name')
    brand_model = request.args.get('brand_model')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    search = request.args.get('search')

    consumables, total_items, total_pages, page = get_consumables_list_paginated(
        page=page,
        per_page=per_page,
        department_id=department_id,
        status=status,
        accountable=accountable,
        item_name=item_name,
        brand_model=brand_model,
        date_from=date_from,
        date_to=date_to,
        search=search
    )

    return jsonify({
        "consumables": consumables,
        "page": page,
        "per_page": per_page,
        "total_items": total_items,
        "total_pages": total_pages
    })


@manage_inventory_bp.route('/manage_inventory/get-departments')
def get_departments():
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT department_id, department_name 
                FROM departments 
                ORDER BY department_name
            """)
            departments = cur.fetchall()
        return jsonify(departments)
    except Exception as e:
        print(f"❌ Error fetching departments: {e}")
        return jsonify([]), 500
    finally:
        conn.close()


@manage_inventory_bp.route('/part-suggestions')
def part_suggestions():
    """Return a short list of device/items that match a requested PC part or free-text query.

    Query params:
      - part: name of the PC part (e.g. 'motherboard', 'ram')
      - q: free-text query to filter results
    """
    part = (request.args.get('part') or '').strip().lower()
    q = (request.args.get('q') or '').strip().lower()

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            params = []
            where_clauses = ["COALESCE(df.is_archived, 0) = 0"]
            search_clauses = []

            if part:
                search_clauses.append("(LOWER(df.item_name) LIKE %s OR LOWER(df.device_type) LIKE %s OR LOWER(df.brand_model) LIKE %s)")
                params.extend([f"%{part}%"] * 3)

            if q:
                # if query is numeric, allow exact accession_id match
                if q.isdigit():
                    search_clauses.append("df.accession_id = %s")
                    params.append(int(q))

                search_clauses.append("(LOWER(df.item_name) LIKE %s OR LOWER(df.device_type) LIKE %s OR LOWER(df.brand_model) LIKE %s OR LOWER(df.serial_no) LIKE %s)")
                params.extend([f"%{q}%"] * 4)

            if search_clauses:
                where_clauses.append("(" + " OR ".join(search_clauses) + ")")

            where_sql = " AND ".join(where_clauses)

            sql = f"""
                SELECT df.accession_id, df.item_name, df.brand_model, df.serial_no
                FROM devices_full df
                WHERE {where_sql}
                ORDER BY df.accession_id DESC
                LIMIT 30
            """

            cur.execute(sql, params)
            rows = cur.fetchall()

            results = []
            for r in rows:
                name = (r.get('item_name') or '').strip()
                brand = (r.get('brand_model') or '').strip()
                label = name or str(r.get('accession_id'))
                if brand:
                    label = f"{label} — {brand}"
                results.append({
                    'accession_id': r.get('accession_id'),
                    'label': label
                })

        return jsonify(success=True, results=results)

    except Exception as e:
        print(f"❌ Error fetching part suggestions: {e}")
        return jsonify(success=False, results=[]), 500
    finally:
        conn.close()

def get_consumables_list_paginated(page=1, per_page=10, department_id=None, status=None,
                                  accountable=None, item_name=None, brand_model=None,
                                  date_from=None, date_to=None, search=None):
    page = _normalize_page(page)
    per_page = _normalize_per_page(per_page)
    conn = get_db_connection()
    try:
        # Detect whether the `is_archived` column exists in the consumables
        # table. Some installations don't have that column, and referencing
        # it directly causes a SQL error which returns an empty result set.
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT COUNT(*) AS cnt
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'consumables'
                  AND COLUMN_NAME = 'is_archived'
            """)
            row = cur.fetchone()
            has_is_archived = bool(row and int(row.get('cnt', 0)) > 0)

        # Build base WHERE clause. Only include is_archived filter when
        # the column exists in the schema.
        where_sql = "WHERE 1=1"
        if has_is_archived:
            where_sql += " AND COALESCE(c.is_archived, 0) = 0"
        params = []

        if department_id:
            where_sql += " AND c.department_id = %s"
            params.append(department_id)

        if status:
            where_sql += " AND c.status = %s"
            params.append(status)

        if accountable:
            where_sql += " AND c.added_by LIKE %s"
            params.append(f"%{accountable}%")

        if item_name:
            where_sql += " AND c.item_name LIKE %s"
            params.append(f"%{item_name}%")

        if brand_model:
            where_sql += " AND c.brand LIKE %s"
            params.append(f"%{brand_model}%")

        if date_from and date_to:
            where_sql += " AND c.date_added BETWEEN %s AND %s"
            params.extend([date_from, date_to])

        # Broad free-text search across common consumable columns
        if search:
            where_sql += " AND (c.accession_id LIKE %s OR c.item_name LIKE %s OR c.brand LIKE %s OR c.location LIKE %s OR dep.department_name LIKE %s OR c.description LIKE %s)"
            params.extend([f"%{search}%"] * 6)

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM consumables c
                LEFT JOIN devices_full df ON df.accession_id = c.accession_id
                LEFT JOIN departments dep ON c.department_id = dep.department_id
                """ + where_sql,
                params
            )
            total_items = cur.fetchone()["total"]

            total_pages = (total_items + per_page - 1) // per_page
            page = _clamp_page(page, total_pages)
            offset = (page - 1) * per_page

            cur.execute(
                """
                SELECT
                    c.accession_id,
                    c.item_name,
                    c.category,
                    c.brand,
                    c.quantity,
                    c.unit,
                    c.department_id,
                    dep.department_name,
                    c.location,
                    c.status,
                    c.description,
                    c.date_added,
                    c.last_updated
                FROM consumables c
                LEFT JOIN devices_full df ON df.accession_id = c.accession_id
                LEFT JOIN departments dep ON c.department_id = dep.department_id
                """ + where_sql + " ORDER BY c.accession_id DESC LIMIT %s OFFSET %s",
                params + [per_page, offset]
            )
            consumables = cur.fetchall()

        return consumables, total_items, total_pages, page

    except Exception as e:
        print(f"❌ Error fetching paginated consumables: {e}")
        return [], 0, 0, 1

    finally:
        conn.close()

@manage_inventory_bp.route('/inventory/pc/bulk-check', methods=['POST'])
def bulk_mark_pc_checked():
    data = request.get_json()
    pcids = data.get("pcids", [])

    if not pcids:
        return jsonify(success=False, error="No PCs selected"), 400

    conn = get_db_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:

            for pcid in pcids:
                # Fetch old state
                cur.execute("""
                    SELECT status, risk_level
                    FROM pcinfofull
                    WHERE pcid = %s
                """, (pcid,))
                old = cur.fetchone()

                if not old:
                    continue

                # Update PC
                cur.execute("""
                    UPDATE pcinfofull
                    SET
                        last_checked = CURDATE(),
                        health_score = 100,
                        risk_level = 'Low',
                        status = 'Available'
                    WHERE pcid = %s
                """, (pcid,))

                # Maintenance log
                cur.execute("""
                    INSERT INTO maintenance_logs (
                        asset_type, asset_id,
                        previous_status, new_status,
                        previous_risk_level, new_risk_level,
                        action
                    ) VALUES (
                        'PC', %s,
                        %s, 'Available',
                        %s, 'Low',
                        'Bulk inspection completed'
                    )
                """, (
                    pcid,
                    old['status'],
                    old['risk_level']
                ))

                # Maintenance history
                cur.execute("""
                    INSERT INTO maintenance_history (
                        pcid,
                        asset_type,
                        asset_id,
                        action,
                        old_status,
                        new_status,
                        risk_level,
                        health_score,
                        performed_by,
                        remarks
                    ) VALUES (
                        %s, 'PC', %s,
                        'Bulk inspection completed',
                        %s, 'Available',
                        'Low', 100,
                        %s, %s
                    )
                """, (
                    pcid,
                    pcid,
                    old['status'],
                    'System',
                    'Bulk marked as checked'
                ))

        conn.commit()
        return jsonify(success=True)

    except Exception as e:
        conn.rollback()
        return jsonify(success=False, error=str(e)), 500

    finally:
        conn.close()


@manage_inventory_bp.route('/inventory/device/bulk-check', methods=['POST'])
def bulk_mark_device_checked():
    data = request.get_json()
    device_ids = data.get("device_ids", [])

    if not device_ids:
        return jsonify(success=False, error="No devices selected"), 400

    conn = get_db_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:

            placeholders = ','.join(['%s'] * len(device_ids))

            # Get previous status/risk
            cur.execute(f"""
                SELECT accession_id, status, risk_level
                FROM devices_full
                WHERE accession_id IN ({placeholders})
            """, tuple(device_ids))

            devices = cur.fetchall()

            # Update devices
            cur.execute(f"""
                UPDATE devices_full
                SET
                    last_checked = CURDATE(),
                    health_score = 100,
                    risk_level = 'Low',
                    status = 'Available'
                WHERE accession_id IN ({placeholders})
            """, tuple(device_ids))

            # Insert logs
            for dev in devices:

                cur.execute("""
                    INSERT INTO maintenance_logs (
                        asset_type,
                        asset_id,
                        previous_status,
                        new_status,
                        previous_risk_level,
                        new_risk_level,
                        action
                    ) VALUES (
                        'Device',
                        %s,
                        %s,
                        'Available',
                        %s,
                        'Low',
                        'Bulk inspection completed'
                    )
                """, (
                    dev['accession_id'],
                    dev['status'],
                    dev['risk_level']
                ))

                cur.execute("""
                    INSERT INTO maintenance_history (
                        asset_type,
                        asset_id,
                        action,
                        old_status,
                        new_status,
                        risk_level,
                        health_score,
                        performed_by,
                        remarks
                    ) VALUES (
                        'Device',
                        %s,
                        'Bulk inspection completed',
                        %s,
                        'Available',
                        'Low',
                        100,
                        %s,
                        %s
                    )
                """, (
                    dev['accession_id'],
                    dev['status'],
                    'System',
                    'Bulk marked as checked'
                ))

        conn.commit()
        return jsonify(success=True)

    except Exception as e:
        conn.rollback()
        return jsonify(success=False, error=str(e)), 500

    finally:
        conn.close()

@manage_inventory_bp.route('/inventory/device/bulk-update', methods=['POST'])
def bulk_update_devices():
    data = request.get_json()
    device_ids = data.get("device_ids", [])
    new_status = data.get("new_status")

    if not device_ids or not new_status:
        return jsonify(success=False, error="Invalid input"), 400

    conn = get_db_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            for accession_id in device_ids:

                # Fetch old state
                cur.execute("""
                    SELECT status, risk_level, health_score
                    FROM devices_full
                    WHERE accession_id = %s
                """, (accession_id,))
                old = cur.fetchone()

                if not old:
                    continue

                # ---- Risk & health rules ----
                if new_status == "Available":
                    risk = "Low"
                    health = 100
                elif new_status == "Needs Checking":
                    risk = "Medium"
                    health = 50
                elif new_status in ("Damaged", "For Repair"):
                    risk = "High"
                    health = 30
                elif new_status in ("Unusable", "Condemned"):
                    risk = "High"
                    health = 0
                else:
                    risk = old["risk_level"]
                    health = old["health_score"]

                # ---- Update device ----
                cur.execute("""
                    UPDATE devices_full
                    SET
                        status = %s,
                        risk_level = %s,
                        health_score = %s,
                        updated_at = NOW()
                    WHERE accession_id = %s
                """, (new_status, risk, health, accession_id))

                # ---- maintenance_logs ----
                cur.execute("""
                    INSERT INTO maintenance_logs (
                        asset_type, asset_id,
                        previous_status, new_status,
                        previous_risk_level, new_risk_level,
                        action
                    ) VALUES (
                        'DEVICE', %s,
                        %s, %s,
                        %s, %s,
                        'Bulk status update'
                    )
                """, (
                    accession_id,
                    old["status"],
                    new_status,
                    old["risk_level"],
                    risk
                ))

                # ---- maintenance_history ----
                cur.execute("""
                    INSERT INTO maintenance_history (
                        asset_type,
                        asset_id,
                        action,
                        old_status,
                        new_status,
                        risk_level,
                        health_score,
                        performed_by,
                        remarks
                    ) VALUES (
                        'Device', %s,
                        'Bulk status update',
                        %s, %s,
                        %s, %s,
                        %s,
                        %s
                    )
                """, (
                    accession_id,
                    old["status"],
                    new_status,
                    risk,
                    health,
                    'System',
                    f'Bulk marked as {new_status}'
                ))

        conn.commit()
        return jsonify(success=True)
    except Exception as e:
        conn.rollback()
        return jsonify(success=False, error=str(e)), 500
    finally:
        conn.close()


@manage_inventory_bp.route('/inventory/pc/bulk-surrender', methods=['POST'])
def bulk_surrender_pcs():
    """Mark selected PCs as surrendered."""
    data = request.get_json(silent=True) or {}
    pcids = data.get('pcids', [])

    if not pcids:
        return jsonify(success=False, error="No PCs selected"), 400

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            placeholders = ','.join(['%s'] * len(pcids))

            # Capture previous state first so history reflects real before/after values.
            cur.execute(
                f"""
                SELECT pcid, status, risk_level
                FROM pcinfofull
                WHERE pcid IN ({placeholders})
                """,
                tuple(pcids)
            )
            old_rows = cur.fetchall()
            old_map = {str(row['pcid']): row for row in old_rows}

            cur.execute(
                # If the schema provides an `is_archived` column, mark surrendered PCs as archived too
                """
                SELECT COUNT(*) AS cnt
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'pcinfofull'
                  AND COLUMN_NAME = 'is_archived'
                """
            )
            row = cur.fetchone()
            has_is_arch = bool(row and int(row.get('cnt', 0)) > 0)

            if has_is_arch:
                cur.execute(
                    f"UPDATE pcinfofull SET status = 'Surrendered', is_archived = 1, deleted_at = NOW() WHERE pcid IN ({placeholders})",
                    tuple(pcids)
                )
            else:
                cur.execute(
                    f"UPDATE pcinfofull SET status = 'Surrendered' WHERE pcid IN ({placeholders})",
                    tuple(pcids)
                )

            # Insert maintenance_logs and history entries for each pc
            for pcid in pcids:
                old = old_map.get(str(pcid))
                prev_status = old['status'] if old else None
                prev_risk = old['risk_level'] if old else None

                cur.execute("""
                    INSERT INTO maintenance_logs (
                        asset_type, asset_id,
                        previous_status, new_status,
                        previous_risk_level, new_risk_level,
                        action
                    ) VALUES (
                        'PC', %s,
                        %s, 'Surrendered',
                        %s, %s,
                        'Bulk surrender'
                    )
                """, (
                    pcid,
                    prev_status,
                    prev_risk,
                    prev_risk
                ))

                cur.execute("""
                    INSERT INTO maintenance_history (
                        pcid,
                        asset_type,
                        asset_id,
                        action,
                        old_status,
                        new_status,
                        risk_level,
                        health_score,
                        performed_by,
                        remarks
                    ) VALUES (
                        %s, 'PC', %s,
                        'Bulk surrender',
                        %s, 'Surrendered',
                        %s, NULL,
                        %s, %s
                    )
                """, (
                    pcid,
                    pcid,
                    prev_status,
                    prev_risk,
                    'System',
                    'Bulk surrendered'
                ))

        conn.commit()
        return jsonify(success=True)
    except Exception as e:
        conn.rollback()
        print(f"❌ Error bulk surrendering PCs: {e}")
        return jsonify(success=False, error=str(e)), 500
    finally:
        conn.close()


@manage_inventory_bp.route('/inventory/device/bulk-surrender', methods=['POST'])
def bulk_surrender_devices():
    """Mark selected devices as surrendered."""
    data = request.get_json(silent=True) or {}
    device_ids = data.get('device_ids', [])

    if not device_ids:
        return jsonify(success=False, error="No devices selected"), 400

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            placeholders = ','.join(['%s'] * len(device_ids))

            # Capture previous state before update for logging.
            cur.execute(
                f"""
                SELECT accession_id, status, risk_level
                FROM devices_full
                WHERE accession_id IN ({placeholders})
                """,
                tuple(device_ids)
            )
            old_rows = cur.fetchall()
            old_map = {str(row['accession_id']): row for row in old_rows}

            # If devices_full supports archiving, mark surrendered devices as archived too
            cur.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'devices_full'
                  AND COLUMN_NAME = 'is_archived'
                """
            )
            row = cur.fetchone()
            has_is_arch = bool(row and int(row.get('cnt', 0)) > 0)

            if has_is_arch:
                cur.execute(
                    f"UPDATE devices_full SET status = 'Surrendered', is_archived = 1, deleted_at = NOW() WHERE accession_id IN ({placeholders})",
                    tuple(device_ids)
                )
            else:
                cur.execute(
                    f"""
                    UPDATE devices_full
                    SET status = 'Surrendered'
                    WHERE accession_id IN ({placeholders})
                    """,
                    tuple(device_ids)
                )

            for accession_id in device_ids:
                old = old_map.get(str(accession_id))
                prev_status = old['status'] if old else None
                prev_risk = old['risk_level'] if old else None

                cur.execute(
                    """
                    INSERT INTO maintenance_logs (
                        asset_type,
                        asset_id,
                        previous_status,
                        new_status,
                        previous_risk_level,
                        new_risk_level,
                        action
                    ) VALUES (
                        'DEVICE',
                        %s,
                        %s,
                        'Surrendered',
                        %s,
                        %s,
                        'Bulk surrender'
                    )
                    """,
                    (
                        accession_id,
                        prev_status,
                        prev_risk,
                        prev_risk,
                    )
                )

                cur.execute(
                    """
                    INSERT INTO maintenance_history (
                        pcid,
                        asset_type,
                        asset_id,
                        action,
                        old_status,
                        new_status,
                        risk_level,
                        health_score,
                        performed_by,
                        remarks
                    ) VALUES (
                        NULL,
                        'Device',
                        %s,
                        'Bulk surrender',
                        %s,
                        'Surrendered',
                        %s,
                        NULL,
                        %s,
                        %s
                    )
                    """,
                    (
                        accession_id,
                        prev_status,
                        prev_risk,
                        'System',
                        'Bulk surrendered',
                    )
                )

        conn.commit()
        return jsonify(success=True)

    except Exception as e:
        conn.rollback()
        print(f"❌ Error bulk surrendering devices: {e}")
        return jsonify(success=False, error=str(e)), 500

    finally:
        conn.close()
 

 
def get_departments_list():
    """Get all departments for filter dropdowns."""
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT department_id, department_name FROM departments ORDER BY department_name")
            return cur.fetchall()
        
        
    except Exception as e:
        print(f"Error fetching departments: {e}")
        return []
    finally:
        conn.close()


 

def get_item_list():
    """Fetch all devices from devices_full (for Manage Items section)."""
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT 
                    df.accession_id AS accession_id,
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
                    dep.department_name,
                    df.status,
                    df.updated_at
                    df.risk_level,
                    df.health_score,
                    df.last_checked,
                    df.maintenance_interval_days
                    df.note,
                    df.location,
                    df.supplier
                FROM devices_full df
                LEFT JOIN departments dep ON df.department_id = dep.department_id
                WHERE COALESCE(df.is_archived, 0) = 0 AND LOWER(COALESCE(df.status, '')) != 'surrendered'
                ORDER BY df.accession_id DESC
            """)
            return cur.fetchall()
    except Exception as e:
        print(f"❌ Error fetching item list: {e}")
        return []
    finally:
        conn.close()
 

@manage_inventory_bp.route('/manage_inventory/get-item-by-id/<int:item_id>')
def get_item_by_id(item_id):
    """Fetch a single device record with full details."""
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT 
                    df.accession_id AS accession_id,
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
                    dep.department_name,
                    df.status,
                    df.updated_at
                FROM devices_full df
                LEFT JOIN departments dep 
                    ON df.department_id = dep.department_id
                WHERE df.accession_id = %s
            """, (item_id,))
            item = cur.fetchone()

        if not item:
            return jsonify({'error': 'Item not found'}), 404

        return jsonify(item)
    except Exception as e:
        print(f"❌ Error fetching item by ID: {e}")
        return jsonify({'error': 'Database error'}), 500
    finally:
        conn.close()

 
 

@manage_inventory_bp.route('/manage_inventory/get-pc-by-id/<int:pcid>')
def get_pc_by_id(pcid):

    
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
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
                    p.maintenance_interval_days,
                    p.monitor,
                    p.motherboard,
                    p.ram,
                    p.storage,
                    p.gpu,
                    p.psu,
                    p.casing,
                    p.mouse,
                    p.keyboard,
                    p.other_parts
                FROM pcinfofull p
                LEFT JOIN departments d ON p.department_id = d.department_id
                WHERE p.pcid = %s
            """, (pcid,))
            pc = cur.fetchone()

            if not pc:
                return {"error": "PC not found"}, 404

            return pc
    except Exception as e:
        print(f"❌ Error fetching PC by ID: {e}")
        return {"error": "Database error"}, 500
    finally:
        conn.close()

 


@manage_inventory_bp.route('/manage_inventory/pc-filter-modal')
def pc_filter_modal():
    return render_template('pcFilterModal.html')
def run_inventory_auto_check():
    print("🔄 Running inventory auto-check...")

    pc_result = _run_asset_risk_update("pcinfofull", "pcid", "PC")
    device_result = _run_asset_risk_update("devices_full", "accession_id", "Device")

    if not pc_result.get("success") or not device_result.get("success"):
        print("❌ Inventory auto-check failed:", pc_result.get("error") or device_result.get("error"))
        return

    print(
        "✅ Inventory auto-check complete. "
        f"PC updated: {pc_result.get('updated', 0)}, "
        f"Device updated: {device_result.get('updated', 0)}"
    )
def get_surrendered_pc_list_paginated(page=1, per_page=10):
    """
    Fetch paginated PCs from pcinfofull where status = 'Surrendered'.
    Returns:
        pcs, total_items, total_pages, current_page
    """
    page = _normalize_page(page)
    per_page = _normalize_per_page(per_page)
    conn = get_db_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Total count for surrendered PCs
            cur.execute("SELECT COUNT(*) AS total FROM pcinfofull WHERE status = 'Surrendered'")
            total_items = cur.fetchone()["total"]

            total_pages = (total_items + per_page - 1) // per_page
            page = _clamp_page(page, total_pages)
            offset = (page - 1) * per_page

            # Paginated surrendered PC data
            cur.execute("""
                SELECT
                    pc.pcid,
                    pc.pcname,
                    pc.department_id,
                    dep.department_name,
                    pc.location,
                    pc.acquisition_cost,
                    pc.date_acquired,
                    pc.accountable,
                    pc.serial_no,
                    pc.municipal_serial_no,
                    pc.note,
                    pc.status,
                    pc.last_checked,
                    pc.health_score,
                    pc.risk_level
                FROM pcinfofull pc
                LEFT JOIN departments dep
                    ON pc.department_id = dep.department_id
                WHERE pc.status = 'Surrendered'
                ORDER BY pc.pcid DESC
                LIMIT %s OFFSET %s
            """, (per_page, offset))

            pcs = cur.fetchall()

        return pcs, total_items, total_pages, page

    except Exception as e:
        print(f"❌ Error fetching surrendered paginated PCs: {e}")
        return [], 0, 0, 1

    finally:
        conn.close()


def get_surrendered_device_list_paginated(page=1, per_page=10):
    """Fetch paginated surrendered devices/items for the surrendered items section."""
    page = _normalize_page(page)
    per_page = _normalize_per_page(per_page)
    conn = get_db_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT COUNT(*) AS total
                FROM devices_full
                WHERE COALESCE(TRIM(LOWER(status)), '') = 'surrendered'
            """)
            total_items = cur.fetchone()["total"]

            total_pages = (total_items + per_page - 1) // per_page
            page = _clamp_page(page, total_pages)
            offset = (page - 1) * per_page

            cur.execute("""
                SELECT
                    df.accession_id,
                    df.item_name,
                    df.brand_model,
                    df.device_type,
                    df.department_id,
                    dep.department_name,
                    df.accountable,
                    df.serial_no,
                    df.municipal_serial_no,
                    df.quantity,
                    df.acquisition_cost,
                    df.date_acquired,
                    df.status
                FROM devices_full df
                LEFT JOIN departments dep
                    ON df.department_id = dep.department_id
                WHERE COALESCE(TRIM(LOWER(df.status)), '') = 'surrendered'
                ORDER BY df.updated_at DESC, df.accession_id DESC
                LIMIT %s OFFSET %s
            """, (per_page, offset))
            items = cur.fetchall()

            return items, total_items, total_pages, page

    except Exception as e:
        print(f"❌ Error fetching surrendered devices: {e}")
        return [], 0, 0, 1

    finally:
        conn.close()
 
def get_item_list_paginated(page=1, per_page=10):
    """
    Fetch paginated devices from devices_full, excluding surrendered records.
    Returns:
        items, total_items, total_pages, current_page
    """
    page = _normalize_page(page)
    per_page = _normalize_per_page(per_page)
    conn = get_db_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Get total count
            cur.execute("""
                SELECT COUNT(*) AS total
                FROM devices_full
                WHERE is_archived = 0
                  AND COALESCE(TRIM(LOWER(status)), '') NOT IN ('surrendered', 'archived')
            """)
            total_items = cur.fetchone()["total"]

            total_pages = (total_items + per_page - 1) // per_page
            page = _clamp_page(page, total_pages)
            offset = (page - 1) * per_page

            # Get paginated data
            cur.execute("""
                SELECT 
                    df.accession_id AS accession_id,
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
                    dep.department_name,
                    df.status,
                    df.updated_at,
                    CASE WHEN LOWER(TRIM(df.status)) IN ('damaged', 'damage', 'unusable') THEN 'High' ELSE df.risk_level END AS risk_level,
                    df.health_score,
                    df.last_checked,
                    df.maintenance_interval_days
                FROM devices_full df
                LEFT JOIN departments dep ON df.department_id = dep.department_id
                WHERE df.is_archived = 0
                  AND COALESCE(TRIM(LOWER(df.status)), '') NOT IN ('surrendered', 'archived')
                ORDER BY df.updated_at DESC, df.accession_id DESC
                LIMIT %s OFFSET %s
            """, (per_page, offset))

            items = cur.fetchall()

            _apply_precise_health_scores(items)

        return items, total_items, total_pages, page

    except Exception as e:
        print(f"❌ Error fetching paginated items: {e}")
        return [], 0, 0, 1
    finally:
        conn.close()

 

def get_filtered_item_list_paginated(page=1, per_page=10, department_id=None, status=None, 
                                   accountable=None, serial_no=None, item_name=None, 
                                   brand_model=None, device_type=None, date_from=None, date_to=None, search=None):
    """
    Fetch paginated and filtered devices from devices_full.
    Returns:
        items, total_items, total_pages, current_page
    """
    page = _normalize_page(page)
    per_page = _normalize_per_page(per_page)
    conn = get_db_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Build the base query with filters
            query = """
                SELECT 
                    df.accession_id AS accession_id,
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
                    dep.department_name,
                    df.status,
                    df.updated_at,
                    CASE WHEN LOWER(TRIM(df.status)) IN ('damaged', 'damage', 'unusable') THEN 'High' ELSE df.risk_level END AS risk_level,
                    df.health_score,
                    df.last_checked,
                    df.maintenance_interval_days
                FROM devices_full df
                LEFT JOIN departments dep ON df.department_id = dep.department_id
                WHERE 1=1
                                    AND df.is_archived = 0
                                    AND COALESCE(TRIM(LOWER(df.status)), '') NOT IN ('surrendered', 'archived')
            """
            params = []

            # Apply filters dynamically
            if department_id:
                query += " AND df.department_id = %s"
                params.append(department_id)
            if status:
                query += " AND df.status = %s"
                params.append(status)
            if accountable:
                query += " AND df.accountable LIKE %s"
                params.append(f"%{accountable}%")
            if serial_no:
                query += " AND (df.serial_no LIKE %s OR df.municipal_serial_no LIKE %s)"
                params.extend([f"%{serial_no}%", f"%{serial_no}%"])
            if item_name:
                query += " AND df.item_name LIKE %s"
                params.append(f"%{item_name}%")
            if brand_model:
                query += " AND df.brand_model LIKE %s"
                params.append(f"%{brand_model}%")
            if device_type:
                query += " AND df.device_type LIKE %s"
                params.append(f"%{device_type}%")
            if date_from and date_to:
                query += " AND df.date_acquired BETWEEN %s AND %s"
                params.extend([date_from, date_to])

            # Broad free-text search across common item columns
            if search:
                query += " AND (df.accession_id LIKE %s OR df.item_name LIKE %s OR df.brand_model LIKE %s OR df.serial_no LIKE %s OR dep.department_name LIKE %s OR df.accountable LIKE %s)"
                params.extend([f"%{search}%"] * 6)

            # Get total count with same filters (subquery is safer than string replacements)
            count_query = f"SELECT COUNT(*) AS total FROM ({query}) AS filtered_items"

            cur.execute(count_query, params)
            total_items = cur.fetchone()["total"]

            total_pages = (total_items + per_page - 1) // per_page
            page = _clamp_page(page, total_pages)
            offset = (page - 1) * per_page

            # Get paginated data
            query += " ORDER BY df.updated_at DESC, df.accession_id DESC LIMIT %s OFFSET %s"
            params.extend([per_page, offset])

            cur.execute(query, params)
            items = cur.fetchall()

            _apply_precise_health_scores(items)

        return items, total_items, total_pages, page

    except Exception as e:
        print(f"❌ Error fetching filtered paginated items: {e}")
        return [], 0, 0, 1

    finally:
        conn.close()
 
@manage_inventory_bp.route('/manage_inventory/run-risk-update', methods=['POST'])
def run_risk_update():
    result = _run_asset_risk_update("pcinfofull", "pcid", "PC")
    return jsonify(result), 200 if result.get("success") else 500


@manage_inventory_bp.route('/manage_inventory/run-device-risk-update', methods=['POST'])
def run_device_risk_update():
    result = _run_asset_risk_update("devices_full", "accession_id", "Device")
    return jsonify(result), 200 if result.get("success") else 500


def _run_asset_risk_update(table_name, id_column, asset_type):
    conn = get_db_connection()
    locked_statuses = {"damaged", "damage", "surrendered", "unusable"}

    stats = {
        "success": False,
        "message": f"{asset_type} risk update failed.",
        "updated": 0,
        "low_count": 0,
        "medium_count": 0,
        "high_count": 0,
        "pc_count": 0,
        "device_count": 0,
    }

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(f"""
                SELECT
                    {id_column} AS asset_id,
                    status,
                    risk_level,
                    date_acquired,
                    last_checked,
                    maintenance_interval_days
                FROM {table_name}
            """)
            assets = cur.fetchall()

            if asset_type == "PC":
                stats["pc_count"] = len(assets)
            else:
                stats["device_count"] = len(assets)

            for asset in assets:
                asset_id = asset.get("asset_id")
                old_status = asset.get("status") or ""
                old_risk = asset.get("risk_level") or ""
                normalized_status = old_status.strip().lower()

                health, risk = calculate_health_and_risk(asset)

                if risk == "Low":
                    stats["low_count"] += 1
                elif risk == "Medium":
                    stats["medium_count"] += 1
                else:
                    stats["high_count"] += 1

                if normalized_status in locked_statuses:
                    new_status = old_status
                else:
                    new_status = "Needs Checking" if risk == "High" else old_status

                cur.execute(
                    f"""
                    UPDATE {table_name}
                    SET
                        health_score = %s,
                        risk_level = %s,
                        status = %s
                    WHERE {id_column} = %s
                    """,
                    (health, risk, new_status, asset_id)
                )

                if old_status != new_status or old_risk != risk:
                    stats["updated"] += 1

                    if asset_type == "PC":
                        cur.execute(
                            """
                            INSERT INTO maintenance_history
                            (pcid, asset_type, asset_id, action, old_status, new_status,
                             risk_level, health_score, performed_by, remarks)
                            VALUES (%s, 'PC', %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                asset_id,
                                asset_id,
                                "Auto Risk Update",
                                old_status,
                                new_status,
                                risk,
                                health,
                                "System",
                                "Updated via Manage Inventory risk recalculation",
                            )
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO maintenance_history
                            (asset_type, asset_id, action, old_status, new_status,
                             risk_level, health_score, performed_by, remarks)
                            VALUES ('Device', %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                asset_id,
                                "Auto Risk Update",
                                old_status,
                                new_status,
                                risk,
                                health,
                                "System",
                                "Updated via Manage Inventory risk recalculation",
                            )
                        )

        conn.commit()

        stats["success"] = True
        stats["message"] = f"{asset_type} risk levels recalculated."
        return stats

    except Exception as e:
        conn.rollback()
        print(f"{asset_type} risk update error:", e)
        stats["error"] = str(e)
        return stats

    finally:
        conn.close()


def apply_medium_risk_rules():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE pcinfofull
                SET status = 'Needs Checking'
                WHERE risk_level = 'Medium'
                  AND status != 'Needs Checking'
            """)

            cur.execute("""
                UPDATE devices_full
                SET status = 'Needs Checking'
                WHERE risk_level = 'Medium'
                  AND status != 'Needs Checking'
            """)

        conn.commit()
    except Exception as e:
        conn.rollback()
        print("❌ Medium risk rule error:", e)
    finally:
        conn.close()
def apply_maintenance_overdue_rules():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:

            # ✅ PCs
            cur.execute("""
                UPDATE pcinfofull
                SET 
                    status = 'Needs Checking',
                    risk_level = 'Medium',
                    health_score = 50
                WHERE 
                    maintenance_interval_days IS NOT NULL
                    AND (
                        last_checked IS NULL
                        OR DATEDIFF(CURDATE(), last_checked) > GREATEST(
                            1,
                            CASE
                                WHEN IFNULL(maintenance_interval_days, 30) < 365
                                    THEN IFNULL(maintenance_interval_days, 30) * 365
                                ELSE IFNULL(maintenance_interval_days, 30)
                            END
                        )
                    )
                    AND status NOT IN ('Damaged', 'Surrendered', 'Unusable')
            """)

            # ✅ Devices
            cur.execute("""
                UPDATE devices_full
                SET 
                    status = 'Needs Checking',
                    risk_level = 'Medium',
                    health_score = 50
                WHERE 
                    maintenance_interval_days IS NOT NULL
                    AND (
                        last_checked IS NULL
                        OR DATEDIFF(CURDATE(), last_checked) > GREATEST(
                            1,
                            CASE
                                WHEN IFNULL(maintenance_interval_days, 30) < 365
                                    THEN IFNULL(maintenance_interval_days, 30) * 365
                                ELSE IFNULL(maintenance_interval_days, 30)
                            END
                        )
                    )
                    AND status NOT IN ('Damaged', 'Unusable')
            """)

        conn.commit()

    except Exception as e:
        conn.rollback()
        print("❌ Maintenance overdue rule error:", e)

    finally:
        conn.close()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:

            # ✅ PCs
            cur.execute("""
                UPDATE pcinfofull
                SET 
                    status = 'Needs Checking',
                    risk_level = 'Medium',
                    health_score = 50
                WHERE 
                    (
    last_checked IS NULL
    OR DATEDIFF(CURDATE(), last_checked) > GREATEST(
        1,
        CASE
            WHEN IFNULL(maintenance_interval_days, 30) < 365
                THEN IFNULL(maintenance_interval_days, 30) * 365
            ELSE IFNULL(maintenance_interval_days, 30)
        END
    )
)
                    AND maintenance_interval_days IS NOT NULL
                    AND DATEDIFF(CURDATE(), last_checked) > GREATEST(
                        1,
                        CASE
                            WHEN IFNULL(maintenance_interval_days, 30) < 365
                                THEN IFNULL(maintenance_interval_days, 30) * 365
                            ELSE IFNULL(maintenance_interval_days, 30)
                        END
                    )
                    AND status NOT IN ('Damaged', 'Surrendered', 'Unusable')
            """)

            # ✅ Devices
            cur.execute("""
                UPDATE devices_full
                SET 
                    status = 'Needs Checking',
                    risk_level = 'Medium',
                    health_score = 50
                WHERE 
(
    last_checked IS NULL
    OR DATEDIFF(CURDATE(), last_checked) > GREATEST(
        1,
        CASE
            WHEN IFNULL(maintenance_interval_days, 30) < 365
                THEN IFNULL(maintenance_interval_days, 30) * 365
            ELSE IFNULL(maintenance_interval_days, 30)
        END
    )
)                    AND maintenance_interval_days IS NOT NULL
                    AND DATEDIFF(CURDATE(), last_checked) > GREATEST(
                        1,
                        CASE
                            WHEN IFNULL(maintenance_interval_days, 30) < 365
                                THEN IFNULL(maintenance_interval_days, 30) * 365
                            ELSE IFNULL(maintenance_interval_days, 30)
                        END
                    )
                    AND status NOT IN ('Damaged', 'Unusable')
            """)

        conn.commit()

    except Exception as e:
        conn.rollback()
        print("❌ Maintenance overdue rule error:", e)

    finally:
        conn.close()
@manage_inventory_bp.route('/inventory/pc/<int:pcid>/check', methods=['POST'])
def mark_pc_checked(pcid):
    conn = get_db_connection()
    try:
        # Prepare the immutable audit schema before the write transaction.
        try:
            ensure_maintenance_history_schema(conn)
        except Exception as schema_error:
            print(f"[mark_pc_checked] maintenance schema error: {schema_error}")

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT status, risk_level
                FROM pcinfofull
                WHERE pcid = %s
            """, (pcid,))
            old = cur.fetchone()

            if not old:
                return jsonify(success=False, error="Asset not found"), 404

            cur.execute("""
                UPDATE pcinfofull
                SET
                    last_checked = CURDATE(),
                    health_score = 100,
                    risk_level = 'Low',
                    status = 'Available'
                WHERE pcid = %s
            """, (pcid,))

            cur.execute("""
                INSERT INTO maintenance_logs (
                    asset_type, asset_id,
                    previous_status, new_status,
                    previous_risk_level, new_risk_level,
                    action
                ) VALUES (
                    'PC', %s,
                    %s, 'Available',
                    %s, 'Low',
                    'Manual inspection completed'
                )
            """, (
                pcid,
                old['status'],
                old['risk_level']
            ))
            technician = current_technician()
            log_maintenance_event(
                cur,
                asset_type='PC',
                asset_id=pcid,
                pcid=pcid,
                action='Manual inspection completed',
                maintenance_type='Inspection',
                old_status=old['status'],
                new_status='Available',
                risk_level='Low',
                health_score=100,
                technician=technician,
                performed_by=technician,
                remarks='Marked as checked manually',
            )

        conn.commit()
        return jsonify(success=True)

    except Exception as e:
        conn.rollback()
        return jsonify(success=False, error=str(e)), 500
    finally:
        conn.close()

 
@manage_inventory_bp.route('/maintenance-history')
def maintenance_history():
    asset_id = request.args.get('id', type=int)
    asset_type = (request.args.get('type') or '').strip().upper()

    if not asset_id or not asset_type:
        return render_template(
            'maintenance_history.html', logs=[], asset_id=None, asset_type=None
        )

    conn = get_db_connection()
    try:
        ensure_maintenance_history_schema(conn)
    except Exception as schema_error:
        print(f"[maintenance-history] maintenance schema error: {schema_error}")

    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        # The immutable maintenance_history table is the source of truth.
        # Legacy maintenance_logs rows are still shown when no matching
        # maintenance_history record exists, so older deployments keep their
        # per-asset trail.
        cur.execute("""
            SELECT * FROM (
                SELECT
                    mh.performed_at,
                    mh.action,
                    COALESCE(mh.maintenance_type, mh.action) AS maintenance_type,
                    mh.replaced_part,
                    mh.previous_part,
                    mh.new_part,
                    mh.previous_part_status,
                    mh.new_part_status,
                    COALESCE(mh.computer_name, pc.pcname, df.item_name) AS computer_name,
                    COALESCE(mh.asset_serial, pc.serial_no, df.serial_no) AS asset_serial,
                    COALESCE(mh.technician, mh.performed_by) AS technician,
                    mh.old_status AS previous_status,
                    mh.new_status,
                    mh.risk_level AS new_risk_level,
                    mh.performed_by,
                    mh.remarks,
                    0 AS is_legacy
                FROM maintenance_history mh
                LEFT JOIN pcinfofull pc
                    ON UPPER(TRIM(mh.asset_type)) = 'PC'
                   AND mh.asset_id = pc.pcid
                LEFT JOIN devices_full df
                    ON UPPER(TRIM(mh.asset_type)) = 'DEVICE'
                   AND mh.asset_id = df.accession_id
                WHERE UPPER(TRIM(mh.asset_type)) = %s
                  AND mh.asset_id = %s

                UNION ALL

                SELECT
                    ml.performed_at,
                    ml.action,
                    ml.action AS maintenance_type,
                    NULL AS replaced_part,
                    NULL AS previous_part,
                    NULL AS new_part,
                    NULL AS previous_part_status,
                    NULL AS new_part_status,
                    COALESCE(pc.pcname, df.item_name) AS computer_name,
                    COALESCE(pc.serial_no, df.serial_no) AS asset_serial,
                    ml.performed_by AS technician,
                    ml.previous_status,
                    ml.new_status,
                    ml.new_risk_level,
                    ml.performed_by,
                    ml.remarks,
                    1 AS is_legacy
                FROM maintenance_logs ml
                LEFT JOIN pcinfofull pc
                    ON UPPER(TRIM(ml.asset_type)) = 'PC'
                   AND ml.asset_id = pc.pcid
                LEFT JOIN devices_full df
                    ON UPPER(TRIM(ml.asset_type)) = 'DEVICE'
                   AND ml.asset_id = df.accession_id
                WHERE UPPER(TRIM(ml.asset_type)) = %s
                  AND ml.asset_id = %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM maintenance_history mh2
                      WHERE UPPER(TRIM(mh2.asset_type)) = UPPER(TRIM(ml.asset_type))
                        AND mh2.asset_id = ml.asset_id
                        AND mh2.action = ml.action
                        AND DATE(mh2.performed_at) = DATE(ml.performed_at)
                  )
            ) combined
            ORDER BY performed_at DESC
        """, (asset_type, asset_id, asset_type, asset_id))
        logs = cur.fetchall()

    conn.close()
    return render_template(
        'maintenance_history.html',
        logs=logs,
        asset_id=asset_id,
        asset_type=asset_type,
    )

@manage_inventory_bp.route('/inventory/<string:asset_type>/<int:asset_id>/check', methods=['POST'])
def mark_asset_checked(asset_type, asset_id):
    route_asset_type = (asset_type or '').upper()
    history_asset_type = 'PC' if route_asset_type == 'PC' else 'Device'
    conn = get_db_connection()

    if route_asset_type not in ['PC', 'DEVICE']:
        return jsonify(success=False, error="Invalid asset type"), 400

    # Prepare the immutable audit schema before the write transaction.
    try:
        ensure_maintenance_history_schema(conn)
    except Exception as schema_error:
        print(f"[mark_asset_checked] maintenance schema error: {schema_error}")

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:

            # ---------- TARGET TABLE ----------
            if route_asset_type == 'PC':
                table = 'pcinfofull'
                id_field = 'pcid'
            else:
                table = 'devices_full'
                id_field = 'accession_id'

            # ---------- FETCH OLD STATE ----------
            cur.execute(f"""
                SELECT status, risk_level, health_score
                FROM {table}
                WHERE {id_field} = %s
            """, (asset_id,))
            old = cur.fetchone()

            if not old:
                return jsonify(success=False, error="Asset not found"), 404

            # ---------- UPDATE ASSET ----------
            cur.execute(f"""
                UPDATE {table}
                SET
                    last_checked = CURDATE(),
                    health_score = 100,
                    risk_level = 'Low',
                    status = 'Available'
                WHERE {id_field} = %s
            """, (asset_id,))

            # ---------- MAINTENANCE LOG ----------
            cur.execute("""
                INSERT INTO maintenance_logs (
                    asset_type,
                    asset_id,
                    previous_status,
                    new_status,
                    previous_risk_level,
                    new_risk_level,
                    action
                ) VALUES (
                    %s, %s,
                    %s, 'Available',
                    %s, 'Low',
                    'Manual inspection completed'
                )
            """, (
                route_asset_type,
                asset_id,
                old['status'],
                old['risk_level']
            ))

            # ---------- MAINTENANCE HISTORY ----------
            technician = current_technician()
            log_maintenance_event(
                cur,
                asset_type=history_asset_type,
                asset_id=asset_id,
                pcid=asset_id if route_asset_type == 'PC' else None,
                action='Manual inspection completed',
                maintenance_type='Inspection',
                old_status=old['status'],
                new_status='Available',
                risk_level='Low',
                health_score=100,
                technician=technician,
                performed_by=technician,
                remarks='Marked as checked manually',
            )

        conn.commit()
        return jsonify(success=True)

    except Exception as e:
        conn.rollback()
        return jsonify(success=False, error=str(e)), 500

    finally:
        conn.close()

def get_consumables_list(department_id=None, status=None, accountable=None,
                        item_name=None, brand_model=None,
                        quantity=None, acquisition_cost=None,
                        date_from=None, date_to=None):

    conn = get_db_connection()
    try:
        query = """
            SELECT 
                c.accession_id,
                c.item_name,
                c.category,
                c.brand,
                c.quantity,
                c.unit,
                c.department_id,
                dep.department_name,
                c.location,
                c.status,
                c.description,
                c.date_added,
                c.last_updated
            FROM consumables c
                        LEFT JOIN devices_full df ON df.accession_id = c.accession_id
            LEFT JOIN departments dep ON c.department_id = dep.department_id
            WHERE 1=1
                            AND (
                                        df.accession_id IS NULL
                                        OR TRIM(LOWER(df.device_type)) = 'consumable'
                                    )
                            AND COALESCE(c.is_archived, 0) = 0
        """
        params = []

        if department_id:
            query += " AND c.department_id = %s"
            params.append(department_id)

        if status:
            query += " AND c.status = %s"
            params.append(status)

        if accountable:
            query += " AND c.added_by LIKE %s"
            params.append(f"%{accountable}%")

        if item_name:
            query += " AND c.item_name LIKE %s"
            params.append(f"%{item_name}%")

        if brand_model:
            query += " AND c.brand LIKE %s"
            params.append(f"%{brand_model}%")

        if date_from and date_to:
            query += " AND c.date_added BETWEEN %s AND %s"
            params.extend([date_from, date_to])

        query += " ORDER BY c.accession_id DESC"

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(query, params)
            return cur.fetchall()

    finally:
        conn.close()
 
@manage_inventory_bp.route('/inventory/pc/bulk-damaged', methods=['POST'])
def bulk_damaged_pcs():
    data = request.get_json()

    pcids = data.get('pcids', [])
    damage_type = data.get('damage_type', 'General Damage')
    description = data.get('description', '')

    if not pcids:
        return jsonify(success=False, error="No PCs selected"), 400

    conn = get_db_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:

            placeholders = ','.join(['%s'] * len(pcids))

            # Capture previous state first for accurate before/after history.
            cur.execute(
                f"""
                SELECT pcid, status, risk_level, health_score
                FROM pcinfofull
                WHERE pcid IN ({placeholders})
                """,
                tuple(pcids)
            )
            old_rows = cur.fetchall()
            old_map = {str(row['pcid']): row for row in old_rows}

            # ✅ 1. Update status and risk
            cur.execute(
                f"UPDATE pcinfofull SET status = 'Damaged', risk_level = 'High' WHERE pcid IN ({placeholders})",
                tuple(pcids)
            )

            # ✅ 2. Insert maintenance logs/history so History matches Manage PC changes.
            for pcid in pcids:
                old = old_map.get(str(pcid))
                prev_status = old['status'] if old else None
                prev_risk = old['risk_level'] if old else None
                prev_health = old['health_score'] if old else None

                cur.execute("""
                    INSERT INTO maintenance_logs (
                        asset_type,
                        asset_id,
                        previous_status,
                        new_status,
                        previous_risk_level,
                        new_risk_level,
                        action
                    ) VALUES (
                        'PC',
                        %s,
                        %s,
                        'Damaged',
                        %s,
                        'High',
                        'Bulk marked as damaged'
                    )
                """, (
                    pcid,
                    prev_status,
                    prev_risk
                ))

                cur.execute("""
                    INSERT INTO maintenance_history (
                        pcid,
                        asset_type,
                        asset_id,
                        action,
                        old_status,
                        new_status,
                        risk_level,
                        health_score,
                        performed_by,
                        remarks
                    ) VALUES (
                        %s, 'PC', %s,
                        'Bulk marked as damaged',
                        %s, 'Damaged',
                        'High', %s,
                        %s, %s
                    )
                """, (
                    pcid,
                    pcid,
                    prev_status,
                    prev_health,
                    'System',
                    description or 'Bulk marked as damaged'
                ))

            # ✅ 3. Insert reports (MATCHES YOUR TABLE)
            cur.execute(f"""
                INSERT INTO damage_reports (
                    pcid,
                    reported_by,
                    damage_type,
                    description
                )
                SELECT 
                    pcid,
                    'System',
                    %s,
                    %s
                FROM pcinfofull
                WHERE pcid IN ({placeholders})
            """, (
                damage_type,
                description,
                *pcids
            ))

        conn.commit()
        return jsonify(success=True)

    except Exception as e:
        conn.rollback()
        print(f"❌ Error: {e}")
        return jsonify(success=False, error=str(e)), 500

    finally:
        conn.close()
@manage_inventory_bp.route('/inventory/device-bulk-damaged', methods=['POST'])
def bulk_damaged_devices():
    data = request.get_json()

    device_ids = data.get('device_ids', [])
    damage_type = data.get('damage_type', 'General Damage')
    description = data.get('description', '')

    if not device_ids:
        return jsonify(success=False, error="No devices selected"), 400

    conn = get_db_connection()

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:

            placeholders = ','.join(['%s'] * len(device_ids))

            # Capture previous state first for accurate before/after history.
            cur.execute(
                f"""
                SELECT accession_id, status, risk_level, health_score
                FROM devices_full
                WHERE accession_id IN ({placeholders})
                """,
                tuple(device_ids)
            )
            old_rows = cur.fetchall()
            old_map = {str(row['accession_id']): row for row in old_rows}

            # 1️⃣ Update status and risk
            cur.execute(
                f"""
                UPDATE devices_full 
                SET status = 'Damaged', risk_level = 'High'
                WHERE accession_id  IN ({placeholders})
                """,
                tuple(device_ids)
            )

            # 2️⃣ Insert maintenance logs/history so History matches Manage Item changes.
            for accession_id in device_ids:
                old = old_map.get(str(accession_id))
                prev_status = old['status'] if old else None
                prev_risk = old['risk_level'] if old else None
                prev_health = old['health_score'] if old else None

                cur.execute("""
                    INSERT INTO maintenance_logs (
                        asset_type,
                        asset_id,
                        previous_status,
                        new_status,
                        previous_risk_level,
                        new_risk_level,
                        action
                    ) VALUES (
                        'DEVICE',
                        %s,
                        %s,
                        'Damaged',
                        %s,
                        'High',
                        'Bulk marked as damaged'
                    )
                """, (
                    accession_id,
                    prev_status,
                    prev_risk
                ))

                cur.execute("""
                    INSERT INTO maintenance_history (
                        pcid,
                        asset_type,
                        asset_id,
                        action,
                        old_status,
                        new_status,
                        risk_level,
                        health_score,
                        performed_by,
                        remarks
                    ) VALUES (
                        NULL, 'Device', %s,
                        'Bulk marked as damaged',
                        %s, 'Damaged',
                        'High', %s,
                        %s, %s
                    )
                """, (
                    accession_id,
                    prev_status,
                    prev_health,
                    'System',
                    description or 'Bulk marked as damaged'
                ))

            # 3️⃣ Insert damage reports
            cur.execute(
                f"""
                INSERT INTO device_damage_reports (
                    accession_id,
                    reported_by,
                    damage_type,
                    description
                )
                SELECT 
                    dev.accession_id,
                    'System',
                    %s,
                    %s
                FROM devices_full dev
                WHERE dev.accession_id IN ({placeholders})
                """,
                (
                    damage_type,
                    description,
                    *device_ids
                )
            )

        conn.commit()
        return jsonify(success=True)

    except Exception as e:
        conn.rollback()
        print(f"❌ Error: {e}")
        return jsonify(success=False, error=str(e)), 500

    finally:
        conn.close()