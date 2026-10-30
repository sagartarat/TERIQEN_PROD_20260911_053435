let agentInvoiceRows = [];
let selectedInvoiceRow = null;

document.addEventListener("DOMContentLoaded", function(){
  setTodayDate();
  loadAgents();
  loadInvoiceLov();
  loadAgentInvoices();

  const tableSearchInput = document.getElementById("tableSearchInput");
  if (tableSearchInput) tableSearchInput.addEventListener("input", renderAgentInvoices);

  ["filterAgent", "filterInvoiceNo", "filterFromDate", "filterToDate", "filterStatus"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", loadAgentInvoices);
  });

  const filterLike = document.getElementById("filterLike");
  if (filterLike) {
    filterLike.addEventListener("keydown", function(e){ if (e.key === "Enter") loadAgentInvoices(); });
    filterLike.addEventListener("blur", loadAgentInvoices);
  }
});

function setTodayDate(){
  const today = new Date().toISOString().split("T")[0];
  const paymentDate = document.getElementById("paymentDate");
  if (paymentDate) paymentDate.value = today;
}

function escapeHtml(value){
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function escapeJs(value){ return String(value ?? "").replace(/\\/g, "\\\\").replace(/'/g, "\\'"); }

function statusBadge(status){
  const s = (status || "PENDING").toUpperCase();
  if (s === "PAID") return `<span class="ap-badge ap-badge-success">PAID</span>`;
  if (s === "PARTIAL") return `<span class="ap-badge ap-badge-warning">PARTIAL</span>`;
  if (s === "HOLD") return `<span class="ap-badge ap-badge-danger">HOLD</span>`;
  return `<span class="ap-badge ap-badge-info">PENDING</span>`;
}

function getInvoiceNo(row){
  return row.invoice_no || row.invoice_number || "";
}

function getAgentCode(row){
  return row.agent_code || row.agent_shrtnm || "";
}

function getInvoiceAgentKey(row){
  return `${getInvoiceNo(row).trim().toUpperCase()}||${getAgentCode(row).trim().toUpperCase()}`;
}

function displayUtrReference(row){
  return row.utr_reference || row.bank_reference || row.utrref_no || "";
}

async function loadAgents(){
  try{
    const res = await fetch("/api/agent-payment/agents");
    const data = await res.json();
    const ddl = document.getElementById("filterAgent");
    if (!ddl) return;
    ddl.innerHTML = `<option value="">All Agents</option>`;
    if (data.success && Array.isArray(data.agents)) {
      data.agents.forEach(agent => {
        const opt = document.createElement("option");
        opt.value = agent.agent_code || agent.short_name || agent.agent_name || "";
        opt.textContent = agent.agent_name || agent.short_name || agent.agent_code || "";
        ddl.appendChild(opt);
      });
    }
  } catch(err) { console.warn("Unable to load agents", err); }
}

async function loadInvoiceLov(){
  try{
    const res = await fetch("/api/agent-payment/invoice-lov");
    const data = await res.json();
    const ddl = document.getElementById("filterInvoiceNo");
    if (!ddl) return;
    ddl.innerHTML = `<option value="">All Invoices</option>`;
    if (data.success && Array.isArray(data.invoices)) {
      data.invoices.forEach(row => {
        const invoiceNo = row.invoice_number || "";
        if (!invoiceNo) return;
        const opt = document.createElement("option");
        opt.value = invoiceNo;
        opt.textContent = invoiceNo;
        ddl.appendChild(opt);
      });
    }
  } catch(err) { console.warn("Unable to load invoice LOV", err); }
}

async function loadAgentInvoices(){
  const params = new URLSearchParams({
    agent: document.getElementById("filterAgent").value || "",
    invoice_no: document.getElementById("filterInvoiceNo").value || "",
    from_date: document.getElementById("filterFromDate").value || "",
    to_date: document.getElementById("filterToDate").value || "",
    status: document.getElementById("filterStatus").value || "",
    like: document.getElementById("filterLike").value || ""
  });

  try{
    const res = await fetch(`/api/agent-payment/invoices?${params.toString()}`);
    const data = await res.json();
    if (data.success && Array.isArray(data.rows)) agentInvoiceRows = data.rows || [];
    else { agentInvoiceRows = []; if (data.message) alert(data.message); }
    selectedInvoiceRow = null;
    renderAgentInvoices();
    updateSummary();
  } catch(err) {
    agentInvoiceRows = [];
    selectedInvoiceRow = null;
    renderAgentInvoices();
    updateSummary();
    alert("Unable to load agent invoice records. Please check console/server log for the exact SQL error.");
  }
}

function getFilteredRows(){
  const tableSearch = (document.getElementById("tableSearchInput")?.value || "").trim().toLowerCase();
  if (!tableSearch) return agentInvoiceRows;
  return agentInvoiceRows.filter(row => {
    const searchable = [getInvoiceNo(row), row.invoice_date || "", row.agent_name || "", row.agent_shrtnm || "", row.patient_name || "", row.case_reference || "", row.currency || "", row.payment_status || ""].join(" ").toLowerCase();
    return searchable.includes(tableSearch);
  });
}

function renderAgentInvoices(){
  const tbody = document.getElementById("agentInvoiceBody");
  const visibleRows = getFilteredRows();
  if (!tbody) return;
  if (!visibleRows.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="ap-empty">No records found.</td></tr>`;
    document.getElementById("visibleRecordCount").textContent = "0";
    updateKpis();
    return;
  }

  tbody.innerHTML = visibleRows.map(row => {
    const invoiceNo = getInvoiceNo(row);
    const agencyPayAmount = Number(row.agency_pay_amount || 0);
    const alreadyPaid = Number(row.paid_amount || 0);
    const outstanding = Number(row.outstanding_amount ?? Math.max(agencyPayAmount - alreadyPaid, 0));
    const rowKey = getInvoiceAgentKey(row);
    const selectedClass =
      selectedInvoiceRow &&
      getInvoiceAgentKey(selectedInvoiceRow) === rowKey
        ? ' style="background:#f5fbff;"'
        : '';
    return `
      <tr${selectedClass}>
        <td><a class="ap-link" onclick="loadInvoiceIntoSummary('${escapeJs(invoiceNo)}','${escapeJs(getAgentCode(row))}')">${escapeHtml(invoiceNo || "-")}</a></td>
        <td>${escapeHtml(row.invoice_date || "-")}</td>
        <td>${escapeHtml(row.agent_name || row.agent_shrtnm || "-")}</td>
        <td>${escapeHtml(row.patient_name || row.case_reference || "-")}</td>
        <td>${escapeHtml(row.currency || "INR")}</td>
        <td>${formatAmount(row.invoice_amount)}</td>
        <td><strong>${formatAmount(agencyPayAmount)}</strong></td>
        <td><strong>${formatAmount(outstanding)}</strong></td>
        <td>${escapeHtml(displayUtrReference(row))}</td>
        <td>${statusBadge(row.payment_status)}</td>
      </tr>`;
  }).join("");

  document.getElementById("visibleRecordCount").textContent = visibleRows.length;
  updateKpis();
}

function updateKpis(){
  const distinctInvoices = new Set(
    agentInvoiceRows
      .map(r => getInvoiceNo(r).trim().toUpperCase())
      .filter(Boolean)
  );

  const totalInvoices = distinctInvoices.size;

  const totalPayable = agentInvoiceRows.reduce(
    (sum, r) => sum + Number(r.outstanding_amount || 0),
    0
  );

  const paidAmount = agentInvoiceRows.reduce(
    (sum, r) => sum + Number(r.paid_amount || 0),
    0
  );

  const selectedAmount = selectedInvoiceRow
    ? Number(selectedInvoiceRow.agency_pay_amount || 0)
    : 0;

  document.getElementById("kpiTotalInvoices").textContent = totalInvoices;
  document.getElementById("kpiTotalPayable").textContent = formatAmount(totalPayable);
  document.getElementById("kpiSelectedAmount").textContent = formatAmount(selectedAmount);
  document.getElementById("kpiPaidAmount").textContent = formatAmount(paidAmount);
  document.getElementById("kpiPendingAmount").textContent = formatAmount(totalPayable);
}

function loadInvoiceIntoSummary(invoiceNo, agentCode){
  const wantedInvoice = String(invoiceNo || "").trim().toUpperCase();
  const wantedAgent = String(agentCode || "").trim().toUpperCase();

  const row = agentInvoiceRows.find(r =>
    getInvoiceNo(r).trim().toUpperCase() === wantedInvoice &&
    getAgentCode(r).trim().toUpperCase() === wantedAgent
  );

  if (!row) return;
  selectedInvoiceRow = row;
  const outstanding = Number(row.outstanding_amount ?? Math.max(Number(row.agency_pay_amount || 0) - Number(row.paid_amount || 0), 0));
  document.getElementById("paymentAmount").value = outstanding > 0 ? outstanding.toFixed(2) : "";
  document.getElementById("bankReference").value = "";
  updateSummary();
  renderAgentInvoices();
}

function recalculatePaymentSummary(){
  if (!selectedInvoiceRow) return;
  const agencyPay = Number(selectedInvoiceRow.agency_pay_amount || 0);
  const alreadyPaid = Number(selectedInvoiceRow.paid_amount || 0);
  let paidNow = Number(document.getElementById("paymentAmount").value || 0);
  const maxPayable = Math.max(agencyPay - alreadyPaid, 0);
  if (paidNow > maxPayable) {
    alert("Paid amount cannot be greater than Outstanding Payment.");
    paidNow = maxPayable;
    document.getElementById("paymentAmount").value = paidNow.toFixed(2);
  }
  document.getElementById("summaryOutstanding").textContent = formatAmount(Math.max(maxPayable - paidNow, 0));
}

function updateSummary(){
  if (!selectedInvoiceRow) {
    document.getElementById("selectedCountText").textContent = "No invoice selected";
    ["summaryInvoiceNo","summaryAgent","summaryPatientCase","summaryCurrency"].forEach(id => document.getElementById(id).textContent = "-");
    ["summaryInvoiceAmount","summaryAgencyPayAmount","summaryAlreadyPaid","summaryOutstanding"].forEach(id => document.getElementById(id).textContent = "0.00");
    document.getElementById("paymentAmount").value = "";
    updateKpis();
    return;
  }
  const invoiceNo = getInvoiceNo(selectedInvoiceRow);
  const agencyPay = Number(selectedInvoiceRow.agency_pay_amount || 0);
  const alreadyPaid = Number(selectedInvoiceRow.paid_amount || 0);
  const outstanding = Number(selectedInvoiceRow.outstanding_amount ?? Math.max(agencyPay - alreadyPaid, 0));
  document.getElementById("selectedCountText").textContent = "1 invoice selected";
  document.getElementById("summaryInvoiceNo").textContent = invoiceNo || "-";
  document.getElementById("summaryAgent").textContent = selectedInvoiceRow.agent_name || selectedInvoiceRow.agent_shrtnm || "-";
  document.getElementById("summaryPatientCase").textContent = selectedInvoiceRow.patient_name || selectedInvoiceRow.case_reference || "-";
  document.getElementById("summaryCurrency").textContent = selectedInvoiceRow.currency || "INR";
  document.getElementById("summaryInvoiceAmount").textContent = formatAmount(selectedInvoiceRow.invoice_amount);
  document.getElementById("summaryAgencyPayAmount").textContent = formatAmount(agencyPay);
  document.getElementById("summaryAlreadyPaid").textContent = formatAmount(alreadyPaid);
  document.getElementById("summaryOutstanding").textContent = formatAmount(outstanding);
  updateKpis();
}

function previewAgentPayment(){
  if (!selectedInvoiceRow) { alert("Please click an Invoice No to load Payment Summary."); return; }
  alert(`Invoice No: ${getInvoiceNo(selectedInvoiceRow)}
Agent: ${selectedInvoiceRow.agent_name || selectedInvoiceRow.agent_shrtnm || "-"}
Patient / Case: ${selectedInvoiceRow.patient_name || selectedInvoiceRow.case_reference || "-"}
Currency: ${selectedInvoiceRow.currency || "INR"}
Agency Pay Amount: ${formatAmount(selectedInvoiceRow.agency_pay_amount)}
Paid Amount: ${document.getElementById("paymentAmount").value || "0.00"}
Bank Reference / UTR No: ${document.getElementById("bankReference").value || "-"}`);
}

async function saveAgentPayment(){
  if (!selectedInvoiceRow) { alert("Please click an Invoice No to load Payment Summary."); return; }
  const paymentAmount = Number(document.getElementById("paymentAmount").value || 0);
  const agencyPay = Number(selectedInvoiceRow.agency_pay_amount || 0);
  const alreadyPaid = Number(selectedInvoiceRow.paid_amount || 0);
  const outstanding = Math.max(agencyPay - alreadyPaid, 0);
  if (paymentAmount <= 0) { alert("Please enter Paid Amount."); return; }
  if (paymentAmount > outstanding) { alert("Paid Amount cannot be greater than Outstanding Payment."); return; }
  if (!document.getElementById("paymentMode").value) { alert("Please select payment mode."); return; }
  if (!document.getElementById("bankReference").value.trim()) { alert("Please enter Bank Reference / UTR No."); return; }
  const payload = {
    invoices: [{
      invoice_no: getInvoiceNo(selectedInvoiceRow),
      agent_code: selectedInvoiceRow.agent_code || selectedInvoiceRow.agent_shrtnm || "",
      agent_name: selectedInvoiceRow.agent_name || "",
      patient_name: selectedInvoiceRow.patient_name || selectedInvoiceRow.case_reference || "",
      currency: selectedInvoiceRow.currency || "INR",
      invoice_amount: Number(selectedInvoiceRow.invoice_amount || 0),
      agency_pay_amount: agencyPay,
      already_paid_amount: alreadyPaid,
      paid_now_amount: paymentAmount,
      outstanding_amount: Math.max(outstanding - paymentAmount, 0),
      utr_reference: document.getElementById("bankReference").value || ""
    }],
    payment_date: document.getElementById("paymentDate").value,
    payment_mode: document.getElementById("paymentMode").value,
    payment_amount: paymentAmount,
    tds_amount: Number(document.getElementById("tdsAmount").value || 0),
    bank_reference: document.getElementById("bankReference").value || "",
    utr_reference: document.getElementById("bankReference").value || "",
    currency: selectedInvoiceRow.currency || "INR",
    remarks: document.getElementById("paymentRemarks").value || ""
  };
  if (!confirm("Do you want to save this agent payment?")) return;
  try{
    const res = await fetch("/api/agent-payment/save", { method:"POST", headers:{ "Content-Type":"application/json" }, body:JSON.stringify(payload) });
    const data = await res.json();
    if (data.success) {
      alert(data.message || "Agent payment saved successfully.");
      selectedInvoiceRow = null;
      document.getElementById("paymentAmount").value = "";
      document.getElementById("tdsAmount").value = "";
      document.getElementById("bankReference").value = "";
      document.getElementById("paymentRemarks").value = "";
      loadAgentInvoices();
    } else alert(data.message || "Unable to save payment.");
  } catch(err) { alert("Error while saving agent payment."); }
}

function resetAgentPaymentScreen(){
  document.getElementById("filterAgent").value = "";
  document.getElementById("filterInvoiceNo").value = "";
  document.getElementById("filterFromDate").value = "";
  document.getElementById("filterToDate").value = "";
  document.getElementById("filterStatus").value = "";
  document.getElementById("filterLike").value = "";
  document.getElementById("tableSearchInput").value = "";
  document.getElementById("paymentMode").value = "";
  document.getElementById("paymentAmount").value = "";
  document.getElementById("tdsAmount").value = "";
  document.getElementById("bankReference").value = "";
  document.getElementById("paymentRemarks").value = "";
  selectedInvoiceRow = null;
  setTodayDate();
  loadAgentInvoices();
}
