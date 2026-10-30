let invoiceRows = [];
let filteredRows = [];
let selectedInvoiceNumber = null;
let currentPage = 1;
const pageSize = 10;
let isPrintInProgress = false;
let pendingNarrationInvoiceNumber = null;

document.addEventListener("DOMContentLoaded", function(){
  bindFilterAutoSearch();
  loadDashboard();
});

function bindFilterAutoSearch(){
  document.getElementById("invoiceSearch").addEventListener("change", searchInvoices);
  document.getElementById("hospitalFilter").addEventListener("change", searchInvoices);
  document.getElementById("fromDate").addEventListener("change", searchInvoices);
  document.getElementById("toDate").addEventListener("change", searchInvoices);
  document.getElementById("printStatusFilter").addEventListener("change", searchInvoices);
}

async function loadDashboard(){
  await loadFilterLovs();
  await searchInvoices();
  await loadHospitalSummary();
}

function clearSelectedInvoice(){
  selectedInvoiceNumber = null;
  updateSelectedInvoicePanelForGroup([]);
  renderInvoiceGrid();
}

function clearFilters(){
  document.getElementById("invoiceSearch").value = "";
  document.getElementById("hospitalFilter").value = "";
  document.getElementById("fromDate").value = "";
  document.getElementById("toDate").value = "";
  document.getElementById("printStatusFilter").value = "ALL";

  document.querySelectorAll(".ip-chip").forEach(btn => btn.classList.remove("active"));
  const allBtn = document.querySelector('.ip-chip[data-status-tab="ALL"]');
  if (allBtn) allBtn.classList.add("active");

  selectedInvoiceNumber = null;
  currentPage = 1;
  updateSelectedInvoicePanelForGroup([]);
  searchInvoices();
  loadHospitalSummary();
}

function setQuickStatus(status, el){
  document.getElementById("printStatusFilter").value = status;
  document.querySelectorAll(".ip-chip").forEach(btn => btn.classList.remove("active"));
  el.classList.add("active");
  currentPage = 1;
  searchInvoices();
}

function getRowsByInvoiceNumber(invoiceNumber){
  return filteredRows.filter(row => String(row.invoice_number || "") === String(invoiceNumber || ""));
}

function getRowsByInvoiceNumberFromAll(invoiceNumber){
  return invoiceRows.filter(row => String(row.invoice_number || "") === String(invoiceNumber || ""));
}

function getUniqueInvoiceGroups(rows){
  const map = new Map();

  (rows || []).forEach(row => {
    const invoiceNumber = String(row.invoice_number || "").trim();
    if (!invoiceNumber) return;

    if (!map.has(invoiceNumber)){
      map.set(invoiceNumber, []);
    }
    map.get(invoiceNumber).push(row);
  });

  return [...map.entries()].map(([invoiceNumber, groupRows]) => ({
    invoice_number: invoiceNumber,
    rows: groupRows
  }));
}

async function loadFilterLovs(){
  const currentInvoiceValue = document.getElementById("invoiceSearch").value || "";
  const currentHospitalValue = document.getElementById("hospitalFilter").value || "";

  try{
    const resp = await fetch("/api/invoices/filter-options");
    if (!resp.ok) throw new Error("LOV API failed");

    const data = await resp.json();
    const invoiceNumbers = Array.isArray(data.invoice_numbers) ? data.invoice_numbers : [];
    const hospitalNames = Array.isArray(data.hospital_names) ? data.hospital_names : [];

    populateLovs(invoiceNumbers, hospitalNames, currentInvoiceValue, currentHospitalValue);
  }catch(e){
    try{
      const resp2 = await fetch("/api/invoices/list");
      if (!resp2.ok) throw new Error("Fallback failed");
      const data2 = await resp2.json();
      const rows = Array.isArray(data2) ? data2 : (data2.items || []);

      const invoiceNumbers = [...new Set(rows.map(x => String(x.invoice_number || "").trim()).filter(Boolean))].sort();
      const hospitalNames = [...new Set(rows.map(x => String(x.hospital_name || "").trim()).filter(Boolean))].sort();

      populateLovs(invoiceNumbers, hospitalNames, currentInvoiceValue, currentHospitalValue);
    }catch(err){
      populateLovs([], [], currentInvoiceValue, currentHospitalValue);
    }
  }
}

async function searchInvoices(){
  const invoiceNumber = document.getElementById("invoiceSearch").value.trim();
  const hospitalName = document.getElementById("hospitalFilter").value.trim();
  const fromDate = document.getElementById("fromDate").value;
  const toDate = document.getElementById("toDate").value;
  const printStatus = document.getElementById("printStatusFilter").value;

  if (fromDate && toDate && fromDate > toDate){
    showToast("From Date cannot be greater than To Date.", "error");
    return;
  }

  const params = new URLSearchParams({
    invoice_number: invoiceNumber,
    hospital_name: hospitalName,
    from_date: fromDate,
    to_date: toDate,
    print_status: printStatus
  });

  try{
    const r = await fetch(`/api/invoices/list?${params.toString()}`);
    const data = await r.json();

    if (!r.ok){
      throw new Error(data.message || "Unable to load invoices.");
    }

    invoiceRows = Array.isArray(data) ? data : (data.items || []);
    filteredRows = invoiceRows;
    currentPage = 1;

    if (selectedInvoiceNumber){
      const matchingRows = getRowsByInvoiceNumber(selectedInvoiceNumber);
      if (!matchingRows.length){
        selectedInvoiceNumber = null;
        updateSelectedInvoicePanelForGroup([]);
      } else {
        updateSelectedInvoicePanelForGroup(matchingRows);
      }
    } else {
      updateSelectedInvoicePanelForGroup([]);
    }

    renderInvoiceGrid();
    updateStats();
    await loadHospitalSummary();
  }catch(e){
    invoiceRows = [];
    filteredRows = [];
    selectedInvoiceNumber = null;
    renderInvoiceGrid();
    updateStats();
    updateSelectedInvoicePanelForGroup([]);
    showToast(e.message || "Unable to load invoices.", "error");
  }
}

function populateLovs(invoiceNumbers, hospitalNames, currentInvoiceValue="", currentHospitalValue=""){
  const invoiceSelect = document.getElementById("invoiceSearch");
  const hospitalSelect = document.getElementById("hospitalFilter");

  invoiceSelect.innerHTML = `<option value="">All Invoice Numbers</option>` +
    invoiceNumbers.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");

  hospitalSelect.innerHTML = `<option value="">All Hospitals</option>` +
    hospitalNames.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join("");

  if (currentInvoiceValue && invoiceNumbers.includes(currentInvoiceValue)){
    invoiceSelect.value = currentInvoiceValue;
  }

  if (currentHospitalValue && hospitalNames.includes(currentHospitalValue)){
    hospitalSelect.value = currentHospitalValue;
  }
}

function isPrintedRow(row){
  return normalizeStatus(row.print_status) === "PRINTED";
}

function openInvoiceWindow(url) {
  if (typeof openManagedWindow === "function") {
    return openManagedWindow(
      url,
      "_blank",
      "width=1200,height=800,scrollbars=yes,resizable=yes"
    );
  }

  return window.open(url, "_blank");
}

function openInvoiceTemplate(invoiceNumber){
  invoiceNumber = String(invoiceNumber || "").trim();

  if (!invoiceNumber){
    showToast("Invoice number not available.", "error");
    return;
  }

  const previewWindow = openInvoiceWindow(`/invoice-print/${encodeURIComponent(invoiceNumber)}`);

  if (!previewWindow){
    showToast("Popup blocked. Please allow popups and try again.", "error");
  }
}

function previewPrintedInvoice(encodedRow){
  try{
    const row = JSON.parse(decodeURIComponent(encodedRow));
    const invoiceNumber = String(row.invoice_number || "").trim();

    if (!invoiceNumber){
      showToast("Invoice number not available.", "error");
      return;
    }

    const previewWindow = openInvoiceWindow(`/invoice-print/${encodeURIComponent(invoiceNumber)}?preview=1`);

    if (!previewWindow){
      showToast("Popup blocked. Please allow popups and try again.", "error");
    }
  }catch(e){
    showToast("Unable to open invoice.", "error");
  }
}

function toggleInvoiceRowFromEncoded(encodedRow){
  try{
    const row = JSON.parse(decodeURIComponent(encodedRow));
    const invoiceNumber = String(row.invoice_number || "");

    if (isPrintedRow(row)){
      showToast("Printed invoice cannot be selected. Click Invoice Number to view it.", "info");
      return;
    }

    if (selectedInvoiceNumber && selectedInvoiceNumber === invoiceNumber){
      clearSelectedInvoice();
      return;
    }

    selectInvoiceRow(row);
  }catch(e){
    showToast("Unable to select invoice row.", "error");
  }
}

function renderInvoiceGrid(){
  const body = document.getElementById("invoiceGridBody");

  if (!filteredRows.length){
    body.innerHTML = `<tr><td colspan="12" class="ip-empty">No records found.</td></tr>`;
    renderPagination();
    updatePaginationInfo();
    return;
  }

  const start = (currentPage - 1) * pageSize;
  const pageRows = filteredRows.slice(start, start + pageSize);

  body.innerHTML = pageRows.map((row, idx) => {
    const encodedRow = encodeURIComponent(JSON.stringify(row));
    const safeInvoiceNumber = escapeJs(row.invoice_number || "");
    const isSelected = selectedInvoiceNumber && selectedInvoiceNumber === String(row.invoice_number || "");
    const inputId = `ipSel_${currentPage}_${idx}`;
    const isPrinted = isPrintedRow(row);

    return `
      <tr class="${isSelected ? "ip-row-selected" : ""}">
        <td>
          <input
            id="${inputId}"
            type="checkbox"
            class="ip-radio ip-invoice-selector"
            ${isSelected ? "checked" : ""}
            ${isPrinted ? "disabled title='Printed invoice cannot be selected'" : ""}
            onclick="toggleInvoiceRowFromEncoded('${encodedRow}')"
          >
        </td>
        <td>
          <a
            href="javascript:void(0)"
            class="ip-link"
            onclick="openInvoiceTemplate('${safeInvoiceNumber}')"
            title="Open invoice template"
          >
            ${escapeHtml(row.invoice_number || "")}
          </a>
        </td>
        <td>${escapeHtml(row.invoice_date || "")}</td>
        <td>${escapeHtml(row.hospital_name || "")}</td>
        <td>${formatAmount(row.netinv_amount)}</td>
        <td>${formatAmount(row.netgst_amount)}</td>
        <td>${formatAmount(row.nettds_amount)}</td>
        <td>${formatAmount(row.totalinv_amount)}</td>
        <td class="ip-narration-cell">${renderNarrationCell(row)}</td>
        <td>${getStatusBadge(row.print_status)}</td>
        <td>${escapeHtml(row.printed_at || "-")}</td>
        <td>${escapeHtml(row.printed_by || "-")}</td>
      </tr>
    `;
  }).join("");

  renderPagination();
  updatePaginationInfo();
}

function selectInvoiceRowFromEncoded(encodedRow){
  try{
    const row = JSON.parse(decodeURIComponent(encodedRow));
    selectInvoiceRow(row);
  }catch(e){
    showToast("Unable to select invoice row.", "error");
  }
}

function renderPagination(){
  const holder = document.getElementById("pagination");
  const totalPages = Math.ceil(filteredRows.length / pageSize);

  if (totalPages <= 1){
    holder.innerHTML = "";
    return;
  }

  let html = '';
  html += `<button class="ip-page-btn" onclick="goToPage(${Math.max(1, currentPage - 1)})">&laquo;</button>`;

  for (let i = 1; i <= totalPages; i++){
    html += `<button class="ip-page-btn ${i === currentPage ? "active" : ""}" onclick="goToPage(${i})">${i}</button>`;
  }

  html += `<button class="ip-page-btn" onclick="goToPage(${Math.min(totalPages, currentPage + 1)})">&raquo;</button>`;
  holder.innerHTML = html;
}

function goToPage(page){
  currentPage = page;
  renderInvoiceGrid();
}

function updatePaginationInfo(){
  const total = filteredRows.length;
  const from = total ? ((currentPage - 1) * pageSize) + 1 : 0;
  const to = Math.min(currentPage * pageSize, total);
  document.getElementById("paginationInfo").textContent = `Showing ${from} to ${to} of ${total} records`;
}

function selectInvoiceRow(row){
  if (isPrintedRow(row)){
    showToast("Printed invoice cannot be selected. Click Invoice Number to view it.", "info");
    return;
  }

  selectedInvoiceNumber = String(row.invoice_number || "");
  const matchingRows = getRowsByInvoiceNumber(selectedInvoiceNumber);
  const count = matchingRows.length;

  document.getElementById("selectionNote").textContent =
    count > 1
      ? `Selected Invoice: ${selectedInvoiceNumber} (${count} rows in same invoice will be printed together)`
      : `Selected Invoice: ${selectedInvoiceNumber}`;

  updateSelectedInvoicePanelForGroup(matchingRows);
  renderInvoiceGrid();

  if (count > 1){
    showToast(`${count} rows under invoice ${selectedInvoiceNumber} have been selected together.`, "info");
  }
}

function updateSelectedInvoicePanelForGroup(rows){
  const container = document.getElementById("selectedInvoiceDetails");

  if (!rows || !rows.length){
    container.innerHTML = `
      <div class="ip-kv"><div class="ip-kv-key">Invoice Number</div><div class="ip-kv-value">-</div></div>
      <div class="ip-kv"><div class="ip-kv-key">Invoice Date</div><div class="ip-kv-value">-</div></div>
      <div class="ip-kv"><div class="ip-kv-key">Hospital Name</div><div class="ip-kv-value">-</div></div>
      <div class="ip-kv"><div class="ip-kv-key">Rows in Invoice</div><div class="ip-kv-value">-</div></div>
      <div class="ip-kv"><div class="ip-kv-key">Net Invoice Amount</div><div class="ip-kv-value">-</div></div>
      <div class="ip-kv"><div class="ip-kv-key">GST Amount</div><div class="ip-kv-value">-</div></div>
      <div class="ip-kv"><div class="ip-kv-key">TDS Amount</div><div class="ip-kv-value">-</div></div>
      <div class="ip-kv"><div class="ip-kv-key">Total Invoice Amount</div><div class="ip-kv-value">-</div></div>
      <div class="ip-kv"><div class="ip-kv-key">Narration</div><div class="ip-kv-value">-</div></div>
      <div class="ip-kv"><div class="ip-kv-key">Print Status</div><div class="ip-kv-value">-</div></div>
      <div class="ip-kv"><div class="ip-kv-key">Printed On</div><div class="ip-kv-value">-</div></div>
      <div class="ip-kv"><div class="ip-kv-key">Printed By</div><div class="ip-kv-value">-</div></div>
    `;

    document.getElementById("selectionNote").textContent = "Select any row to pick the full invoice group.";
    document.getElementById("highlightInvoiceNumber").textContent = "-";
    document.getElementById("highlightHospitalName").textContent = "-";
    document.getElementById("highlightRowCount").textContent = "0";
    document.getElementById("highlightInvoiceTotal").textContent = "0.00";
    document.getElementById("highlightPrintStatus").textContent = "-";
    return;
  }

  const first = rows[0];
  const totalNet = rows.reduce((sum, r) => sum + Number(r.netinv_amount || 0), 0);
  const totalGst = rows.reduce((sum, r) => sum + Number(r.netgst_amount || 0), 0);
  const totalTds = rows.reduce((sum, r) => sum + Number(r.nettds_amount || 0), 0);
  const totalInv = totalNet + (totalGst - totalTds);

  const printedOnValues = [...new Set(rows.map(r => String(r.printed_at || "").trim()).filter(v => v && v !== "-"))];
  const printedByValues = [...new Set(rows.map(r => String(r.printed_by || "").trim()).filter(v => v && v !== "-"))];
  const groupStatus = deriveGroupStatus(rows);

  container.innerHTML = `
    <div class="ip-kv"><div class="ip-kv-key">Invoice Number</div><div class="ip-kv-value">${escapeHtml(first.invoice_number || "-")}</div></div>
    <div class="ip-kv"><div class="ip-kv-key">Invoice Date</div><div class="ip-kv-value">${escapeHtml(first.invoice_date || "-")}</div></div>
    <div class="ip-kv"><div class="ip-kv-key">Hospital Name</div><div class="ip-kv-value">${escapeHtml(first.hospital_name || "-")}</div></div>
    <div class="ip-kv"><div class="ip-kv-key">Rows in Invoice</div><div class="ip-kv-value">${rows.length}</div></div>
    <div class="ip-kv"><div class="ip-kv-key">Net Invoice Amount</div><div class="ip-kv-value">${formatAmount(totalNet)}</div></div>
    <div class="ip-kv"><div class="ip-kv-key">GST Amount</div><div class="ip-kv-value">${formatAmount(totalGst)}</div></div>
    <div class="ip-kv"><div class="ip-kv-key">TDS Amount</div><div class="ip-kv-value">${formatAmount(totalTds)}</div></div>
    <div class="ip-kv"><div class="ip-kv-key">Total Invoice Amount</div><div class="ip-kv-value">${formatAmount(totalInv)}</div></div>
    <div class="ip-kv"><div class="ip-kv-key">Narration</div><div class="ip-kv-value">${escapeHtml(getInvoiceGroupNarration(rows) || "-")}</div></div>
    <div class="ip-kv"><div class="ip-kv-key">Print Status</div><div class="ip-kv-value">${escapeHtml(groupStatus)}</div></div>
    <div class="ip-kv"><div class="ip-kv-key">Printed On</div><div class="ip-kv-value">${escapeHtml(printedOnValues.join(", ") || "-")}</div></div>
    <div class="ip-kv"><div class="ip-kv-key">Printed By</div><div class="ip-kv-value">${escapeHtml(printedByValues.join(", ") || "-")}</div></div>
  `;

  document.getElementById("highlightInvoiceNumber").textContent = first.invoice_number || "-";
  document.getElementById("highlightHospitalName").textContent = first.hospital_name || "-";
  document.getElementById("highlightRowCount").textContent = String(rows.length);
  document.getElementById("highlightInvoiceTotal").textContent = formatAmount(totalInv);
  document.getElementById("highlightPrintStatus").textContent = groupStatus;
}

function deriveGroupStatus(rows){
  if (!rows || !rows.length) return "-";
  const statuses = [...new Set(rows.map(r => normalizeStatus(r.print_status)))];
  if (statuses.length === 1){
    return normalizeStatusLabel(statuses[0]);
  }
  return "MIXED STATUS";
}

function updateStats(){
  const groups = getUniqueInvoiceGroups(invoiceRows);
  const total = groups.length;
  const printed = groups.filter(g => deriveGroupStatus(g.rows) === "PRINTED").length;
  const pending = groups.filter(g => deriveGroupStatus(g.rows) === "YET TO PRINT").length;

  document.getElementById("statTotalInvoices").textContent = total;
  document.getElementById("statPrintedInvoices").textContent = printed;
  document.getElementById("statPendingInvoices").textContent = pending;
}

async function loadHospitalSummary(){
  const params = new URLSearchParams({
    invoice_number: document.getElementById("invoiceSearch").value.trim(),
    hospital_name: document.getElementById("hospitalFilter").value.trim(),
    from_date: document.getElementById("fromDate").value,
    to_date: document.getElementById("toDate").value,
    print_status: document.getElementById("printStatusFilter").value
  });

  try{
    const r = await fetch(`/api/invoices/hospital-print-summary?${params.toString()}`);
    const data = await r.json();
    const rows = Array.isArray(data) ? data : (data.items || []);
    renderHospitalSummary(rows);
  }catch(e){
    renderHospitalSummary([]);
  }
}

function renderHospitalSummary(rows){
  const body = document.getElementById("hospitalSummaryBody");

  if (!rows.length){
    body.innerHTML = `<tr><td colspan="4" class="ip-empty">No summary available.</td></tr>`;
    return;
  }

  body.innerHTML = rows.map(row => `
    <tr>
      <td>${escapeHtml(row.hospital_name || "")}</td>
      <td>${row.pending_count ?? 0}</td>
      <td>${row.printed_count ?? 0}</td>
      <td>${row.total_count ?? ((row.pending_count ?? 0) + (row.printed_count ?? 0))}</td>
    </tr>
  `).join("");
}

function getPrintButton(){
  return document.querySelector('button[onclick="printSelectedInvoice()"]');
}

function setPrintButtonState(disabled){
  const btn = getPrintButton();
  if (!btn) return;
  btn.disabled = !!disabled;
  btn.style.opacity = disabled ? "0.65" : "1";
  btn.style.cursor = disabled ? "not-allowed" : "pointer";
  btn.textContent = disabled ? "Generating PDF..." : "Print Selected Invoice";
}

function applyPrintedStatusLocally(invoiceNumber, printedBy = "SYSTEM"){
  const now = new Date();
  const dd = String(now.getDate()).padStart(2, "0");
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const yyyy = now.getFullYear();
  let hours = now.getHours();
  const minutes = String(now.getMinutes()).padStart(2, "0");
  const ampm = hours >= 12 ? "PM" : "AM";
  hours = hours % 12;
  hours = hours ? hours : 12;
  const hh = String(hours).padStart(2, "0");
  const printedAtDisplay = `${dd}-${mm}-${yyyy} ${hh}:${minutes} ${ampm}`;

  invoiceRows = (invoiceRows || []).map(row => {
    if (String(row.invoice_number || "") === String(invoiceNumber || "")) {
      return {
        ...row,
        print_status: "PRINTED",
        printed_at: printedAtDisplay,
        printed_by: printedBy || "SYSTEM"
      };
    }
    return row;
  });

  filteredRows = (filteredRows || []).map(row => {
    if (String(row.invoice_number || "") === String(invoiceNumber || "")) {
      return {
        ...row,
        print_status: "PRINTED",
        printed_at: printedAtDisplay,
        printed_by: printedBy || "SYSTEM"
      };
    }
    return row;
  });
}

function removeInvoiceGroupLocally(invoiceNumber){
  invoiceRows = (invoiceRows || []).filter(
    row => String(row.invoice_number || "") !== String(invoiceNumber || "")
  );
  filteredRows = (filteredRows || []).filter(
    row => String(row.invoice_number || "") !== String(invoiceNumber || "")
  );
}

function syncSelectionAfterLocalPrint(invoiceNumber){
  const activeFilter = String(document.getElementById("printStatusFilter").value || "ALL").toUpperCase();

  if (activeFilter === "PENDING"){
    selectedInvoiceNumber = null;
    updateSelectedInvoicePanelForGroup([]);
    document.getElementById("selectionNote").textContent =
      `Invoice ${invoiceNumber} marked as PRINTED and removed from Yet to Print list.`;
    return;
  }

  const localRows = getRowsByInvoiceNumber(invoiceNumber);
  if (localRows.length){
    updateSelectedInvoicePanelForGroup(localRows);
    document.getElementById("selectionNote").textContent =
      `Selected Invoice: ${invoiceNumber} (${localRows.length} row(s))`;
  } else {
    selectedInvoiceNumber = null;
    updateSelectedInvoicePanelForGroup([]);
  }
}

async function refreshInvoiceScreenAfterPrint(invoiceNumber){
  try{
    await searchInvoices();
    await loadHospitalSummary();
    await loadFilterLovs();

    if (invoiceNumber){
      const refreshedRows = getRowsByInvoiceNumber(invoiceNumber);
      if (refreshedRows.length){
        updateSelectedInvoicePanelForGroup(refreshedRows);
      } else {
        selectedInvoiceNumber = null;
        updateSelectedInvoicePanelForGroup([]);
      }
    } else {
      updateSelectedInvoicePanelForGroup([]);
    }

    renderInvoiceGrid();
    updateStats();
  }catch(e){
    console.error("Refresh after print failed:", e);
  }
}

function isTruthyFlag(value){
  if (value === true || value === 1) return true;
  const v = String(value ?? "").trim().toUpperCase();
  return v === "Y" || v === "YES" || v === "TRUE" || v === "1" || v === "ON" || v === "T";
}

function isNarrationEnabledForInvoice(rows){
  return (rows || []).some(row => {
    return isTruthyFlag(row.narration_enabled) ||
      isTruthyFlag(row.template_narration_enabled) ||
      isTruthyFlag(row.narration_required) ||
      isTruthyFlag(row.customer_narration) ||
      isTruthyFlag(row.template_customer_narration) ||
      isTruthyFlag(row.custom_narration_enabled) ||
      isTruthyFlag(row.template_custom_narration_enabled) ||
      isTruthyFlag(row.is_customer_narration_enabled) ||
      isTruthyFlag(row.is_custom_narration_enabled);
  });
}

function getInvoiceGroupNarration(rows){
  const values = [...new Set((rows || []).map(r => {
    return String(
      r.custom_narration_text ??
      r.invoice_narration ??
      r.print_narration ??
      r.narration_text ??
      r.custom_narration ??
      ""
    ).trim();
  }).filter(Boolean))];
  return values.join("\n");
}

function renderNarrationCell(row){
  const enabled = isNarrationEnabledForInvoice([row]);
  const narration = String(row.custom_narration || "").trim();

  if (!enabled){
    return `<span class="ip-narration-readonly">Not Required</span>`;
  }

  if (narration){
    return `<span class="ip-narration-readonly">${escapeHtml(narration)}</span>`;
  }

  return `<span class="ip-narration-needed">Required at Print</span>`;
}

async function fetchTemplateNarrationStatus(invoiceNumber){
  try{
    const resp = await fetch(`/api/invoices/template-narration-status?invoice_number=${encodeURIComponent(invoiceNumber)}`);
    if (!resp.ok) return null;
    const data = await resp.json();

    return {
      ...data,
      enabled: isTruthyFlag(data.enabled) ||
        isTruthyFlag(data.narration_enabled) ||
        isTruthyFlag(data.template_narration_enabled) ||
        isTruthyFlag(data.narration_required) ||
        isTruthyFlag(data.customer_narration) ||
        isTruthyFlag(data.template_customer_narration) ||
        isTruthyFlag(data.custom_narration_enabled) ||
        isTruthyFlag(data.template_custom_narration_enabled) ||
        isTruthyFlag(data.is_customer_narration_enabled) ||
        isTruthyFlag(data.is_custom_narration_enabled),
      custom_narration: String(
        data.custom_narration_text ??
        data.invoice_narration ??
        data.print_narration ??
        data.narration_text ??
        data.custom_narration ??
        ""
      ).trim()
    };
  }catch(e){
    return null;
  }
}

function openNarrationModal(invoiceNumber, existingNarration=""){
  pendingNarrationInvoiceNumber = invoiceNumber;
  document.getElementById("customNarrationInput").value = existingNarration || "";
  document.getElementById("narrationModalBackdrop").classList.add("is-open");
  document.getElementById("narrationModal").classList.add("is-open");
  document.getElementById("narrationModal").setAttribute("aria-hidden", "false");
  setTimeout(() => document.getElementById("customNarrationInput").focus(), 50);
}

function closeNarrationModal(){
  pendingNarrationInvoiceNumber = null;
  document.getElementById("narrationModalBackdrop").classList.remove("is-open");
  document.getElementById("narrationModal").classList.remove("is-open");
  document.getElementById("narrationModal").setAttribute("aria-hidden", "true");
}

async function saveInvoiceNarration(invoiceNumber, narration){
  const resp = await fetch("/api/invoices/save-custom-narration", {
    method:"POST",
    headers:{ "Content-Type":"application/json" },
    body:JSON.stringify({ invoice_number: invoiceNumber, custom_narration: narration })
  });

  const data = await resp.json().catch(() => ({}));
  if (!resp.ok || data.success === false){
    throw new Error(data.message || "Unable to save narration.");
  }

  invoiceRows = (invoiceRows || []).map(row => String(row.invoice_number || "") === String(invoiceNumber || "") ? { ...row, custom_narration: narration, custom_narration_text: narration } : row);
  filteredRows = (filteredRows || []).map(row => String(row.invoice_number || "") === String(invoiceNumber || "") ? { ...row, custom_narration: narration, custom_narration_text: narration } : row);

  return data;
}

async function confirmNarrationAndPrint(){
  const invoiceNumber = pendingNarrationInvoiceNumber;
  const narration = String(document.getElementById("customNarrationInput").value || "").trim();

  if (!invoiceNumber){
    showToast("Invoice number not available for narration.", "error");
    return;
  }

  if (!narration){
    showToast("Please write narration before printing this invoice.", "error");
    return;
  }

  try{
    await saveInvoiceNarration(invoiceNumber, narration);
    closeNarrationModal();
    continuePrintSelectedInvoice();
  }catch(e){
    showToast(e.message || "Unable to save narration.", "error");
  }
}

async function ensureNarrationBeforePrint(invoiceNumber, matchingRows){
  let narrationEnabled = isNarrationEnabledForInvoice(matchingRows);
  let existingNarration = getInvoiceGroupNarration(matchingRows);

  if (!narrationEnabled){
    const serverStatus = await fetchTemplateNarrationStatus(invoiceNumber);
    if (serverStatus){
      narrationEnabled = !!serverStatus.enabled;
      existingNarration = String(serverStatus.custom_narration || existingNarration || "").trim();
    }
  }

  if (narrationEnabled && !existingNarration){
    openNarrationModal(invoiceNumber, "");
    return false;
  }

  return true;
}

async function printSelectedInvoice(){
  if (isPrintInProgress){
    return;
  }

  if (!selectedInvoiceNumber){
    showToast("Please select an invoice first.", "info");
    return;
  }

  const matchingRows = getRowsByInvoiceNumber(selectedInvoiceNumber);

  if (!matchingRows.length){
    showToast("No rows found for selected invoice.", "error");
    return;
  }

  const statusFilter = document.getElementById("printStatusFilter").value;
  const groupedStatus = deriveGroupStatus(matchingRows);

  if (statusFilter === "PENDING" && groupedStatus !== "YET TO PRINT" && groupedStatus !== "MIXED STATUS"){
    showToast("Selected invoice is not in Yet to Print status.", "error");
    return;
  }

  const invoiceNumber = selectedInvoiceNumber;

  const narrationOk = await ensureNarrationBeforePrint(invoiceNumber, matchingRows);
  if (!narrationOk){
    return;
  }

  continuePrintSelectedInvoice();
}

async function continuePrintSelectedInvoice(){
  if (isPrintInProgress){
    return;
  }

  const invoiceNumber = String(selectedInvoiceNumber || "").trim();
  const matchingRows = getRowsByInvoiceNumber(invoiceNumber);
  const statusFilter = document.getElementById("printStatusFilter").value;

  if (!invoiceNumber){
    showToast("Invoice number not available.", "error");
    return;
  }

  isPrintInProgress = true;
  setPrintButtonState(true);

  let saveHandle = null;

  try {
    const safeInvoiceNumber = invoiceNumber.replace(/[\\/:*?"<>|]/g, "_");
    const suggestedFileName = `${safeInvoiceNumber}.pdf`;

    /*
     * Preferred method:
     * Open a controlled PDF Save As dialog.
     */
    if (typeof window.showSaveFilePicker === "function"){
      try {
        saveHandle = await window.showSaveFilePicker({
          suggestedName: suggestedFileName,
          startIn: "downloads",
          types: [
            {
              description: "PDF Document",
              accept: {
                "application/pdf": [".pdf"]
              }
            }
          ],
          excludeAcceptAllOption: true
        });
      } catch (pickerError) {
        if (pickerError && pickerError.name === "AbortError"){
          showToast("PDF save cancelled.", "info");
          return;
        }

        console.warn(
          "Controlled PDF Save As unavailable. Falling back to browser download.",
          pickerError
        );

        saveHandle = null;
      }
    }

    const pdfUrl = `/invoice-print-pdf/${encodeURIComponent(invoiceNumber)}`;

    const response = await fetch(pdfUrl, {
      method: "GET",
      credentials: "same-origin"
    });

    if (!response.ok){
      throw new Error("Unable to generate invoice PDF.");
    }

    const contentType = String(
      response.headers.get("Content-Type") || ""
    ).toLowerCase();

    if (!contentType.includes("application/pdf")){
      throw new Error("Server did not return a valid PDF file.");
    }

    const blob = await response.blob();

    /*
     * If the controlled Save As picker succeeded,
     * write the PDF directly to the selected file.
     */
    if (saveHandle){
      const writable = await saveHandle.createWritable();

      await writable.write(blob);
      await writable.close();

      showToast(
        `PDF saved successfully as ${saveHandle.name}`,
        "success"
      );
    } else {
      /*
       * Fallback for browsers where showSaveFilePicker
       * is unavailable or blocked.
       */
      const blobUrl = window.URL.createObjectURL(blob);

      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = suggestedFileName;
      link.style.display = "none";

      document.body.appendChild(link);
      link.click();
      link.remove();

      setTimeout(() => {
        window.URL.revokeObjectURL(blobUrl);
      }, 2000);

      showToast(
        "PDF downloaded successfully.",
        "success"
      );
    }

    applyPrintedStatusLocally(invoiceNumber, "SYSTEM");

    if (String(statusFilter || "").toUpperCase() === "PENDING"){
      removeInvoiceGroupLocally(invoiceNumber);
    }

    syncSelectionAfterLocalPrint(invoiceNumber);
    renderInvoiceGrid();
    updateStats();

    setTimeout(async () => {
      await refreshInvoiceScreenAfterPrint(invoiceNumber);
    }, 1200);

  } catch (error) {
    console.error("Invoice PDF save failed:", error);

    showToast(
      error.message || "Unable to save invoice PDF.",
      "error"
    );

  } finally {
    setTimeout(() => {
      isPrintInProgress = false;
      setPrintButtonState(false);
    }, 1200);
  }
}
function normalizeStatus(status){
  const s = String(status || "").trim().toUpperCase();
  if (s === "PRINTED") return "PRINTED";
  if (s === "PENDING" || s === "YET TO PRINT" || s === "") return "PENDING";
  return s;
}

function normalizeStatusLabel(status){
  const s = normalizeStatus(status);
  if (s === "PRINTED") return "PRINTED";
  if (s === "PENDING") return "YET TO PRINT";
  return s || "N/A";
}

function getStatusBadge(status){
  const s = normalizeStatus(status);
  if (s === "PRINTED"){
    return `<span class="ip-badge ip-badge-printed">PRINTED</span>`;
  }
  if (s === "PENDING"){
    return `<span class="ip-badge ip-badge-pending">YET TO PRINT</span>`;
  }
  return `<span class="ip-badge ip-badge-all">${escapeHtml(status || "N/A")}</span>`;
}

function formatAmount(val){
  const num = Number(val || 0);
  return num.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
}

function escapeHtml(value){
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeJs(value){
  return String(value ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/'/g, "\\'")
    .replace(/"/g, '\\"')
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r");
}

function showToast(message, type="info"){
  const wrap = document.getElementById("toastWrap");
  const toast = document.createElement("div");
  toast.className = `ip-toast ip-toast-${type}`;
  toast.textContent = message;
  wrap.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(-8px)";
    toast.style.transition = ".2s ease";
    setTimeout(() => toast.remove(), 220);
  }, 2600);
}
