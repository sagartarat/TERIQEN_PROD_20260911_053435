let currentInvoiceReport = "invoice_detail";

function buildInvoiceQueryString(){
    const params = new URLSearchParams();

    const ids = [
        "from_date",
        "to_date",
        "hospital_name",
        "invoice_number",
        "print_status",
        "recon_status"
    ];

    ids.forEach(id=>{
        const el = document.getElementById(id);
        if(el && el.value && el.value !== "ALL"){
            params.append(id, el.value);
        }
    });

    params.append("report_type", currentInvoiceReport);

    return params.toString();
}

function updateInvoiceKpis(summary){
    summary = summary || {};

    document.getElementById("kpi_invoices_generated").innerText = summary.invoices_generated || 0;
    document.getElementById("kpi_printed_invoices").innerText = summary.printed_invoices || 0;
    document.getElementById("kpi_pending_print").innerText = summary.pending_print || 0;
    document.getElementById("kpi_net_invoice_amount").innerText = formatAmount(summary.net_invoice_amount);
    document.getElementById("kpi_received_amount").innerText = formatAmount(summary.received_amount);
    document.getElementById("kpi_outstanding_amount").innerText = formatAmount(summary.outstanding_amount);
}

function renderInvoiceRows(rows){
    const head = document.getElementById("reportHead");
    const body = document.getElementById("reportBody");

    head.innerHTML = "";
    body.innerHTML = "";

    if(!rows || rows.length === 0){
        body.innerHTML = `
            <tr>
                <td colspan="20" class="empty-row">
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
                ${headers.map(h => `<td>${renderInvoiceCell(h, row[h])}</td>`).join("")}
            </tr>
        `;
    });
}

function renderInvoiceCell(key, value){
    const lowerKey = key.toLowerCase();

    if(lowerKey.includes("amount") || lowerKey.includes("value") || lowerKey.includes("ratio")){
        return formatAmount(value);
    }

    if(lowerKey.includes("status") || lowerKey.includes("bucket")){
        const v = String(value || "").toUpperCase();

        if(v.includes("PAID") || v.includes("MATCHED")){
            return `<span class="badge badge-paid">${formatValue(value)}</span>`;
        }

        if(v.includes("PARTIAL") || v.includes("8-15") || v.includes("16-30")){
            return `<span class="badge badge-partial">${formatValue(value)}</span>`;
        }

        if(v){
            return `<span class="badge badge-pending">${formatValue(value)}</span>`;
        }
    }

    return formatValue(value);
}

async function generateInvoiceDashboard(){
    try{
        const qs = buildInvoiceQueryString();
        const response = await fetch(`/api/reports/invoice-management/dashboard?${qs}`);
        const result = await response.json();

        if(!result.success){
            alert(result.message || "Unable to fetch invoice dashboard");
            return;
        }

        updateInvoiceKpis(result.data.summary || {});

    }catch(err){
        console.error(err);
        alert("Unable to fetch invoice dashboard.");
    }
}

async function generateInvoiceReport(){
    try{
        await generateInvoiceDashboard();

        const qs = buildInvoiceQueryString();
        const response = await fetch(`/api/reports/invoice-management/rows?${qs}`);
        const result = await response.json();

        if(!result.success){
            alert(result.message || "Unable to fetch invoice report");
            return;
        }

        renderInvoiceRows(result.data || []);

    }catch(err){
        console.error(err);
        alert("Unable to fetch invoice report.");
    }
}

function changeInvoiceReport(btn){
    document.querySelectorAll(".op-tab").forEach(b=>b.classList.remove("active"));
    btn.classList.add("active");

    currentInvoiceReport = btn.getAttribute("data-report");

    const title = btn.innerText + " Report";
    document.getElementById("reportTitle").innerText = title;
    document.getElementById("gridTitle").innerText = title + " Results";

    generateInvoiceReport();
}

function clearInvoiceReportFilters(){
    document.getElementById("from_date").value = "";
    document.getElementById("to_date").value = "";
    document.getElementById("hospital_name").value = "";
    document.getElementById("invoice_number").value = "";
    document.getElementById("print_status").value = "ALL";
    document.getElementById("recon_status").value = "ALL";

    setTodayDefaults();
    generateInvoiceReport();
}

function exportInvoiceReportExcel(){
    const qs = buildInvoiceQueryString();
    window.location.href = `/api/reports/invoice-management/export-excel?${qs}`;
}

function exportInvoiceReportPdf(){
    const qs = buildInvoiceQueryString();
    window.location.href = `/api/reports/invoice-management/export-pdf?${qs}`;
}

document.addEventListener("DOMContentLoaded", function(){
    setTodayDefaults();
    generateInvoiceReport();
});
