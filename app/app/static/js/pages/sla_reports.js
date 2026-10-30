function buildSlaQueryString(){
    const params = new URLSearchParams();

    [
        "from_date",
        "to_date",
        "sla_status",
        "priority_status",
        "hospital_shrtnm",
        "agent_shrtnm",
        "search"
    ].forEach(id => {
        const val = document.getElementById(id).value;
        if(val){
            params.append(id, val);
        }
    });

    return params.toString();
}

function populateSelect(id, values){
    const select = document.getElementById(id);
    const current = select.value;

    select.innerHTML = `<option value="">All</option>`;

    (values || []).forEach(v => {
        if(v){
            const opt = document.createElement("option");
            opt.value = v;
            opt.textContent = v;
            select.appendChild(opt);
        }
    });

    select.value = current;
}

function updateFilters(filters){
    filters = filters || {};
    populateSelect("hospital_shrtnm", filters.hospitals || []);
    populateSelect("agent_shrtnm", filters.agencies || []);
    populateSelect("sla_status", filters.statuses || []);
    populateSelect("priority_status", filters.priorities || []);
}

function updateKpis(kpis){
    kpis = kpis || {};
    document.getElementById("kpi_total").innerText = kpis.total_records || 0;
    document.getElementById("kpi_within").innerText = kpis.within_sla || 0;
    document.getElementById("kpi_near").innerText = kpis.near_breach || 0;
    document.getElementById("kpi_breached").innerText = kpis.breached || 0;
    document.getElementById("kpi_escalated").innerText = kpis.escalated || 0;
    document.getElementById("kpi_avg").innerText = kpis.avg_elapsed_minutes || 0;
}

function badgeStatus(value){
    const v = String(value || "").toUpperCase();

    if(v === "BREACHED"){
        return `<span class="badge badge-breach">${formatValue(value)}</span>`;
    }

    if(v === "NEAR BREACH"){
        return `<span class="badge badge-near">${formatValue(value)}</span>`;
    }

    if(v){
        return `<span class="badge badge-good">${formatValue(value)}</span>`;
    }

    return "";
}

function renderRows(rows){
    const body = document.getElementById("slaReportBody");
    body.innerHTML = "";

    if(!rows || rows.length === 0){
        body.innerHTML = `
            <tr>
                <td colspan="17" class="empty-row">
                    No records found for selected filters.
                </td>
            </tr>`;
        return;
    }

    rows.forEach(r => {
        body.innerHTML += `
            <tr>
                <td>${formatValue(r.channel_thread_id || "")}</td>
                <td>${formatValue(r.patpassport_no || "")}</td>
                <td>${formatValue(r.patient_name || "")}</td>
                <td>${formatValue(r.patient_country || "")}</td>
                <td>${formatValue(r.hospital_shrtnm || "")}</td>
                <td>${formatValue(r.agent_shrtnm || "")}</td>
                <td>${formatValue(r.priority_status || "")}</td>
                <td>${formatValue(r.wrkflw_status || "")}</td>
                <td>${formatValue(r.rule_code || "")}</td>
                <td>${formatValue(r.rule_name || "")}</td>
                <td>${badgeStatus(r.sla_status)}</td>
                <td>${formatValue(r.minutes_elapsed || r.elapsed_minutes || 0)}</td>
                <td>${formatValue(r.target_minutes || 0)}</td>
                <td>${formatValue(r.near_breach_minutes || 0)}</td>
                <td>${formatValue(r.current_escalation_level || 0)}</td>
                <td>${formatValue(r.started_on || "")}</td>
                <td>${formatValue(r.stopped_on || "")}</td>
            </tr>`;
    });
}

async function generateSlaDashboard(){
    const qs = buildSlaQueryString();
    const response = await fetch(`/api/reports/sla/dashboard?${qs}`);
    const result = await response.json();

    if(!result.success){
        alert(result.message || "Unable to fetch SLA dashboard");
        return;
    }

    updateKpis(result.data.kpis || {});
    updateFilters(result.data.filters || {});
}

async function generateSlaRows(){
    const qs = buildSlaQueryString();
    const response = await fetch(`/api/reports/sla/rows?${qs}`);
    const result = await response.json();

    if(!result.success){
        alert(result.message || "Unable to fetch SLA report");
        return;
    }

    renderRows(result.data || []);
}

async function generateSlaReport(){
    try{
        await generateSlaDashboard();
        await generateSlaRows();
    }catch(err){
        console.error(err);
        alert("Unable to fetch SLA report.");
    }
}

function clearSlaFilters(){
    document.getElementById("from_date").value = "";
    document.getElementById("to_date").value = "";
    document.getElementById("sla_status").value = "";
    document.getElementById("priority_status").value = "";
    document.getElementById("hospital_shrtnm").value = "";
    document.getElementById("agent_shrtnm").value = "";
    document.getElementById("search").value = "";

    setTodayDefaults();
    generateSlaReport();
}

function exportSlaExcel(){
    const qs = buildSlaQueryString();
    window.location.href = `/api/reports/sla/export-excel?${qs}`;
}

function exportSlaPdf(){
    const qs = buildSlaQueryString();
    window.location.href = `/api/reports/sla/export-pdf?${qs}`;
}

document.addEventListener("DOMContentLoaded", function(){
    setTodayDefaults();
    generateSlaReport();
});
