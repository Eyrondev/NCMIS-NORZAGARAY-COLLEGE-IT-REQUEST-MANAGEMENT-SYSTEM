from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
import pymysql
from datetime import datetime
from db import get_db_connection
from utils.permissions import check_permission

receive_item_bp = Blueprint('receive_item', __name__)


def _date_to_str(value):
    if not value:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime('%Y-%m-%d')
    return str(value)


@receive_item_bp.route('/receive-item')
@check_permission('receive_item', 'view')
def receive_item():
    if 'user' not in session or not session['user'].get('user_id'):
        return redirect(url_for('login_bp.login'))

    return render_template('receive_item.html')


@receive_item_bp.route('/get-borrowed-items')
@check_permission('receive_item', 'view')
def get_borrowed_items():
    if 'user' not in session or not session['user'].get('user_id'):
        return jsonify({'error': 'Unauthorized'}), 401

    # Pagination parameters
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)

    if per_page not in [5, 10, 25, 50]:
        per_page = 10
    if page < 1:
        page = 1

    offset = (page - 1) * per_page

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Borrow requests are backed by the `borrow_requests` table.  Only
            # approved requests represent items that are currently on loan.
            cursor.execute(
                "SELECT COUNT(*) AS total FROM borrow_requests WHERE status='Approved'"
            )
            total_items = int((cursor.fetchone() or {}).get('total') or 0)

            cursor.execute(
                """
                SELECT
                    br.borrow_id,
                    br.device_id,
                    br.borrow_date,
                    br.return_date,
                    d.item_name,
                    d.device_type,
                    d.brand_model,
                    dep.department_name
                FROM borrow_requests br
                LEFT JOIN devices d ON d.device_id = br.device_id
                LEFT JOIN departments dep ON dep.department_id = d.department_id
                WHERE br.status = 'Approved'
                ORDER BY br.borrow_date DESC, br.borrow_id DESC
                LIMIT %s OFFSET %s
                """,
                (per_page, offset),
            )
            items = cursor.fetchall() or []
    finally:
        conn.close()

    total_pages = (total_items + per_page - 1) // per_page

    result = []
    for item in items:
        result.append({
            'borrow_id': item['borrow_id'],
            'item_id': item['device_id'],
            'item_type': 'Device',
            'item_name': item.get('item_name') or 'Unknown device',
            'category': item.get('device_type') or '',
            'location': '',
            'department': item.get('department_name') or '',
            'borrow_date': _date_to_str(item.get('borrow_date')),
            'expected_return_date': _date_to_str(item.get('return_date')),
        })

    return jsonify({
        'items': result,
        'page': page,
        'per_page': per_page,
        'total_items': total_items,
        'total_pages': total_pages,
    })


@receive_item_bp.route('/receive-item-submit', methods=['POST'])
@check_permission('receive_item', 'edit')
def receive_item_submit():
    if 'user' not in session or not session['user'].get('user_id'):
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(silent=True) or {}
    borrow_id = data.get('borrow_id')
    item_condition = data.get('item_condition')
    damage_description = (data.get('damage_description') or '').strip()
    has_damage = bool(data.get('has_damage', False))

    if not borrow_id or not item_condition:
        return jsonify({'error': 'Missing required fields'}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    br.borrow_id,
                    br.device_id,
                    br.status,
                    d.item_name,
                    d.device_type,
                    dep.department_name
                FROM borrow_requests br
                LEFT JOIN devices d ON d.device_id = br.device_id
                LEFT JOIN departments dep ON dep.department_id = d.department_id
                WHERE br.borrow_id = %s
                """,
                (borrow_id,),
            )
            item = cursor.fetchone()

            if not item or item.get('status') != 'Approved':
                return jsonify({'error': 'Item not found or already returned'}), 404

            cursor.execute(
                """
                UPDATE borrow_requests
                SET status='Returned', return_date=CURDATE()
                WHERE borrow_id=%s AND status='Approved'
                """,
                (borrow_id,),
            )

            cursor.execute(
                """
                UPDATE devices_units
                SET status='Available'
                WHERE device_id=%s AND status='Borrowed'
                LIMIT 1
                """,
                (item['device_id'],),
            )

            # If the item came back damaged, mark the device and record the
            # damage details.  Failures here are non-fatal for the return.
            if has_damage and damage_description:
                severity = 'Minor'
                if item_condition == 'Poor':
                    severity = 'Major'
                elif item_condition == 'Damaged':
                    severity = 'Critical'

                try:
                    cursor.execute(
                        "UPDATE devices SET status='Damaged' WHERE device_id=%s",
                        (item['device_id'],),
                    )
                    cursor.execute(
                        "SELECT accession_id FROM devices_full WHERE device_id=%s "
                        "ORDER BY accession_id LIMIT 1",
                        (item['device_id'],),
                    )
                    device_row = cursor.fetchone()
                    if device_row:
                        accession_id = device_row['accession_id']
                        cursor.execute(
                            "UPDATE devices_full SET status='Damaged' WHERE accession_id=%s",
                            (accession_id,),
                        )
                        cursor.execute(
                            """
                            INSERT INTO device_damage_reports
                                (accession_id, reported_by, damage_type, description)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (
                                accession_id,
                                (session.get('user') or {}).get('fullname', 'Unknown'),
                                severity,
                                damage_description,
                            ),
                        )
                except Exception as exc:
                    print(f"RECEIVE ITEM DAMAGE REPORT ERROR: {exc}")

        conn.commit()
        return jsonify({'success': True, 'message': 'Item received successfully'})

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"RECEIVE ITEM ERROR: {exc}")
        return jsonify({'error': str(exc)}), 500
    finally:
        conn.close()
