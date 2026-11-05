/* Manage Device page interactions */
(function () {
  "use strict";

  const URLs = window.MD_URLS || {};

  function qs(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  /* ------------------------- Tabs ------------------------- */
  window.mdSwitchTab = function (tabName) {
    const inventory = qs("mdInventorySection");
    const report = qs("mdReportSection");
    const inventoryBtn = qs("mdTabInventoryBtn");
    const reportBtn = qs("mdTabReportBtn");
    if (!inventory || !report) return;

    const showInventory = tabName !== "report";
    inventory.classList.toggle("hidden", !showInventory);
    report.classList.toggle("hidden", showInventory);

    const activeClasses = ["md-tab-active"];
    const idleClasses = ["border", "border-slate-200", "bg-white", "text-slate-600", "hover:bg-slate-50"];

    if (inventoryBtn) {
      inventoryBtn.classList.toggle("md-tab-active", showInventory);
      idleClasses.forEach((c) => inventoryBtn.classList.toggle(c, !showInventory));
    }
    if (reportBtn) {
      reportBtn.classList.toggle("md-tab-active", !showInventory);
      idleClasses.forEach((c) => reportBtn.classList.toggle(c, showInventory));
    }

    const url = new URL(window.location.href);
    url.searchParams.set("tab", showInventory ? "inventory" : "report");
    window.history.replaceState({}, "", url.toString());
    refreshIcons();
  };

  /* --------------------- Filter panel --------------------- */
  window.mdToggleFilter = function () {
    const panel = qs("mdFilterPanel");
    if (panel) panel.classList.toggle("hidden");
  };

  /* --------------------- Quick search --------------------- */
  window.mdApplyQuickSearch = function () {
    const input = qs("mdQuickSearch");
    const tbody = qs("mdTableBody");
    if (!input || !tbody) return;
    const term = (input.value || "").trim().toLowerCase();

    Array.from(tbody.querySelectorAll("tr")).forEach((row) => {
      if (!term) {
        row.hidden = false;
        return;
      }
      row.hidden = !(row.textContent || "").toLowerCase().includes(term);
    });
  };

  window.mdClearQuickSearch = function () {
    const input = qs("mdQuickSearch");
    if (input) {
      input.value = "";
      window.mdApplyQuickSearch();
    }
  };

  /* --------------------- Selection --------------------- */
  window.mdToggleAll = function (master) {
    document.querySelectorAll(".md-row-checkbox").forEach((box) => {
      box.checked = master.checked;
    });
  };

  window.mdSyncSelectAll = function () {
    const boxes = Array.from(document.querySelectorAll(".md-row-checkbox"));
    const master = qs("mdSelectAll");
    if (!master || !boxes.length) return;
    master.checked = boxes.every((box) => box.checked);
    master.indeterminate = boxes.some((box) => box.checked) && !master.checked;
  };

  function selectedIds() {
    return Array.from(document.querySelectorAll(".md-row-checkbox:checked")).map(
      (box) => box.value
    );
  }

  window.mdSubmitBulk = function (actionUrl) {
    const ids = selectedIds();
    if (!ids.length) {
      alert("Select at least one device first.");
      return;
    }
    const form = qs("mdBulkForm");
    if (!form) return;
    form.action = actionUrl;
    form.submit();
  };

  /* --------------------- Add / Edit modal --------------------- */
  function resetForm() {
    const form = qs("mdDeviceForm");
    if (!form) return;
    form.reset();
    qs("md_accession_id").value = "";
    qs("md_interval_years").value = "1";
    qs("md_device_type").value = "Device";
    qs("md_status").value = "Available";
    qs("md_usage_scope").value = "UNASSIGNED";
    window.mdOnScopeChange();
    const errorBox = qs("mdFormError");
    if (errorBox) {
      errorBox.textContent = "";
      errorBox.classList.add("hidden");
    }
  }

  window.mdOnScopeChange = function () {
    const scope = qs("md_usage_scope");
    const wrap = qs("mdPcSelectWrap");
    if (!scope || !wrap) return;
    wrap.classList.toggle("show", scope.value === "PC");
  };

  window.mdOpenAddModal = function () {
    resetForm();
    qs("mdModalTitle").innerHTML =
      '<i data-lucide="plus-circle" class="h-5 w-5"></i> Add Device';
    qs("mdDeviceForm").action = URLs.add;
    qs("mdSubmitBtn").innerHTML =
      '<i data-lucide="save" class="h-4 w-4"></i> Add Device';
    qs("mdDeviceModal").classList.remove("hidden");
    refreshIcons();
    qs("md_item_name").focus();
  };

  window.mdOpenEditModal = async function (deviceId) {
    try {
      const response = await fetch(URLs.get + "/" + deviceId, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("Unable to load device");
      const data = await response.json();

      resetForm();
      qs("md_accession_id").value = data.accession_id || "";
      qs("md_item_name").value = data.item_name || "";
      qs("md_brand_model").value = data.brand_model || "";
      qs("md_serial_no").value = data.serial_no || "";
      qs("md_municipal_serial_no").value = data.municipal_serial_no || "";
      qs("md_device_type").value = data.device_type || "Device";
      qs("md_acquisition_cost").value =
        data.acquisition_cost === null || data.acquisition_cost === undefined
          ? ""
          : data.acquisition_cost;
      qs("md_date_acquired").value = data.date_acquired || "";
      qs("md_accountable").value = data.accountable || "";
      qs("md_department_id").value = data.department_id || "";
      qs("md_status").value = data.status || "Available";
      qs("md_interval_years").value = data.interval_years || "1";
      qs("md_usage_scope").value = data.usage_scope || "UNASSIGNED";
      qs("md_used_on_pcid").value = data.used_on_pcid || "";
      window.mdOnScopeChange();

      qs("mdModalTitle").innerHTML =
        '<i data-lucide="pencil" class="h-5 w-5"></i> Edit Device';
      qs("mdDeviceForm").action = URLs.update;
      qs("mdSubmitBtn").innerHTML =
        '<i data-lucide="save" class="h-4 w-4"></i> Update Device';
      qs("mdDeviceModal").classList.remove("hidden");
      refreshIcons();
    } catch (error) {
      alert("Could not load device details. Please try again.");
      console.error(error);
    }
  };

  window.mdCloseModal = function () {
    const modal = qs("mdDeviceModal");
    if (modal) modal.classList.add("hidden");
  };

  /* --------------------- Status check --------------------- */
  window.mdMarkChecked = function (deviceId) {
    if (!window.MD_CAN_EDIT) return;
    if (!confirm("Mark this device as checked today and recalculate its risk level?")) return;
    const form = document.createElement("form");
    form.method = "post";
    form.action = URLs.check + "/" + deviceId;
    document.body.appendChild(form);
    form.submit();
  };

  /* --------------------- Archive --------------------- */
  window.mdConfirmArchive = function (deviceId, name) {
    qs("mdArchiveName").textContent = name || "this device";
    qs("mdArchiveForm").action = URLs.archive + "/" + deviceId;
    qs("mdArchiveModal").classList.remove("hidden");
    refreshIcons();
  };

  window.mdCloseArchive = function () {
    qs("mdArchiveModal").classList.add("hidden");
  };

  /* --------------------- History --------------------- */
  window.mdOpenHistory = async function (deviceId, name) {
    const title = qs("mdHistoryTitle");
    const body = qs("mdHistoryBody");
    if (title) title.textContent = name || ("#" + deviceId);
    if (body) body.innerHTML = '<p class="text-slate-500">Loading history…</p>';
    qs("mdHistoryModal").classList.remove("hidden");

    try {
      const response = await fetch(URLs.history + "/" + deviceId, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("Unable to load history");
      const data = await response.json();
      const logs = data.logs || [];

      if (!logs.length) {
        body.innerHTML =
          '<div class="rounded-xl border border-slate-200 bg-slate-50 p-6 text-center text-slate-500">No change history recorded for this device yet.</div>';
        return;
      }

      const rows = logs
        .map(
          (log) => `
          <tr class="align-top border-b border-slate-100">
            <td class="px-3 py-2 whitespace-nowrap text-slate-500">${escapeHtml(log.changed_at)}</td>
            <td class="px-3 py-2"><span class="rounded-full bg-indigo-100 px-2 py-0.5 text-[11px] font-bold text-indigo-700">${escapeHtml(log.action)}</span></td>
            <td class="px-3 py-2 text-slate-600">${escapeHtml(log.field_name || "—")}</td>
            <td class="px-3 py-2 text-slate-500">${escapeHtml(log.old_value || "—")}</td>
            <td class="px-3 py-2 font-medium text-slate-700">${escapeHtml(log.new_value || "—")}</td>
            <td class="px-3 py-2 text-slate-500">${escapeHtml(log.remarks || "—")}</td>
            <td class="px-3 py-2 text-slate-700">${escapeHtml(log.changed_by_name || "—")}</td>
          </tr>`
        )
        .join("");

      body.innerHTML = `
        <div class="overflow-auto rounded-xl border border-slate-200">
          <table class="min-w-[900px] w-full text-sm">
            <thead class="bg-slate-100 text-left text-[11px] uppercase tracking-wide text-slate-600">
              <tr>
                <th class="px-3 py-2">Date / Time</th>
                <th class="px-3 py-2">Action</th>
                <th class="px-3 py-2">Field</th>
                <th class="px-3 py-2">Old Value</th>
                <th class="px-3 py-2">New Value</th>
                <th class="px-3 py-2">Remarks</th>
                <th class="px-3 py-2">Changed By</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
    } catch (error) {
      if (body) {
        body.innerHTML =
          '<div class="rounded-xl border border-red-200 bg-red-50 p-4 text-red-700">Could not load device history.</div>';
      }
      console.error(error);
    }
  };

  window.mdCloseHistory = function () {
    qs("mdHistoryModal").classList.add("hidden");
  };

  /* --------------------- Export --------------------- */
  window.mdExportSelected = async function () {
    const ids = selectedIds();
    if (!ids.length) {
      alert("Select at least one device to export.");
      return;
    }

    try {
      const response = await fetch(URLs.export, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_ids: ids }),
      });

      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || "Export failed");
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "manage_device_export.xlsx";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (error) {
      alert(error.message || "Export failed.");
      console.error(error);
    }
  };

  /* --------------------- Import --------------------- */
  window.mdImportDevices = async function () {
    const fileInput = qs("mdImportFile");
    const status = qs("mdImportStatus");
    if (!fileInput || !fileInput.files.length) {
      alert("Choose an Excel file first.");
      return;
    }

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);
    formData.append("duplicate_option", qs("mdImportDuplicate").value);

    if (status) {
      status.textContent = "Importing…";
      status.className = "text-xs font-medium text-slate-600";
    }

    try {
      const response = await fetch(URLs.import, { method: "POST", body: formData });
      const result = await response.json();
      if (!result.success) throw new Error(result.error || "Import failed");

      if (status) {
        status.textContent = `Added ${result.added}, updated ${result.updated}, skipped ${result.skipped}.`;
        status.className = "text-xs font-medium text-green-700";
      }
      setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      if (status) {
        status.textContent = error.message || "Import failed.";
        status.className = "text-xs font-medium text-red-700";
      }
      console.error(error);
    }
  };

  /* --------------------- Init --------------------- */
  document.addEventListener("DOMContentLoaded", function () {
    window.mdSwitchTab(window.MD_ACTIVE_TAB === "report" ? "report" : "inventory");
    refreshIcons();

    const modal = qs("mdDeviceModal");
    if (modal) {
      modal.addEventListener("click", function (event) {
        if (event.target === modal) window.mdCloseModal();
      });
    }

    const archiveModal = qs("mdArchiveModal");
    if (archiveModal) {
      archiveModal.addEventListener("click", function (event) {
        if (event.target === archiveModal) window.mdCloseArchive();
      });
    }

    const historyModal = qs("mdHistoryModal");
    if (historyModal) {
      historyModal.addEventListener("click", function (event) {
        if (event.target === historyModal) window.mdCloseHistory();
      });
    }
  });
})();
