document.addEventListener("DOMContentLoaded", function () {

  const searchInput = document.getElementById("irSearchInput");
  const tabs = Array.from(document.querySelectorAll(".ir-tab"));

  const pageSizeSelect = document.getElementById("irPageSize");
  const prevBtn = document.getElementById("irPrevPageBtn");
  const nextBtn = document.getElementById("irNextPageBtn");
  const pageButtons = document.getElementById("irPageButtons");

  const visibleRecordCount = document.getElementById("visibleRecordCount");

  const rows = Array.from(
    document.querySelectorAll(".ir-data-row")
  );

  let activeFilter = "all";
  let currentPage = 1;
  let pageSize = Number(pageSizeSelect?.value || 10);

  let selectedPaymentRow = null;
  let selectedFinalRow = null;
  let reloadAfterPopup = false;


  function toNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }


  function money(value) {
    return "\u20B9 " + toNumber(value).toLocaleString(
      "en-IN",
      {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
      }
    );
  }


  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }


  function filteredRows() {
    const search = String(searchInput?.value || "")
      .trim()
      .toLowerCase();

    return rows.filter(row => {
      const rowStatus = row.dataset.status || "pending";
      const rowSearch = row.dataset.search || "";

      const statusOk =
        activeFilter === "all" ||
        rowStatus === activeFilter;

      const searchOk =
        !search ||
        rowSearch.includes(search);

      return statusOk && searchOk;
    });
  }


  function renderTable() {
    const matches = filteredRows();

    const totalPages = Math.max(
      1,
      Math.ceil(matches.length / pageSize)
    );

    if (currentPage > totalPages) {
      currentPage = totalPages;
    }

    rows.forEach(row => {
      row.classList.add("ir-hidden-row");
    });

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;

    matches.slice(start, end).forEach(row => {
      row.classList.remove("ir-hidden-row");
    });

    const noSearchRow =
      document.getElementById("irNoSearchMatchRow");

    if (noSearchRow) {
      noSearchRow.classList.toggle(
        "ir-hidden-row",
        matches.length !== 0
      );
    }

    if (visibleRecordCount) {
      visibleRecordCount.textContent =
        String(matches.length);
    }

    if (prevBtn) {
      prevBtn.disabled =
        currentPage <= 1 || matches.length === 0;
    }

    if (nextBtn) {
      nextBtn.disabled =
        currentPage >= totalPages ||
        matches.length === 0;
    }

    if (pageButtons) {
      pageButtons.innerHTML = "";

      if (matches.length > 0) {
        for (let page = 1; page <= totalPages; page++) {

          const button =
            document.createElement("button");

          button.type = "button";
          button.className = "ir-page-btn";

          if (page === currentPage) {
            button.classList.add("active");
          }

          button.textContent = String(page);

          button.addEventListener("click", function () {
            currentPage = page;
            renderTable();
          });

          pageButtons.appendChild(button);
        }
      }
    }
  }


  if (searchInput) {
    searchInput.addEventListener("input", function () {
      currentPage = 1;
      renderTable();
    });
  }


  tabs.forEach(tab => {
    tab.addEventListener("click", function () {

      tabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");

      activeFilter =
        tab.dataset.filter || "all";

      currentPage = 1;
      renderTable();
    });
  });


  if (pageSizeSelect) {
    pageSizeSelect.addEventListener("change", function () {
      pageSize = Number(pageSizeSelect.value || 10);
      currentPage = 1;
      renderTable();
    });
  }


  if (prevBtn) {
    prevBtn.addEventListener("click", function () {
      if (currentPage > 1) {
        currentPage -= 1;
        renderTable();
      }
    });
  }


  if (nextBtn) {
    nextBtn.addEventListener("click", function () {
      const matches = filteredRows();

      const totalPages = Math.max(
        1,
        Math.ceil(matches.length / pageSize)
      );

      if (currentPage < totalPages) {
        currentPage += 1;
        renderTable();
      }
    });
  }


  const dateForm =
    document.getElementById("irDateFilterForm");

  if (dateForm) {
    dateForm.addEventListener("submit", function (event) {

      const fromDate =
        document.getElementById("irFromDate")?.value || "";

      const toDate =
        document.getElementById("irToDate")?.value || "";

      if (
        (fromDate && !toDate) ||
        (!fromDate && toDate)
      ) {
        event.preventDefault();

        showPopup(
          "warning",
          "Date Filter",
          "Please select both From Date and To Date.",
          []
        );

        return;
      }

      if (fromDate && toDate) {
        const from = new Date(fromDate);
        const to = new Date(toDate);

        if (from > to) {
          event.preventDefault();

          showPopup(
            "warning",
            "Date Filter",
            "From Date cannot be later than To Date.",
            []
          );
        }
      }
    });
  }


  const popupModal =
    document.getElementById("irPopupModal");

  const popupCard =
    document.getElementById("irPopupCard");

  const popupIcon =
    document.getElementById("irPopupIcon");

  const popupTitle =
    document.getElementById("irPopupTitle");

  const popupSubtitle =
    document.getElementById("irPopupSubtitle");

  const popupList =
    document.getElementById("irPopupList");

  const popupCloseBtn =
    document.getElementById("irPopupCloseBtn");


  function showPopup(type, title, subtitle, items = []) {

    if (!popupModal || !popupCard) {
      alert(subtitle || title);
      return;
    }

    popupCard.className = "ir-modal-card";

    popupCard.classList.add(
      type === "success"
        ? "ir-modal-success"
        : type === "warning"
          ? "ir-modal-warning"
          : "ir-modal-error"
    );

    if (popupIcon) {
      popupIcon.textContent =
        type === "success"
          ? "\u2713"
          : type === "warning"
            ? "!"
            : "\u2715";
    }


    if (popupTitle) {
      popupTitle.textContent = title || "";
    }

    if (popupSubtitle) {
      popupSubtitle.textContent = subtitle || "";
    }

    if (popupList) {
      popupList.innerHTML = "";

      items.forEach(item => {
        const li = document.createElement("li");
        li.textContent = item;
        popupList.appendChild(li);
      });
    }

    popupModal.classList.add("show");
    popupModal.setAttribute("aria-hidden", "false");
  }


  function hidePopup() {
    popupModal?.classList.remove("show");
    popupModal?.setAttribute("aria-hidden", "true");

    if (reloadAfterPopup) {
      reloadAfterPopup = false;
      window.location.reload();
    }
  }


  popupCloseBtn?.addEventListener(
    "click",
    hidePopup
  );

  popupModal?.addEventListener(
    "click",
    function (event) {
      if (event.target === popupModal) {
        hidePopup();
      }
    }
  );


  /* -------------------------------------------------
     RECORD PAYMENT
     ------------------------------------------------- */

  const paymentModal =
    document.getElementById("irPaymentModal");

  const paymentInvoiceText =
    document.getElementById("irPaymentModalInvoice");

  const payInvoiceAmount =
    document.getElementById("irPayInvoiceAmount");

  const payAlreadyReceived =
    document.getElementById("irPayAlreadyReceived");

  const payCurrentOutstanding =
    document.getElementById("irPayCurrentOutstanding");

  const payReceivedNow =
    document.getElementById("irPayReceivedNow");

  const payDate =
    document.getElementById("irPayDate");

  const payUTR =
    document.getElementById("irPayUTR");

  const payRemarks =
    document.getElementById("irPayRemarks");

  const payPreviewTotal =
    document.getElementById("irPayPreviewTotal");

  const payPreviewOutstanding =
    document.getElementById("irPayPreviewOutstanding");

  const paymentSaveBtn =
    document.getElementById("irPaymentSaveBtn");

  const paymentCancelBtn =
    document.getElementById("irPaymentCancelBtn");


  function updatePaymentPreview() {

    if (!selectedPaymentRow) return;

    const invoiceAmount =
      toNumber(
        selectedPaymentRow.dataset.invoiceAmount
      );

    const alreadyReceived =
      toNumber(
        selectedPaymentRow.dataset.totalReceived
      );

    const receivedNow =
      toNumber(payReceivedNow?.value);

    const newTotal =
      alreadyReceived + receivedNow;

    const newOutstanding =
      invoiceAmount - newTotal;

    if (payPreviewTotal) {
      payPreviewTotal.textContent =
        money(newTotal);
    }

    if (payPreviewOutstanding) {
      payPreviewOutstanding.textContent =
        money(newOutstanding);

      payPreviewOutstanding.classList.toggle(
        "ir-outstanding-open",
        newOutstanding > 0
      );

      payPreviewOutstanding.classList.toggle(
        "ir-outstanding-clear",
        newOutstanding <= 0
      );
    }
  }


  function openPaymentModal(row) {

    selectedPaymentRow = row;

    const invoiceNumber =
      row.dataset.invoiceNumber || "";

    const invoiceAmount =
      toNumber(row.dataset.invoiceAmount);

    const totalReceived =
      toNumber(row.dataset.totalReceived);

    const outstanding =
      toNumber(row.dataset.outstanding);

    if (paymentInvoiceText) {
      paymentInvoiceText.textContent =
        invoiceNumber;
    }

    if (payInvoiceAmount) {
      payInvoiceAmount.textContent =
        money(invoiceAmount);
    }

    if (payAlreadyReceived) {
      payAlreadyReceived.textContent =
        money(totalReceived);
    }

    if (payCurrentOutstanding) {
      payCurrentOutstanding.textContent =
        money(outstanding);
    }

    if (payReceivedNow) {
      payReceivedNow.value = "";
    }

    if (payUTR) {
      payUTR.value = "";
    }

    if (payRemarks) {
      payRemarks.value = "";
    }

    updatePaymentPreview();

    paymentModal?.classList.add("show");
    paymentModal?.setAttribute(
      "aria-hidden",
      "false"
    );

    setTimeout(() => {
      payReceivedNow?.focus();
    }, 50);
  }


  function closePaymentModal() {
    paymentModal?.classList.remove("show");
    paymentModal?.setAttribute(
      "aria-hidden",
      "true"
    );

    selectedPaymentRow = null;
  }


  document.querySelectorAll(
    ".ir-record-payment-btn"
  ).forEach(button => {

    button.addEventListener("click", function () {
      const row = button.closest(".ir-data-row");

      if (row) {
        openPaymentModal(row);
      }
    });

  });


  payReceivedNow?.addEventListener(
    "input",
    updatePaymentPreview
  );


  paymentCancelBtn?.addEventListener(
    "click",
    closePaymentModal
  );


  paymentModal?.addEventListener(
    "click",
    function (event) {
      if (event.target === paymentModal) {
        closePaymentModal();
      }
    }
  );


  paymentSaveBtn?.addEventListener(
    "click",
    async function () {

      if (!selectedPaymentRow) return;

      const invoiceNumber =
        selectedPaymentRow.dataset.invoiceNumber;

      const amount =
        toNumber(payReceivedNow?.value);

      const paymentDate =
        payDate?.value || "";

      const utr =
        String(payUTR?.value || "").trim();

      const remarks =
        String(payRemarks?.value || "").trim();

      if (amount <= 0) {
        showPopup(
          "warning",
          "Payment Validation",
          "Received Now must be greater than zero.",
          []
        );
        return;
      }

      if (!paymentDate) {
        showPopup(
          "warning",
          "Payment Validation",
          "Payment Date is required.",
          []
        );
        return;
      }

      if (!utr) {
        showPopup(
          "warning",
          "Payment Validation",
          "UTR / Reference Number is required.",
          []
        );
        return;
      }

      paymentSaveBtn.disabled = true;
      paymentSaveBtn.classList.add("ir-loading");

      try {

        const response = await fetch(
          "/api/invoice-reconciliation/payment",
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json"
            },
            body: JSON.stringify({
              invoice_number: invoiceNumber,
              received_amount: amount,
              payment_date: paymentDate,
              utr_ref_no: utr,
              payment_remarks: remarks
            })
          }
        );

        const data = await response.json();

        if (!response.ok || !data.success) {
          throw new Error(
            data.message ||
            "Payment could not be recorded."
          );
        }

        closePaymentModal();

        reloadAfterPopup = true;

        showPopup(
          "success",
          "Payment Recorded",
          data.message ||
          "Hospital payment registered successfully.",
          [
            "Invoice: " + invoiceNumber,
            "Received Now: " +
              money(data.payment?.received_now),
            "Total Received: " +
              money(data.payment?.total_received),
            "Outstanding: " +
              money(data.payment?.outstanding_amount),
            "Payment Status: " +
              (data.payment?.payment_status || "")
          ]
        );

      } catch (error) {

        showPopup(
          "error",
          "Payment Failed",
          error.message ||
          "Unable to record hospital payment.",
          []
        );

      } finally {

        paymentSaveBtn.disabled = false;
        paymentSaveBtn.classList.remove(
          "ir-loading"
        );
      }
    }
  );


  /* -------------------------------------------------
     PAYMENT HISTORY
     ------------------------------------------------- */

  const historyModal =
    document.getElementById("irHistoryModal");

  const historyInvoice =
    document.getElementById("irHistoryInvoice");

  const historySummary =
    document.getElementById("irHistorySummary");

  const historyBody =
    document.getElementById("irHistoryBody");

  const historyCloseBtn =
    document.getElementById("irHistoryCloseBtn");


  async function openHistoryModal(row) {

    const invoiceNumber =
      row.dataset.invoiceNumber || "";

    if (historyInvoice) {
      historyInvoice.textContent =
        invoiceNumber;
    }

    if (historySummary) {
      historySummary.innerHTML =
        "<div><span>Loading</span><strong>Please wait...</strong></div>";
    }

    if (historyBody) {
      historyBody.innerHTML = "";
    }

    historyModal?.classList.add("show");
    historyModal?.setAttribute(
      "aria-hidden",
      "false"
    );

    try {

      const response = await fetch(
        "/api/invoice-reconciliation/payment-history/" +
        encodeURIComponent(invoiceNumber)
      );

      const data = await response.json();

      if (!response.ok || !data.success) {
        throw new Error(
          data.message ||
          "Payment history could not be loaded."
        );
      }

      const inv = data.invoice || {};

      if (historySummary) {
        historySummary.innerHTML = `
          <div>
            <span>Invoice Amount</span>
            <strong>${money(inv.invoice_amount)}</strong>
          </div>

          <div>
            <span>Total Received</span>
            <strong>${money(inv.total_received)}</strong>
          </div>

          <div>
            <span>Outstanding</span>
            <strong class="${
              toNumber(inv.outstanding_amount) > 0
                ? "ir-outstanding-open"
                : "ir-outstanding-clear"
            }">
              ${money(inv.outstanding_amount)}
            </strong>
          </div>

          <div>
            <span>Payment Status</span>
            <strong>${escapeHtml(inv.payment_status || "")}</strong>
          </div>
        `;
      }

      if (historyBody) {

        const payments =
          Array.isArray(data.payments)
            ? data.payments
            : [];

        if (!payments.length) {

          historyBody.innerHTML = `
            <tr>
              <td colspan="6" class="ir-empty">
                No hospital payments recorded.
              </td>
            </tr>
          `;

        } else {

          historyBody.innerHTML =
            payments.map(payment => `
              <tr>
                <td>${escapeHtml(payment.payment_seq)}</td>
                <td>${escapeHtml(payment.payment_date || "-")}</td>
                <td>${money(payment.received_amount)}</td>
                <td>${escapeHtml(payment.utr_ref_no || "-")}</td>
                <td>${escapeHtml(payment.payment_status || "-")}</td>
                <td>${escapeHtml(payment.remarks || "-")}</td>
              </tr>
            `).join("");
        }
      }

    } catch (error) {

      closeHistoryModal();

      showPopup(
        "error",
        "Payment History",
        error.message ||
        "Unable to load payment history.",
        []
      );
    }
  }


  function closeHistoryModal() {
    historyModal?.classList.remove("show");
    historyModal?.setAttribute(
      "aria-hidden",
      "true"
    );
  }


  document.querySelectorAll(
    ".ir-payment-history-btn"
  ).forEach(button => {

    button.addEventListener("click", function () {
      const row = button.closest(".ir-data-row");

      if (row) {
        openHistoryModal(row);
      }
    });

  });


  historyCloseBtn?.addEventListener(
    "click",
    closeHistoryModal
  );


  historyModal?.addEventListener(
    "click",
    function (event) {
      if (event.target === historyModal) {
        closeHistoryModal();
      }
    }
  );


  /* -------------------------------------------------
     FINAL RECONCILIATION
     ------------------------------------------------- */

  const finalModal =
    document.getElementById("irFinalReconModal");

  const finalInvoiceText =
    document.getElementById("irFinalReconInvoice");

  const finalInvoiceAmount =
    document.getElementById("irFinalInvoiceAmount");

  const finalTotalReceived =
    document.getElementById("irFinalTotalReceived");

  const finalOutstanding =
    document.getElementById("irFinalOutstanding");

  const finalRemarks =
    document.getElementById("irFinalRemarks");

  const finalCancelBtn =
    document.getElementById("irFinalCancelBtn");

  const finalConfirmBtn =
    document.getElementById("irFinalConfirmBtn");


  function openFinalModal(row) {

    selectedFinalRow = row;

    const invoiceNumber =
      row.dataset.invoiceNumber || "";

    const invoiceAmount =
      toNumber(row.dataset.invoiceAmount);

    const totalReceived =
      toNumber(row.dataset.totalReceived);

    const outstanding =
      toNumber(row.dataset.outstanding);

    if (finalInvoiceText) {
      finalInvoiceText.textContent =
        invoiceNumber;
    }

    if (finalInvoiceAmount) {
      finalInvoiceAmount.textContent =
        money(invoiceAmount);
    }

    if (finalTotalReceived) {
      finalTotalReceived.textContent =
        money(totalReceived);
    }

    if (finalOutstanding) {
      finalOutstanding.textContent =
        money(outstanding);

      finalOutstanding.classList.toggle(
        "ir-outstanding-open",
        outstanding > 0
      );

      finalOutstanding.classList.toggle(
        "ir-outstanding-clear",
        outstanding <= 0
      );
    }

    if (finalRemarks) {
      finalRemarks.value = "";
    }

    finalModal?.classList.add("show");
    finalModal?.setAttribute(
      "aria-hidden",
      "false"
    );
  }


  function closeFinalModal() {

    finalModal?.classList.remove("show");
    finalModal?.setAttribute(
      "aria-hidden",
      "true"
    );

    selectedFinalRow = null;
  }


  document.querySelectorAll(
    ".ir-final-recon-btn"
  ).forEach(button => {

    button.addEventListener("click", function () {
      const row = button.closest(".ir-data-row");

      if (row) {
        openFinalModal(row);
      }
    });

  });


  finalCancelBtn?.addEventListener(
    "click",
    closeFinalModal
  );


  finalModal?.addEventListener(
    "click",
    function (event) {
      if (event.target === finalModal) {
        closeFinalModal();
      }
    }
  );


  finalConfirmBtn?.addEventListener(
    "click",
    async function () {

      if (!selectedFinalRow) return;

      const invoiceNumber =
        selectedFinalRow.dataset.invoiceNumber;

      const remarks =
        String(finalRemarks?.value || "").trim();

      const fromDate =
        document.getElementById("irFromDate")?.value || "";

      const toDate =
        document.getElementById("irToDate")?.value || "";

      finalConfirmBtn.disabled = true;
      finalConfirmBtn.classList.add("ir-loading");

      try {

        const response = await fetch(
          "/api/invoice-reconciliation/save",
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json"
            },
            body: JSON.stringify({
              invoice_number: invoiceNumber,
              remarks: remarks,
              from_date: fromDate,
              to_date: toDate
            })
          }
        );

        const data = await response.json();

        if (!response.ok || !data.success) {
          throw new Error(
            data.message ||
            "Final reconciliation could not be completed."
          );
        }

        closeFinalModal();

        reloadAfterPopup = true;

        const result =
          data.reconciliation || {};

        showPopup(
          "success",
          "Final Reconciliation Completed",
          data.message ||
          "Invoice has been reconciled.",
          [
            "Invoice: " + invoiceNumber,
            "Total Received: " +
              money(result.total_received),
            "Difference: " +
              money(result.difference),
            "Recon Status: " +
              (result.display_recon_status || ""),
            "Workflow: " +
              (result.workflow_status || "")
          ]
        );

      } catch (error) {

        showPopup(
          "error",
          "Final Reconciliation Not Completed",
          error.message ||
          "The invoice cannot be finally reconciled.",
          []
        );

      } finally {

        finalConfirmBtn.disabled = false;
        finalConfirmBtn.classList.remove(
          "ir-loading"
        );
      }
    }
  );


  /* -------------------------------------------------
     OTHER CONTROLS
     ------------------------------------------------- */

  document.getElementById(
    "resetNotesBtn"
  )?.addEventListener("click", function () {

    const notes =
      document.getElementById("recon_notes");

    if (notes) {
      notes.value = "";
    }
  });


  document.addEventListener(
    "keydown",
    function (event) {

      if (event.key !== "Escape") return;

      if (paymentModal?.classList.contains("show")) {
        closePaymentModal();
        return;
      }

      if (historyModal?.classList.contains("show")) {
        closeHistoryModal();
        return;
      }

      if (finalModal?.classList.contains("show")) {
        closeFinalModal();
        return;
      }

      if (popupModal?.classList.contains("show")) {
        hidePopup();
      }
    }
  );


  renderTable();

});
