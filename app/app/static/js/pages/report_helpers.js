function setTodayDefaults(){
    const today = new Date().toISOString().slice(0,10);
    document.getElementById("to_date").value = today;
}

function formatAmount(value){
    const num = Number(value || 0);
    return num.toLocaleString("en-IN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

function escapeHtml(value){
    if(value === null || value === undefined){ return ""; }
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function formatValue(value){
    if(value === null || value === undefined || value === ""){
        return "";
    }
    return escapeHtml(value);
}
