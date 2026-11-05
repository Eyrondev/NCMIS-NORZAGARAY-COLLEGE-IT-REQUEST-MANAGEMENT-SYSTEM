"""Helpers for the Manage Device module.

- ``ensure_device_schema`` adds the columns/table used by the module the first
  time it is needed, so deployments do not require a manual migration.
- ``log_device_changes`` writes field-level change rows to
  ``device_change_logs`` for the modification report.
"""

# Cache the discovered schema capabilities for the process lifetime.
_SCHEMA_STATE = None

FIELD_LABELS = {
    "item_name": "Item Name",
    "brand_model": "Brand / Model",
    "serial_no": "Serial No",
    "municipal_serial_no": "Municipal Serial No",
    "acquisition_cost": "Acquisition Cost",
    "date_acquired": "Date Acquired",
    "accountable": "Accountable",
    "department_id": "Department",
    "status": "Status",
    "maintenance_interval_days": "Maintenance Interval",
    "risk_level": "Risk Level",
    "used_on_pcid": "Used On PC",
    "usage_scope": "Usage Scope",
    "quantity": "Quantity",
}


def _column_exists(cur, table, column):
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (table, column),
    )
    row = cur.fetchone()
    return bool(row and row.get("cnt"))


def _table_exists(cur, table):
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table,),
    )
    row = cur.fetchone()
    return bool(row and row.get("cnt"))


def ensure_device_schema(conn):
    """Create the columns/table required by the Manage Device module.

    Failures are logged and ignored so the rest of the application keeps
    working on databases where DDL is not permitted.
    """
    global _SCHEMA_STATE
    if _SCHEMA_STATE is not None:
        return _SCHEMA_STATE

    state = {"used_on_pcid": False, "usage_scope": False, "change_logs": False}

    try:
        with conn.cursor() as cur:
            if not _column_exists(cur, "devices_full", "used_on_pcid"):
                try:
                    cur.execute(
                        "ALTER TABLE devices_full "
                        "ADD COLUMN used_on_pcid INT NULL DEFAULT NULL"
                    )
                    conn.commit()
                except Exception as exc:  # noqa: BLE001
                    print(f"DEVICE SCHEMA: could not add used_on_pcid: {exc}")

            if not _column_exists(cur, "devices_full", "usage_scope"):
                try:
                    cur.execute(
                        "ALTER TABLE devices_full "
                        "ADD COLUMN usage_scope VARCHAR(20) NOT NULL DEFAULT 'UNASSIGNED'"
                    )
                    conn.commit()
                except Exception as exc:  # noqa: BLE001
                    print(f"DEVICE SCHEMA: could not add usage_scope: {exc}")

            if not _table_exists(cur, "device_change_logs"):
                try:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS device_change_logs (
                            log_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                            device_id INT NOT NULL,
                            item_name VARCHAR(255) DEFAULT NULL,
                            serial_no VARCHAR(255) DEFAULT NULL,
                            action VARCHAR(40) NOT NULL,
                            field_name VARCHAR(100) DEFAULT NULL,
                            old_value TEXT DEFAULT NULL,
                            new_value TEXT DEFAULT NULL,
                            remarks TEXT DEFAULT NULL,
                            changed_by INT DEFAULT NULL,
                            changed_by_name VARCHAR(255) DEFAULT NULL,
                            changed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (log_id),
                            KEY idx_device_change_device (device_id),
                            KEY idx_device_change_changed_at (changed_at),
                            KEY idx_device_change_action (action)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                        COLLATE=utf8mb4_general_ci
                        """
                    )
                    conn.commit()
                except Exception as exc:  # noqa: BLE001
                    print(f"DEVICE SCHEMA: could not create device_change_logs: {exc}")

            state["used_on_pcid"] = _column_exists(cur, "devices_full", "used_on_pcid")
            state["usage_scope"] = _column_exists(cur, "devices_full", "usage_scope")
            state["change_logs"] = _table_exists(cur, "device_change_logs")
    except Exception as exc:  # noqa: BLE001
        print(f"DEVICE SCHEMA ERROR: {exc}")

    _SCHEMA_STATE = state
    return state


def _action_for_field(field_name):
    if field_name == "status":
        return "STATUS_CHANGED"
    if field_name in ("used_on_pcid", "usage_scope"):
        return "ASSIGNED"
    if field_name in ("serial_no", "item_name", "brand_model"):
        return "REPLACED"
    return "UPDATED"


def log_device_changes(
    conn,
    device_id,
    item_name=None,
    serial_no=None,
    changes=None,
    action=None,
    remarks=None,
    changed_by=None,
    changed_by_name=None,
):
    """Insert one or more rows into ``device_change_logs``.

    ``changes`` is an iterable of ``(field_name, old_value, new_value)``.
    When omitted a single summary row using ``action`` is written.
    """
    if not conn:
        return

    try:
        with conn.cursor() as cur:
            if not _table_exists(cur, "device_change_logs"):
                return

            rows = []
            if changes:
                for field_name, old_value, new_value in changes:
                    if str(old_value) == str(new_value):
                        continue
                    rows.append(
                        (
                            device_id,
                            item_name,
                            serial_no,
                            action or _action_for_field(field_name),
                            FIELD_LABELS.get(field_name, field_name),
                            None if old_value is None else str(old_value),
                            None if new_value is None else str(new_value),
                            remarks,
                            changed_by,
                            changed_by_name,
                        )
                    )

            if not rows:
                rows.append(
                    (
                        device_id,
                        item_name,
                        serial_no,
                        action or "UPDATED",
                        None,
                        None,
                        None,
                        remarks,
                        changed_by,
                        changed_by_name,
                    )
                )

            cur.executemany(
                """
                INSERT INTO device_change_logs (
                    device_id, item_name, serial_no, action, field_name,
                    old_value, new_value, remarks, changed_by, changed_by_name
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        print(f"DEVICE CHANGE LOG ERROR: {exc}")
