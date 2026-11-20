// Inventory category page helpers: CSV export, and reload hooks.
(function () {
  'use strict';

  // CRUD modals in the legacy inventory pages refreshed their own AJAX tables.
  // Category pages render one server-side table, so a full reload keeps the
  // user on the current category and immediately reflects the change.
  window.refreshPcTableWithoutReload = function () {
    window.location.reload();
  };
  window.refreshItemTableWithoutReload = function () {
    window.location.reload();
  };
  window.refreshConsumableTableWithoutReload = function () {
    window.location.reload();
  };

  if (typeof window.openMaintenanceLog !== 'function') {
    window.openMaintenanceLog = function (id, type) {
      window.location.href = '/maintenance-history?type=' + encodeURIComponent(type || 'DEVICE') + '&id=' + encodeURIComponent(id);
    };
  }

  function csvCell(value) {
    const text = String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
    return '"' + text.replace(/"/g, '""') + '"';
  }

  function csvRows(rows) {
    return rows.map((row) => row.map(csvCell).join(',')).join('\r\n');
  }

  function tableHeaders(table) {
    return Array.from(table.querySelectorAll('thead th'))
      .filter((th) => !th.classList.contains('no-export'))
      .map((th) => th.textContent.replace(/\s+/g, ' ').trim());
  }

  function tableRowValues(row) {
    return Array.from(row.querySelectorAll('td'))
      .filter((td) => !td.classList.contains('no-export'))
      .map((td) => td.textContent.replace(/\s+/g, ' ').trim());
  }

  window.downloadCategoryCsv = function () {
    const table = document.getElementById('categoryTable');
    if (!table) return;

    const categoryTitle = table.getAttribute('data-category') || 'Inventory';
    const rows = [];

    rows.push([categoryTitle + ' Report']);
    rows.push(['Generated', new Date().toLocaleString()]);
    rows.push([]);

    const summaryRoot = document.getElementById('categoryReportSummary');
    if (summaryRoot) {
      rows.push(['Summary']);
      summaryRoot.querySelectorAll('[data-report-metric]').forEach((metric) => {
        rows.push([metric.getAttribute('data-label') || '', metric.getAttribute('data-value') || '']);
      });
      rows.push([]);
    }

    document.querySelectorAll('#categoryReportBreakdowns .report-breakdown').forEach((section) => {
      const title = section.getAttribute('data-report-title') || 'Breakdown';
      const headers = tableHeaders(section.querySelector('table'));
      rows.push([title]);
      rows.push(headers);
      section.querySelectorAll('tbody tr').forEach((row) => {
        rows.push(tableRowValues(row));
      });
      rows.push([]);
    });

    rows.push(['Current page records']);
    rows.push(tableHeaders(table));
    table.querySelectorAll('tbody tr').forEach((row) => {
      if (row.querySelector('td[colspan]')) return;
      rows.push(tableRowValues(row));
    });

    const blob = new Blob(['\uFEFF' + csvRows(rows)], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const safeName = categoryTitle.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
    link.href = url;
    link.download = (safeName || 'inventory') + '-report-' + new Date().toISOString().slice(0, 10) + '.csv';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);

    if (typeof showToast === 'function') {
      showToast('Category report exported as CSV.', 'success');
    }
  };

  document.addEventListener('DOMContentLoaded', function () {
    const filterForm = document.getElementById('categoryFilterForm');
    if (filterForm) {
      filterForm.querySelectorAll('[data-auto-submit]').forEach((field) => {
        field.addEventListener('change', function () {
          filterForm.submit();
        });
      });
    }

    const perPageForm = document.getElementById('categoryPerPageForm');
    if (perPageForm) {
      const perPageSelect = perPageForm.querySelector('select[name="per_page"]');
      if (perPageSelect) {
        perPageSelect.addEventListener('change', function () {
          perPageForm.submit();
        });
      }
    }
  lucide.createIcons();
});
})();
