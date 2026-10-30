(function () {
  const page = document.getElementById("invoicePage");
  const hospitalEl = document.getElementById("hospital_short_name");
  const tableBody = document.getElementById("invoiceTableBody");
  const selectAllEl = document.getElementById("selectAllRows");
  const toastWrap = document.getElementById("toastWrap");

  const toolbarSelectedCount = document.getElementById("toolbarSelectedCount");
  const toolbarHospitalName = document.getElementById("toolbarHospitalName");

  const summaryHospitalName = document.getElementById("summaryHospitalName");
  const summaryStatePill = document.getElementById("summaryStatePill");
  const summaryInvoiceDate = document.getElementById("summaryInvoiceDate");
  const summarySelectedRecords = document.getElementById("summarySelectedRecords");
  const summaryBaseAmount = document.getElementById("summaryBaseAmount");
  const summaryGstAmount = document.getElementById("summaryGstAmount");
  const summaryTdsAmount = document.getElementById("summaryTdsAmount");
  const summaryNetAmount = document.getElementById("summaryNetAmount");

  const invoiceNumberEl = document.getElementById("invoice_number");
  const displayHospitalNameEl = document.getElementById("display_hospital_name");
  const hospitalAddressEl = document.getElementById("hospital_address");
  const hospitalGstEl = document.getElementById("hospital_gst");
  const hospitalPanEl = document.getElementById("hospital_pan");
  const hospitalCountryEl = document.getElementById("hospital_country");
  const hospitalStateEl = document.getElementById("hospital_state");
  const placeOfSupplyEl = document.getElementById("placeof_supply");
  const hospitalCityEl = document.getElementById("hospital_city");
  const hospitalPincodeEl = document.getElementById("hospital_pincode");

  const btnSearch = document.getElementById("btnSearch");
  const btnClearTop = document.getElementById("btnClearTop");
  const btnClearSelection = document.getElementById("btnClearSelection");
  const btnGenerateTop = document.getElementById("btnGenerateTop");
  const btnGenerateBottom = document.getElementById("btnGenerateBottom");

  const generateConfirmModal = document.getElementById("generateConfirmModal");
  const modalSelectedCount = document.getElementById("modalSelectedCount");
  const modalHospitalName = document.getElementById("modalHospitalName");
  const modalInvoiceDate = document.getElementById("modalInvoiceDate");
  const modalNetAmount = document.getElementById("modalNetAmount");
  const btnCancelGenerate = document.getElementById("btnCancelGenerate");
  const btnConfirmGenerate = document.getElementById("btnConfirmGenerate");

  let initialRecords = [];
  const initialRecordsEl = document.getElementById("initial-invoice-records");
  if (initialRecordsEl && initialRecordsEl.textContent) {
    try {
      initialRecords = JSON.parse(initialRecordsEl.textContent);
    } catch (e) {
      console.error("Unable to parse initial invoice records:", e);
      initialRecords = [];
    }
  }

  function toNumber(value) {
    const n = parseFloat(String(value || "").replace(/[^0-9.\-]/g, ""));
    return isNaN(n) ? 0 : n;
  }

  function formatMoney(value) {
    return toNumber(value).toLocaleString("en-IN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }

  function getTodayDateOnly() {
    const now = new Date();
    const day = String(now.getDate()).padStart(2, "0");
    const month = String(now.getMonth() + 1).padStart(2, "0");
    const year = now.getFullYear();
    return day + "-" + month + "-" + year;
  }

  function getTodayDateForApi() {
    const now = new Date();
    const day = String(now.getDate()).padStart(2, "0");
    const month = String(now.getMonth() + 1).padStart(2, "0");
    const year = now.getFullYear();
    return year + "-" + month + "-" + day;
  }

  function getMonthShortName() {
    const months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
    return months[new Date().getMonth()];
  }

  function buildInvoiceNumberPreview(hospitalName) {
    const cleaned = String(hospitalName || "")
      .trim()
      .replace(/\s+/g, "")
      .replace(/[^A-Za-z0-9]/g, "")
      .toUpperCase();

    if (!cleaned) return "";

    const year = new Date().getFullYear();
    return cleaned + "-" + getMonthShortName() + "-" + year + "-01";
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function showToast(message, type) {
    if (!toastWrap) return;

    const toast = document.createElement("div");
    toast.className = "inv-toast " + (type === "success" ? "inv-toast-success" : "inv-toast-error");
    toast.textContent = message || (type === "success" ? "Success" : "Something went wrong");
    toastWrap.appendChild(toast);

    setTimeout(function () {
      toast.style.opacity = "0";
      toast.style.transform = "translateY(-8px) translateX(12px)";
      toast.style.transition = "all .25s ease";
    }, 2600);

    setTimeout(function () {
      if (toast.parentNode) {
        toast.parentNode.removeChild(toast);
      }
    }, 3000);
  }

  function setLoading(button, loading) {
    if (!button) return;
    if (loading) {
      button.dataset.originalText = button.textContent;
      button.innerHTML = '<span class="inv-spinner"></span>';
      button.disabled = true;
    } else {
      button.innerHTML = button.dataset.originalText || button.textContent;
      delete button.dataset.originalText;
      button.disabled = false;
    }
  }

  function getRowChecks() {
    return Array.from(document.querySelectorAll(".row-check"));
  }

  function getSelectedRows() {
    return Array.from(tableBody.querySelectorAll("tr")).filter(function (row) {
      const cb = row.querySelector(".row-check");
      return cb && cb.checked;
    });
  }

  function getCurrentHospitalName() {
    return hospitalEl && hospitalEl.value ? hospitalEl.value : "Not Selected";
  }

  function setFieldValue(el, value) {
    if (el) el.value = value || "";
  }

  function getHospitalDetailFromRecord(record, keys) {
    if (!record) return "";
    for (let i = 0; i < keys.length; i++) {
      if (record[keys[i]] !== undefined && record[keys[i]] !== null && String(record[keys[i]]).trim() !== "") {
        return String(record[keys[i]]);
      }
    }
    return "";
  }

  function populateHeaderDetails(details, records) {
    const firstRecord = records && records.length ? records[0] : {};
    const selectedHospital = hospitalEl && hospitalEl.value ? hospitalEl.value : "";

    const invoiceNumber =
      (details && details.invoice_number) ||
      getHospitalDetailFromRecord(firstRecord, ["invoice_number"]) ||
      buildInvoiceNumberPreview(selectedHospital);

    const hospitalName =
      (details && details.hospital_name) ||
      getHospitalDetailFromRecord(firstRecord, ["hospital_name", "hospital", "hospital_short_name"]) ||
      selectedHospital;

    const hospitalAddress =
      (details && details.hospital_address) ||
      getHospitalDetailFromRecord(firstRecord, ["hospital_address", "partner_address"]);

    const hospitalGst =
      (details && details.hospital_gst) ||
      getHospitalDetailFromRecord(firstRecord, ["hospital_gst", "gst_number"]);

    const hospitalPan =
      (details && details.hospital_pan) ||
      getHospitalDetailFromRecord(firstRecord, ["hospital_pan", "pan_number"]);

    const hospitalCountry =
      (details && details.hospital_country) ||
      getHospitalDetailFromRecord(firstRecord, ["hospital_country", "partner_country"]);

    const hospitalState =
      (details && details.hospital_state) ||
      getHospitalDetailFromRecord(firstRecord, ["hospital_state", "partner_state"]);

    const placeOfSupply =
      (details && details.placeof_supply) ||
      getHospitalDetailFromRecord(firstRecord, ["placeof_supply"]);

    const hospitalCity =
      (details && details.hospital_city) ||
      getHospitalDetailFromRecord(firstRecord, ["hospital_city", "partner_city"]);

    const hospitalPincode =
      (details && details.hospital_pincode) ||
      getHospitalDetailFromRecord(firstRecord, ["hospital_pincode", "partner_pincode"]);

    setFieldValue(invoiceNumberEl, invoiceNumber);
    setFieldValue(displayHospitalNameEl, hospitalName);
    setFieldValue(hospitalAddressEl, hospitalAddress);
    setFieldValue(hospitalGstEl, hospitalGst);
    setFieldValue(hospitalPanEl, hospitalPan);
    setFieldValue(hospitalCountryEl, hospitalCountry);
    setFieldValue(hospitalStateEl, hospitalState);
    setFieldValue(placeOfSupplyEl, placeOfSupply);
    setFieldValue(hospitalCityEl, hospitalCity);
    setFieldValue(hospitalPincodeEl, hospitalPincode);
  }

  function resetHeaderDetails() {
    setFieldValue(invoiceNumberEl, "");
    setFieldValue(displayHospitalNameEl, "");
    setFieldValue(hospitalAddressEl, "");
    setFieldValue(hospitalGstEl, "");
    setFieldValue(hospitalPanEl, "");
    setFieldValue(hospitalCountryEl, "");
    setFieldValue(hospitalStateEl, "");
    setFieldValue(placeOfSupplyEl, "");
    setFieldValue(hospitalCityEl, "");
    setFieldValue(hospitalPincodeEl, "");
  }

  function syncHeaderPreviewOnly() {
    const selectedHospital = hospitalEl && hospitalEl.value ? hospitalEl.value : "";
    setFieldValue(displayHospitalNameEl, selectedHospital);
    setFieldValue(invoiceNumberEl, selectedHospital ? buildInvoiceNumberPreview(selectedHospital) : "");
  }

  function bindRowEvents() {
    getRowChecks().forEach(function (cb) {
      cb.addEventListener("change", function () {
        const row = cb.closest("tr");
        if (row) {
          row.classList.toggle("is-selected", cb.checked);
          calculateSingleRow(row);
        }
        updateSummaryFromSelection();
      });
    });

    Array.from(document.querySelectorAll(".base-amount-input")).forEach(function (input) {
      input.addEventListener("input", function () {
        const row = input.closest("tr");
        if (!row) return;
        input.classList.toggle("is-edited", toNumber(input.value) > 0);
        calculateSingleRow(row);
        updateSummaryFromSelection();
      });
    });
  }

  function renderEmptyRow(message) {
    tableBody.innerHTML = '<tr class="inv-empty-row"><td colspan="14">' + escapeHtml(message) + '</td></tr>';
    if (selectAllEl) {
      selectAllEl.checked = false;
    }
    updateSummaryFromSelection();
  }

  function renderRows(records) {
    if (!records || !records.length) {
      renderEmptyRow("No invoice candidate records found for selected hospital");
      return;
    }

    let html = "";
    records.forEach(function (row, idx) {
      html += ''
        + '<tr'
        + ' data-lead-no="' + escapeHtml(row.lead_no || "") + '"'
        + ' data-passport-no="' + escapeHtml(row.passport_no || "") + '"'
        + ' data-patient-name="' + escapeHtml(row.patient_name || "") + '"'
        + ' data-status="' + escapeHtml(row.status || "") + '"'
        + ' data-status-date="' + escapeHtml(row.status_date || "") + '"'
        + ' data-sac-code="' + escapeHtml(row.sac_code || "999723") + '"'
        + ' data-gst-percentage="' + escapeHtml(row.gst_percentage || "18%") + '"'
        + ' data-tds-percentage="' + escapeHtml(String(row.tds_percentage || "0")) + '"'
        + ' data-inv-currency="' + escapeHtml(row.inv_currency || "INR") + '"'
        + ' data-hospital="' + escapeHtml(row.hospital || row.hospital_short_name || "") + '"'
        + '>'
        + '  <td><input type="checkbox" class="inv-check row-check"></td>'
        + '  <td class="inv-index">' + (idx + 1) + '</td>'
        + '  <td>' + escapeHtml(row.lead_no || "") + '</td>'
        + '  <td>' + escapeHtml(row.passport_no || "") + '</td>'
        + '  <td>' + escapeHtml(row.patient_name || "") + '</td>'
        + '  <td>' + escapeHtml(row.status || "") + '</td>'
        + '  <td class="inv-money"><input type="number" class="inv-amount-input base-amount-input" value="0.00" min="0" step="0.01"></td>'
        + '  <td>' + escapeHtml(row.sac_code || "999723") + '</td>'
        + '  <td class="gst-percent">' + escapeHtml(row.gst_percentage || "18%") + '</td>'
        + '  <td class="tds-percent">' + escapeHtml(String(row.tds_percentage || "0")) + '</td>'
        + '  <td class="inv-money gst-amount">0.00</td>'
        + '  <td class="inv-money tds-amount">0.00</td>'
        + '  <td class="inv-money net-amount">0.00</td>'
        + '  <td>' + escapeHtml(row.hospital || row.hospital_short_name || "") + '</td>'
        + '</tr>';
    });

    tableBody.innerHTML = html;
    bindRowEvents();

    Array.from(tableBody.querySelectorAll("tr")).forEach(function (row) {
      calculateSingleRow(row);
    });

    if (selectAllEl) {
      selectAllEl.checked = false;
    }
    updateSummaryFromSelection();
  }

  function clearFilters() {
    if (hospitalEl) hospitalEl.value = "";
    syncHospitalText();
    resetHeaderDetails();
  }

  function clearSelection(showMessage) {
    getRowChecks().forEach(function (cb) {
      cb.checked = false;
      const row = cb.closest("tr");
      if (row) {
        row.classList.remove("is-selected");
      }
    });
    if (selectAllEl) {
      selectAllEl.checked = false;
    }
    updateSummaryFromSelection();
    if (showMessage !== false) {
      showToast("Selection cleared successfully.", "success");
    }
  }

  function calculateSingleRow(row) {
    const baseInput = row.querySelector(".base-amount-input");
    const gstPercentEl = row.querySelector(".gst-percent");
    const tdsPercentEl = row.querySelector(".tds-percent");
    const gstAmountEl = row.querySelector(".gst-amount");
    const tdsAmountEl = row.querySelector(".tds-amount");
    const netAmountEl = row.querySelector(".net-amount");

    const baseAmount = toNumber(baseInput ? baseInput.value : "0");
    const gstPercent = toNumber(gstPercentEl ? gstPercentEl.textContent : "18");
    const tdsPercent = toNumber(tdsPercentEl ? tdsPercentEl.textContent : "0");

    const gstAmount = (baseAmount * gstPercent) / 100;
    const tdsAmount = (baseAmount * tdsPercent) / 100;
    const netAmount = baseAmount + gstAmount - tdsAmount;

    if (gstAmountEl) gstAmountEl.textContent = formatMoney(gstAmount);
    if (tdsAmountEl) tdsAmountEl.textContent = formatMoney(tdsAmount);
    if (netAmountEl) netAmountEl.textContent = formatMoney(netAmount);
  }

  function syncHospitalText() {
    const hospitalName = getCurrentHospitalName();
    toolbarHospitalName.textContent = hospitalName;
    summaryHospitalName.textContent = hospitalName;
  }

  function updateSummaryFromSelection() {
    const rows = getSelectedRows();

    let totalBase = 0;
    let totalGst = 0;
    let totalTds = 0;
    let totalNet = 0;
    let validForGeneration = rows.length > 0;

    rows.forEach(function (row) {
      calculateSingleRow(row);

      const baseInput = row.querySelector(".base-amount-input");
      const base = toNumber(baseInput ? baseInput.value : 0);
      const gst = toNumber(row.querySelector(".gst-amount") ? row.querySelector(".gst-amount").textContent : 0);
      const tds = toNumber(row.querySelector(".tds-amount") ? row.querySelector(".tds-amount").textContent : 0);
      const net = toNumber(row.querySelector(".net-amount") ? row.querySelector(".net-amount").textContent : 0);

      totalBase += base;
      totalGst += gst;
      totalTds += tds;
      totalNet += net;

      if (base <= 0 || net <= 0) {
        validForGeneration = false;
      }
    });

    toolbarSelectedCount.textContent = String(rows.length);
    summarySelectedRecords.textContent = String(rows.length);
    summaryBaseAmount.textContent = formatMoney(totalBase);
    summaryGstAmount.textContent = formatMoney(totalGst);
    summaryTdsAmount.textContent = formatMoney(totalTds);
    summaryNetAmount.textContent = formatMoney(totalNet);
    summaryInvoiceDate.textContent = getTodayDateOnly();

    btnGenerateTop.disabled = !validForGeneration;
    btnGenerateBottom.disabled = !validForGeneration;

    if (rows.length > 0 && validForGeneration) {
      summaryStatePill.textContent = "Ready to Generate";
      summaryStatePill.className = "inv-state-pill ready";
    } else {
      summaryStatePill.textContent = "Pending Selection";
      summaryStatePill.className = "inv-state-pill pending";
    }
  }

  function collectSelectedRowsPayload() {
    const rows = getSelectedRows();
    return rows.map(function (row) {
      const amountInput = row.querySelector(".base-amount-input");
      const gstPercentEl = row.querySelector(".gst-percent");
      const tdsPercentEl = row.querySelector(".tds-percent");
      const gstAmountEl = row.querySelector(".gst-amount");
      const tdsAmountEl = row.querySelector(".tds-amount");
      const netAmountEl = row.querySelector(".net-amount");

      return {
        lead_no: row.getAttribute("data-lead-no") || "",
        passport_no: row.getAttribute("data-passport-no") || "",
        patient_name: row.getAttribute("data-patient-name") || "",
        status: row.getAttribute("data-status") || "",
        status_date: row.getAttribute("data-status-date") || "",
        invoice_amount: toNumber(amountInput ? amountInput.value : 0),
        inv_amount: toNumber(amountInput ? amountInput.value : 0),
        totalinv_amount: toNumber(amountInput ? amountInput.value : 0),
        sac_code: row.getAttribute("data-sac-code") || "999723",
        gst_percentage: gstPercentEl ? gstPercentEl.textContent : (row.getAttribute("data-gst-percentage") || "18%"),
        tds_percentage: tdsPercentEl ? tdsPercentEl.textContent : (row.getAttribute("data-tds-percentage") || "0"),
        gst_amount: toNumber(gstAmountEl ? gstAmountEl.textContent : 0),
        totalgst_amount: toNumber(gstAmountEl ? gstAmountEl.textContent : 0),
        tds_amount: toNumber(tdsAmountEl ? tdsAmountEl.textContent : 0),
        totaltds_amount: toNumber(tdsAmountEl ? tdsAmountEl.textContent : 0),
        net_amount: toNumber(netAmountEl ? netAmountEl.textContent : 0),
        netinv_amount: toNumber(netAmountEl ? netAmountEl.textContent : 0),
        inv_currency: row.getAttribute("data-inv-currency") || "INR",
        hospital: row.getAttribute("data-hospital") || getCurrentHospitalName()
      };
    });
  }

  async function searchRecords() {
    const hospital = hospitalEl ? hospitalEl.value.trim() : "";

    if (!hospital) {
      showToast("Please select Hospital Short Name.", "error");
      if (hospitalEl) hospitalEl.focus();
      return;
    }

    const params = new URLSearchParams({
      hospital_short_name: hospital
    });

    try {
      page.classList.add("inv-loading");
      setLoading(btnSearch, true);

      const response = await fetch("/api/invoicegeneration/search?" + params.toString(), {
        method: "GET",
        headers: { "X-Requested-With": "XMLHttpRequest" }
      });

      const data = await response.json();

      if (!response.ok || !data.success) {
        throw new Error(data.message || "Unable to load records.");
      }

      renderRows(data.records || []);
      syncHospitalText();
      populateHeaderDetails(data.hospital_details || data.header || {}, data.records || []);

      if (data.records && data.records.length) {
        showToast("Invoice candidate records loaded successfully.", "success");
      } else {
        showToast("No invoice candidate records found.", "error");
      }
    } catch (err) {
      console.error(err);
      renderEmptyRow("Unable to load invoice candidate records");
      syncHospitalText();
      syncHeaderPreviewOnly();
      showToast(err.message || "Unable to load records.", "error");
    } finally {
      page.classList.remove("inv-loading");
      setLoading(btnSearch, false);
    }
  }

  function clearPage() {
    clearFilters();
    clearSelection(false);
    renderEmptyRow("Select Hospital Short Name and click Search to load records");
    summaryInvoiceDate.textContent = getTodayDateOnly();
    updateSummaryFromSelection();
    showToast("Page cleared successfully.", "success");
  }

  function openGenerateModal() {
    const rows = getSelectedRows();
    if (!rows.length) {
      showToast("Please select at least one record.", "error");
      return;
    }

    let invalidAmount = false;
    rows.forEach(function (row) {
      calculateSingleRow(row);
      const amountInput = row.querySelector(".base-amount-input");
      const amt = toNumber(amountInput ? amountInput.value : 0);
      if (amt <= 0) {
        invalidAmount = true;
      }
    });

    updateSummaryFromSelection();

    if (invalidAmount) {
      showToast("One or more selected rows have zero invoice amount. Please enter a valid amount greater than zero.", "error");
      return;
    }

    modalSelectedCount.textContent = String(rows.length);
    modalHospitalName.textContent = getCurrentHospitalName();
    modalInvoiceDate.textContent = getTodayDateOnly();
    modalNetAmount.textContent = summaryNetAmount.textContent;
    generateConfirmModal.classList.add("show");
    generateConfirmModal.setAttribute("aria-hidden", "false");
  }

  function closeGenerateModal() {
    generateConfirmModal.classList.remove("show");
    generateConfirmModal.setAttribute("aria-hidden", "true");
  }

  async function confirmGenerate() {
    const selectedRows = collectSelectedRowsPayload();

    if (!selectedRows.length) {
      closeGenerateModal();
      showToast("Please select at least one record.", "error");
      return;
    }

    const payload = {
      hospital_short_name: hospitalEl ? hospitalEl.value.trim() : "",
      invoice_number: invoiceNumberEl ? invoiceNumberEl.value.trim() : "",
      invoice_date: getTodayDateForApi(),
      header: {
        hospital_name: displayHospitalNameEl ? displayHospitalNameEl.value.trim() : "",
        hospital_address: hospitalAddressEl ? hospitalAddressEl.value.trim() : "",
        hospital_gst: hospitalGstEl ? hospitalGstEl.value.trim() : "",
        hospital_pan: hospitalPanEl ? hospitalPanEl.value.trim() : "",
        hospital_country: hospitalCountryEl ? hospitalCountryEl.value.trim() : "",
        hospital_state: hospitalStateEl ? hospitalStateEl.value.trim() : "",
        placeof_supply: placeOfSupplyEl ? placeOfSupplyEl.value.trim() : "",
        hospital_city: hospitalCityEl ? hospitalCityEl.value.trim() : "",
        hospital_pincode: hospitalPincodeEl ? hospitalPincodeEl.value.trim() : ""
      },
      selected_rows: selectedRows
    };

    try {
      setLoading(btnConfirmGenerate, true);

      const response = await fetch("/api/invoicegeneration/generate", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest"
        },
        body: JSON.stringify(payload)
      });

      const data = await response.json();

      if (!response.ok || !data.success) {
        throw new Error(data.message || "Invoice generation failed.");
      }

      if (data.invoice_number) {
        setFieldValue(invoiceNumberEl, data.invoice_number);
      }

      closeGenerateModal();
      showToast(data.message || "Invoice generated successfully.", "success");

      await searchRecords();
      clearSelection(false);

      if (selectAllEl) {
        selectAllEl.checked = false;
      }

      updateSummaryFromSelection();

    } catch (err) {
      console.error(err);
      showToast(err.message || "Invoice generation failed.", "error");
    } finally {
      setLoading(btnConfirmGenerate, false);
    }
  }

  if (btnSearch) {
    btnSearch.addEventListener("click", searchRecords);
  }

  if (btnClearTop) {
    btnClearTop.addEventListener("click", clearPage);
  }

  if (btnClearSelection) {
    btnClearSelection.addEventListener("click", function () {
      clearSelection(true);
    });
  }

  if (btnGenerateTop) {
    btnGenerateTop.addEventListener("click", openGenerateModal);
  }

  if (btnGenerateBottom) {
    btnGenerateBottom.addEventListener("click", openGenerateModal);
  }

  if (btnCancelGenerate) {
    btnCancelGenerate.addEventListener("click", closeGenerateModal);
  }

  if (btnConfirmGenerate) {
    btnConfirmGenerate.addEventListener("click", confirmGenerate);
  }

  if (generateConfirmModal) {
    generateConfirmModal.addEventListener("click", function (e) {
      if (e.target === generateConfirmModal) {
        closeGenerateModal();
      }
    });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && generateConfirmModal.classList.contains("show")) {
      closeGenerateModal();
    }
  });

  if (selectAllEl) {
    selectAllEl.addEventListener("change", function () {
      getRowChecks().forEach(function (cb) {
        cb.checked = selectAllEl.checked;
        const row = cb.closest("tr");
        if (row) {
          row.classList.toggle("is-selected", cb.checked);
          calculateSingleRow(row);
        }
      });
      updateSummaryFromSelection();
    });
  }

  if (hospitalEl) {
    hospitalEl.addEventListener("change", function () {
      syncHospitalText();
      syncHeaderPreviewOnly();
    });
  }

  syncHospitalText();
  bindRowEvents();

  Array.from(tableBody.querySelectorAll("tr")).forEach(function (row) {
    calculateSingleRow(row);
  });

  if (initialRecords && initialRecords.length) {
    populateHeaderDetails({}, initialRecords);
  } else {
    syncHeaderPreviewOnly();
  }

  summaryInvoiceDate.textContent = getTodayDateOnly();
  updateSummaryFromSelection();
})();
