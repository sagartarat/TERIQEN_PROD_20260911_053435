(function () {
    const rowsPerPageSelect = document.getElementById("fusRowsPerPage");
    const prevBtn = document.getElementById("fusPrevPage");
    const nextBtn = document.getElementById("fusNextPage");
    const pageNumbersWrap = document.getElementById("fusPageNumbers");
    const paginationInfo = document.getElementById("fusPaginationInfo");
    const footerRecordCount = document.getElementById("fusFooterRecordCount");
    const tableRows = Array.from(document.querySelectorAll("#fusTableBody .fus-data-row"));

    if (!tableRows.length) {
      return;
    }

    let currentPage = 1;
    let rowsPerPage = parseInt(rowsPerPageSelect.value, 10) || 10;

    function getTotalPages() {
      return Math.max(1, Math.ceil(tableRows.length / rowsPerPage));
    }

    function renderRows() {
      const totalPages = getTotalPages();

      if (currentPage > totalPages) {
        currentPage = totalPages;
      }
      if (currentPage < 1) {
        currentPage = 1;
      }

      const startIndex = (currentPage - 1) * rowsPerPage;
      const endIndex = startIndex + rowsPerPage;

      tableRows.forEach((row, index) => {
        if (index >= startIndex && index < endIndex) {
          row.style.display = "";
          const serialCell = row.querySelector(".fus-serial-no");
          if (serialCell) {
            serialCell.textContent = index + 1;
          }
        } else {
          row.style.display = "none";
        }
      });

      const showingStart = tableRows.length ? startIndex + 1 : 0;
      const showingEnd = Math.min(endIndex, tableRows.length);

      paginationInfo.innerHTML = `Showing <strong>${showingStart}</strong> to <strong>${showingEnd}</strong> of <strong>${tableRows.length}</strong> record(s)`;
      if (footerRecordCount) {
        footerRecordCount.textContent = tableRows.length;
      }

      prevBtn.disabled = currentPage === 1;
      nextBtn.disabled = currentPage === totalPages;

      renderPageNumbers(totalPages);
    }

    function renderPageNumbers(totalPages) {
      pageNumbersWrap.innerHTML = "";

      let startPage = Math.max(1, currentPage - 2);
      let endPage = Math.min(totalPages, currentPage + 2);

      if (currentPage <= 3) {
        endPage = Math.min(totalPages, 5);
      }

      if (currentPage > totalPages - 2) {
        startPage = Math.max(1, totalPages - 4);
      }

      for (let page = startPage; page <= endPage; page++) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "fus-page-btn" + (page === currentPage ? " active" : "");
        btn.textContent = page;
        btn.addEventListener("click", function () {
          currentPage = page;
          renderRows();
        });
        pageNumbersWrap.appendChild(btn);
      }
    }

    rowsPerPageSelect.addEventListener("change", function () {
      rowsPerPage = parseInt(this.value, 10) || 10;
      currentPage = 1;
      renderRows();
    });

    prevBtn.addEventListener("click", function () {
      if (currentPage > 1) {
        currentPage--;
        renderRows();
      }
    });

    nextBtn.addEventListener("click", function () {
      if (currentPage < getTotalPages()) {
        currentPage++;
        renderRows();
      }
    });

    renderRows();
  })();
