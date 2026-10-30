function buildQueryString(){
    const params = new URLSearchParams();

    const ids = [
        "from_date","to_date","country","hospital_shrtnm",
        "agent_shrtnm","assigned_to","wrkflw_status","search"
    ];

    ids.forEach(id=>{
        const el = document.getElementById(id);
        const val = el ? el.value : "";
        if(val){ params.append(id,val); }
    });

    return params.toString();
}

function safeText(value){
    if(value === null || value === undefined){ return ""; }
    return escapeHtml(value);
}

function uniqueValues(rows, field){
    const set = new Set();

    (rows || []).forEach(r=>{
        const v = r[field];
        if(v !== null && v !== undefined && String(v).trim() !== ""){
            set.add(String(v).trim());
        }
    });

    return Array.from(set).sort();
}

function populateSelect(id, values){
    const select = document.getElementById(id);
    if(!select){ return; }

    const current = select.value;
    select.innerHTML = `<option value="">All</option>`;

    (values || []).forEach(v=>{
        const opt = document.createElement("option");
        opt.value = v;
        opt.textContent = v;
        select.appendChild(opt);
    });

    if(current){
        select.value = current;
    }
}

function updateFilters(filters, rows){
    filters = filters || {};

    populateSelect("country", filters.countries || uniqueValues(rows, "patient_country"));
    populateSelect("hospital_shrtnm", filters.hospitals || uniqueValues(rows, "hospital_shrtnm"));
    populateSelect("agent_shrtnm", filters.agents || filters.agencies || uniqueValues(rows, "agent_shrtnm"));
    populateSelect("assigned_to", filters.assigned_users || uniqueValues(rows, "assigned_to"));
    populateSelect("wrkflw_status", filters.statuses || uniqueValues(rows, "wrkflw_status"));
}

function calculateKpis(rows){
    rows = rows || [];

    let openRecords = 0;
    let closedRecords = 0;
    let highPriority = 0;
    let breached = 0;

    rows.forEach(r=>{
        const status = String(r.wrkflw_status || "").toUpperCase();
        const priority = String(r.priority_status || "").toUpperCase();
        const slaStatus = String(r.sla_status || "").toUpperCase();

        if(status !== "CLOSED"){
            openRecords++;
        }

        if(status === "CLOSED"){
            closedRecords++;
        }

        if(priority === "HIGH"){
            highPriority++;
        }

        if(slaStatus === "BREACHED" || slaStatus === "ESCALATED"){
            breached++;
        }
    });

    return {
        total_records: rows.length,
        open_records: openRecords,
        closed_records: closedRecords,
        high_priority_records: highPriority,
        breached_records: breached
    };
}

function updateKpis(kpis){
    kpis = kpis || {};

    document.getElementById("kpi_total").innerText = kpis.total_records || 0;
    document.getElementById("kpi_open").innerText = kpis.open_records || 0;
    document.getElementById("kpi_closed").innerText = kpis.closed_records || 0;
    document.getElementById("kpi_high").innerText = kpis.high_priority_records || 0;
    document.getElementById("kpi_breached").innerText = kpis.breached_records || 0;
}

function getAgeingDisplay(r){
    if(r.ageing_display){ return r.ageing_display; }

    const days = r.ageing_days;
    const hours = r.ageing_hours;

    if(days !== null && days !== undefined && days !== ""){
        return `${days} day(s)`;
    }

    if(hours !== null && hours !== undefined && hours !== ""){
        return `${hours} hour(s)`;
    }

    return "";
}

function getCreatedDisplay(r){
    return r.queue_created_on_display || r.queue_created_on || r.created_on || "";
}

function renderRows(rows){
    const body = document.getElementById("reportBody");
    body.innerHTML = "";

    if(!rows || rows.length === 0){
        body.innerHTML = `
            <tr>
                <td colspan="12" class="empty-row">
                    No records found for selected filters.
                </td>
            </tr>`;
        return;
    }

    rows.forEach(r=>{
        body.innerHTML += `
        <tr>
            <td>${safeText(r.channel_thread_id)}</td>
            <td>${safeText(getCreatedDisplay(r))}</td>
            <td>${safeText(r.wrkflw_status)}</td>
            <td>${safeText(r.priority_status)}</td>
            <td>${safeText(r.sla_status)}</td>
            <td>${safeText(getAgeingDisplay(r))}</td>
            <td>${safeText(r.patpassport_no)}</td>
            <td>${safeText(r.patient_name)}</td>
            <td>${safeText(r.hospital_shrtnm)}</td>
            <td>${safeText(r.agent_shrtnm)}</td>
            <td>${safeText(r.patient_country)}</td>
            <td>${safeText(r.assigned_to)}</td>
        </tr>`;
    });
}

function extractRows(result){
    if(Array.isArray(result.rows)){
        return result.rows;
    }

    if(Array.isArray(result.data)){
        return result.data;
    }

    if(result.data && Array.isArray(result.data.rows)){
        return result.data.rows;
    }

    return [];
}

function extractKpis(result, rows){
    if(result.data && result.data.kpis){
        return result.data.kpis;
    }

    if(result.kpis){
        return result.kpis;
    }

    return calculateKpis(rows);
}

function extractFilters(result){
    if(result.data && result.data.filters){
        return result.data.filters;
    }

    if(result.filters){
        return result.filters;
    }

    return {};
}

async function generateQueueAgeingReport(){
    try{
        const qs = buildQueryString();
        const response = await fetch(`/api/reports/queue-ageing?${qs}`);
        const result = await response.json();

        if(!result.success){
            alert(result.message || "Unable to fetch report");
            return;
        }

        const rows = extractRows(result);
        const kpis = extractKpis(result, rows);
        const filters = extractFilters(result);

        updateKpis(kpis);
        updateFilters(filters, rows);
        renderRows(rows);

    }catch(err){
        console.error(err);
        alert("Unable to fetch report.");
    }
}

function clearReportFilters(){
    document.getElementById("from_date").value = "";
    document.getElementById("to_date").value = "";
    document.getElementById("country").value = "";
    document.getElementById("hospital_shrtnm").value = "";
    document.getElementById("agent_shrtnm").value = "";
    document.getElementById("assigned_to").value = "";
    document.getElementById("wrkflw_status").value = "";
    document.getElementById("search").value = "";

    generateQueueAgeingReport();
}

function exportQueueAgeingExcel(){
    const qs = buildQueryString();
    window.location.href = `/api/reports/queue-ageing/export-excel?${qs}`;
}

function exportQueueAgeingPdf(){
    const qs = buildQueryString();
    window.location.href = `/api/reports/queue-ageing/export-pdf?${qs}`;
}

document.addEventListener("DOMContentLoaded", function(){
    setTodayDefaults();
    generateQueueAgeingReport();
});
