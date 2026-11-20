"""Maintenance Reports module (Section 5).

The report page is a read-only, report-oriented view over the append-only
``maintenance_history`` audit table created in Section 4.  It supports search,
filters, a date range, pagination, PDF/Excel export, and a print view.

Every row is presented as a *completed* maintenance activity because the
maintenance-history table only stores events that already happened.
"""

from datetime import datetime
from io import BytesIO
from urllib.parse import urlencode
from xml.sax.saxutils import escape as xml_escape

import pymysql
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    session,
    send_file,
    url_for,
)

from db import get_db_connection
from utils.maintenance_audit import ensure_maintenance_history_schema

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
except Exception:  # pragma: no cover - optional dependency
    Workbook = None

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

    PDF_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    PDF_AVAILABLE = False


maintenance_reports_bp = Blueprint(
    "maintenance_reports_bp",
    __name__,
    template_folder="templates",
)

REPORT_PER_PAGE_OPTIONS = (10, 25, 50, 100)
REPORT_STATUS_COMPLETED = "Completed"

_REPORT_FROM = """
FROM maintenance_history mh
LEFT JOIN pcinfofull pc
    ON UPPER(TRIM(mh.asset_type)) = 'PC'
   AND mh.asset_id = pc.pcid
LEFT JOIN devices_full df
    ON UPPER(TRIM(mh.asset_type)) = 'DEVICE'
   AND mh.asset_id = df.accession_id
"""

_REPORT_SELECT = """
SELECT
    mh.id,
    mh.asset_type,
    mh.asset_id,
    mh.action,
    COALESCE(NULLIF(TRIM(mh.maintenance_type), ''), NULLIF(TRIM(mh.action), ''), 'Maintenance') AS maintenance_type,
    NULLIF(TRIM(mh.replaced_part), '') AS replaced_part,
    NULLIF(TRIM(mh.previous_part), '') AS previous_part,
    NULLIF(TRIM(mh.new_part), '') AS new_part,
    NULLIF(TRIM(mh.previous_part_status), '') AS previous_part_status,
    NULLIF(TRIM(mh.new_part_status), '') AS new_part_status,
    COALESCE(
        NULLIF(TRIM(mh.computer_name), ''),
        NULLIF(TRIM(pc.pcname), ''),
        NULLIF(TRIM(df.item_name), ''),
        CONCAT('Asset #', mh.asset_id)
    ) AS computer_name,
    COALESCE(
        NULLIF(TRIM(mh.asset_serial), ''),
        NULLIF(TRIM(pc.serial_no), ''),
        NULLIF(TRIM(pc.municipal_serial_no), ''),
        NULLIF(TRIM(df.serial_no), ''),
        NULLIF(TRIM(df.municipal_serial_no), '')
    ) AS asset_serial,
    COALESCE(
        NULLIF(TRIM(mh.technician), ''),
        NULLIF(TRIM(mh.performed_by), ''),
        'System'
    ) AS technician,
    NULLIF(TRIM(mh.remarks), '') AS remarks,
    NULLIF(TRIM(mh.old_status), '') AS old_status,
    NULLIF(TRIM(mh.new_status), '') AS new_status,
    NULLIF(TRIM(mh.risk_level), '') AS risk_level,
    mh.performed_at
""" + _REPORT_FROM


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _require_login():
    if not session.get("user"):
        return redirect(url_for("login_bp.login"))
    return None


def _as_datetime(value):
    """Return a ``datetime`` for DB values and ISO-like strings."""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _parse_report_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _pretty_report_date(value):
    parsed = _parse_report_date(value)
    return parsed.strftime("%B %d, %Y") if parsed else (value or "")


def _format_report_datetime(value):
    parsed = _as_datetime(value)
    return parsed.strftime("%B %d, %Y %I:%M %p") if parsed else "—"


def _report_filters_from_request(args):
    """Normalise report filters from a request query string."""
    asset_type = (args.get("asset_type") or "").strip()
    if asset_type.lower() == "all":
        asset_type = ""
    elif asset_type:
        asset_type = "PC" if asset_type.upper() == "PC" else "Device"

    maintenance_type = (args.get("maintenance_type") or "").strip()
    if maintenance_type.lower() == "all":
        maintenance_type = ""

    technician = (args.get("technician") or "").strip()
    if technician.lower() == "all":
        technician = ""

    date_from = _parse_report_date(args.get("date_from"))
    date_to = _parse_report_date(args.get("date_to"))
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    return {
        "q": (args.get("q") or "").strip(),
        "asset_type": asset_type,
        "maintenance_type": maintenance_type,
        "technician": technician,
        "date_from": date_from.isoformat() if date_from else "",
        "date_to": date_to.isoformat() if date_to else "",
    }


def _report_filter_args(filters):
    return {key: value for key, value in filters.items() if value}


def _build_report_where(filters):
    clauses = []
    params = []

    if filters.get("q"):
        like = f"%{filters['q']}%"
        clauses.append(
            "("
            "mh.computer_name LIKE %s OR mh.asset_serial LIKE %s OR CAST(mh.asset_id AS CHAR) LIKE %s "
            "OR mh.replaced_part LIKE %s OR mh.previous_part LIKE %s OR mh.new_part LIKE %s "
            "OR mh.technician LIKE %s OR mh.performed_by LIKE %s "
            "OR mh.maintenance_type LIKE %s OR mh.action LIKE %s "
            "OR mh.remarks LIKE %s OR mh.old_status LIKE %s OR mh.new_status LIKE %s "
            "OR pc.pcname LIKE %s OR df.item_name LIKE %s"
            ")"
        )
        params.extend([like] * 15)

    if filters.get("asset_type"):
        clauses.append("UPPER(TRIM(mh.asset_type)) = %s")
        params.append(filters["asset_type"].upper())

    if filters.get("maintenance_type"):
        clauses.append(
            "COALESCE(NULLIF(TRIM(mh.maintenance_type), ''), NULLIF(TRIM(mh.action), '')) = %s"
        )
        params.append(filters["maintenance_type"])

    if filters.get("technician"):
        clauses.append(
            "COALESCE(NULLIF(TRIM(mh.technician), ''), NULLIF(TRIM(mh.performed_by), '')) = %s"
        )
        params.append(filters["technician"])

    if filters.get("date_from"):
        clauses.append("mh.performed_at >= %s")
        params.append(f"{filters['date_from']} 00:00:00")

    if filters.get("date_to"):
        clauses.append("mh.performed_at < DATE_ADD(%s, INTERVAL 1 DAY)")
        params.append(f"{filters['date_to']} 00:00:00")

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


def _load_filter_options(cur):
    cur.execute(
        "SELECT DISTINCT COALESCE(NULLIF(TRIM(maintenance_type), ''), NULLIF(TRIM(action), '')) AS value "
        "FROM maintenance_history "
        "WHERE COALESCE(NULLIF(TRIM(maintenance_type), ''), NULLIF(TRIM(action), '')) IS NOT NULL "
        "ORDER BY value"
    )
    maintenance_types = [row["value"] for row in (cur.fetchall() or [])]

    cur.execute(
        "SELECT DISTINCT COALESCE(NULLIF(TRIM(technician), ''), NULLIF(TRIM(performed_by), '')) AS value "
        "FROM maintenance_history "
        "WHERE COALESCE(NULLIF(TRIM(technician), ''), NULLIF(TRIM(performed_by), '')) IS NOT NULL "
        "ORDER BY value"
    )
    technicians = [row["value"] for row in (cur.fetchall() or [])]

    return maintenance_types, technicians


def _components_for_row(row):
    """Return (headline, detail, combined text) for the replaced-components cell."""
    replaced = (row.get("replaced_part") or "").strip()
    previous = (row.get("previous_part") or "").strip()
    new_part = (row.get("new_part") or "").strip()

    if replaced:
        headline = replaced
    elif new_part:
        headline = f"Installed: {new_part}"
    else:
        headline = "No components replaced"

    detail_parts = []
    if previous and previous not in {"—", "-"}:
        detail_parts.append(f"Removed: {previous}")
    if new_part and replaced and new_part.lower() != replaced.lower():
        detail_parts.append(f"Installed: {new_part}")

    detail = " · ".join(detail_parts)
    combined = headline + (f" ({detail})" if detail else "")
    return headline, detail, combined


def _shape_report_row(row):
    performed_at = _as_datetime(row.get("performed_at"))
    asset_type = (row.get("asset_type") or "").strip()
    asset_type_key = "PC" if asset_type.upper() == "PC" else "DEVICE"
    asset_type_label = "PC" if asset_type_key == "PC" else "Device"

    report_id = row.get("id")
    try:
        report_no = f"MR-{int(report_id):05d}"
    except (TypeError, ValueError):
        report_no = "MR"

    old_status = (row.get("old_status") or "").strip()
    new_status = (row.get("new_status") or "").strip()
    if old_status and new_status and old_status.lower() != new_status.lower():
        status_transition = f"{old_status} → {new_status}"
    else:
        status_transition = new_status or old_status or "—"

    headline, detail, combined = _components_for_row(row)

    return {
        "id": report_id,
        "report_no": report_no,
        "asset_type": asset_type,
        "asset_type_key": asset_type_key,
        "asset_type_label": asset_type_label,
        "asset_id": row.get("asset_id"),
        "asset_serial": row.get("asset_serial") or "",
        "computer_name": row.get("computer_name") or f"Asset #{row.get('asset_id')}",
        "maintenance_type": row.get("maintenance_type") or "Maintenance",
        "action": row.get("action") or "",
        "replaced_part": row.get("replaced_part") or "",
        "previous_part": row.get("previous_part") or "",
        "new_part": row.get("new_part") or "",
        "previous_part_status": row.get("previous_part_status") or "",
        "new_part_status": row.get("new_part_status") or "",
        "components_headline": headline,
        "components_detail": detail,
        "components_text": combined,
        "technician": row.get("technician") or "System",
        "remarks": row.get("remarks") or "",
        "old_status": old_status,
        "new_status": new_status,
        "asset_status": new_status or old_status or "—",
        "status_transition": status_transition,
        "risk_level": row.get("risk_level") or "",
        "status": REPORT_STATUS_COMPLETED,
        "date_display": performed_at.strftime("%B %d, %Y") if performed_at else "—",
        "time_display": performed_at.strftime("%I:%M %p") if performed_at else "",
        "date_short": performed_at.strftime("%Y-%m-%d") if performed_at else "",
        "date_iso": performed_at.strftime("%Y-%m-%d %H:%M:%S") if performed_at else "",
    }


def _load_report_data(filters, page=None, per_page=None):
    """Load filtered report rows, summary stats, and filter options."""
    conn = get_db_connection()
    try:
        try:
            ensure_maintenance_history_schema(conn)
        except Exception as schema_error:  # noqa: BLE001
            print(f"[maintenance/reports] maintenance schema error: {schema_error}")

        cur = conn.cursor(pymysql.cursors.DictCursor)
        where_sql, params = _build_report_where(filters)

        cur.execute(
            "SELECT COUNT(*) AS total, "
            "COUNT(DISTINCT CONCAT(UPPER(TRIM(mh.asset_type)), ':', mh.asset_id)) AS assets_serviced, "
            "COUNT(DISTINCT COALESCE(NULLIF(TRIM(mh.technician), ''), NULLIF(TRIM(mh.performed_by), ''), 'System')) AS technicians, "
            "SUM(CASE WHEN mh.performed_at >= DATE_FORMAT(CURDATE(), '%%Y-%%m-01') THEN 1 ELSE 0 END) AS this_month, "
            "MAX(mh.performed_at) AS latest_service "
            f"{_REPORT_FROM} {where_sql}",
            params,
        )
        stats_row = cur.fetchone() or {}
        total = int(stats_row.get("total") or 0)
        total_pages = ((total + per_page - 1) // per_page) if per_page else 1

        if page is None:
            cur.execute(
                _REPORT_SELECT + where_sql + " ORDER BY mh.performed_at DESC, mh.id DESC",
                params,
            )
        else:
            if total > 0 and page > total_pages:
                page = total_pages
            offset = (page - 1) * per_page
            cur.execute(
                _REPORT_SELECT
                + where_sql
                + " ORDER BY mh.performed_at DESC, mh.id DESC LIMIT %s OFFSET %s",
                params + [per_page, offset],
            )

        raw_rows = cur.fetchall() or []
        maintenance_types, technicians = _load_filter_options(cur)
    finally:
        conn.close()

    return {
        "rows": [_shape_report_row(row) for row in raw_rows],
        "total": total,
        "stats": {
            "total": total,
            "assets_serviced": int(stats_row.get("assets_serviced") or 0),
            "technicians": int(stats_row.get("technicians") or 0),
            "this_month": int(stats_row.get("this_month") or 0),
            "latest_service": _format_report_datetime(stats_row.get("latest_service")),
        },
        "maintenance_types": maintenance_types,
        "technicians": technicians,
        "page": page or 1,
        "per_page": per_page,
        "total_pages": total_pages,
    }


def _report_filter_summary(filters):
    parts = []
    if filters.get("q"):
        parts.append(f'Search: "{filters["q"]}"')
    if filters.get("asset_type"):
        parts.append(f"Asset Type: {filters['asset_type']}")
    if filters.get("maintenance_type"):
        parts.append(f"Maintenance Type: {filters['maintenance_type']}")
    if filters.get("technician"):
        parts.append(f"Technician: {filters['technician']}")
    if filters.get("date_from") or filters.get("date_to"):
        start = _pretty_report_date(filters["date_from"]) if filters.get("date_from") else "Start"
        end = _pretty_report_date(filters["date_to"]) if filters.get("date_to") else "Today"
        parts.append(f"Date Range: {start} to {end}")
    return " | ".join(parts) if parts else "All maintenance records"


# ---------------------------------------------------------------------------
# Page and print routes
# ---------------------------------------------------------------------------

@maintenance_reports_bp.route("/maintenance/reports")
def maintenance_reports_page():
    login_redirect = _require_login()
    if login_redirect:
        return login_redirect

    filters = _report_filters_from_request(request.args)

    page = request.args.get("page", 1, type=int) or 1
    if page < 1:
        page = 1

    per_page = request.args.get("per_page", 10, type=int) or 10
    if per_page not in REPORT_PER_PAGE_OPTIONS:
        per_page = 10

    data = _load_report_data(filters, page=page, per_page=per_page)
    filter_args = _report_filter_args(filters)
    pagination_query = urlencode({**filter_args, "per_page": per_page})

    return render_template(
        "maintenance_reports.html",
        records=data["rows"],
        stats=data["stats"],
        total_items=data["total"],
        page=data["page"],
        per_page=per_page,
        total_pages=data["total_pages"],
        filters=filters,
        filter_args=filter_args,
        pagination_query=pagination_query,
        maintenance_types=data["maintenance_types"],
        technicians=data["technicians"],
        generated_at=_format_report_datetime(datetime.now()),
        pdf_available=PDF_AVAILABLE,
    )


@maintenance_reports_bp.route("/maintenance/reports/print")
def maintenance_reports_print():
    login_redirect = _require_login()
    if login_redirect:
        return login_redirect

    filters = _report_filters_from_request(request.args)
    data = _load_report_data(filters)

    return render_template(
        "maintenance_reports_print.html",
        records=data["rows"],
        stats=data["stats"],
        total_items=data["total"],
        filters=filters,
        filter_summary=_report_filter_summary(filters),
        generated_at=_format_report_datetime(datetime.now()),
        auto_print=request.args.get("auto_print") == "1",
    )


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

@maintenance_reports_bp.route("/maintenance/reports/export/excel")
def export_maintenance_reports_excel():
    login_redirect = _require_login()
    if login_redirect:
        return login_redirect

    if Workbook is None:
        return "Excel export requires openpyxl. Please install openpyxl to enable this export.", 503

    filters = _report_filters_from_request(request.args)
    data = _load_report_data(filters)
    rows = data["rows"]
    headers = [
        "Report No.",
        "Computer Name",
        "Asset Type",
        "Asset ID",
        "Serial No.",
        "Replaced Component",
        "Previous Part",
        "New Part",
        "Technician",
        "Date",
        "Maintenance Type",
        "Remarks",
        "Status",
        "Asset Status",
    ]
    column_count = len(headers)

    wb = Workbook()
    ws = wb.active
    ws.title = "Maintenance Reports"
    ws.sheet_view.showGridLines = False

    title_font = Font(name="Calibri", bold=True, color="1F4E79", size=16)
    subtitle_font = Font(name="Calibri", bold=False, color="4472C4", size=10)
    meta_font = Font(name="Calibri", color="475569", size=9)
    header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=10)
    data_font = Font(name="Calibri", size=9.5, color="333333")
    status_font = Font(name="Calibri", size=9.5, color="166534", bold=True)

    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    even_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    odd_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
    status_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    thin = Side(style="thin", color="CBD5E1")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    left_top = Alignment(horizontal="left", vertical="top", wrap_text=True)
    center_top = Alignment(horizontal="center", vertical="top", wrap_text=True)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=column_count)
    title_cell = ws.cell(row=1, column=1, value="Norzagaray College — IT Request Management System")
    title_cell.font = title_font
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=column_count)
    subtitle_cell = ws.cell(
        row=2,
        column=1,
        value=f"Maintenance Reports — Exported {datetime.now().strftime('%B %d, %Y %I:%M %p')}",
    )
    subtitle_cell.font = subtitle_font
    subtitle_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 18

    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=column_count)
    filters_cell = ws.cell(row=3, column=1, value=f"Filters: {_report_filter_summary(filters)}")
    filters_cell.font = meta_font
    filters_cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[3].height = 26

    stats = data["stats"]
    ws.merge_cells(start_row=4, start_column=1, end_row=4, end_column=column_count)
    stats_cell = ws.cell(
        row=4,
        column=1,
        value=(
            f"{stats['total']} record(s)  |  {stats['assets_serviced']} asset(s) serviced  |  "
            f"{stats['technicians']} technician(s)  |  {stats['this_month']} this month  |  "
            "Status: Completed"
        ),
    )
    stats_cell.font = meta_font
    stats_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[4].height = 18
    ws.row_dimensions[5].height = 6

    header_row = 6
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
    ws.row_dimensions[header_row].height = 30

    for row_idx, row in enumerate(rows, start=header_row + 1):
        row_fill = even_fill if row_idx % 2 == 0 else odd_fill
        values = [
            row.get("report_no"),
            row.get("computer_name"),
            row.get("asset_type_label"),
            row.get("asset_id"),
            row.get("asset_serial"),
            row.get("components_text"),
            row.get("previous_part"),
            row.get("new_part"),
            row.get("technician"),
            _as_datetime(row.get("date_iso")),
            row.get("maintenance_type"),
            row.get("remarks"),
            row.get("status"),
            row.get("asset_status"),
        ]

        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.font = data_font
            cell.fill = row_fill
            cell.border = border
            cell.alignment = left_top

            if col_idx in {3, 4, 13}:
                cell.alignment = center_top
            if col_idx == 10:
                cell.alignment = center_top
                cell.number_format = "yyyy-mm-dd hh:mm"
            if col_idx == 13:
                cell.fill = status_fill
                cell.font = status_font

        ws.row_dimensions[row_idx].height = 30

    widths = [13, 26, 10, 9, 20, 30, 24, 28, 20, 18, 20, 46, 12, 16]
    for col_idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    last_row = max(header_row, header_row + len(rows))
    ws.freeze_panes = f"A{header_row + 1}"
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(column_count)}{last_row}"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = f"{header_row}:{header_row}"

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"maintenance_reports_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        download_name=filename,
        as_attachment=True,
    )


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------

def _pdf_text(value):
    """Escape text for ReportLab paragraphs using a Latin-1-safe font."""
    text = "" if value is None else str(value)
    replacements = {
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u20b1": "PHP ",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = text.encode("cp1252", errors="replace").decode("cp1252")
    return xml_escape(text).replace("\n", "<br/>")


def _pdf_footer(canvas, doc):
    canvas.saveState()
    page_width, _ = landscape(A4)
    canvas.setStrokeColor(colors.HexColor("#cbd5e1"))
    canvas.setLineWidth(0.4)
    canvas.line(24, 24, page_width - 24, 24)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawString(
        24,
        15,
        "Norzagaray College  |  IT Request Management System  |  Maintenance Reports",
    )
    canvas.drawRightString(page_width - 24, 15, f"Page {doc.page}")
    canvas.restoreState()


@maintenance_reports_bp.route("/maintenance/reports/export/pdf")
def export_maintenance_reports_pdf():
    login_redirect = _require_login()
    if login_redirect:
        return login_redirect

    if not PDF_AVAILABLE:
        return "PDF export requires reportlab. Please install reportlab to enable this export.", 503

    filters = _report_filters_from_request(request.args)
    data = _load_report_data(filters)
    rows = data["rows"]
    stats = data["stats"]
    generated_at = _format_report_datetime(datetime.now())
    filter_summary = _report_filter_summary(filters)

    output = BytesIO()
    page_size = landscape(A4)
    doc = SimpleDocTemplate(
        output,
        pagesize=page_size,
        leftMargin=24,
        rightMargin=24,
        topMargin=26,
        bottomMargin=30,
        title="Maintenance Reports",
        author="Norzagaray College",
        subject="Completed maintenance activities",
    )

    styles = getSampleStyleSheet()
    college_style = ParagraphStyle(
        "MaintenanceCollege",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#1e3a8a"),
        alignment=1,
        spaceAfter=1,
    )
    system_style = ParagraphStyle(
        "MaintenanceSystem",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#475569"),
        alignment=1,
    )
    title_style = ParagraphStyle(
        "MaintenanceReportTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#0f172a"),
        alignment=1,
        spaceBefore=4,
    )
    meta_style = ParagraphStyle(
        "MaintenanceReportMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.8,
        leading=10,
        textColor=colors.HexColor("#475569"),
    )
    cell_style = ParagraphStyle(
        "MaintenanceReportCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.2,
        leading=9.2,
        textColor=colors.HexColor("#1e293b"),
    )
    header_style = ParagraphStyle(
        "MaintenanceReportHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.2,
        leading=9.2,
        textColor=colors.white,
    )
    empty_style = ParagraphStyle(
        "MaintenanceReportEmpty",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#64748b"),
        alignment=1,
        spaceBefore=20,
        spaceAfter=20,
    )
    note_style = ParagraphStyle(
        "MaintenanceReportNote",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7,
        leading=9,
        textColor=colors.HexColor("#64748b"),
    )

    story = [
        Paragraph("NORZAGARAY COLLEGE", college_style),
        Paragraph("IT Request Management System", system_style),
        Paragraph("Maintenance Reports", title_style),
        Spacer(1, 5),
        Paragraph(f"Generated: {_pdf_text(generated_at)}", meta_style),
        Paragraph(f"Filters: {_pdf_text(filter_summary)}", meta_style),
        Paragraph(
            _pdf_text(
                f"{stats['total']} record(s)  |  {stats['assets_serviced']} asset(s) serviced  |  "
                f"{stats['technicians']} technician(s)  |  {stats['this_month']} this month  |  "
                "Status: Completed"
            ),
            meta_style,
        ),
        Spacer(1, 9),
    ]

    if rows:
        table_rows = [
            [
                Paragraph("Computer Information", header_style),
                Paragraph("Replaced Components", header_style),
                Paragraph("Technician", header_style),
                Paragraph("Date", header_style),
                Paragraph("Maintenance Type", header_style),
                Paragraph("Remarks", header_style),
                Paragraph("Status", header_style),
            ]
        ]

        for row in rows:
            serial_line = (
                f"<br/><font size='6.3' color='#64748b'>SN: {_pdf_text(row.get('asset_serial'))}</font>"
                if row.get("asset_serial")
                else ""
            )
            computer_html = (
                f"<b>{_pdf_text(row.get('computer_name'))}</b>"
                f"<br/><font size='6.3' color='#64748b'>{_pdf_text(row.get('asset_type_label'))} "
                f"| Asset #{_pdf_text(row.get('asset_id'))}</font>{serial_line}"
            )

            components_html = f"<b>{_pdf_text(row.get('components_headline'))}</b>"
            if row.get("components_detail"):
                components_html += (
                    f"<br/><font size='6.3' color='#64748b'>"
                    f"{_pdf_text(row.get('components_detail'))}</font>"
                )

            remarks = row.get("remarks") or "—"
            if len(remarks) > 600:
                remarks = remarks[:600] + "..."

            table_rows.append(
                [
                    Paragraph(computer_html, cell_style),
                    Paragraph(components_html, cell_style),
                    Paragraph(_pdf_text(row.get("technician")), cell_style),
                    Paragraph(
                        f"<b>{_pdf_text(row.get('date_display'))}</b>"
                        f"<br/><font size='6.3' color='#64748b'>{_pdf_text(row.get('time_display'))}</font>",
                        cell_style,
                    ),
                    Paragraph(_pdf_text(row.get("maintenance_type")), cell_style),
                    Paragraph(_pdf_text(remarks), cell_style),
                    Paragraph("<font color='#15803d'><b>Completed</b></font>", cell_style),
                ]
            )

        table = LongTable(
            table_rows,
            colWidths=[119, 190, 78, 82, 84, 172, 68],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e40af")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f8fafc")],
                    ),
                    ("BACKGROUND", (6, 1), (6, -1), colors.HexColor("#dcfce7")),
                ]
            )
        )
        story.append(table)
    else:
        story.append(
            Paragraph("No maintenance records match the selected filters.", empty_style)
        )

    story.extend(
        [
            Spacer(1, 9),
            Paragraph(
                "Status: Completed. Every record in this report represents a completed "
                "maintenance activity. Maintenance history is read-only and cannot be "
                "edited or deleted.",
                note_style,
            ),
        ]
    )

    doc.build(story, onFirstPage=_pdf_footer, onLaterPages=_pdf_footer)
    output.seek(0)

    filename = f"maintenance_reports_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    return send_file(
        output,
        mimetype="application/pdf",
        download_name=filename,
        as_attachment=True,
    )
