// Replace Part workflow (Section 3)
// One part, one available inventory unit, one explicit disposition for the old part.

(function () {
  'use strict';

  const RP_PARTS = [
    { key: 'monitor', label: 'Monitor' },
    { key: 'motherboard', label: 'Motherboard' },
    { key: 'ram', label: 'RAM' },
    { key: 'storage', label: 'Storage' },
    { key: 'gpu', label: 'GPU' },
    { key: 'psu', label: 'PSU' },
    { key: 'casing', label: 'Casing' },
    { key: 'mouse', label: 'Mouse' },
    { key: 'keyboard', label: 'Keyboard' },
    { key: 'other_parts', label: 'Other Parts' }
  ];

  const RP_STATE = {
    pcid: null,
    pc: null,
    part: null,
    available: [],
    filtered: [],
    loading: false
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function partLabel(key) {
    const found = RP_PARTS.find((part) => part.key === key);
    return found ? found.label : 'Part';
  }

  function clearError() {
    const box = byId('replacePartError');
    if (!box) return;
    box.textContent = '';
    box.classList.add('hidden');
  }

  function showError(message) {
    const box = byId('replacePartError');
    if (!box) {
      if (typeof showPopup === 'function') showPopup('error', message);
      return;
    }
    box.textContent = message;
    box.classList.remove('hidden');
  }

  function setSubmitting(isSubmitting) {
    const submitBtn = byId('replaceSubmitBtn');
    const label = byId('replaceSubmitLabel');
    const spinner = byId('replaceSubmitSpinner');
    const partSelect = byId('replacePartSelect');
    const search = byId('replacePartSearch');
    const inventorySelect = byId('replaceInventorySelect');

    if (submitBtn) submitBtn.disabled = isSubmitting;
    if (label) label.textContent = isSubmitting ? 'Replacing…' : 'Confirm Replacement';
    if (spinner) spinner.classList.toggle('hidden', !isSubmitting);
    if (partSelect) partSelect.disabled = isSubmitting;

    const hasOptions = !!(inventorySelect && inventorySelect.value);
    if (search) search.disabled = isSubmitting || RP_STATE.loading || RP_STATE.available.length === 0;
    if (inventorySelect) {
      inventorySelect.disabled = isSubmitting || RP_STATE.loading || !hasOptions;
    }

    document.querySelectorAll('#replaceRemovedStatusGroup input[type="radio"]').forEach((radio) => {
      radio.disabled = isSubmitting || !hasRecordedPart();
    });

    if (typeof window.lucide !== 'undefined' && window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons({ nodes: [byId('replacePartModal')].filter(Boolean) });
    }
  }

  function hasRecordedPart() {
    if (!RP_STATE.pc || !RP_STATE.part) return false;
    const value = RP_STATE.pc[RP_STATE.part];
    return !!(value && String(value).trim());
  }

  function refreshSubmitState() {
    const submitBtn = byId('replaceSubmitBtn');
    if (!submitBtn) return;
    const selected = byId('replaceInventorySelect');
    submitBtn.disabled = RP_STATE.loading || !selected || !selected.value;
  }

  function updateStatusSelection() {
    document.querySelectorAll('.rp-status-option').forEach((option) => {
      const radio = option.querySelector('input[type="radio"]');
      option.classList.toggle('is-selected', !!(radio && radio.checked));
    });
  }

  function setRemovedStatusEnabled(enabled) {
    document.querySelectorAll('#replaceRemovedStatusGroup input[type="radio"]').forEach((radio) => {
      radio.disabled = !enabled;
      if (!enabled && radio.value === 'Available') radio.checked = true;
    });
    updateStatusSelection();
  }

  function updateCurrentPartText() {
    const currentEl = byId('replaceCurrentPart');
    const warning = byId('replacePartWarning');
    if (!currentEl) return;

    const hasCurrent = hasRecordedPart();
    const currentValue = hasCurrent ? String(RP_STATE.pc[RP_STATE.part]).trim() : '';

    currentEl.textContent = hasCurrent
      ? `Currently recorded: ${currentValue}`
      : `No ${partLabel(RP_STATE.part).toLowerCase()} is recorded for this PC.`;
    currentEl.classList.toggle('text-slate-500', hasCurrent);
    currentEl.classList.toggle('text-amber-700', !hasCurrent);

    if (warning) warning.classList.toggle('hidden', hasCurrent);
    setRemovedStatusEnabled(hasCurrent);
  }

  function updateSelectedDetails() {
    const select = byId('replaceInventorySelect');
    const details = byId('replaceSelectedDetails');
    const title = byId('replaceSelectedTitle');
    const meta = byId('replaceSelectedMeta');
    if (!select || !details || !title || !meta) return;

    const selectedId = select.value;
    const item = RP_STATE.filtered.find(
      (entry) => String(entry.accession_id) === String(selectedId)
    );

    if (!item) {
      details.classList.add('hidden');
      return;
    }

    const pieces = [];
    if (item.serial_no) pieces.push(`Serial: ${item.serial_no}`);
    if (item.device_type) pieces.push(`Type: ${item.device_type}`);
    pieces.push(`Item #${item.accession_id}`);

    title.textContent = item.label || `Item #${item.accession_id}`;
    meta.textContent = pieces.join(' · ');
    details.classList.remove('hidden');
  }

  function renderAvailableOptions() {
    const select = byId('replaceInventorySelect');
    const searchInput = byId('replacePartSearch');
    const countEl = byId('replaceAvailableCount');
    const emptyBox = byId('replaceInventoryEmpty');
    if (!select || !countEl) return;

    const needle = (searchInput ? searchInput.value : '').trim().toLowerCase();

    RP_STATE.filtered = RP_STATE.available.filter((item) => {
      if (!needle) return true;
      const haystack = [
        item.label,
        item.item_name,
        item.brand_model,
        item.serial_no,
        item.municipal_serial_no,
        item.device_type,
        item.accession_id
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.indexOf(needle) !== -1;
    });

    select.innerHTML = '';

    if (RP_STATE.filtered.length === 0) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = RP_STATE.available.length
        ? 'No item matches your search'
        : 'No available units';
      select.appendChild(option);
      select.disabled = true;
    } else {
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = 'Select an available unit…';
      select.appendChild(placeholder);

      RP_STATE.filtered.forEach((item) => {
        const option = document.createElement('option');
        option.value = item.accession_id;
        const serial = item.serial_no ? ` · ${item.serial_no}` : '';
        option.textContent = `${item.label || 'Item'}${serial} (#${item.accession_id})`;
        select.appendChild(option);
      });
      select.disabled = false;
    }

    countEl.textContent = `${RP_STATE.available.length} available`;

    if (emptyBox) emptyBox.classList.toggle('hidden', RP_STATE.available.length > 0);
    if (searchInput) searchInput.disabled = RP_STATE.available.length === 0 || RP_STATE.loading;

    updateSelectedDetails();
    refreshSubmitState();
  }

  function setEmptyInventoryLink() {
    const link = byId('replaceInventoryEmptyLink');
    if (!link) return;
    const peripheralParts = ['monitor', 'mouse', 'keyboard'];
    link.href = peripheralParts.indexOf(RP_STATE.part) !== -1
      ? '/inventory/peripherals'
      : '/inventory/components';
  }

  async function loadAvailableParts() {
    if (!RP_STATE.pcid || !RP_STATE.part) return;

    const requestPart = RP_STATE.part;
    RP_STATE.loading = true;
    RP_STATE.available = [];
    RP_STATE.filtered = [];

    const select = byId('replaceInventorySelect');
    const search = byId('replacePartSearch');
    const countEl = byId('replaceAvailableCount');
    const emptyBox = byId('replaceInventoryEmpty');

    if (select) {
      select.innerHTML = '<option value="">Loading available inventory…</option>';
      select.disabled = true;
    }
    if (search) {
      search.value = '';
      search.disabled = true;
    }
    if (countEl) countEl.textContent = 'Loading…';
    if (emptyBox) emptyBox.classList.add('hidden');
    clearError();
    refreshSubmitState();

    try {
      const response = await fetch(
        `/pc-parts/available?pcid=${encodeURIComponent(RP_STATE.pcid)}&part=${encodeURIComponent(requestPart)}`,
        { headers: { Accept: 'application/json' } }
      );
      const data = await response.json().catch(() => ({}));

      if (!response.ok || !data.success) {
        throw new Error(data.error || 'Could not load available inventory.');
      }
      if (requestPart !== RP_STATE.part) return;

      RP_STATE.available = Array.isArray(data.results) ? data.results : [];
      if (RP_STATE.pc && typeof data.current_value === 'string') {
        RP_STATE.pc[requestPart] = data.current_value;
      }
      setEmptyInventoryLink();
      updateCurrentPartText();
      renderAvailableOptions();
    } catch (error) {
      if (select) {
        select.innerHTML = '<option value="">Could not load inventory</option>';
        select.disabled = true;
      }
      if (countEl) countEl.textContent = 'Unavailable';
      showError(error.message || 'Could not load available inventory.');
    } finally {
      RP_STATE.loading = false;
      refreshSubmitState();
    }
  }

  function populatePartSelect() {
    const select = byId('replacePartSelect');
    if (!select) return;
    select.innerHTML = '';

    RP_PARTS.forEach((part) => {
      const option = document.createElement('option');
      option.value = part.key;
      option.textContent = part.label;
      select.appendChild(option);
    });
  }

  function chooseDefaultPart() {
    const select = byId('replacePartSelect');
    if (!select) return;
    if (!RP_STATE.pc) {
      select.value = 'motherboard';
      return;
    }

    const firstWithValue = RP_PARTS.find((part) => {
      const value = RP_STATE.pc[part.key];
      return value && String(value).trim();
    });

    select.value = firstWithValue ? firstWithValue.key : 'motherboard';
  }

  function handlePartChange() {
    const select = byId('replacePartSelect');
    if (!select) return;
    RP_STATE.part = select.value;
    updateCurrentPartText();
    if (!RP_STATE.pc) return;
    loadAvailableParts();
  }

  async function openReplacePartModal(pcid, triggerEl) {
    const modal = byId('replacePartModal');
    const content = byId('replacePartModalContent');
    if (!modal) return;

    const numericId = Number(pcid);
    if (!numericId) {
      if (typeof showPopup === 'function') showPopup('error', 'Open this dialog from a PC row.');
      return;
    }

    RP_STATE.pcid = numericId;
    RP_STATE.pc = null;
    RP_STATE.part = null;
    RP_STATE.available = [];
    RP_STATE.filtered = [];
    RP_STATE.loading = false;

    clearError();
    const hiddenPcId = byId('replacePcId');
    if (hiddenPcId) hiddenPcId.value = String(numericId);

    const nameEl = byId('replacePcName');
    const serialEl = byId('replacePcSerial');
    if (nameEl) nameEl.textContent = 'Loading PC…';
    if (serialEl) serialEl.textContent = '—';

    populatePartSelect();
    const partSelect = byId('replacePartSelect');
    if (partSelect) {
      partSelect.disabled = false;
      partSelect.value = 'motherboard';
      RP_STATE.part = 'motherboard';
    }

    const search = byId('replacePartSearch');
    if (search) {
      search.value = '';
      search.disabled = true;
    }
    const select = byId('replaceInventorySelect');
    if (select) {
      select.innerHTML = '<option value="">Select a component first</option>';
      select.disabled = true;
    }
    const countEl = byId('replaceAvailableCount');
    if (countEl) countEl.textContent = '0 available';
    const details = byId('replaceSelectedDetails');
    if (details) details.classList.add('hidden');
    const emptyBox = byId('replaceInventoryEmpty');
    if (emptyBox) emptyBox.classList.add('hidden');

    const availableRadio = document.querySelector(
      '#replaceRemovedStatusGroup input[name="removed_status"][value="Available"]'
    );
    if (availableRadio) availableRadio.checked = true;
    updateStatusSelection();
    setRemovedStatusEnabled(false);
    refreshSubmitState();

    modal.classList.remove('hidden');
    document.body.classList.add('modal-open-pc');
    if (content) {
      content.classList.remove('scale-100');
      window.setTimeout(() => content.classList.add('scale-100'), 40);
    }
    if (typeof window.lucide !== 'undefined' && window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons();
    }

    try {
      const response = await fetch(`/manage_inventory/get-pc-by-id/${encodeURIComponent(numericId)}`, {
        headers: { Accept: 'application/json' }
      });
      if (!response.ok) throw new Error('Failed to load PC data.');
      const pc = await response.json();
      if (!pc || pc.error) throw new Error(pc.error || 'Failed to load PC data.');

      RP_STATE.pc = pc;
      if (nameEl) nameEl.textContent = pc.pcname || `PC #${numericId}`;
      if (serialEl) serialEl.textContent = pc.serial_no || pc.municipal_serial_no || 'No serial recorded';

      chooseDefaultPart();
      handlePartChange();
    } catch (error) {
      if (nameEl) nameEl.textContent = `PC #${numericId}`;
      showError(error.message || 'Could not load PC data.');
    }
  }

  function closeReplacePartModal() {
    const modal = byId('replacePartModal');
    const content = byId('replacePartModalContent');
    if (content) content.classList.remove('scale-100');
    if (modal) {
      modal.classList.add('hidden');
      document.body.classList.remove('modal-open-pc');
    }
  }

  function openReplacementHistory(pcid) {
    if (!pcid) return;
    window.location.href = `/pc-replacement-history?pcid=${encodeURIComponent(pcid)}`;
  }

  function bindEvents() {
    const form = byId('replacePartForm');
    if (!form) return;

    const partSelect = byId('replacePartSelect');
    if (partSelect) partSelect.addEventListener('change', handlePartChange);

    const search = byId('replacePartSearch');
    if (search) {
      search.addEventListener('input', renderAvailableOptions);
    }

    const inventorySelect = byId('replaceInventorySelect');
    if (inventorySelect) {
      inventorySelect.addEventListener('change', () => {
        updateSelectedDetails();
        refreshSubmitState();
      });
    }

    document.querySelectorAll('#replaceRemovedStatusGroup input[type="radio"]').forEach((radio) => {
      radio.addEventListener('change', updateStatusSelection);
    });

    const modal = byId('replacePartModal');
    if (modal) {
      modal.addEventListener('click', (event) => {
        if (event.target === modal) closeReplacePartModal();
      });
    }

    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      const modalEl = byId('replacePartModal');
      if (modalEl && !modalEl.classList.contains('hidden')) closeReplacePartModal();
    });

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearError();

      const pcid = RP_STATE.pcid || Number((byId('replacePcId') || {}).value);
      const part = (byId('replacePartSelect') || {}).value;
      const replacementId = (byId('replaceInventorySelect') || {}).value;
      const statusRadio = form.querySelector('input[name="removed_status"]:checked');
      const removedStatus = statusRadio ? statusRadio.value : 'Available';

      if (!pcid) {
        showError('Select a PC first.');
        return;
      }
      if (!part) {
        showError('Choose the component you want to replace.');
        return;
      }
      if (!replacementId) {
        showError('Choose an available replacement unit from inventory.');
        return;
      }

      setSubmitting(true);

      try {
        const response = await fetch('/replace-pc-part', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json'
          },
          body: JSON.stringify({
            pcid: pcid,
            part: part,
            replacement_accession_id: Number(replacementId),
            removed_status: removedStatus
          })
        });

        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.success) {
          throw new Error(data.error || 'Failed to replace the part.');
        }

        if (typeof showPopup === 'function') {
          showPopup('success', data.message || 'Part replaced successfully.');
        }
        closeReplacePartModal();

        // Reload so the PC part, inventory status, quantities, and report all
        // reflect the committed transaction immediately.
        window.setTimeout(() => window.location.reload(), 650);
      } catch (error) {
        showError(error.message || 'Something went wrong while replacing the part.');
      } finally {
        setSubmitting(false);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindEvents);
  } else {
    bindEvents();
  }

  // Inline onclick handlers in the PC table need these on the global scope.
  window.openReplacePartModal = openReplacePartModal;
  window.closeReplacePartModal = closeReplacePartModal;
  window.openReplacementHistory = openReplacementHistory;
})();
