from flask import Blueprint, render_template, session, redirect, url_for, flash, jsonify
from db import get_db_connection
from utils.user_activity import humanize_activity_action
import pymysql

from datetime import datetime, timedelta

dashboard_bp = Blueprint('dashboard_bp', __name__, template_folder='templates')

# Simple cache for table column existence checks within this process
_table_column_cache = {}

def _table_has_column(conn, table_name, column_name='is_archived'):
    """Return True if the given table has the specified column in the current database.

    Uses a small in-memory cache to avoid repeated information_schema queries
    during a single request/worker lifetime.
    """
    key = f"{table_name}.{column_name}"
    if key in _table_column_cache:
        return _table_column_cache[key]

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                (table_name, column_name)
            )
            row = cur.fetchone()
            if isinstance(row, dict):
                exists = int(row.get('cnt', 0)) > 0
            else:
                exists = bool(row and row[0] > 0)
    except Exception:
        exists = False

    _table_column_cache[key] = exists
    return exists




@dashboard_bp.route('/dashboardlist')
def dashboard_list():
    """Fallback route for dashboard."""
    return redirect(url_for('dashboard_bp.dashboard_load'))

@dashboard_bp.route('/inventory_category_data')
def inventory_category_data():
    """Returns JSON for inventory by category chart."""
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Get device categories from devices_full
            has_dev_arch = _table_has_column(conn, 'devices_full')
            has_pc_arch = _table_has_column(conn, 'pcinfofull')

            dev_where_arch = 'AND COALESCE(is_archived, 0) = 0' if has_dev_arch else ''
            pc_where_arch = 'AND COALESCE(is_archived, 0) = 0' if has_pc_arch else ''

            cur.execute(f"""
                SELECT device_type as category, COUNT(*) as count, SUM(acquisition_cost) as total_cost
                FROM devices_full
                WHERE device_type IS NOT NULL AND device_type != '' {dev_where_arch} AND LOWER(COALESCE(status, '')) != 'surrendered'
                GROUP BY device_type
                ORDER BY count DESC
            """)
            device_categories = cur.fetchall()

            # Get PCs as a separate category
            cur.execute(f"""
                SELECT 'PCs' as category, COUNT(*) as count, SUM(acquisition_cost) as total_cost
                FROM pcinfofull
                WHERE { '1=1 ' + pc_where_arch if pc_where_arch else '1=1' } AND LOWER(COALESCE(status, '')) != 'surrendered'
            """)
            pc_data = cur.fetchone()
            
            # Combine data
            categories = device_categories
            if pc_data and pc_data['count'] > 0:
                categories.append(pc_data)
            
            return jsonify({
                'categories': [cat['category'] for cat in categories],
                'counts': [cat['count'] for cat in categories],
                'costs': [float(cat['total_cost'] or 0) for cat in categories]
            })
            
    except Exception as e:
        print("❌ Inventory category data error:", e)
        return jsonify({'categories': [], 'counts': [], 'costs': []})
    finally:
        conn.close()

@dashboard_bp.route('/stock_status_data')
def stock_status_data():
    """Returns JSON for stock status distribution chart."""
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Get device statuses
            has_dev_arch = _table_has_column(conn, 'devices_full')
            has_pc_arch = _table_has_column(conn, 'pcinfofull')

            dev_where_arch = 'AND is_archived = 0' if has_dev_arch else ''
            pc_where_arch = 'AND is_archived = 0' if has_pc_arch else ''

            cur.execute(f"""
                SELECT status, COUNT(*) as count
                FROM devices_full
                WHERE 1=1 {dev_where_arch}
                GROUP BY status
            """)
            device_statuses = cur.fetchall()

            # Get PC statuses
            cur.execute(f"""
                SELECT status, COUNT(*) as count
                FROM pcinfofull
                WHERE 1=1 {pc_where_arch}
                GROUP BY status
            """)
            pc_statuses = cur.fetchall()
            
            # Combine and aggregate statuses
            status_map = {}
            
            # Add device counts
            for status in device_statuses:
                status_name = status['status'] or 'Unknown'
                status_map[status_name] = status_map.get(status_name, 0) + status['count']
            
            # Add PC counts
            for status in pc_statuses:
                status_name = status['status'] or 'Unknown'
                status_map[status_name] = status_map.get(status_name, 0) + status['count']
            
            return jsonify({
                'statuses': list(status_map.keys()),
                'counts': list(status_map.values())
            })
            
    except Exception as e:
        print("❌ Stock status data error:", e)
        return jsonify({'statuses': [], 'counts': []})
    finally:
        conn.close()

@dashboard_bp.route('/cost_data/<string:filter_type>')
def cost_data(filter_type):
    """
    Returns JSON for cost chart.
    Supports Weekly, Monthly, and Yearly aggregations.
    """
    conn = get_db_connection()
    try:
        filter_key = (filter_type or '').strip().lower()
        current_year = datetime.now().year

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            if filter_key == 'weekly':
                # Last 8 weeks (including current week), grouped by week start (Monday)
                today = datetime.now().date()
                current_week_start = today - timedelta(days=today.weekday())
                week_starts = [current_week_start - timedelta(weeks=i) for i in range(7, -1, -1)]
                weekly_cost = {ws.isoformat(): 0.0 for ws in week_starts}
                range_start = week_starts[0]

                for table_name in ('devices_full', 'pcinfofull'):
                    has_arch = _table_has_column(conn, table_name)
                    arch_clause = 'AND is_archived = 0' if has_arch else ''
                    cur.execute(f"""
                        SELECT
                            DATE_SUB(DATE(created_at), INTERVAL WEEKDAY(created_at) DAY) AS week_start,
                            SUM(acquisition_cost) AS total
                        FROM {table_name}
                        WHERE created_at >= %s {arch_clause} AND LOWER(COALESCE(status, '')) != 'surrendered'
                        GROUP BY week_start
                    """, (range_start,))
                    for row in cur.fetchall():
                        week_start = row.get('week_start')
                        if week_start is None:
                            continue
                        week_key = week_start.isoformat() if hasattr(week_start, 'isoformat') else str(week_start)
                        if week_key in weekly_cost:
                            weekly_cost[week_key] += float(row.get('total') or 0)

                labels = [ws.strftime('%b %d') for ws in week_starts]
                cost_in = [weekly_cost[ws.isoformat()] for ws in week_starts]

            elif filter_key == 'yearly':
                # Last 5 years (including current year)
                start_year = current_year - 4
                years = list(range(start_year, current_year + 1))
                yearly_cost = {year: 0.0 for year in years}

                for table_name in ('devices_full', 'pcinfofull'):
                    has_arch = _table_has_column(conn, table_name)
                    arch_clause = 'AND is_archived = 0' if has_arch else ''
                    cur.execute(f"""
                        SELECT YEAR(created_at) AS year, SUM(acquisition_cost) AS total
                        FROM {table_name}
                        WHERE YEAR(created_at) BETWEEN %s AND %s {arch_clause} AND LOWER(COALESCE(status, '')) != 'surrendered'
                        GROUP BY YEAR(created_at)
                    """, (start_year, current_year))
                    for row in cur.fetchall():
                        year = row.get('year')
                        if year in yearly_cost:
                            yearly_cost[year] += float(row.get('total') or 0)

                labels = [str(year) for year in years]
                cost_in = [yearly_cost[year] for year in years]

            else:
                # Default: monthly totals for current year
                monthly_cost = {m: 0.0 for m in range(1, 13)}

                for table_name in ('devices_full', 'pcinfofull'):
                    has_arch = _table_has_column(conn, table_name)
                    arch_clause = 'AND is_archived = 0' if has_arch else ''
                    cur.execute(f"""
                        SELECT MONTH(created_at) AS month, SUM(acquisition_cost) AS total
                        FROM {table_name}
                        WHERE YEAR(created_at) = %s {arch_clause} AND LOWER(COALESCE(status, '')) != 'surrendered'
                        GROUP BY MONTH(created_at)
                    """, (current_year,))
                    for row in cur.fetchall():
                        month = row.get('month')
                        if month in monthly_cost:
                            monthly_cost[month] += float(row.get('total') or 0)

                labels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
                cost_in = [monthly_cost[i] for i in range(1, 13)]

        return jsonify({
            'labels': labels,
            'cost_in': cost_in
        })

    except Exception as e:
        print("❌ Cost chart error:", e)
        return jsonify({'labels': [], 'cost_in': []})
    finally:
        conn.close()

        
@dashboard_bp.route('/consumable_analytics_data')
def consumable_analytics_data():
    """Returns JSON for consumable analytics."""
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Get consumable usage rate statistics (based on RETURN transactions)
            cur.execute("""
                SELECT
                    c.item_name as consumable_name,
                    c.quantity as current_quantity,
                    1 as minimum_level,
                    GREATEST(c.quantity, 1) as maximum_level,
                    0 as unit_cost,
                    NULL as last_restocked,
                    COALESCE(SUM(CASE WHEN t.action = 'RETURN' THEN t.quantity ELSE 0 END), 0) as total_used,
                    COUNT(CASE WHEN t.action = 'RETURN' THEN t.transaction_id END) as usage_count,
                    AVG(CASE WHEN t.action = 'RETURN' THEN t.quantity END) as avg_usage_per_transaction,
                    MAX(CASE WHEN t.action = 'RETURN' THEN t.created_at END) as last_usage_date,
                    NULL as days_since_restock,
                    CASE
                        WHEN c.quantity <= 0 THEN 'Critical'
                        WHEN c.quantity <= 1 THEN 'Low'
                        ELSE 'Normal'
                    END as stock_status
                FROM consumables c
                LEFT JOIN consumable_transactions t
                    ON c.accession_id = t.accession_id
                GROUP BY c.accession_id, c.item_name, c.quantity
                ORDER BY total_used DESC
            """)
            consumable_usage = cur.fetchall()
            
            # Get reorder analytics
            cur.execute("""
                SELECT
                    c.item_name as consumable_name,
                    c.quantity as current_quantity,
                    1 as minimum_level,
                    GREATEST(c.quantity, 1) as maximum_level,
                    0 as unit_cost,
                    NULL as last_restocked,
                    COALESCE(SUM(CASE WHEN t.action = 'RETURN' THEN t.quantity ELSE 0 END), 0) as total_used,
                    CASE
                        WHEN c.quantity <= 0 THEN 'Immediate'
                        WHEN c.quantity <= 1 THEN 'Soon'
                        ELSE 'Monitor'
                    END as reorder_priority,
                    NULL as estimated_days_remaining,
                    NULL as days_until_reorder,
                    0 as quantity_needed
                FROM consumables c
                LEFT JOIN consumable_transactions t
                    ON c.accession_id = t.accession_id
                    AND t.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                GROUP BY c.accession_id, c.item_name, c.quantity
                ORDER BY
                    CASE
                        WHEN c.quantity <= 0 THEN 1
                        WHEN c.quantity <= 1 THEN 2
                        ELSE 3
                    END,
                    total_used DESC
            """)
            reorder_analytics = cur.fetchall()
            
            # Get cost analysis over time
            cur.execute("""
                SELECT
                    DATE_FORMAT(t.created_at, '%Y-%m') as month,
                    SUM(t.quantity) as monthly_cost,
                    SUM(t.quantity) as monthly_quantity,
                    COUNT(DISTINCT t.accession_id) as active_consumables
                FROM consumable_transactions t
                WHERE t.action = 'RETURN'
                  AND t.created_at >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
                GROUP BY DATE_FORMAT(t.created_at, '%Y-%m')
                ORDER BY month DESC
            """)
            cost_over_time_raw = cur.fetchall() or []

            # Ensure the chart always has the last 12 months (zero-filled)
            month_map = {row['month']: row for row in cost_over_time_raw if row.get('month')}
            cost_over_time = []
            for i in range(11, -1, -1):
                month_dt = datetime.now().replace(day=1)
                year = month_dt.year
                month = month_dt.month - i
                while month <= 0:
                    year -= 1
                    month += 12
                while month > 12:
                    year += 1
                    month -= 12
                key = f"{year:04d}-{month:02d}"
                row = month_map.get(key)
                cost_over_time.append({
                    'month': key,
                    'monthly_cost': float(row.get('monthly_cost', 0)) if row else 0,
                    'monthly_quantity': int(row.get('monthly_quantity', 0)) if row else 0,
                    'active_consumables': int(row.get('active_consumables', 0)) if row else 0,
                })
            
            # Get consumable type analysis
            cur.execute("""
                SELECT
                    c.item_name as consumable_name,
                    c.quantity as current_quantity,
                    0 as minimum_level,
                    0 as unit_cost,
                    COALESCE(SUM(CASE WHEN t.action = 'RETURN' THEN t.quantity ELSE 0 END), 0) as total_used,
                    0 as current_value,
                    NULL as avg_monthly_usage,
                    NULL as projected_usage,
                    NULL as days_of_supply
                FROM consumables c
                LEFT JOIN consumable_transactions t
                    ON c.accession_id = t.accession_id
                    AND t.created_at >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
                GROUP BY c.accession_id, c.item_name, c.quantity
                ORDER BY total_used DESC
            """)
            consumable_analysis = cur.fetchall()
            
            # Get top consumable users
            cur.execute("""
                SELECT
                    u.faculty_name as department_name,
                    COUNT(t.transaction_id) as usage_count,
                    SUM(t.quantity) as total_quantity,
                    0 as total_cost,
                    AVG(t.quantity) as avg_per_usage
                FROM consumable_transactions t
                LEFT JOIN users u ON t.performed_by = u.user_id
                WHERE t.action = 'RETURN'
                  AND t.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                GROUP BY u.faculty_name
                ORDER BY total_quantity DESC
                LIMIT 10
            """)
            top_users = cur.fetchall()
            
            return jsonify({
                'consumable_usage': consumable_usage,
                'reorder_analytics': reorder_analytics,
                'cost_over_time': cost_over_time,
                'consumable_analysis': consumable_analysis,
                'top_users': top_users
            })
            
    except Exception as e:
        print("❌ Consumable analytics data error:", e)
        return jsonify({
            'consumable_usage': [],
            'reorder_analytics': [],
            'cost_over_time': [],
            'consumable_analysis': [],
            'top_users': []
        })
    finally:
        conn.close()

@dashboard_bp.route('/consumable_monthly_cost_data')
def consumable_monthly_cost_data():
    """Returns JSON for monthly consumable cost chart."""
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Get monthly cost data for consumables (based on RECEIVE transactions = stock in)
            # Consumables currently have no unit-cost column in the database,
            # so the chart reports received quantities and zero monetary cost.
            cur.execute("""
                SELECT
                    DATE_FORMAT(t.created_at, '%Y-%m') as month,
                    0 as monthly_cost,
                    SUM(t.quantity) as monthly_quantity
                FROM consumable_transactions t
                WHERE t.action = 'RECEIVE'
                  AND t.created_at >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
                GROUP BY DATE_FORMAT(t.created_at, '%Y-%m')
                ORDER BY month
            """)
            cost_data_raw = cur.fetchall() or []

            # Ensure the chart always has the last 12 months (zero-filled)
            month_map = {row['month']: row for row in cost_data_raw if row.get('month')}
            
            labels = []
            monthly_costs = []
            monthly_quantities = []
            
            for i in range(11, -1, -1):
                month_dt = datetime.now().replace(day=1)
                year = month_dt.year
                month = month_dt.month - i
                while month <= 0:
                    year -= 1
                    month += 12
                while month > 12:
                    year += 1
                    month -= 12
                key = f"{year:04d}-{month:02d}"
                row = month_map.get(key)
                
                # Format label as full month name
                label_date = datetime(year, month, 1)
                labels.append(label_date.strftime('%B'))
                monthly_costs.append(float(row.get('monthly_cost', 0)) if row else 0)
                monthly_quantities.append(int(row.get('monthly_quantity', 0)) if row else 0)
            
            return jsonify({
                'labels': labels,
                'monthly_cost': monthly_costs,
                'monthly_quantity': monthly_quantities
            })
            
    except Exception as e:
        print("❌ Consumable monthly cost data error:", e)
        return jsonify({
            'labels': [],
            'monthly_cost': [],
            'monthly_quantity': []
        })
    finally:
        conn.close()

@dashboard_bp.route('/department_analytics_data')
def department_analytics_data():
    """Returns JSON for department analytics."""
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Get department usage statistics
            has_dev_arch = _table_has_column(conn, 'devices_full')
            has_pc_arch = _table_has_column(conn, 'pcinfofull')

            df_join_arch = 'AND df.is_archived = 0' if has_dev_arch else ''
            pcf_join_arch = 'AND pcf.is_archived = 0' if has_pc_arch else ''

            cur.execute(f"""
                SELECT 
                    d.department_name,
                    COUNT(DISTINCT df.accession_id) as device_count,
                    COUNT(DISTINCT pcf.pcid) as pc_count,
                    (COUNT(DISTINCT df.accession_id) + COUNT(DISTINCT pcf.pcid)) as total_assets,
                    COALESCE(SUM(df.acquisition_cost), 0) + COALESCE(SUM(pcf.acquisition_cost), 0) as total_cost,
                    COUNT(DISTINCT CASE WHEN LOWER(COALESCE(df.status, '')) IN ('in used','in use','in-used','inuse') THEN df.accession_id END) +
                    COUNT(DISTINCT CASE WHEN LOWER(COALESCE(pcf.status, '')) IN ('in used','in use','in-used','inuse') THEN pcf.pcid END) as in_use_count,
                    COUNT(DISTINCT CASE WHEN df.status = 'Available' THEN df.accession_id END) +
                    COUNT(DISTINCT CASE WHEN pcf.status = 'Available' THEN pcf.pcid END) as available_count
                FROM departments d
                LEFT JOIN devices_full df ON d.department_id = df.department_id {df_join_arch} AND LOWER(COALESCE(df.status, '')) != 'surrendered'
                LEFT JOIN pcinfofull pcf ON d.department_id = pcf.department_id {pcf_join_arch} AND LOWER(COALESCE(pcf.status, '')) != 'surrendered'
                GROUP BY d.department_id, d.department_name
                ORDER BY total_assets DESC
            """)
            department_usage = cur.fetchall()
            
            # Get cross-department efficiency metrics
            cur.execute(f"""
                SELECT 
                    d.department_name,
                    COUNT(DISTINCT br.borrow_id) as total_borrows,
                    COUNT(DISTINCT CASE WHEN br.status = 'Returned' THEN br.borrow_id END) as returned_borrows,
                    AVG(DATEDIFF(COALESCE(br.return_date, CURDATE()), br.borrow_date)) as avg_borrow_days,
                    ROUND(COUNT(DISTINCT CASE WHEN br.status = 'Returned' THEN br.borrow_id END) * 100.0 / 
                          COUNT(DISTINCT br.borrow_id), 2) as return_rate,
                    (COUNT(DISTINCT df.accession_id) + COUNT(DISTINCT pcf.pcid)) as total_assets,
                    ROUND(COUNT(DISTINCT br.borrow_id) * 1.0 / 
                          NULLIF((COUNT(DISTINCT df.accession_id) + COUNT(DISTINCT pcf.pcid)), 0), 2) as utilization_rate
                FROM departments d
                LEFT JOIN devices_full df ON d.department_id = df.department_id {df_join_arch} AND LOWER(COALESCE(df.status, '')) != 'surrendered'
                LEFT JOIN pcinfofull pcf ON d.department_id = pcf.department_id {pcf_join_arch} AND LOWER(COALESCE(pcf.status, '')) != 'surrendered'
                LEFT JOIN borrow_requests br ON (df.accession_id = br.device_id OR pcf.pcid = br.device_id)
                GROUP BY d.department_id, d.department_name
                HAVING total_assets > 0
                ORDER BY utilization_rate DESC
            """)
            department_efficiency = cur.fetchall()
            
            # Get cost per department breakdown
            cur.execute(f"""
                SELECT 
                    d.department_name,
                    COALESCE(SUM(df.acquisition_cost), 0) as device_costs,
                    COALESCE(SUM(pcf.acquisition_cost), 0) as pc_costs,
                    COALESCE(SUM(df.acquisition_cost), 0) + COALESCE(SUM(pcf.acquisition_cost), 0) as total_costs,
                    COUNT(DISTINCT df.accession_id) + COUNT(DISTINCT pcf.pcid) as asset_count,
                    ROUND((COALESCE(SUM(df.acquisition_cost), 0) + COALESCE(SUM(pcf.acquisition_cost), 0)) / 
                          NULLIF(COUNT(DISTINCT df.accession_id) + COUNT(DISTINCT pcf.pcid), 0), 2) as avg_cost_per_asset
                FROM departments d
                LEFT JOIN devices_full df ON d.department_id = df.department_id {df_join_arch} AND LOWER(COALESCE(df.status, '')) != 'surrendered'
                LEFT JOIN pcinfofull pcf ON d.department_id = pcf.department_id {pcf_join_arch} AND LOWER(COALESCE(pcf.status, '')) != 'surrendered'
                GROUP BY d.department_id, d.department_name
                ORDER BY total_costs DESC
            """)
            department_costs = cur.fetchall()
            
            # Get monthly department usage trends
            current_year = datetime.now().year
            cur.execute(f"""
                SELECT 
                    d.department_name,
                    MONTH(br.borrow_date) as month,
                    COUNT(*) as borrow_count
                FROM departments d
                LEFT JOIN devices_full df ON d.department_id = df.department_id {df_join_arch} AND LOWER(COALESCE(df.status, '')) != 'surrendered'
                LEFT JOIN pcinfofull pcf ON d.department_id = pcf.department_id {pcf_join_arch} AND LOWER(COALESCE(pcf.status, '')) != 'surrendered'
                LEFT JOIN borrow_requests br ON (df.accession_id = br.device_id OR pcf.pcid = br.device_id)
                WHERE YEAR(br.borrow_date) = %s AND br.borrow_date IS NOT NULL
                GROUP BY d.department_id, d.department_name, MONTH(br.borrow_date)
                ORDER BY month, department_name
            """, (current_year,))
            monthly_trends = cur.fetchall()
            
            return jsonify({
                'department_usage': department_usage,
                'department_efficiency': department_efficiency,
                'department_costs': department_costs,
                'monthly_trends': monthly_trends
            })
            
    except Exception as e:
        print("❌ Department analytics data error:", e)
        return jsonify({
            'department_usage': [],
            'department_efficiency': [],
            'department_costs': [],
            'monthly_trends': []
        })
    finally:
        conn.close()

@dashboard_bp.route('/recently_received_data')
def recently_received_data():
    """Returns JSON for recently received items."""
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("""
                SELECT
                    t.transaction_id,
                    t.accession_id,
                    t.item_name,
                    t.action,
                    t.quantity,
                    t.previous_stock,
                    t.new_stock,
                    t.reference_no,
                    t.notes,
                    DATE_FORMAT(t.created_at, '%Y-%m-%d %H:%i') as created_at,
                    u.faculty_name as performed_by
                FROM consumable_transactions t
                LEFT JOIN users u
                    ON u.user_id = t.performed_by
                WHERE t.action = 'RECEIVE'
                ORDER BY t.created_at DESC
                LIMIT 10
            """)
            received_items = cur.fetchall() or []
            
            return jsonify({
                'received_items': received_items
            })
            
    except Exception as e:
        print("❌ Recently received data error:", e)
        return jsonify({
            'received_items': []
        })
    finally:
        conn.close()


@dashboard_bp.route('/return_rate_data')
def return_rate_data():
    """Returns JSON for return rate analysis."""
    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            # Get return rate statistics
            cur.execute("""
                SELECT 
                    COUNT(*) as total_borrowed,
                    SUM(CASE WHEN status = 'Returned' THEN 1 ELSE 0 END) as returned_count,
                    SUM(CASE WHEN status = 'Pending' THEN 1 ELSE 0 END) as pending_count,
                    SUM(CASE WHEN status = 'Approved' THEN 1 ELSE 0 END) as approved_count,
                    AVG(DATEDIFF(COALESCE(return_date, CURDATE()), borrow_date)) as avg_days_borrowed
                FROM borrow_requests
                WHERE borrow_date IS NOT NULL
            """)
            stats = cur.fetchone()
            
            # Get monthly return trends for the current year
            current_year = datetime.now().year
            cur.execute("""
                SELECT 
                    MONTH(borrow_date) as month,
                    COUNT(*) as borrowed,
                    SUM(CASE WHEN status = 'Returned' THEN 1 ELSE 0 END) as returned
                FROM borrow_requests
                WHERE YEAR(borrow_date) = %s
                GROUP BY MONTH(borrow_date)
                ORDER BY month
            """, (current_year,))
            monthly_trends = cur.fetchall()
            
            # Get return rate by device type
            cur.execute("""
                SELECT 
                    df.device_type,
                    COUNT(*) as total_borrowed,
                    SUM(CASE WHEN br.status = 'Returned' THEN 1 ELSE 0 END) as returned_count,
                    ROUND(SUM(CASE WHEN br.status = 'Returned' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) as return_rate
                FROM borrow_requests br
                LEFT JOIN devices_full df ON br.device_id = df.accession_id
                WHERE br.borrow_date IS NOT NULL
                GROUP BY df.device_type
                ORDER BY return_rate DESC
            """)
            device_type_stats = cur.fetchall()
            
            cur.execute("""
                SELECT
                    t.transaction_id,
                    t.accession_id,
                    t.item_name,
                    t.action,
                    t.quantity,
                    t.previous_stock,
                    t.new_stock,
                    t.reference_no,
                    t.reason,
                    t.notes,
                    DATE_FORMAT(t.created_at, '%Y-%m-%d %H:%i') as created_at,
                    u.faculty_name as performed_by
                FROM consumable_transactions t
                LEFT JOIN users u
                    ON u.user_id = t.performed_by
                WHERE t.action = 'RETURN'
                ORDER BY t.created_at DESC
                LIMIT 10
            """)
            returned_items = cur.fetchall() or []

            # Normalize aggregate values to Python numeric types before arithmetic.
            total_borrowed = int(stats.get('total_borrowed') or 0)
            returned_count = int(stats.get('returned_count') or 0)
            pending_count = int(stats.get('pending_count') or 0)
            approved_count = int(stats.get('approved_count') or 0)
            avg_days_borrowed = round(float(stats.get('avg_days_borrowed') or 0), 1)
            return_rate_percentage = round((returned_count * 100.0) / (total_borrowed or 1), 2)
            
            return jsonify({
                'stats': {
                    'total_borrowed': total_borrowed,
                    'returned_count': returned_count,
                    'pending_count': pending_count,
                    'approved_count': approved_count,
                    'avg_days_borrowed': avg_days_borrowed,
                    'return_rate_percentage': return_rate_percentage
                },
                'monthly_trends': monthly_trends,
                'device_type_stats': device_type_stats,
                'overdue_returns': returned_items
            })
            
    except Exception as e:
        print("❌ Return rate data error:", e)
        return jsonify({
            'stats': {'total_borrowed': 0, 'returned_count': 0, 'pending_count': 0, 'approved_count': 0, 'avg_days_borrowed': 0, 'return_rate_percentage': 0},
            'monthly_trends': [],
            'device_type_stats': [],
            'overdue_returns': []
        })
    finally:
        conn.close()

def _table_exists(conn, table_name):
    """Return True when a table exists in the active database."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM information_schema.TABLES"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                (table_name,),
            )
            row = cur.fetchone()
            if isinstance(row, dict):
                return int(row.get('cnt', 0)) > 0
            return bool(row and row[0] > 0)
    except Exception:
        return False


def _relative_time(value):
    """Human-friendly timestamp for the dashboard activity feed."""
    if not value:
        return ''
    if not isinstance(value, datetime):
        return str(value)

    seconds = int((datetime.now() - value).total_seconds())
    if seconds < 60:
        return 'Just now'

    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes}m ago'

    hours = minutes // 60
    if hours < 24:
        return f'{hours}h ago'

    days = hours // 24
    if days < 7:
        return f'{days}d ago'

    return value.strftime('%b %d, %Y')


_ACTIVITY_STYLES = (
    (('delete', 'remove'), ('trash-2', 'rose')),
    (('archive',), ('archive', 'amber')),
    (('add', 'create'), ('plus-circle', 'emerald')),
    (('edit', 'update'), ('pencil', 'blue')),
    (('import', 'export'), ('file-spreadsheet', 'violet')),
    (('check', 'mark'), ('check-circle-2', 'emerald')),
    (('surrender',), ('package-x', 'amber')),
    (('damage',), ('alert-triangle', 'rose')),
    (('login', 'logout'), ('log-in', 'slate')),
    (('view', 'filter'), ('eye', 'sky')),
)


def _activity_style(action):
    """Pick a lucide icon and accent tone based on an activity label."""
    text = (action or '').lower()
    for keywords, style in _ACTIVITY_STYLES:
        if any(keyword in text for keyword in keywords):
            return style
    return ('activity', 'slate')


def _get_ticket_counts(conn):
    """Return ticket status totals from the active ticket table."""
    counts = {
        'pending': 0,
        'ongoing': 0,
        'resolved': 0,
        'unresolved': 0,
        'invalid': 0,
    }

    if _table_exists(conn, 'tickets'):
        table_name = 'tickets'
    elif _table_exists(conn, 'concerns'):
        table_name = 'concerns'
    else:
        return counts

    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                f"SELECT LOWER(COALESCE(status, '')) AS status_key, COUNT(*) AS total "
                f"FROM {table_name} GROUP BY status_key"
            )
            for row in cur.fetchall() or []:
                status_key = (row.get('status_key') or '').strip()
                total = int(row.get('total') or 0)

                if status_key in ('open', 'pending'):
                    counts['pending'] += total
                elif status_key in (
                    'ongoing', 'accepted', 'in progress', 'processing',
                    'assigned', 'in-progress', 'inprogress',
                ):
                    counts['ongoing'] += total
                elif status_key in ('closed', 'resolved'):
                    counts['resolved'] += total
                elif status_key in ('declined', 'rejected', 'invalid'):
                    counts['invalid'] += total
                elif status_key == 'unresolved':
                    counts['unresolved'] += total
    except Exception as exc:
        print('Dashboard ticket count error:', exc)

    return counts


@dashboard_bp.route('/dashboardload')
def dashboard_load():
    """Overall system summary, recent activity, alerts, and global analytics."""
    total_assets = 0
    total_asset_value = 0.0
    open_requests = 0
    pending_requests = 0
    ongoing_requests = 0
    maintenance_due = 0
    overdue_maintenance = 0
    due_soon_maintenance = 0
    never_checked_maintenance = 0
    maintenance_notifications = []
    low_stock_count = 0
    low_stock_items = []
    recent_activities = []

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            has_dev_arch = _table_has_column(conn, 'devices_full')
            has_pc_arch = _table_has_column(conn, 'pcinfofull')
            dev_arch_clause = 'AND COALESCE(is_archived, 0) = 0' if has_dev_arch else ''
            pc_arch_clause = 'AND COALESCE(is_archived, 0) = 0' if has_pc_arch else ''

            # Overall asset totals across devices and PCs
            cur.execute(f"""
                SELECT COUNT(*) AS total, COALESCE(SUM(acquisition_cost), 0) AS asset_value
                FROM devices_full
                WHERE LOWER(COALESCE(status, '')) != 'surrendered' {dev_arch_clause}
            """)
            device_totals = cur.fetchone() or {}

            cur.execute(f"""
                SELECT COUNT(*) AS total, COALESCE(SUM(acquisition_cost), 0) AS asset_value
                FROM pcinfofull
                WHERE LOWER(COALESCE(status, '')) != 'surrendered' {pc_arch_clause}
            """)
            pc_totals = cur.fetchone() or {}

            total_assets = int(device_totals.get('total') or 0) + int(pc_totals.get('total') or 0)
            total_asset_value = float(device_totals.get('asset_value') or 0) + float(pc_totals.get('asset_value') or 0)

            # Open requests: pending, ongoing, and explicitly unresolved tickets
            ticket_counts = _get_ticket_counts(conn)
            pending_requests = ticket_counts['pending']
            ongoing_requests = ticket_counts['ongoing']
            open_requests = pending_requests + ongoing_requests + ticket_counts['unresolved']

            # Maintenance due / overdue across devices and PCs
            try:
                maintenance_items_sql = f"""
                    SELECT
                        accession_id as item_id,
                        item_name,
                        brand_model,
                        department_name,
                        DATEDIFF(CURDATE(), COALESCE(df.last_checked, DATE_SUB(CURDATE(), INTERVAL GREATEST(1, CASE WHEN IFNULL(df.maintenance_interval_days, 30) < 365 THEN IFNULL(df.maintenance_interval_days, 30) * 365 ELSE IFNULL(df.maintenance_interval_days, 30) END) DAY))) as days_since_check,
                        maintenance_interval_days,
                        CASE
                            WHEN df.last_checked IS NULL THEN 'Never Checked'
                            WHEN DATEDIFF(CURDATE(), df.last_checked) >= GREATEST(1, CASE WHEN IFNULL(df.maintenance_interval_days, 30) < 365 THEN IFNULL(df.maintenance_interval_days, 30) * 365 ELSE IFNULL(df.maintenance_interval_days, 30) END) THEN 'Overdue'
                            WHEN DATEDIFF(CURDATE(), df.last_checked) >= (GREATEST(1, CASE WHEN IFNULL(df.maintenance_interval_days, 30) < 365 THEN IFNULL(df.maintenance_interval_days, 30) * 365 ELSE IFNULL(df.maintenance_interval_days, 30) END) * 0.8) THEN 'Due Soon'
                            ELSE 'OK'
                        END as maintenance_status,
                        'Device' as item_type
                    FROM devices_full df
                    LEFT JOIN departments dep ON df.department_id = dep.department_id
                    WHERE df.status != 'Damaged' AND LOWER(COALESCE(df.status, '')) != 'surrendered' {dev_arch_clause}
                    UNION ALL
                    SELECT
                        pcid as item_id,
                        pcname as item_name,
                        '' as brand_model,
                        department_name,
                        DATEDIFF(CURDATE(), COALESCE(pcf.last_checked, DATE_SUB(CURDATE(), INTERVAL GREATEST(1, CASE WHEN IFNULL(pcf.maintenance_interval_days, 30) < 365 THEN IFNULL(pcf.maintenance_interval_days, 30) * 365 ELSE IFNULL(pcf.maintenance_interval_days, 30) END) DAY))) as days_since_check,
                        maintenance_interval_days,
                        CASE
                            WHEN pcf.last_checked IS NULL THEN 'Never Checked'
                            WHEN DATEDIFF(CURDATE(), pcf.last_checked) >= GREATEST(1, CASE WHEN IFNULL(pcf.maintenance_interval_days, 30) < 365 THEN IFNULL(pcf.maintenance_interval_days, 30) * 365 ELSE IFNULL(pcf.maintenance_interval_days, 30) END) THEN 'Overdue'
                            WHEN DATEDIFF(CURDATE(), pcf.last_checked) >= (GREATEST(1, CASE WHEN IFNULL(pcf.maintenance_interval_days, 30) < 365 THEN IFNULL(pcf.maintenance_interval_days, 30) * 365 ELSE IFNULL(pcf.maintenance_interval_days, 30) END) * 0.8) THEN 'Due Soon'
                            ELSE 'OK'
                        END as maintenance_status,
                        'PC' as item_type
                    FROM pcinfofull pcf
                    LEFT JOIN departments dep ON pcf.department_id = dep.department_id
                    WHERE pcf.status != 'Damaged' AND LOWER(COALESCE(pcf.status, '')) != 'surrendered' {pc_arch_clause}
                """

                cur.execute(f"""
                    SELECT maintenance_status, COUNT(*) AS total
                    FROM ({maintenance_items_sql}) AS due_items
                    WHERE maintenance_status != 'OK'
                    GROUP BY maintenance_status
                """)
                for row in cur.fetchall() or []:
                    status = row.get('maintenance_status')
                    total = int(row.get('total') or 0)
                    maintenance_due += total
                    if status == 'Overdue':
                        overdue_maintenance = total
                    elif status == 'Due Soon':
                        due_soon_maintenance = total
                    elif status == 'Never Checked':
                        never_checked_maintenance = total

                cur.execute(f"""
                    SELECT *
                    FROM ({maintenance_items_sql}) AS due_items
                    WHERE maintenance_status != 'OK'
                    ORDER BY
                        CASE maintenance_status
                            WHEN 'Overdue' THEN 1
                            WHEN 'Due Soon' THEN 2
                            WHEN 'Never Checked' THEN 3
                            ELSE 4
                        END,
                        days_since_check DESC
                    LIMIT 8
                """)
                maintenance_notifications = cur.fetchall() or []
            except Exception as exc:
                print('Dashboard maintenance query error:', exc)

            # Consumables that are low or out of stock
            try:
                has_cons_arch = _table_has_column(conn, 'consumables')
                cons_arch_clause = 'AND COALESCE(c.is_archived, 0) = 0' if has_cons_arch else ''

                cur.execute(f"""
                    SELECT COUNT(*) AS total
                    FROM consumables c
                    WHERE c.status IN ('Low Stock', 'Out of Stock') {cons_arch_clause}
                """)
                low_stock_count = int((cur.fetchone() or {}).get('total') or 0)

                cur.execute(f"""
                    SELECT
                        c.accession_id,
                        c.item_name,
                        c.brand,
                        c.category,
                        c.quantity,
                        c.unit,
                        c.status,
                        d.department_name
                    FROM consumables c
                    LEFT JOIN departments d ON c.department_id = d.department_id
                    WHERE c.status IN ('Low Stock', 'Out of Stock') {cons_arch_clause}
                    ORDER BY
                        CASE c.status WHEN 'Out of Stock' THEN 0 ELSE 1 END,
                        c.item_name ASC
                    LIMIT 8
                """)
                low_stock_items = cur.fetchall() or []
            except Exception as exc:
                print('Dashboard low stock query error:', exc)

            # Recent system activity, excluding this dashboard's own page views
            try:
                cur.execute("""
                    SELECT username, role, action, module, details, route, created_at
                    FROM user_activity_log
                    WHERE NOT (
                        action = 'View Page'
                        AND (
                            LOWER(COALESCE(module, '')) = 'dashboard'
                            OR LOWER(COALESCE(route, '')) LIKE '/dashboard%'
                        )
                    )
                    ORDER BY created_at DESC, log_id DESC
                    LIMIT 8
                """)
                for row in cur.fetchall() or []:
                    item = dict(row)
                    action_label = humanize_activity_action(item.get('action'), route=item.get('route'))
                    item['action_label'] = action_label
                    item['icon'], item['tone'] = _activity_style(action_label)
                    item['when'] = _relative_time(item.get('created_at'))
                    recent_activities.append(item)
            except Exception as exc:
                print('Dashboard recent activity error:', exc)

    except Exception as exc:
        print('Dashboard load error:', exc)
        flash('Error loading dashboard data.', 'error')
    finally:
        conn.close()

    return render_template(
        'dashboard.html',
        total_assets=total_assets,
        total_asset_value=total_asset_value,
        open_requests=open_requests,
        pending_requests=pending_requests,
        ongoing_requests=ongoing_requests,
        maintenance_due=maintenance_due,
        overdue_maintenance=overdue_maintenance,
        due_soon_maintenance=due_soon_maintenance,
        never_checked_maintenance=never_checked_maintenance,
        maintenance_notifications=maintenance_notifications,
        low_stock_count=low_stock_count,
        low_stock_items=low_stock_items,
        recent_activities=recent_activities,
    )
