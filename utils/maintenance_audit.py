"""Maintenance history helpers.

The maintenance history table is an append-only audit trail.  This module
ensures the extended columns exist, offers one insert helper so every event
stores the same fields, and installs database triggers that reject updates and
deletes.

Call ``ensure_maintenance_history_schema`` *before* opening the transaction
that writes the maintenance event: trigger/column creation is DDL and commits
implicitly.
"""

from flask import session
import pymysql

_SCHEMA_STATE = None

_EXTRA_COLUMNS = {
    "maintenance_type": "VARCHAR(100) DEFAULT NULL",
    "replaced_part": "VARCHAR(100) DEFAULT NULL",
    "previous_part": "TEXT DEFAULT NULL",
    "new_part": "TEXT DEFAULT NULL",
    "previous_part_status": "VARCHAR(100) DEFAULT NULL",
    "new_part_status": "VARCHAR(100) DEFAULT NULL",
    "computer_name": "VARCHAR(255) DEFAULT NULL",
    "asset_serial": "VARCHAR(255) DEFAULT NULL",
    "technician": "VARCHAR(255) DEFAULT NULL",
}

_UPDATE_TRIGGER = "trg_maintenance_history_immutable_update"
_DELETE_TRIGGER = "trg_maintenance_history_immutable_delete"

_BROKEN_STATUSES = {
    "damaged",
    "damage",
    "unusable",
    "defective",
    "needs fixing",
    "needs checking",
    "for repair",
}


def _table_exists(cur, table):
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    row = cur.fetchone()
    return bool(row and row.get("cnt"))


def _column_exists(cur, table, column):
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    row = cur.fetchone()
    return bool(row and row.get("cnt"))


def _trigger_exists(cur, trigger_name):
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM information_schema.TRIGGERS "
        "WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME = %s",
        (trigger_name,),
    )
    row = cur.fetchone()
    return bool(row and row.get("cnt"))


def ensure_maintenance_history_schema(conn, force=False):
    """Create the extended maintenance columns and immutability triggers."""
    global _SCHEMA_STATE
    if _SCHEMA_STATE is not None and not force:
        return _SCHEMA_STATE

    state = {"columns": set(), "update_trigger": False, "delete_trigger": False}

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            if not _table_exists(cur, "maintenance_history"):
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS maintenance_history (
                        id INT NOT NULL AUTO_INCREMENT,
                        pcid INT DEFAULT NULL,
                        asset_type ENUM('PC','Device') NOT NULL,
                        asset_id INT NOT NULL,
                        action VARCHAR(100) NOT NULL,
                        maintenance_type VARCHAR(100) DEFAULT NULL,
                        replaced_part VARCHAR(100) DEFAULT NULL,
                        previous_part TEXT DEFAULT NULL,
                        new_part TEXT DEFAULT NULL,
                        previous_part_status VARCHAR(100) DEFAULT NULL,
                        new_part_status VARCHAR(100) DEFAULT NULL,
                        computer_name VARCHAR(255) DEFAULT NULL,
                        asset_serial VARCHAR(255) DEFAULT NULL,
                        technician VARCHAR(255) DEFAULT NULL,
                        remarks TEXT DEFAULT NULL,
                        performed_by VARCHAR(255) DEFAULT NULL,
                        old_status VARCHAR(100) DEFAULT NULL,
                        new_status VARCHAR(100) DEFAULT NULL,
                        risk_level VARCHAR(20) DEFAULT NULL,
                        health_score INT DEFAULT NULL,
                        performed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (id),
                        KEY fk_maintenance_pc (pcid)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    COLLATE=utf8mb4_general_ci
                    """
                )
                conn.commit()

            for column, definition in _EXTRA_COLUMNS.items():
                if not _column_exists(cur, "maintenance_history", column):
                    cur.execute(
                        f"ALTER TABLE maintenance_history ADD COLUMN `{column}` {definition}"
                    )
                    conn.commit()

            if not _trigger_exists(cur, _UPDATE_TRIGGER):
                cur.execute(
                    f"""
                    CREATE TRIGGER {_UPDATE_TRIGGER}
                    BEFORE UPDATE ON maintenance_history
                    FOR EACH ROW
                    SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'Maintenance history is immutable and cannot be updated.'
                    """
                )
                conn.commit()

            if not _trigger_exists(cur, _DELETE_TRIGGER):
                cur.execute(
                    f"""
                    CREATE TRIGGER {_DELETE_TRIGGER}
                    BEFORE DELETE ON maintenance_history
                    FOR EACH ROW
                    SIGNAL SQLSTATE '45000'
                    SET MESSAGE_TEXT = 'Maintenance history is immutable and cannot be deleted.'
                    """
                )
                conn.commit()

            state["columns"] = {
                column
                for column in _EXTRA_COLUMNS
                if _column_exists(cur, "maintenance_history", column)
            }
            state["update_trigger"] = _trigger_exists(cur, _UPDATE_TRIGGER)
            state["delete_trigger"] = _trigger_exists(cur, _DELETE_TRIGGER)
    except Exception as exc:  # noqa: BLE001
        print(f"MAINTENANCE SCHEMA ERROR: {exc}")
        # Do not cache a failed setup; retry on the next maintenance write.
        _SCHEMA_STATE = None
        return state

    _SCHEMA_STATE = state
    return state


def current_technician():
    """Return the signed-in username, or ``System`` outside a request."""
    try:
        user = session.get("user") or {}
        return (
            user.get("username")
            or user.get("full_name")
            or user.get("name")
            or "System"
        )
    except Exception:  # noqa: BLE001
        return "System"


def is_repair_transition(old_status, new_status):
    """True when a status change moves a broken asset back into service."""
    old_key = str(old_status or "").strip().lower()
    new_key = str(new_status or "").strip().lower()
    if not old_key or old_key == new_key:
        return False
    return old_key in _BROKEN_STATUSES and new_key not in _BROKEN_STATUSES


def log_maintenance_event(
    cur,
    *,
    asset_type,
    asset_id,
    action,
    maintenance_type,
    pcid=None,
    old_status=None,
    new_status=None,
    previous_part_status=None,
    new_part_status=None,
    replaced_part=None,
    previous_part=None,
    new_part=None,
    computer_name=None,
    asset_serial=None,
    technician=None,
    performed_by=None,
    risk_level=None,
    health_score=None,
    remarks=None,
):
    """Insert one append-only maintenance record and return its id."""
    asset_key = "PC" if str(asset_type or "").strip().upper() == "PC" else "Device"
    technician = (technician or current_technician()).strip() or "System"
    performed_by = performed_by or technician

    # Snapshot the asset identity so later renames cannot rewrite history.
    if not computer_name or not asset_serial:
        try:
            if asset_key == "PC":
                cur.execute(
                    "SELECT pcname, serial_no, municipal_serial_no "
                    "FROM pcinfofull WHERE pcid = %s",
                    (asset_id,),
                )
            else:
                cur.execute(
                    "SELECT item_name, serial_no, municipal_serial_no "
                    "FROM devices_full WHERE accession_id = %s",
                    (asset_id,),
                )
            row = cur.fetchone()
            if row:
                if not computer_name:
                    computer_name = row.get("pcname") or row.get("item_name")
                if not asset_serial:
                    asset_serial = row.get("serial_no") or row.get("municipal_serial_no")
        except Exception:  # noqa: BLE001
            pass

    cur.execute(
        """
        INSERT INTO maintenance_history
            (pcid, asset_type, asset_id, action, maintenance_type,
             replaced_part, previous_part, new_part,
             previous_part_status, new_part_status,
             computer_name, asset_serial, technician,
             old_status, new_status, risk_level, health_score,
             performed_by, remarks)
        VALUES
            (%s, %s, %s, %s, %s,
             %s, %s, %s,
             %s, %s,
             %s, %s, %s,
             %s, %s, %s, %s,
             %s, %s)
        """,
        (
            asset_id if asset_key == "PC" else pcid,
            asset_key,
            asset_id,
            action,
            maintenance_type,
            replaced_part,
            previous_part,
            new_part,
            previous_part_status,
            new_part_status,
            computer_name,
            asset_serial,
            technician,
            old_status,
            new_status,
            risk_level,
            health_score,
            performed_by,
            remarks,
        ),
    )
    return cur.lastrowid
