const patientGridState = {
  currentPage: 1,
  pageSize: 5,
  filteredRows: [],
  totalPages: 1
};

function parseJsonScript(scriptId, fallbackValue) {
  const el = document.getElementById(scriptId);
  if (!el) return fallbackValue;

  try {
    const raw = (el.textContent || "").trim();
    return raw ? JSON.parse(raw) : fallbackValue;
  } catch (err) {
    return fallbackValue;
  }
}

const workflowStatusColors = parseJsonScript("wfStatusColorMapData", {});
window.wfFlashMessages = parseJsonScript("wfFlashMessagesData", []);

function openQuickView() {
  const drawer = document.getElementById("drawer");
  const overlay = document.getElementById("drawerOverlay");
  if (drawer && overlay) {
    drawer.classList.add("open");
    overlay.classList.add("show");
    document.body.style.overflow = "hidden";
  }
}

function closeQuickView() {
  const drawer = document.getElementById("drawer");
  const overlay = document.getElementById("drawerOverlay");
  if (drawer && overlay) {
    drawer.classList.remove("open");
    overlay.classList.remove("show");
    document.body.style.overflow = "";
  }
}

function normalizeStatus(value) {
  return String(value || "").trim().toUpperCase();
}

function normalizeColorName(value) {
  return String(value || "").trim().toLowerCase();
}

function getStatusColorCode(status) {
  const normalizedStatus = normalizeStatus(status);
  const colorName = normalizeColorName(workflowStatusColors[normalizedStatus] || "blue");

  if (colorName === "red") {
    return { bg: "#fff1f1", text: "#b42318", border: "#f3c7c4" };
  }
  if (colorName === "green") {
    return { bg: "#eefaf2", text: "#16794d", border: "#bfe7cd" };
  }
  if (colorName === "yellow") {
    return { bg: "#fff8e8", text: "#9a6b00", border: "#f0dd9b" };
  }
  if (colorName === "orange") {
    return { bg: "#fff4ea", text: "#b54708", border: "#f6d0ae" };
  }
  if (colorName === "purple") {
    return { bg: "#f4efff", text: "#6941c6", border: "#d9ccff" };
  }
  if (colorName === "gray" || colorName === "grey") {
    return { bg: "#f2f4f7", text: "#475467", border: "#d0d5dd" };
  }

  return { bg: "#eaf2ff", text: "#1f5aad", border: "#c9dafc" };
}

function applyStatusColorToPill(pill, status) {
  if (!pill) return;
  const palette = getStatusColorCode(status);
  pill.style.background = palette.bg;
  pill.style.color = palette.text;
  pill.style.borderColor = palette.border;
}

function applyAllStatusColors() {
  const pills = document.querySelectorAll(".status-pill");
  pills.forEach(function(pill) {
    const status = pill.getAttribute("data-status") || pill.textContent || "";
    applyStatusColorToPill(pill, status);
  });
}

function updateNextStatusBadge() {
  const dd = document.getElementById("wrkflw_status");
  const badge = document.getElementById("nextStatusBadge");
  if (dd && badge) {
    const value = dd.value || "";
    badge.setAttribute("data-status", value);
    badge.innerHTML = '<span class="wf-dot"></span>' + value;
    applyStatusColorToPill(badge, value);
  }
}

function clearWorkflowForm() {
  const wrkflwStatus = document.getElementById("wrkflw_status");
  const vilDate = document.getElementById("vil_date");
  const apptDate = document.getElementById("appointment_date");
  const remarks = document.getElementById("remarks");
  const ivac = document.getElementById("ivac");

  if (wrkflwStatus) wrkflwStatus.selectedIndex = 0;
  if (vilDate) vilDate.value = "";
  if (apptDate) apptDate.value = "";
  if (remarks) remarks.value = "";
  if (ivac) ivac.selectedIndex = 0;

  updateNextStatusBadge();
}

function loadPatientLead(leadId) {
  const hidden = document.getElementById("lead_selector_hidden");
  const form = document.getElementById("patientGridLoadForm");
  if (hidden && form && leadId) {
    hidden.value = leadId;
    form.submit();
  }
}

function getPatientGridRows() {
  return Array.from(document.querySelectorAll("#patientGridBody .patient-grid-row"));
}

function getPatientGridFilterValue() {
  const input = document.getElementById("patientGridSearch");
  return (input ? input.value : "").toLowerCase().trim();
}

function filterPatientGrid() {
  const filter = getPatientGridFilterValue();
  const rows = getPatientGridRows();

  patientGridState.filteredRows = rows.filter(function(row) {
    const text = (row.innerText || row.textContent || "").toLowerCase();
    return !filter || text.indexOf(filter) > -1;
  });

  patientGridState.currentPage = 1;
  renderPatientGridPage();
}

function renderPatientGridPage() {
  const rows = getPatientGridRows();
  const noMatchRow = document.getElementById("patientGridNoMatchRow");
  const noDataRow = document.getElementById("patientGridNoDataRow");
  const meta = document.getElementById("patientGridMeta");
  const pageNumbers = document.getElementById("patientGridPageNumbers");
  const firstBtn = document.getElementById("patientGridFirstBtn");
  const prevBtn = document.getElementById("patientGridPrevBtn");
  const nextBtn = document.getElementById("patientGridNextBtn");
  const lastBtn = document.getElementById("patientGridLastBtn");
  const paginationWrap = document.getElementById("patientGridPagination");

  rows.forEach(function(row) {
    row.style.display = "none";
  });

  const totalFiltered = patientGridState.filteredRows.length;
  const totalRows = rows.length;
  patientGridState.totalPages = Math.max(1, Math.ceil(totalFiltered / patientGridState.pageSize));

  if (patientGridState.currentPage < 1) {
    patientGridState.currentPage = 1;
  }
  if (patientGridState.currentPage > patientGridState.totalPages) {
    patientGridState.currentPage = patientGridState.totalPages;
  }

  if (noDataRow) {
    noDataRow.style.display = totalRows === 0 ? "" : "none";
  }

  if (noMatchRow) {
    const showNoMatch = totalRows > 0 && totalFiltered === 0;
    noMatchRow.classList.toggle("wf-hidden", !showNoMatch);
    noMatchRow.style.display = showNoMatch ? "" : "none";
  }

  if (totalFiltered > 0) {
    const startIndex = (patientGridState.currentPage - 1) * patientGridState.pageSize;
    const endIndex = Math.min(startIndex + patientGridState.pageSize, totalFiltered);

    for (let i = startIndex; i < endIndex; i++) {
      patientGridState.filteredRows[i].style.display = "";
    }

    if (meta) {
      meta.textContent = "Showing " + (startIndex + 1) + " to " + endIndex + " of " + totalFiltered + " records";
    }
  } else {
    if (meta) {
      meta.textContent = "Showing 0 to 0 of " + totalFiltered + " records";
    }
  }

  if (pageNumbers) {
    pageNumbers.innerHTML = "";

    if (totalFiltered > 0) {
      const maxButtons = 5;
      let startPage = Math.max(1, patientGridState.currentPage - 2);
      let endPage = Math.min(patientGridState.totalPages, startPage + maxButtons - 1);

      if ((endPage - startPage + 1) < maxButtons) {
        startPage = Math.max(1, endPage - maxButtons + 1);
      }

      for (let p = startPage; p <= endPage; p++) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "wf-page-btn" + (p === patientGridState.currentPage ? " active" : "");
        btn.textContent = String(p);
        btn.addEventListener("click", function() {
          goToPatientGridPage(p);
        });
        pageNumbers.appendChild(btn);
      }
    }
  }

  const disablePagination = totalFiltered === 0 || patientGridState.totalPages <= 1;

  if (firstBtn) firstBtn.disabled = disablePagination || patientGridState.currentPage === 1;
  if (prevBtn) prevBtn.disabled = disablePagination || patientGridState.currentPage === 1;
  if (nextBtn) nextBtn.disabled = disablePagination || patientGridState.currentPage === patientGridState.totalPages;
  if (lastBtn) lastBtn.disabled = disablePagination || patientGridState.currentPage === patientGridState.totalPages;

  if (paginationWrap) {
    paginationWrap.style.opacity = totalRows === 0 ? ".5" : "1";
  }
}

function goToPatientGridPage(page) {
  patientGridState.currentPage = page;
  renderPatientGridPage();
}

function changePatientGridPageSize() {
  const dd = document.getElementById("patientGridPageSize");
  const size = parseInt(dd ? dd.value : "5", 10);
  patientGridState.pageSize = size > 0 ? size : 5;
  patientGridState.currentPage = 1;
  renderPatientGridPage();
}

function resetPatientGridSearch() {
  const input = document.getElementById("patientGridSearch");
  if (input) {
    input.value = "";
  }
  patientGridState.currentPage = 1;
  filterPatientGrid();
}

function scrollToLeadSnapshotIfNeeded() {
  const params = new URLSearchParams(window.location.search);
  const shouldScroll = params.get("scroll_to");
  const snapshotSection = document.getElementById("leadSnapshotSection");

  if (shouldScroll === "lead_snapshot" && snapshotSection) {
    setTimeout(function() {
      snapshotSection.scrollIntoView({
        behavior: "smooth",
        block: "start"
      });
    }, 150);
  }
}

function getPopupCategoryClass(category) {
  const c = String(category || "").toLowerCase();
  if (c.includes("success")) return "wf-popup-success";
  if (c.includes("error") || c.includes("danger") || c.includes("warning")) return "wf-popup-error";
  return "wf-popup-info";
}

function getPopupTitle(category) {
  const c = String(category || "").toLowerCase();
  if (c.includes("success")) return "Success";
  if (c.includes("error") || c.includes("danger") || c.includes("warning")) return "Error";
  return "Message";
}

function getPopupIcon(category) {
  const c = String(category || "").toLowerCase();
  if (c.includes("success")) return "✓";
  if (c.includes("error") || c.includes("danger") || c.includes("warning")) return "!";
  return "i";
}

function showWorkflowPopup(message, category) {
  const overlay = document.getElementById("wfPopupOverlay");
  const popup = document.getElementById("wfPopup");
  const title = document.getElementById("wfPopupTitle");
  const body = document.getElementById("wfPopupBody");
  const icon = document.getElementById("wfPopupIcon");

  if (!overlay || !popup || !title || !body || !icon) {
    alert(message || "Message");
    return;
  }

  popup.className = "wf-popup " + getPopupCategoryClass(category);
  title.textContent = getPopupTitle(category);
  icon.textContent = getPopupIcon(category);
  body.textContent = message || "";
  overlay.classList.add("show");
  document.body.style.overflow = "hidden";
}

function closeWorkflowPopup() {
  const overlay = document.getElementById("wfPopupOverlay");
  if (overlay) {
    overlay.classList.remove("show");
  }

  const drawer = document.getElementById("drawer");
  const drawerOverlay = document.getElementById("drawerOverlay");
  const drawerOpen = drawer && drawer.classList.contains("open");
  const overlayOpen = drawerOverlay && drawerOverlay.classList.contains("show");

  if (!drawerOpen && !overlayOpen) {
    document.body.style.overflow = "";
  }
}

function handlePopupOverlayClick(event) {
  if (event.target && event.target.id === "wfPopupOverlay") {
    closeWorkflowPopup();
  }
}

function showFlashMessagesAsPopup() {
  if (Array.isArray(window.wfFlashMessages) && window.wfFlashMessages.length > 0) {
    const first = window.wfFlashMessages[0];

    if (Array.isArray(first)) {
      showWorkflowPopup(first[1] || "", first[0] || "info");
      return;
    }

    showWorkflowPopup(first.message || "", first.category || "info");
  }
}

document.addEventListener("DOMContentLoaded", function() {
  updateNextStatusBadge();

  const dd = document.getElementById("wrkflw_status");
  if (dd) {
    dd.addEventListener("change", updateNextStatusBadge);
  }

  const pageSizeDd = document.getElementById("patientGridPageSize");
  if (pageSizeDd) {
    patientGridState.pageSize = parseInt(pageSizeDd.value || "5", 10);
  }

  patientGridState.filteredRows = getPatientGridRows();
  renderPatientGridPage();
  applyAllStatusColors();
  scrollToLeadSnapshotIfNeeded();
  showFlashMessagesAsPopup();
});

document.addEventListener("keydown", function(e) {
  if (e.key === "Escape") {
    closeQuickView();
    closeWorkflowPopup();
  }
});
