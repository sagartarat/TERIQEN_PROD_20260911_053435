document.addEventListener("DOMContentLoaded", function(){
  const table = document.getElementById("patientTable");
  if(!table) return;

  const tbody = table.querySelector("tbody");
  if(!tbody) return;

  const passportInput = document.getElementById("searchPassport");
  const nameInput = document.getElementById("searchName");
  const countrySelect = document.getElementById("searchCountry");
  const resetBtn = document.getElementById("btnReset");

  const pageSizeEl = document.getElementById("pageSize");
  const pagerInfo = document.getElementById("pagerInfo");
  const pagerButtons = document.getElementById("pagerButtons");
  const btnPrev = document.getElementById("btnPrev");
  const btnNext = document.getElementById("btnNext");
  const tableScroll = document.getElementById("tableScroll");

  const allRows = Array.from(tbody.querySelectorAll("tr.data-row"));
  const noDataRow = tbody.querySelector("tr.no-data-row");

  function norm(v){ return (v || "").toString().toLowerCase().trim(); }

  function getText(row, selector){
    const el = row.querySelector(selector);
    return el ? (el.textContent || "") : "";
  }

  let currentPage = 1;

  function getFilteredRows(){
    const pVal = norm(passportInput?.value);
    const nVal = norm(nameInput?.value);
    const cVal = norm(countrySelect?.value);

    return allRows.filter(row=>{
      const passport = norm(getText(row, ".col-passport"));
      const given = norm(getText(row, ".col-given"));
      const country = norm(getText(row, ".col-country"));

      return (
        (pVal === "" || passport.includes(pVal)) &&
        (nVal === "" || given.includes(nVal)) &&
        (cVal === "" || country === cVal)
      );
    });
  }

  function clearPageNumberButtons(){
    if(!pagerButtons || !btnNext) return;
    Array.from(pagerButtons.querySelectorAll("button.page-num")).forEach(n=>n.remove());
  }

  function buildPageNumberButtons(totalPages){
    if(!pagerButtons || !btnNext) return;
    clearPageNumberButtons();
    if(totalPages <= 1) return;

    const maxButtons = 7;
    let start = Math.max(1, currentPage - Math.floor(maxButtons/2));
    let end = start + maxButtons - 1;

    if(end > totalPages){
      end = totalPages;
      start = Math.max(1, end - maxButtons + 1);
    }

    for(let p = start; p <= end; p++){
      const b = document.createElement("button");
      b.type = "button";
      b.className = "page-btn page-num" + (p === currentPage ? " active" : "");
      b.textContent = String(p);
      b.addEventListener("click", ()=>{
        currentPage = p;
        render();
      });
      pagerButtons.insertBefore(b, btnNext);
    }
  }

  function render(){
    const pageSize = parseInt(pageSizeEl?.value || "20", 10);
    const filtered = getFilteredRows();
    const total = filtered.length;

    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    if(currentPage > totalPages) currentPage = totalPages;
    if(currentPage < 1) currentPage = 1;

    allRows.forEach(r => r.style.display = "none");

    if(total === 0){
      if(noDataRow) noDataRow.style.display = "";
      if(pagerInfo) pagerInfo.textContent = "Showing 0–0 of 0";
      if(btnPrev) btnPrev.disabled = true;
      if(btnNext) btnNext.disabled = true;
      buildPageNumberButtons(1);
      return;
    }else{
      if(noDataRow) noDataRow.style.display = "none";
    }

    const startIdx = (currentPage - 1) * pageSize;
    const endIdx = Math.min(startIdx + pageSize, total);

    filtered.slice(startIdx, endIdx).forEach(r => r.style.display = "");

    if(pagerInfo) pagerInfo.textContent = `Showing ${startIdx + 1}–${endIdx} of ${total}`;

    if(btnPrev) btnPrev.disabled = currentPage <= 1;
    if(btnNext) btnNext.disabled = currentPage >= totalPages;

    buildPageNumberButtons(totalPages);

    if(tableScroll) tableScroll.scrollTop = 0;
  }

  function resetFilters(){
    if(passportInput) passportInput.value = "";
    if(nameInput) nameInput.value = "";
    if(countrySelect) countrySelect.value = "";
    currentPage = 1;
    render();
  }

  passportInput?.addEventListener("input", ()=>{ currentPage = 1; render(); });
  nameInput?.addEventListener("input", ()=>{ currentPage = 1; render(); });
  countrySelect?.addEventListener("change", ()=>{ currentPage = 1; render(); });
  resetBtn?.addEventListener("click", resetFilters);

  pageSizeEl?.addEventListener("change", ()=>{ currentPage = 1; render(); });

  btnPrev?.addEventListener("click", ()=>{ if(currentPage > 1){ currentPage--; render(); } });
  btnNext?.addEventListener("click", ()=>{ currentPage++; render(); });

  render();
});
