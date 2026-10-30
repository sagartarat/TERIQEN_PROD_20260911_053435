(function () {
  const page = document.getElementById("alertPage");
  const tableBody = document.getElementById("alertTableBody");
  const selectAllEl = document.getElementById("selectAllAlerts");
  const quickSearch = document.getElementById("quickSearch");
  const toastWrap = document.getElementById("toastWrap");
  const drawer = document.getElementById("alertDrawer");
  const drawerOverlay = document.getElementById("alertDrawerOverlay");
  const snoozeModalOverlay = document.getElementById("snoozeModalOverlay");
  const snoozeValueInput = document.getElementById("snoozeValue");
  const snoozeUnitDisplay = document.getElementById("snoozeUnitDisplay");
  const snoozeValueError = document.getElementById("snoozeValueError");
  const actionUrl = page ? String(page.dataset.alertActionUrl || "").trim() : "";

  if (!page || !tableBody) return;

  const filterIds = [
    "alert_name", "severity", "alert_status", "workflow_status",
    "assigned_user", "agency", "country", "passport_no",
    "ageing_from", "ageing_to", "from_date", "to_date"
  ];

  const recordsById = new Map();
  const initialRecordsEl = document.getElementById("initial-alert-records");

  if (initialRecordsEl) {
    try {
      const records = JSON.parse(initialRecordsEl.textContent || "[]");
      if (Array.isArray(records)) {
        records.forEach(function (record) {
          const alertId = String(record.alert_id || "").trim();
          if (alertId) recordsById.set(alertId, record);
        });
      }
    } catch (error) {
      console.error("Unable to parse initial alert records.", error);
    }
  }

  let activeRow = null;
  let actionInProgress = false;
  let pendingSnoozeRows = [];

  function showToast(message, type) {
    if (!toastWrap) return;
    const toast = document.createElement("div");
    toast.className = "alt-toast " + (type === "success" ? "alt-toast-success" : "alt-toast-error");
    toast.textContent = message;
    toastWrap.appendChild(toast);
    setTimeout(function () {
      toast.style.opacity = "0";
      toast.style.transform = "translateY(-6px)";
      toast.style.transition = "all .25s ease";
    }, 2400);
    setTimeout(function () { toast.remove(); }, 2750);
  }

  function normaliseTimeUnit(value) {
    const unit = String(value || "").trim().toUpperCase();
    return ["MINUTES", "HOURS", "DAYS"].includes(unit) ? unit : "MINUTES";
  }

  function timeUnitLabel(unit, value) {
    const normalised = normaliseTimeUnit(unit);
    const singular = Number(value) === 1;
    if (normalised === "DAYS") return singular ? "Day" : "Days";
    if (normalised === "HOURS") return singular ? "Hour" : "Hours";
    return singular ? "Minute" : "Minutes";
  }

  function getSnoozeUnitForRow(row) {
    if (!row) return "MINUTES";
    const severity = String(row.dataset.severity || "WARNING").toUpperCase();
    const severityUnit = severity === "CRITICAL"
      ? row.dataset.criticalUnit
      : row.dataset.warningUnit;
    return normaliseTimeUnit(row.dataset.snoozeUnit || severityUnit);
  }

  function closeSnoozeModal() {
    pendingSnoozeRows = [];
    if (snoozeModalOverlay) {
      snoozeModalOverlay.classList.remove("show");
      snoozeModalOverlay.setAttribute("aria-hidden", "true");
    }
    if (snoozeValueInput) snoozeValueInput.value = "";
    if (snoozeValueError) snoozeValueError.textContent = "";
  }

  function openSnoozeModal(rows) {
    if (!rows || !rows.length) {
      showToast("Please select an alert to snooze.", "error");
      return;
    }

    const units = Array.from(new Set(rows.map(getSnoozeUnitForRow)));
    if (units.length !== 1) {
      showToast(
        "Selected alerts use different Alert Rule units. Snooze them separately.",
        "error"
      );
      return;
    }

    pendingSnoozeRows = rows.slice();
    const unit = units[0];
    if (snoozeUnitDisplay) snoozeUnitDisplay.textContent = timeUnitLabel(unit, 2);
    if (snoozeValueInput) snoozeValueInput.value = "";
    if (snoozeValueError) snoozeValueError.textContent = "";

    if (snoozeModalOverlay) {
      snoozeModalOverlay.classList.add("show");
      snoozeModalOverlay.setAttribute("aria-hidden", "false");
    }

    setTimeout(function () {
      snoozeValueInput?.focus();
    }, 0);
  }

  async function submitSnooze() {
    const rawValue = snoozeValueInput ? String(snoozeValueInput.value || "").trim() : "";

    if (!/^\d+$/.test(rawValue) || Number(rawValue) < 1) {
      if (snoozeValueError) {
        snoozeValueError.textContent = "Enter a whole number greater than zero.";
      }
      snoozeValueInput?.focus();
      return;
    }

    const value = Number(rawValue);
    const unit = pendingSnoozeRows.length
      ? getSnoozeUnitForRow(pendingSnoozeRows[0])
      : "MINUTES";

    if (snoozeUnitDisplay) {
      snoozeUnitDisplay.textContent = timeUnitLabel(unit, value);
    }
    if (snoozeValueError) snoozeValueError.textContent = "";

    const succeeded = await performAlertAction(
      pendingSnoozeRows,
      "SNOOZE",
      { snooze_value: value }
    );

    if (succeeded) closeSnoozeModal();
  }

  function getRows() {
    return Array.from(tableBody.querySelectorAll("tr")).filter(function (row) {
      return !row.classList.contains("alt-empty-row");
    });
  }

  function getVisibleRows() {
    return getRows().filter(function (row) { return row.style.display !== "none"; });
  }

  function getSelectedRows() {
    return getRows().filter(function (row) {
      const checkbox = row.querySelector(".alert-row-check");
      return checkbox && checkbox.checked;
    });
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value === null || value === undefined || value === "" ? "-" : String(value);
  }

  function formatDurationMinutes(rawValue) {
    const minutes = Math.max(Number(rawValue || 0), 0);
    if (!Number.isFinite(minutes) || minutes === 0) return "Immediate";

    const days = Math.floor(minutes / 1440);
    const hours = Math.floor((minutes % 1440) / 60);
    const mins = Math.floor(minutes % 60);
    const parts = [];

    if (days) parts.push(days + " Day" + (days === 1 ? "" : "s"));
    if (hours) parts.push(hours + " Hour" + (hours === 1 ? "" : "s"));
    if (mins) parts.push(mins + " Minute" + (mins === 1 ? "" : "s"));

    return parts.join(" ") || "Immediate";
  }

  function formatDateTime(value) {
    if (!value) return "";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);

    return parsed.toLocaleString("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false
    });
  }

  function updateSummary() {
    const visibleRows = getVisibleRows();
    const selectedRows = getSelectedRows();

    const counts = {
      WARNING: 0,
      CRITICAL: 0,
      ACKNOWLEDGED: 0,
      SNOOZED: 0
    };

    visibleRows.forEach(function (row) {
      const severity = row.dataset.severity || "";
      const status = row.dataset.alertStatus || "";
      if (counts[severity] !== undefined) counts[severity] += 1;
      if (counts[status] !== undefined) counts[status] += 1;
    });

    setText("toolbarVisibleCount", visibleRows.length);
    setText("toolbarSelectedCount", selectedRows.length);
    setText("summaryVisible", visibleRows.length);
    setText("summaryWarning", counts.WARNING);
    setText("summaryCritical", counts.CRITICAL);
    setText("summaryAcknowledged", counts.ACKNOWLEDGED);
    setText("summarySelected", selectedRows.length);

    const paginationText = document.getElementById("paginationText");
    if (paginationText) paginationText.textContent = "Showing " + visibleRows.length + " alert(s)";

    const openSelected = document.getElementById("btnOpenSelected");
    if (openSelected) openSelected.disabled = selectedRows.length !== 1;

    const severityPill = document.getElementById("summarySeverityPill");
    if (severityPill) {
      if (counts.CRITICAL > 0) {
        severityPill.textContent = "Critical";
        severityPill.className = "alt-state-pill critical";
      } else if (counts.WARNING > 0) {
        severityPill.textContent = "Warning";
        severityPill.className = "alt-state-pill warning";
      } else {
        severityPill.textContent = "Normal";
        severityPill.className = "alt-state-pill normal";
      }
    }
  }

  function matchesText(row, searchText) {
    if (!searchText) return true;
    return row.innerText.toLowerCase().includes(searchText.toLowerCase());
  }

  function applyFilters() {
    const values = {};
    filterIds.forEach(function (id) {
      const el = document.getElementById(id);
      values[id] = el ? String(el.value || "").trim() : "";
    });

    const searchText = quickSearch ? quickSearch.value.trim() : "";

    getRows().forEach(function (row) {
      const ageing = Number(row.dataset.ageingDays || 0);
      let visible = true;

      if (values.alert_name && row.dataset.alertName !== values.alert_name) visible = false;
      if (values.severity && row.dataset.severity !== values.severity) visible = false;
      if (values.alert_status && row.dataset.alertStatus !== values.alert_status) visible = false;
      if (values.workflow_status && row.dataset.workflowStatus !== values.workflow_status) visible = false;
      if (values.assigned_user && row.dataset.assignedUser !== values.assigned_user) visible = false;
      if (values.agency && row.dataset.agency !== values.agency) visible = false;
      if (values.country && row.dataset.country !== values.country) visible = false;
      if (values.passport_no && !String(row.dataset.passportNo || "").toLowerCase().includes(values.passport_no.toLowerCase())) visible = false;
      if (values.ageing_from && ageing < Number(values.ageing_from)) visible = false;
      if (values.ageing_to && ageing > Number(values.ageing_to)) visible = false;
      if (!matchesText(row, searchText)) visible = false;

      row.style.display = visible ? "" : "none";
    });

    const alertNameEl = document.getElementById("alert_name");
    setText(
      "summaryCurrentView",
      alertNameEl && alertNameEl.value
        ? alertNameEl.options[alertNameEl.selectedIndex].text
        : "All Active Alerts"
    );
    updateSummary();
  }

  function clearFilters() {
    filterIds.forEach(function (id) {
      const el = document.getElementById(id);
      if (!el) return;
      if (el.tagName === "SELECT") el.selectedIndex = 0;
      else el.value = "";
    });
    if (quickSearch) quickSearch.value = "";
    applyFilters();
    showToast("Filters cleared successfully.", "success");
  }

  function clearSelection(showMessage) {
    getRows().forEach(function (row) {
      const cb = row.querySelector(".alert-row-check");
      if (cb) cb.checked = false;
      row.classList.remove("is-selected");
    });
    if (selectAllEl) selectAllEl.checked = false;
    updateSummary();
    if (showMessage !== false) showToast("Selection cleared successfully.", "success");
  }

  function buildFallbackActivity(record) {
    const activity = [];

    function add(label, occurredOn, actor, detail) {
      if (!occurredOn) return;
      activity.push({
        label: label,
        occurred_on: formatDateTime(occurredOn),
        actor: actor || "SYSTEM",
        detail: detail || ""
      });
    }

    add("Alert resolved", record.resolved_on, record.resolved_by, record.resolution_reason);
    add(
      "Alert snoozed",
      record.snoozed_on,
      record.snoozed_by,
      record.snoozed_until ? "Snoozed until " + formatDateTime(record.snoozed_until) : ""
    );
    add("Alert acknowledged", record.acknowledged_on, record.acknowledged_by, "");
    add("Alert notification sent", record.last_notified_on, "SYSTEM", "Notification count: " + Number(record.notification_count || 0));
    add("Critical threshold reached", record.critical_triggered_on, "SYSTEM", "");
    add("Warning threshold reached", record.warning_triggered_on, "SYSTEM", "");
    add("Alert created", record.created_on, "SYSTEM", "");

    return activity;
  }

  function renderActivity(record) {
    const wrap = document.getElementById("drawerActivity");
    if (!wrap) return;
    wrap.innerHTML = "";

    const activity = Array.isArray(record && record.activity) && record.activity.length
      ? record.activity
      : buildFallbackActivity(record || {});

    if (!activity.length) {
      const empty = document.createElement("div");
      empty.className = "alt-empty-state";
      empty.textContent = "No activity available";
      wrap.appendChild(empty);
      return;
    }

    activity.slice(0, 8).forEach(function (item) {
      const card = document.createElement("div");
      card.className = "alt-activity-item";

      const head = document.createElement("div");
      head.className = "alt-activity-item-head";

      const title = document.createElement("div");
      title.className = "alt-activity-item-title";
      title.textContent = item.label || "Alert activity";

      const time = document.createElement("div");
      time.className = "alt-activity-item-time";
      time.textContent = item.occurred_on || "";

      head.appendChild(title);
      head.appendChild(time);
      card.appendChild(head);

      const meta = document.createElement("div");
      meta.className = "alt-activity-item-meta";
      meta.textContent = "By: " + (item.actor || "SYSTEM");
      card.appendChild(meta);

      if (item.detail) {
        const detail = document.createElement("div");
        detail.className = "alt-activity-item-detail";
        detail.textContent = item.detail;
        card.appendChild(detail);
      }

      wrap.appendChild(card);
    });
  }

  function getRecordForRow(row) {
    if (!row) return {};
    const alertId = String(row.dataset.alertId || "").trim();
    return recordsById.get(alertId) || {};
  }

  function fillDrawer(row) {
    if (!row) return;
    const record = getRecordForRow(row);
    const assignedDisplay = [
      row.dataset.assignedUser,
      row.dataset.assignedUserName
    ].filter(Boolean).join(" - ");

    setText("drawerAlertId", row.dataset.alertId || "-");
    setText("drawerPatientName", row.dataset.patientName || "-");
    setText("drawerPassportNo", row.dataset.passportNo || "-");
    setText("drawerQueueNo", row.dataset.operationQueueNo || "-");
    setText("drawerCountry", row.dataset.country || "-");
    setText("drawerAgency", row.dataset.agency || "-");
    setText("drawerAssignedTo", assignedDisplay || "-");
    setText("drawerAlertName", row.dataset.alertName || "-");
    setText(
      "drawerWarningThreshold",
      row.dataset.warningThresholdDisplay || formatDurationMinutes(row.dataset.warningThresholdMinutes)
    );
    setText(
      "drawerCriticalThreshold",
      row.dataset.criticalThresholdDisplay || formatDurationMinutes(row.dataset.criticalThresholdMinutes)
    );
    setText("drawerWorkflow", row.dataset.workflowStatus || "-");
    setText("drawerStatusSince", row.dataset.statusSince || "-");
    setText("drawerAgeing", row.dataset.ageingDisplay || ((row.dataset.ageingDays || "0") + " Days"));

    const severityEl = document.getElementById("drawerSeverity");
    if (severityEl) {
      severityEl.textContent = row.dataset.severity || "WARNING";
      severityEl.className = "alt-badge " + String(row.dataset.severity || "WARNING").toLowerCase();
    }

    const statusEl = document.getElementById("drawerStatus");
    if (statusEl) {
      statusEl.textContent = row.dataset.alertStatus || "OPEN";
      statusEl.className = "alt-badge status-" + String(row.dataset.alertStatus || "OPEN").toLowerCase();
    }

    renderActivity(record);
  }

  function openDrawer(row) {
    activeRow = row;
    fillDrawer(row);
    if (drawer) {
      drawer.classList.add("open");
      drawer.setAttribute("aria-hidden", "false");
    }
    if (drawerOverlay) drawerOverlay.classList.add("show");
  }

  function closeDrawer() {
    if (drawer) {
      drawer.classList.remove("open");
      drawer.setAttribute("aria-hidden", "true");
    }
    if (drawerOverlay) drawerOverlay.classList.remove("show");
  }

  function updateRowStatus(row, status) {
    if (!row) return;
    row.dataset.alertStatus = status;
    const badge = row.querySelector(".alert-status-badge");
    if (badge) {
      badge.textContent = status;
      badge.className = "alt-badge alert-status-badge status-" + status.toLowerCase();
    }
    updateSummary();
    if (drawer && drawer.classList.contains("open") && row === activeRow) fillDrawer(row);
  }

  function prependActionActivity(record, action, response) {
    if (!record) return;
    const performedBy = response.performed_by || "SYSTEM";
    const actionOn = formatDateTime(response.action_on || new Date().toISOString());
    let item;

    if (action === "ACKNOWLEDGE") {
      record.acknowledged_by = performedBy;
      record.acknowledged_on = response.action_on;
      item = {
        activity_type: "ACKNOWLEDGED",
        label: "Alert acknowledged",
        actor: performedBy,
        occurred_on: actionOn,
        detail: ""
      };
    } else if (action === "SNOOZE") {
      record.snoozed_by = performedBy;
      record.snoozed_on = response.action_on;
      record.snoozed_until = response.snoozed_until;
      item = {
        activity_type: "SNOOZED",
        label: "Alert snoozed",
        actor: performedBy,
        occurred_on: actionOn,
        detail: response.snoozed_until
          ? "Snoozed until " + formatDateTime(response.snoozed_until)
          : ""
      };
    } else {
      record.resolved_by = performedBy;
      record.resolved_on = response.action_on;
      record.resolution_reason = response.resolution_reason || "Resolved from Alert Dashboard.";
      item = {
        activity_type: "RESOLVED",
        label: "Alert resolved",
        actor: performedBy,
        occurred_on: actionOn,
        detail: record.resolution_reason
      };
    }

    if (!Array.isArray(record.activity)) record.activity = buildFallbackActivity(record);
    record.activity = record.activity.filter(function (existing) {
      return !(existing.activity_type === item.activity_type && existing.occurred_on === item.occurred_on);
    });
    record.activity.unshift(item);
  }

  function setActionButtonsDisabled(disabled) {
    document.querySelectorAll(
      ".btn-ack-alert, .btn-snooze-alert, .btn-resolve-alert, " +
      "#btnBulkAcknowledge, #btnBulkResolve, #drawerAcknowledge, " +
      "#drawerSnooze, #drawerResolve, #btnConfirmSnooze"
    ).forEach(function (button) {
      button.disabled = disabled;
    });
  }

  async function performAlertAction(rows, action, extraPayload) {
    if (actionInProgress) return false;
    if (!rows || !rows.length) {
      showToast("Please select at least one alert.", "error");
      return false;
    }
    if (!actionUrl) {
      showToast("Alert action URL is not configured.", "error");
      return false;
    }

    const alertIds = rows
      .map(function (row) { return Number(row.dataset.alertId || 0); })
      .filter(function (alertId) { return Number.isInteger(alertId) && alertId > 0; });

    if (!alertIds.length) {
      showToast("No valid alert ID was found.", "error");
      return false;
    }

    actionInProgress = true;
    setActionButtonsDisabled(true);

    try {
      const response = await fetch(actionUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json"
        },
        body: JSON.stringify(Object.assign({
          action: action,
          alert_ids: alertIds
        }, extraPayload || {}))
      });

      let payload = {};
      try {
        payload = await response.json();
      } catch (error) {
        payload = {};
      }

      if (!response.ok || !payload.success) {
        throw new Error(payload.message || "Unable to update alert status.");
      }

      rows.forEach(function (row) {
        updateRowStatus(row, payload.alert_status);
        const record = getRecordForRow(row);
        record.alert_status = payload.alert_status;
        prependActionActivity(record, action, payload);
      });

      if (activeRow && rows.includes(activeRow)) fillDrawer(activeRow);
      showToast(payload.message || "Alert action completed successfully.", "success");
      return true;
    } catch (error) {
      console.error("Alert action failed.", error);
      showToast(error.message || "Unable to update alert status.", "error");
      return false;
    } finally {
      actionInProgress = false;
      setActionButtonsDisabled(false);
    }
  }

  function bindRowEvents() {
    getRows().forEach(function (row) {
      const cb = row.querySelector(".alert-row-check");
      if (cb) {
        cb.addEventListener("change", function () {
          row.classList.toggle("is-selected", cb.checked);
          updateSummary();
        });
      }

      row.querySelector(".btn-view-alert")?.addEventListener("click", function () {
        openDrawer(row);
      });
      row.querySelector(".btn-ack-alert")?.addEventListener("click", function () {
        performAlertAction([row], "ACKNOWLEDGE");
      });
      row.querySelector(".btn-snooze-alert")?.addEventListener("click", function () {
        openSnoozeModal([row]);
      });
      row.querySelector(".btn-resolve-alert")?.addEventListener("click", function () {
        performAlertAction([row], "RESOLVE");
      });
    });
  }

  document.getElementById("btnSearchAlerts")?.addEventListener("click", applyFilters);
  document.getElementById("btnClearFilters")?.addEventListener("click", clearFilters);
  document.getElementById("btnClearSelection")?.addEventListener("click", function () { clearSelection(true); });
  document.getElementById("btnRefreshTop")?.addEventListener("click", function () { window.location.reload(); });
  document.getElementById("btnExportTop")?.addEventListener("click", function () {
    showToast("Export action is ready for backend integration.", "success");
  });
  document.getElementById("btnCreateRuleTop")?.addEventListener("click", function () {
    window.location.href = "/alert-management/rules";
  });

  quickSearch?.addEventListener("input", applyFilters);

  selectAllEl?.addEventListener("change", function () {
    getVisibleRows().forEach(function (row) {
      const cb = row.querySelector(".alert-row-check");
      if (cb) cb.checked = selectAllEl.checked;
      row.classList.toggle("is-selected", selectAllEl.checked);
    });
    updateSummary();
  });

  document.getElementById("btnBulkAcknowledge")?.addEventListener("click", function () {
    performAlertAction(getSelectedRows(), "ACKNOWLEDGE");
  });

  document.getElementById("btnBulkResolve")?.addEventListener("click", function () {
    performAlertAction(getSelectedRows(), "RESOLVE");
  });

  document.getElementById("btnOpenSelected")?.addEventListener("click", function () {
    const rows = getSelectedRows();
    if (rows.length === 1) openDrawer(rows[0]);
  });

  document.getElementById("btnCloseDrawer")?.addEventListener("click", closeDrawer);
  drawerOverlay?.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeDrawer();
      closeSnoozeModal();
    }
  });

  document.getElementById("btnCloseSnoozeModal")?.addEventListener("click", closeSnoozeModal);
  document.getElementById("btnCancelSnooze")?.addEventListener("click", closeSnoozeModal);
  document.getElementById("btnConfirmSnooze")?.addEventListener("click", submitSnooze);
  snoozeModalOverlay?.addEventListener("click", function (event) {
    if (event.target === snoozeModalOverlay) closeSnoozeModal();
  });

  snoozeValueInput?.addEventListener("keydown", function (event) {
    if (["e", "E", "+", "-", "."].includes(event.key)) {
      event.preventDefault();
    }
    if (event.key === "Enter") {
      event.preventDefault();
      submitSnooze();
    }
  });

  snoozeValueInput?.addEventListener("input", function () {
    const cleaned = String(snoozeValueInput.value || "").replace(/\D/g, "");
    if (snoozeValueInput.value !== cleaned) snoozeValueInput.value = cleaned;
    if (snoozeValueError) snoozeValueError.textContent = "";

    const unit = pendingSnoozeRows.length
      ? getSnoozeUnitForRow(pendingSnoozeRows[0])
      : "MINUTES";
    if (snoozeUnitDisplay) {
      snoozeUnitDisplay.textContent = timeUnitLabel(unit, Number(cleaned || 2));
    }
  });

  document.getElementById("drawerAcknowledge")?.addEventListener("click", function () {
    if (activeRow) performAlertAction([activeRow], "ACKNOWLEDGE");
  });
  document.getElementById("drawerSnooze")?.addEventListener("click", function () {
    if (activeRow) openSnoozeModal([activeRow]);
  });
  document.getElementById("drawerResolve")?.addEventListener("click", function () {
    if (activeRow) performAlertAction([activeRow], "RESOLVE");
  });
  document.getElementById("drawerReassign")?.addEventListener("click", function () {
    showToast("Reassignment modal can be connected to the user master API.", "success");
  });

  bindRowEvents();
  applyFilters();
})();
