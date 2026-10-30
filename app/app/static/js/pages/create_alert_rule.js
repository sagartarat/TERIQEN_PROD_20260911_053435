(function () {
  "use strict";

  const page = document.getElementById("createAlertRulePage");
  const form = document.getElementById("alertRuleForm");
  const recipientTableBody = document.getElementById("recipientTableBody");
  const toastWrap = document.getElementById("toastWrap");
  const referenceDataEl = document.getElementById("alert-rule-reference-data");

  const mode = page ? (page.dataset.mode || "create") : "create";
  const currentRuleId = page ? String(page.dataset.ruleId || "") : "";

  let referenceData = { users: [], roles: [], selectedRule: null, selectedRecipients: [] };
  let pendingDelete = null;

  try {
    if (referenceDataEl && referenceDataEl.textContent) {
      referenceData = JSON.parse(referenceDataEl.textContent);
    }
  } catch (error) {
    console.error("Unable to parse alert rule reference data:", error);
  }

  const requiredFields = [
    "alert_name", "module_code", "trigger_type", "workflow_status",
    "ageing_basis", "warning_value", "warning_unit",
    "critical_value", "critical_unit"
  ];

  function byId(id) { return document.getElementById(id); }
  function getValue(id) {
    const element = byId(id);
    return element ? String(element.value || "").trim() : "";
  }
  function getChecked(id) {
    const element = byId(id);
    return Boolean(element && element.checked);
  }

  function isPartnerModule() {
    const code = getValue("module_code").toUpperCase();
    return ["PARTNER","PARTNER_MANAGEMENT","PARTNER_REGISTRATION","PARTNER_MODULE","PARTNER_DETAILS"].includes(code);
  }
  function isPartnerContractExpiryRule() {
    return isPartnerModule() && getValue("trigger_type") === "DATE_EXPIRY";
  }
  function syncTriggerFields() {
    const trigger = byId("trigger_type");
    const workflow = byId("workflow_status");
    const ageing = byId("ageing_basis");
    const triggerOption = byId("contractEndDateTriggerOption");
    const ageingOption = byId("contractEndDateAgeingOption");
    const requiredMark = byId("workflowStatusRequired");
    const contractRule = isPartnerContractExpiryRule();

    if (triggerOption) triggerOption.disabled = !isPartnerModule();
    if (trigger && trigger.value === "DATE_EXPIRY" && !isPartnerModule()) trigger.value = "";

    const activeContractRule = isPartnerContractExpiryRule();
    if (ageingOption) ageingOption.disabled = !activeContractRule;
    if (workflow) {
      if (activeContractRule) {
        workflow.value = "";
        workflow.disabled = true;
        clearFieldError("workflow_status");
      } else {
        workflow.disabled = mode === "view";
      }
    }
    if (requiredMark) requiredMark.hidden = activeContractRule;
    if (ageing) {
      if (activeContractRule) {
        ageing.value = "CONTRACT_END_DATE";
        ageing.disabled = true;
      } else {
        if (ageing.value === "CONTRACT_END_DATE") ageing.value = "";
        ageing.disabled = mode === "view";
      }
    }
    if (byId("warningTimingLabel")) byId("warningTimingLabel").textContent = activeContractRule ? "Before Expiry" : "After";
    if (byId("criticalTimingLabel")) byId("criticalTimingLabel").textContent = activeContractRule ? "Before Expiry" : "After";
  }
  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
      .replace(/"/g,"&quot;").replace(/'/g,"&#039;");
  }
  function flagIsOn(value) {
    return value === true || value === 1 || value === "1" ||
           String(value || "").toUpperCase() === "Y";
  }
  function showToast(message, type) {
    if (!toastWrap) return;
    const toast = document.createElement("div");
    toast.className = "car-toast " + (type || "success");
    toast.textContent = message;
    toastWrap.appendChild(toast);
    setTimeout(() => { toast.style.opacity = "0"; }, 2600);
    setTimeout(() => toast.remove(), 3000);
  }
  function setLoading(value) {
    if (page) page.classList.toggle("car-loading", value);
  }
  function clearFieldError(id) {
    const field = byId(id);
    const error = document.querySelector('[data-error-for="' + id + '"]');
    if (field) field.classList.remove("is-invalid");
    if (error) error.textContent = "";
  }
  function setFieldError(id, message) {
    const field = byId(id);
    const error = document.querySelector('[data-error-for="' + id + '"]');
    if (field) field.classList.add("is-invalid");
    if (error) error.textContent = message;
  }
  function normalizeToMinutes(value, unit) {
    const number = Number(value || 0);
    if (unit === "DAYS") return number * 1440;
    if (unit === "HOURS") return number * 60;
    return number;
  }
  function minutesToValueUnit(minutes) {
    const value = Number(minutes || 0);
    if (value > 0 && value % 1440 === 0) return { value: value / 1440, unit: "DAYS" };
    if (value > 0 && value % 60 === 0) return { value: value / 60, unit: "HOURS" };
    return { value: value || "", unit: value ? "MINUTES" : "" };
  }
  function validateForm() {
    requiredFields.forEach(clearFieldError);
    let valid = true;
    requiredFields.forEach(id => {
      if (id === "workflow_status" && isPartnerContractExpiryRule()) return;
      if (!getValue(id)) {
        setFieldError(id, "Required.");
        valid = false;
      }
    });
    const warning = normalizeToMinutes(getValue("warning_value"), getValue("warning_unit"));
    const critical = normalizeToMinutes(getValue("critical_value"), getValue("critical_unit"));
    if (warning <= 0) {
      setFieldError("warning_value", "Must be greater than zero.");
      valid = false;
    }
    if (critical <= 0) {
      setFieldError("critical_value", "Must be greater than zero.");
      valid = false;
    } else if (isPartnerContractExpiryRule()) {
      if (warning <= critical) {
        setFieldError("warning_value", "Warning must be farther before expiry than Critical.");
        valid = false;
      }
    } else if (critical <= warning) {
      setFieldError("critical_value", "Must be greater than warning.");
      valid = false;
    }
    if ((getValue("repeat_value") && !getValue("repeat_unit")) ||
        (!getValue("repeat_value") && getValue("repeat_unit"))) {
      showToast("Repeat value and unit must be entered together.", "error");
      valid = false;
    }
    return valid;
  }
  function buildReferenceOptions(type, selectedValue) {
    let html = '<option value="">-- Select Recipient --</option>';
    const selected = String(selectedValue || "");
    function option(value, label) {
      html += '<option value="' + escapeHtml(value) + '"' +
        (String(value) === selected ? " selected" : "") + ">" +
        escapeHtml(label) + "</option>";
    }

    if (type === "SPECIFIC_USER") {
      (referenceData.users || []).forEach(user => {
        const code = user.user_code || "";
        option(code, code + (user.user_name ? " - " + user.user_name : ""));
      });
    } else if (type === "ROLE") {
      (referenceData.roles || []).forEach(role => {
        const code = role.role_code || "";
        option(code, role.role_name || code);
      });
    } else if (type === "ASSIGNED_USER") {
      option("ASSIGNED_USER", "Current Assigned User");
    } else if (type === "TEAM_LEADER") {
      option("TEAM_LEADER", "Assigned User Team Leader");
    } else if (type === "ADMINISTRATOR") {
      option("ADMINISTRATOR", "System Administrator");
    }
    return html;
  }
  function renderEmptyRecipientRow() {
    if (!recipientTableBody || recipientTableBody.querySelector(".recipient-row")) return;
    recipientTableBody.innerHTML =
      '<tr class="car-empty-row"><td colspan="8">' +
      'No recipients added. Click Add Recipient.</td></tr>';
  }
  function addRecipientRow(data) {
    if (!recipientTableBody) return;
    const empty = recipientTableBody.querySelector(".car-empty-row");
    if (empty) empty.remove();

    data = data || {};
    const row = document.createElement("tr");
    row.className = "recipient-row";
    row.innerHTML = `
      <td><select class="car-table-select recipient-severity">
        <option value="WARNING">Warning</option>
        <option value="CRITICAL">Critical</option>
      </select></td>
      <td><select class="car-table-select recipient-type">
        <option value="">-- Select Type --</option>
        <option value="ASSIGNED_USER">Assigned User</option>
        <option value="SPECIFIC_USER">Specific User</option>
        <option value="ROLE">Role</option>
        <option value="TEAM_LEADER">Team Leader</option>
        <option value="ADMINISTRATOR">Administrator</option>
      </select></td>
      <td><select class="car-table-select recipient-value" disabled>
        <option value="">-- Select Recipient --</option>
      </select></td>
      <td class="car-center"><input type="checkbox" class="car-channel-check channel-in-app"></td>
      <td class="car-center"><input type="checkbox" class="car-channel-check channel-email"></td>
      <td class="car-center"><input type="checkbox" class="car-channel-check channel-whatsapp"></td>
      <td class="car-center"><input type="checkbox" class="car-channel-check channel-sms"></td>
      <td class="car-center"><button type="button" class="car-delete-btn btn-delete-recipient" title="Delete Recipient"><i class="fa-solid fa-trash"></i></button></td>`;

    recipientTableBody.appendChild(row);

    const severity = row.querySelector(".recipient-severity");
    const type = row.querySelector(".recipient-type");
    const value = row.querySelector(".recipient-value");

    severity.value = data.severity || "WARNING";
    type.value = data.recipient_type || "";
    value.innerHTML = buildReferenceOptions(type.value, data.recipient_value);
    value.disabled = !type.value;
    row.querySelector(".channel-in-app").checked = data.in_app_flag == null ? true : flagIsOn(data.in_app_flag);
    row.querySelector(".channel-email").checked = flagIsOn(data.email_flag);
    row.querySelector(".channel-whatsapp").checked = flagIsOn(data.whatsapp_flag);
    row.querySelector(".channel-sms").checked = flagIsOn(data.sms_flag);

    type.addEventListener("change", function () {
      value.innerHTML = buildReferenceOptions(type.value, "");
      value.disabled = !type.value;
      updatePreview();
    });
    row.querySelectorAll("select,input").forEach(el => el.addEventListener("change", updatePreview));
    row.querySelector(".btn-delete-recipient").addEventListener("click", function () {
      row.remove(); renderEmptyRecipientRow(); updatePreview();
    });
  }
  function collectRecipients() {
    if (!recipientTableBody) return [];
    return Array.from(recipientTableBody.querySelectorAll(".recipient-row")).map(row => ({
      severity: row.querySelector(".recipient-severity").value,
      recipient_type: row.querySelector(".recipient-type").value,
      recipient_value: row.querySelector(".recipient-value").value,
      in_app_flag: row.querySelector(".channel-in-app").checked ? 1 : 0,
      email_flag: row.querySelector(".channel-email").checked ? 1 : 0,
      whatsapp_flag: row.querySelector(".channel-whatsapp").checked ? 1 : 0,
      sms_flag: row.querySelector(".channel-sms").checked ? 1 : 0,
      active_flag: 1
    }));
  }
  function collectPayload() {
    return {
      alert_name:getValue("alert_name"), alert_description:getValue("alert_description"),
      module_code:getValue("module_code"), active_flag:getChecked("active_flag") ? "Y":"N",
      trigger_type:getValue("trigger_type"),
      workflow_status:isPartnerContractExpiryRule() ? "" : getValue("workflow_status"),
      ageing_basis:isPartnerContractExpiryRule() ? "CONTRACT_END_DATE" : getValue("ageing_basis"),
      warning_after_minutes:normalizeToMinutes(getValue("warning_value"),getValue("warning_unit")),
      warning_unit:getValue("warning_unit"),
      critical_after_minutes:normalizeToMinutes(getValue("critical_value"),getValue("critical_unit")),
      critical_unit:getValue("critical_unit"),
      repeat_interval_minutes:getValue("repeat_value") ?
        normalizeToMinutes(getValue("repeat_value"),getValue("repeat_unit")) : null,
      repeat_unit:getValue("repeat_value") ? getValue("repeat_unit") : null,
      maximum_repeat_count:getValue("maximum_repeat_count") ?
        Number(getValue("maximum_repeat_count")) : 0,
      business_hours_only:getChecked("business_hours_only") ? "Y":"N",
      exclude_weekends:getChecked("exclude_weekends") ? "Y":"N",
      exclude_holidays:getChecked("exclude_holidays") ? "Y":"N",
      auto_resolve:getChecked("auto_resolve") ? "Y":"N",
      recipients:collectRecipients()
    };
  }
  function getSelectedText(id) {
    const el = byId(id);
    return el && el.selectedIndex >= 0 ? el.options[el.selectedIndex].text.trim() : "";
  }
  function updatePreview() {
    const summary = byId("ruleSummaryPreview");
    const condition = byId("conditionPreview");
    if (!summary || !condition) return;

    const warning = normalizeToMinutes(getValue("warning_value"),getValue("warning_unit"));
    const critical = normalizeToMinutes(getValue("critical_value"),getValue("critical_unit"));

    if (isPartnerContractExpiryRule()) {
      if (!getValue("alert_name") || warning <= 0 || critical <= 0) {
        summary.textContent = "Complete required fields to generate the preview.";
        condition.textContent = "No condition generated";
        return;
      }
      summary.textContent = getValue("alert_name") +
        " monitors Partner Contract End Date, warns " + getValue("warning_value") + " " +
        getSelectedText("warning_unit") + " before expiry, and becomes critical " +
        getValue("critical_value") + " " + getSelectedText("critical_unit") +
        " before expiry. Recipients: " + collectRecipients().length + ".";
      condition.textContent =
        "ENTITY: PARTNER\n" +
        "DATE FIELD: contend_date\n" +
        "WARNING WHEN minutes_to_expiry <= " + warning + "\n" +
        "CRITICAL WHEN minutes_to_expiry <= " + critical + "\n" +
        "AUTO_RESOLVE = " + (getChecked("auto_resolve") ? "YES" : "NO");
      return;
    }

    if (!getValue("alert_name") && !getValue("workflow_status")) {
      summary.textContent = "Complete required fields to generate the preview.";
      condition.textContent = "No condition generated"; return;
    }
    summary.textContent = (getValue("alert_name") || "This alert") +
      " monitors " + (getSelectedText("workflow_status") || "the selected status") +
      ", warns after " + (getValue("warning_value") || "-") + " " +
      getSelectedText("warning_unit") + ", and becomes critical after " +
      (getValue("critical_value") || "-") + " " + getSelectedText("critical_unit") +
      ". Recipients: " + collectRecipients().length + ".";
    condition.textContent =
      "WHERE wrkflw_status = '" + (getValue("workflow_status") || "<STATUS>") + "'\n" +
      "WARNING >= " + (warning || "<MINUTES>") + "\n" +
      "CRITICAL >= " + (critical || "<MINUTES>") + "\n" +
      "AUTO_RESOLVE = " + (getChecked("auto_resolve") ? "YES" : "NO");
  }
  function populateRule() {
    const rule = referenceData.selectedRule;
    if (!rule) { renderEmptyRecipientRow(); return; }

    const fields = ["alert_name","alert_description","module_code","trigger_type","workflow_status","ageing_basis","maximum_repeat_count"];
    fields.forEach(id => { if (byId(id) && rule[id] != null) byId(id).value = rule[id]; });

    const warning = minutesToValueUnit(rule.warning_after_minutes);
    const critical = minutesToValueUnit(rule.critical_after_minutes);
    const repeat = minutesToValueUnit(rule.repeat_interval_minutes);
    if (rule.warning_unit) warning.unit = String(rule.warning_unit).toUpperCase();
    if (rule.critical_unit) critical.unit = String(rule.critical_unit).toUpperCase();
    if (rule.repeat_unit) repeat.unit = String(rule.repeat_unit).toUpperCase();
    byId("warning_value").value = warning.value; byId("warning_unit").value = warning.unit;
    byId("critical_value").value = critical.value; byId("critical_unit").value = critical.unit;
    byId("repeat_value").value = repeat.value; byId("repeat_unit").value = repeat.unit;

    ["active_flag","business_hours_only","exclude_weekends","exclude_holidays","auto_resolve"]
      .forEach(id => { if (byId(id)) byId(id).checked = flagIsOn(rule[id]); });

    byId("activeFlagText").textContent = byId("active_flag").checked ? "Active" : "Inactive";
    (referenceData.selectedRecipients || []).forEach(addRecipientRow);
    renderEmptyRecipientRow();
    syncTriggerFields();
    updatePreview();
  }
  async function saveRule() {
    if (mode === "view") return;
    if (!validateForm()) { showToast("Please correct the highlighted fields.", "error"); return; }

    const payload = collectPayload();
    if (!payload.recipients.length) { showToast("Add at least one recipient.", "error"); return; }
    const invalid = payload.recipients.some(r =>
      !r.recipient_type || !r.recipient_value ||
      !(r.in_app_flag || r.email_flag || r.whatsapp_flag || r.sms_flag)
    );
    if (invalid) {
      showToast("Complete each recipient and select at least one channel.", "error");
      return;
    }

    const isEdit = mode === "edit" && currentRuleId;
    const url = isEdit
      ? "/alert-management/alert-rules/" + encodeURIComponent(currentRuleId)
      : "/alert-management/create-alert-rule";

    try {
      setLoading(true);
      const response = await fetch(url, {
        method: isEdit ? "PUT" : "POST",
        headers:{"Content-Type":"application/json","Accept":"application/json","X-Requested-With":"XMLHttpRequest"},
        body:JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.message || "Unable to save rule.");
      showToast(data.message, "success");
      setTimeout(() => { window.location.href = "/alert-management/create-alert-rule"; }, 600);
    } catch (error) {
      console.error(error); showToast(error.message || "Unable to save rule.", "error");
    } finally { setLoading(false); }
  }
  function resetForm() {
    if (!form || mode === "view") return;
    if (mode === "edit") { populateRule(); return; }
    form.reset();
    byId("active_flag").checked = true; byId("auto_resolve").checked = true;
    recipientTableBody.innerHTML = ""; renderEmptyRecipientRow(); syncTriggerFields(); updatePreview();
  }
  function openDeleteModal(button) {
    pendingDelete = { id:button.dataset.ruleId, name:button.dataset.ruleName };
    byId("deleteModalText").textContent =
      'Delete "' + pendingDelete.name + '"? This action cannot be undone.';
    byId("deleteModal").hidden = false;
  }
  function closeDeleteModal() { pendingDelete = null; byId("deleteModal").hidden = true; }
  async function confirmDelete() {
    if (!pendingDelete) return;
    try {
      setLoading(true);
      const response = await fetch("/alert-management/alert-rules/" + encodeURIComponent(pendingDelete.id), {
        method:"DELETE", headers:{"Accept":"application/json","X-Requested-With":"XMLHttpRequest"}
      });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.message || "Unable to delete rule.");
      showToast(data.message, "success");
      setTimeout(() => { window.location.href = "/alert-management/create-alert-rule"; }, 500);
    } catch (error) {
      showToast(error.message || "Unable to delete rule.", "error");
    } finally { setLoading(false); closeDeleteModal(); }
  }

  if (form) {
    form.addEventListener("submit", event => { event.preventDefault(); saveRule(); });
    form.querySelectorAll("input,select,textarea").forEach(el => {
      el.addEventListener("input", () => { clearFieldError(el.id); updatePreview(); });
      el.addEventListener("change", () => {
        clearFieldError(el.id);
        if (el.id === "module_code" || el.id === "trigger_type") syncTriggerFields();
        updatePreview();
      });
    });
  }
  if (byId("active_flag")) byId("active_flag").addEventListener("change", function () {
    byId("activeFlagText").textContent = this.checked ? "Active" : "Inactive";
  });
  if (byId("btnAddRecipient")) byId("btnAddRecipient").addEventListener("click", () => addRecipientRow());
  if (byId("btnSaveTop")) byId("btnSaveTop").addEventListener("click", saveRule);
  if (byId("btnSaveBottom")) byId("btnSaveBottom").addEventListener("click", event => { event.preventDefault(); saveRule(); });
  if (byId("btnResetForm")) byId("btnResetForm").addEventListener("click", resetForm);

  document.querySelectorAll(".btn-delete-rule").forEach(button =>
    button.addEventListener("click", () => openDeleteModal(button)));
  if (byId("btnCancelDelete")) byId("btnCancelDelete").addEventListener("click", closeDeleteModal);
  if (byId("btnConfirmDelete")) byId("btnConfirmDelete").addEventListener("click", confirmDelete);
  if (byId("deleteModal")) byId("deleteModal").addEventListener("click", event => {
    if (event.target === byId("deleteModal")) closeDeleteModal();
  });

  if (byId("ruleSearch")) byId("ruleSearch").addEventListener("input", function () {
    const term = this.value.trim().toLowerCase();
    document.querySelectorAll("#alertRulesTable tbody tr[data-search]").forEach(row => {
      row.hidden = term && !row.dataset.search.includes(term);
    });
  });

  populateRule();
  syncTriggerFields();
  updatePreview();
})();