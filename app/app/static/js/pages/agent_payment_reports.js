let currentAgentPaymentReport = "payment_detail";
let agentPaymentFilterOptionsLoaded = false;

function buildAgentPaymentQueryString(){
    const params = new URLSearchParams();

    const ids = [
        "from_date",
        "to_date",
        "agent",
        "invoice_number",
        "payment_no",
        "payment_mode",
        "payment_status",
        "currency_code",
        "search"
    ];

    ids.forEach(id=>{
        const el = document.getElementById(id);
        if(el && el.value && el.value !== "ALL"){
            params.append(id, el.value);
        }
    });

    params.append("report_type", currentAgentPaymentReport);

    return params.toString();
}

function updateAgentPaymentKpis(summary){
    summary = summary || {};

    document.getElementById("kpi_total_payments").innerText = summary.total_payments || 0;
    document.getElementById("kpi_invoices_paid").innerText = summary.invoices_paid || 0;
    document.getElementById("kpi_unique_agents_paid").innerText = summary.unique_agents_paid || 0;
    document.getElementById("kpi_total_paid_amount").innerText = formatAmount(summary.total_paid_amount);
    document.getElementById("kpi_total_tds_amount").innerText = formatAmount(summary.total_tds_amount);
    document.getElementById("kpi_net_transfer_amount").innerText = formatAmount(summary.net_transfer_amount);
}

function populateAgentPaymentFilterOptions(filters){
    if(agentPaymentFilterOptionsLoaded || !filters){
        return;
    }

    const agentList = document.getElementById("agentList");
    if(agentList && Array.isArray(filters.agents)){
        agentList.innerHTML = "";
        filters.agents.forEach(v=>{
            const opt = document.createElement("option");
            opt.value = v;
            agentList.appendChild(opt);
        });
    }

    populateSelectOptions("payment_mode", filters.payment_modes || []);
    populateSelectOptions("payment_status", filters.payment_statuses || []);
    populateSelectOptions("currency_code", filters.currencies || []);

    agentPaymentFilterOptionsLoaded = true;
}

function populateSelectOptions(id, values){
    const ddl = document.getElementById(id);
    if(!ddl) return;

    const current = ddl.value || "ALL";
    ddl.innerHTML = `<option value="ALL">All</option>`;

    values.forEach(v=>{
        if(!v) return;
        const opt = document.createElement("option");
        opt.value = v;
        opt.textContent = v;
        ddl.appendChild(opt);
    });

    ddl.value = current;
}

function renderAgentPaymentRows(rows){
    const head = document.getElementById("reportHead");
    const body = document.getElementById("reportBody");

    head.innerHTML = "";
    body.innerHTML = "";

    if(!rows || rows.length === 0){
        body.innerHTML = `
            <tr>
                <td colspan="25" class="empty-row">
                    No records found for selected filters.
                </td>
            </tr>`;
        return;
    }

    const headers = Object.keys(rows[0]);

    head.innerHTML = `
        <tr>
            ${headers.map(h => `<th>${h.replaceAll("_"," ").toUpperCase()}</th>`).join("")}
        </tr>
    `;

    rows.forEach(row=>{
        body.innerHTML += `
            <tr>
                ${headers.map(h => `<td>${renderAgentPaymentCell(h, row[h])}</td>`).join("")}
            </tr>
        `;
    });
}

function renderAgentPaymentCell(key, value){
    const lowerKey = key.toLowerCase();

    if(
        lowerKey.includes("amount") ||
        lowerKey.includes("paid") ||
        lowerKey.includes("payable") ||
        lowerKey.includes("balance") ||
        lowerKey.includes("tds") ||
        lowerKey.includes("transfer") ||
        lowerKey.includes("percent")
    ){
        return formatAmount(value);
    }

    if(lowerKey.includes("status")){
        const v = String(value || "").toUpperCase();

        if(v.includes("POSTED") || v.includes("PAID")){
            return `<span class="badge badge-posted">${formatValue(value)}</span>`;
        }

        if(v.includes("PARTIAL")){
            return `<span class="badge badge-partial">${formatValue(value)}</span>`;
        }

        if(v.includes("CANCEL")){
            return `<span class="badge badge-cancelled">${formatValue(value)}</span>`;
        }

        if(v){
            return `<span class="badge badge-neutral">${formatValue(value)}</span>`;
        }
    }

    return formatValue(value);
}

async function generateAgentPaymentDashboard(){
    try{
        const qs = buildAgentPaymentQueryString();
        const response = await fetch(`/api/reports/agent-payment/dashboard?${qs}`);
        const result = await response.json();

        if(!result.success){
            alert(result.message || "Unable to fetch agent payment dashboard");
            return;
        }

        updateAgentPaymentKpis(result.data.summary || {});
        populateAgentPaymentFilterOptions(result.data.filters || {});

    }catch(err){
        console.error(err);
        alert("Unable to fetch agent payment dashboard.");
    }
}

async function generateAgentPaymentReport(){
    try{
        await generateAgentPaymentDashboard();

        const qs = buildAgentPaymentQueryString();
        const response = await fetch(`/api/reports/agent-payment/rows?${qs}`);
        const result = await response.json();

        if(!result.success){
            alert(result.message || "Unable to fetch agent payment report");
            return;
        }

        renderAgentPaymentRows(result.data || []);

    }catch(err){
        console.error(err);
        alert("Unable to fetch agent payment report.");
    }
}

function changeAgentPaymentReport(btn){
    document.querySelectorAll(".ap-tab").forEach(b=>b.classList.remove("active"));
    btn.classList.add("active");

    currentAgentPaymentReport = btn.getAttribute("data-report");

    const title = btn.innerText + " Report";
    document.getElementById("reportTitle").innerText = title;
    document.getElementById("gridTitle").innerText = title + " Results";

    generateAgentPaymentReport();
}

function clearAgentPaymentFilters(){
    document.getElementById("from_date").value = "";
    document.getElementById("to_date").value = "";
    document.getElementById("agent").value = "";
    document.getElementById("invoice_number").value = "";
    document.getElementById("payment_no").value = "";
    document.getElementById("payment_mode").value = "ALL";
    document.getElementById("payment_status").value = "ALL";
    document.getElementById("currency_code").value = "ALL";
    document.getElementById("search").value = "";

    setTodayDefaults();
    generateAgentPaymentReport();
}

function exportAgentPaymentExcel(){
    const qs = buildAgentPaymentQueryString();
    window.location.href = `/api/reports/agent-payment/export-excel?${qs}`;
}

function exportAgentPaymentPdf(){
    const qs = buildAgentPaymentQueryString();
    window.location.href = `/api/reports/agent-payment/export-pdf?${qs}`;
}

document.addEventListener("DOMContentLoaded", function(){
    setTodayDefaults();
    generateAgentPaymentReport();
});
