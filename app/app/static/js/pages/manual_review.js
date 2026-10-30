const manualReviewRows = JSON.parse(document.getElementById("manualReviewRowsData").textContent || "[]");
  const manualReviewUsers = JSON.parse(document.getElementById("manualReviewUsersData").textContent || "[]");

  let mrqFilteredRows = [];
  let mrqSelectedIds = new Set();
  let mrqCurrentRow = null;
  let mrqQuickFilter = "";

  // Department multi-select UI state.
  // Existing persistence remains one comma-separated Department value.
  let mrqDepartmentWorkingSelection = new Set();
  let mrqDepartmentUnknownValues = [];

  function safeValue(value){
    return value === null || value === undefined || value === "" ? "" : String(value);
  }

  function displayValue(value){
    return safeValue(value) || "—";
  }

  function escapeHtml(value){
    return safeValue(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function htmlValue(value){
    const displayed = displayValue(value);
    return displayed === "—" ? displayed : escapeHtml(displayed);
  }

  function safeInlineId(value){
    return safeValue(value).replace(/[^A-Za-z0-9_.:-]/g, "");
  }

  function upperValue(value){
    return safeValue(value).trim().toUpperCase();
  }

  function getFirstValue(row, keys){
    for(let i = 0; i < keys.length; i++){
      const key = keys[i];
      if(row && row[key] !== null && row[key] !== undefined && row[key] !== ""){
        return row[key];
      }
    }
    return "";
  }

  function toHtmlDateValue(value){
    const raw = safeValue(value).trim();
    if(!raw) return "";

    if(raw.length === 10 && raw.charAt(4) === "-" && raw.charAt(7) === "-"){
      return raw;
    }

    const sep = raw.includes("/") ? "/" : (raw.includes("-") ? "-" : "");
    if(sep){
      const parts = raw.split(sep);
      if(parts.length === 3 && parts[2].length === 4){
        return `${parts[2]}-${String(parts[1]).padStart(2, "0")}-${String(parts[0]).padStart(2, "0")}`;
      }
    }

    return raw;
  }

  function normalizeGenderValue(value){
    const raw = upperValue(value);
    if(raw === "M" || raw === "MALE") return "Male";
    if(raw === "F" || raw === "FEMALE") return "Female";
    if(raw === "O" || raw === "OTHER" || raw === "OTHERS") return "Others";
    return safeValue(value);
  }

  function splitSelectValues(value){
    return safeValue(value)
      .replace(/\|/g, ",")
      .replace(/;/g, ",")
      .split(",")
      .map(item => item.trim())
      .filter(Boolean);
  }

  function setSelectValue(selectElement, value){
    if(!selectElement) return;

    const suppliedValues = splitSelectValues(value);
    const expectedValues = suppliedValues.map(item => upperValue(item));

    Array.from(selectElement.options).forEach(option => {
      option.selected = false;
    });

    if(!expectedValues.length){
      selectElement.value = "";
      return;
    }

    if(selectElement.multiple){
      Array.from(selectElement.options).forEach(option => {
        const optionValue = upperValue(option.value);
        const optionText = upperValue(option.textContent);

        option.selected = expectedValues.some(expected => {
          return optionValue === expected || optionText === expected;
        });
      });
      return;
    }

    const matchingOption = Array.from(selectElement.options).find(option => {
      const optionValue = upperValue(option.value);
      const optionText = upperValue(option.textContent);

      return expectedValues.some(expected => {
        return optionValue === expected || optionText === expected;
      });
    });

    selectElement.value = matchingOption ? matchingOption.value : "";
  }

  function setInputValue(id, value){
    const el = document.getElementById(id);
    if(!el) return;

    if(el.type === "date"){
      el.value = toHtmlDateValue(value);
      return;
    }

    let normalizedValue = value;

    if(id.toLowerCase().includes("gender")){
      normalizedValue = normalizeGenderValue(value);
    }

    if(el.tagName === "SELECT"){
      setSelectValue(el, normalizedValue);
      return;
    }

    el.value = safeValue(normalizedValue);
  }

  function normalizeDepartmentKey(value){
    return upperValue(value).replace(/\s+/g, " ").trim();
  }

  function splitDepartmentValues(value){
    const rawValues = Array.isArray(value) ? value : [value];
    const out = [];
    const seen = new Set();

    rawValues.forEach(rawValue => {
      safeValue(rawValue)
        .replace(/\|/g, ",")
        .replace(/;/g, ",")
        .split(",")
        .map(item => item.trim())
        .filter(Boolean)
        .forEach(item => {
          const key = normalizeDepartmentKey(item);
          if(!key || seen.has(key)) return;
          seen.add(key);
          out.push(item);
        });
    });

    return out;
  }

  function getManualDepartmentSelect(){
    return document.getElementById("detail_department");
  }

  function getAvailableManualDepartmentOptions(){
    const dept = getManualDepartmentSelect();
    if(!dept) return [];

    return Array.from(dept.options || []).filter(option => {
      return !!safeValue(option.value).trim() && !option.hidden && !option.disabled;
    });
  }

  function getUniqueAvailableManualDepartments(){
    const out = [];
    const seen = new Set();

    getAvailableManualDepartmentOptions().forEach(option => {
      const value = safeValue(option.value).trim();
      const key = normalizeDepartmentKey(value);
      if(!key || seen.has(key)) return;
      seen.add(key);
      out.push({
        key,
        value,
        label: safeValue(option.textContent || option.value).trim() || value
      });
    });

    return out;
  }

  function getSelectedManualDepartments(){
    const dept = getManualDepartmentSelect();
    if(!dept) return [];

    const out = [];
    const seen = new Set();

    Array.from(dept.selectedOptions || []).forEach(option => {
      const value = safeValue(option.value).trim();
      const key = normalizeDepartmentKey(value);

      if(!key || seen.has(key)) return;
      seen.add(key);
      out.push(value);
    });

    return out;
  }

  function getSelectedManualDepartmentCsv(){
    return getSelectedManualDepartments().join(",");
  }

  function updateManualDepartmentWarning(){
    const warning = document.getElementById("detail_department_warning");
    if(!warning) return;

    if(!mrqDepartmentUnknownValues.length){
      warning.style.display = "none";
      warning.textContent = "";
      return;
    }

    const label = mrqDepartmentUnknownValues.length === 1
      ? "Unrecognized / unavailable Department"
      : "Unrecognized / unavailable Departments";

    warning.textContent = `${label}: ${mrqDepartmentUnknownValues.join(", ")}`;
    warning.style.display = "block";
  }

  function refreshManualDepartmentControl(){
    const dept = getManualDepartmentSelect();
    const chips = document.getElementById("detail_department_chips");
    const count = document.getElementById("detail_department_count");

    if(!dept || !chips) return;

    const selectedValues = getSelectedManualDepartments();
    chips.innerHTML = "";

    if(!selectedValues.length){
      const placeholder = document.createElement("span");
      placeholder.className = "mrq-dept-placeholder";
      placeholder.id = "detail_department_placeholder";
      placeholder.textContent = "Select Department";
      chips.appendChild(placeholder);
    }else{
      selectedValues.forEach(value => {
        const chip = document.createElement("span");
        chip.className = "mrq-dept-chip";
        chip.setAttribute("data-department", value);

        const label = document.createElement("span");
        label.className = "mrq-dept-chip-label";
        label.textContent = value;
        chip.appendChild(label);

        const remove = document.createElement("span");
        remove.className = "mrq-dept-chip-remove";
        remove.setAttribute("role", "button");
        remove.setAttribute("tabindex", dept.disabled ? "-1" : "0");
        remove.setAttribute("aria-label", `Remove ${value}`);
        remove.textContent = "×";

        const removeHandler = event => {
          event.preventDefault();
          event.stopPropagation();
          if(dept.disabled) return;
          removeManualDepartmentSelection(value);
        };

        remove.addEventListener("click", removeHandler);
        remove.addEventListener("keydown", event => {
          if(event.key === "Enter" || event.key === " "){
            removeHandler(event);
          }
        });

        chip.appendChild(remove);
        chips.appendChild(chip);
      });
    }

    if(count){
      if(selectedValues.length > 1){
        count.textContent = `${selectedValues.length} Departments Selected`;
        count.style.display = "block";
      }else{
        count.textContent = "";
        count.style.display = "none";
      }
    }

    updateManualDepartmentWarning();
  }

  function setManualDepartmentSelection(value){
    const dept = getManualDepartmentSelect();
    if(!dept) return;

    const incomingValues = splitDepartmentValues(value);
    const availableDepartments = getUniqueAvailableManualDepartments();
    const availableMap = new Map(
      availableDepartments.map(item => [item.key, item.value])
    );

    Array.from(dept.options || []).forEach(option => {
      option.selected = false;
    });

    const unknownValues = [];
    const unknownSeen = new Set();
    const selectedKeys = new Set();

    incomingValues.forEach(incomingValue => {
      const key = normalizeDepartmentKey(incomingValue);
      if(!key) return;

      if(availableMap.has(key)){
        selectedKeys.add(key);
        return;
      }

      if(!unknownSeen.has(key)){
        unknownSeen.add(key);
        unknownValues.push(incomingValue);
      }
    });

    // Select only one valid option for each Department name.
    // This avoids duplicate selections when the same Department appears
    // in hospdept_master for more than one Hospital.
    const appliedKeys = new Set();
    Array.from(dept.options || []).forEach(option => {
      const key = normalizeDepartmentKey(option.value);
      if(
        key &&
        selectedKeys.has(key) &&
        !option.hidden &&
        !option.disabled &&
        !appliedKeys.has(key)
      ){
        option.selected = true;
        appliedKeys.add(key);
      }
    });

    mrqDepartmentUnknownValues = unknownValues;
    mrqDepartmentWorkingSelection = new Set(
      getSelectedManualDepartments().map(normalizeDepartmentKey)
    );

    refreshManualDepartmentControl();
  }

  function filterDepartmentByHospital(hospitalName, selectedDepartment){
    const dept = getManualDepartmentSelect();
    if(!dept) return;

    const selectedHospital = upperValue(hospitalName);
    const retainedValues = selectedDepartment !== undefined
      ? splitDepartmentValues(selectedDepartment)
      : getSelectedManualDepartments();

    Array.from(dept.options).forEach(option => {
      if(!option.value){
        option.hidden = false;
        option.disabled = false;
        option.selected = false;
        return;
      }

      const optionHospital = upperValue(option.getAttribute("data-hospital"));
      const shouldHide = !!selectedHospital &&
                         !!optionHospital &&
                         optionHospital !== selectedHospital;

      option.hidden = shouldHide;
      option.disabled = shouldHide;

      if(shouldHide){
        option.selected = false;
      }
    });

    // Re-apply only Departments that are actually available for the
    // selected Hospital. Invalid/unknown values are warned separately
    // and are never synthesized as new dropdown options.
    setManualDepartmentSelection(retainedValues);
    renderManualDepartmentOptions();
  }

  function renderManualDepartmentOptions(){
    const container = document.getElementById("detail_department_options");
    const search = normalizeDepartmentKey(
      document.getElementById("detail_department_search")?.value || ""
    );

    if(!container) return;

    container.innerHTML = "";
    let visibleCount = 0;

    getUniqueAvailableManualDepartments().forEach(item => {
      if(search && !normalizeDepartmentKey(item.label).includes(search)){
        return;
      }

      visibleCount += 1;

      const row = document.createElement("label");
      row.className = "mrq-dept-option";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "mrq-dept-option-checkbox";
      checkbox.value = item.value;
      checkbox.checked = mrqDepartmentWorkingSelection.has(item.key);

      checkbox.addEventListener("change", function(){
        if(this.checked){
          mrqDepartmentWorkingSelection.add(item.key);
        }else{
          mrqDepartmentWorkingSelection.delete(item.key);
        }
      });

      const label = document.createElement("span");
      label.textContent = item.label;

      row.appendChild(checkbox);
      row.appendChild(label);
      container.appendChild(row);
    });

    if(!visibleCount){
      const empty = document.createElement("div");
      empty.className = "mrq-dept-option-empty";
      empty.textContent = "No Departments available for the selected Hospital.";
      container.appendChild(empty);
    }
  }

  function openManualDepartmentDropdown(){
    const dept = getManualDepartmentSelect();
    const dropdown = document.getElementById("detail_department_dropdown");
    const summary = document.getElementById("detail_department_summary");
    const search = document.getElementById("detail_department_search");

    if(!dept || !dropdown || !summary || dept.disabled || summary.disabled){
      return;
    }

    mrqDepartmentWorkingSelection = new Set(
      getSelectedManualDepartments().map(normalizeDepartmentKey)
    );

    if(search){
      search.value = "";
    }

    renderManualDepartmentOptions();
    dropdown.hidden = false;
    summary.setAttribute("aria-expanded", "true");

    if(search){
      setTimeout(() => search.focus(), 0);
    }
  }

  function closeManualDepartmentDropdown(applyChanges){
    const dept = getManualDepartmentSelect();
    const dropdown = document.getElementById("detail_department_dropdown");
    const summary = document.getElementById("detail_department_summary");

    if(!dept || !dropdown || !summary) return;

    if(applyChanges){
      const before = getSelectedManualDepartmentCsv();

      Array.from(dept.options || []).forEach(option => {
        option.selected = false;
      });

      const appliedKeys = new Set();
      Array.from(dept.options || []).forEach(option => {
        const key = normalizeDepartmentKey(option.value);

        if(
          key &&
          mrqDepartmentWorkingSelection.has(key) &&
          !option.hidden &&
          !option.disabled &&
          !appliedKeys.has(key)
        ){
          option.selected = true;
          appliedKeys.add(key);
        }
      });

      // Once the reviewer applies a corrected selection, stale incoming
      // unknown values are intentionally removed rather than added to LOV.
      mrqDepartmentUnknownValues = [];
      refreshManualDepartmentControl();

      const after = getSelectedManualDepartmentCsv();
      if(before !== after){
        dept.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }else{
      mrqDepartmentWorkingSelection = new Set(
        getSelectedManualDepartments().map(normalizeDepartmentKey)
      );
    }

    dropdown.hidden = true;
    summary.setAttribute("aria-expanded", "false");
  }

  function removeManualDepartmentSelection(value){
    const dept = getManualDepartmentSelect();
    if(!dept || dept.disabled) return;

    const targetKey = normalizeDepartmentKey(value);
    let changed = false;

    Array.from(dept.options || []).forEach(option => {
      if(normalizeDepartmentKey(option.value) === targetKey && option.selected){
        option.selected = false;
        changed = true;
      }
    });

    if(!changed) return;

    mrqDepartmentWorkingSelection = new Set(
      getSelectedManualDepartments().map(normalizeDepartmentKey)
    );

    refreshManualDepartmentControl();
    dept.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function initManualDepartmentMultiSelect(){
    const control = document.getElementById("detail_department_control");
    const dept = getManualDepartmentSelect();
    const summary = document.getElementById("detail_department_summary");
    const dropdown = document.getElementById("detail_department_dropdown");
    const search = document.getElementById("detail_department_search");
    const applyBtn = document.getElementById("detail_department_apply");
    const cancelBtn = document.getElementById("detail_department_cancel");

    if(!control || !dept || !summary || !dropdown){
      return;
    }

    summary.addEventListener("click", function(event){
      event.preventDefault();
      event.stopPropagation();

      if(dropdown.hidden){
        openManualDepartmentDropdown();
      }else{
        closeManualDepartmentDropdown(false);
      }
    });

    dropdown.addEventListener("click", function(event){
      event.stopPropagation();
    });

    if(search){
      search.addEventListener("input", renderManualDepartmentOptions);
    }

    if(applyBtn){
      applyBtn.addEventListener("click", function(event){
        event.preventDefault();
        event.stopPropagation();
        closeManualDepartmentDropdown(true);
      });
    }

    if(cancelBtn){
      cancelBtn.addEventListener("click", function(event){
        event.preventDefault();
        event.stopPropagation();
        closeManualDepartmentDropdown(false);
      });
    }

    dept.addEventListener("change", refreshManualDepartmentControl);

    document.addEventListener("click", function(event){
      if(!dropdown.hidden && !control.contains(event.target)){
        closeManualDepartmentDropdown(false);
      }
    });

    document.addEventListener("keydown", function(event){
      if(event.key === "Escape" && !dropdown.hidden){
        closeManualDepartmentDropdown(false);
      }
    });

    refreshManualDepartmentControl();
  }

  function setTextValue(id, value){
    const el = document.getElementById(id);
    if(el) el.textContent = displayValue(value);
  }

  function toggleMrqSection(bodyId, buttonId){
    const body = document.getElementById(bodyId);
    const btn = document.getElementById(buttonId);
    if(!body || !btn) return;

    const isCollapsed = body.classList.toggle("collapsed");
    btn.textContent = isCollapsed ? "+" : "−";
  }

  function resetAttenderSections(){
    [
      ["attender1Body", "attender1Toggle"],
      ["attender2Body", "attender2Toggle"],
      ["attender3Body", "attender3Toggle"]
    ].forEach(pair => {
      const body = document.getElementById(pair[0]);
      const btn = document.getElementById(pair[1]);
      if(body) body.classList.add("collapsed");
      if(btn) btn.textContent = "+";
    });
  }

  function getRowId(row){
    return row.id || row.message_log_id || row.channel_message_log_id;
  }

  function getReviewStatus(row){
    return upperValue(row.review_status || row.manual_review_status || "PENDING");
  }

  function getMessageType(row){
    return upperValue(row.message_type);
  }

  function getParseStatus(row){
    return upperValue(row.parse_status);
  }

  function getThreadStatus(row){
    return upperValue(row.thread_resolution_status);
  }

  function getRawMessage(row){
    return safeValue(row.raw_message_text || row.message_body || row.latest_message || row.message_text);
  }

  function getAttachmentCount(row){
    return Number(row.attachment_count || 0);
  }

  function getPossiblePassport(row){
    return safeValue(row.possible_passport || row.patpassport_no || row.patient_passport || row.extracted_passport);
  }

  function badgeClassForMessageType(type){
    type = upperValue(type);
    if(type === "ATTACHMENT_ONLY") return "mrq-badge-attach";
    if(type === "UNKNOWN") return "mrq-badge-unknown";
    return "mrq-badge-review";
  }

  function badgeClassForParseStatus(status){
    status = upperValue(status);
    if(status === "MANUAL_REVIEW") return "mrq-badge-manual";
    if(status === "FAILED") return "mrq-badge-danger";
    if(status === "PARSED") return "mrq-badge-success";
    return "mrq-badge-review";
  }

  function updateKpiCards(){
    const total = manualReviewRows.length;
    const attachment = manualReviewRows.filter(r => getMessageType(r) === "ATTACHMENT_ONLY").length;
    const noThread = manualReviewRows.filter(r => getThreadStatus(r) === "NO_THREAD_FOUND").length;
    const failed = manualReviewRows.filter(r => getParseStatus(r) === "FAILED").length;
    const reviewedToday = manualReviewRows.filter(r => getReviewStatus(r) === "RESOLVED").length;

    document.getElementById("mrqTotalCount").textContent = total;
    document.getElementById("mrqAttachmentCount").textContent = attachment;
    document.getElementById("mrqNoThreadCount").textContent = noThread;
    document.getElementById("mrqFailedCount").textContent = failed;
    document.getElementById("mrqReviewedTodayCount").textContent = reviewedToday;
  }

  function applyQuickFilter(filterValue){
    mrqQuickFilter = upperValue(filterValue);
    applyManualReviewFilters();
  }

  function applyManualReviewFilters(){
    const search = upperValue(document.getElementById("mrqSearchInput").value);
    const messageType = upperValue(document.getElementById("mrqMessageTypeFilter").value);
    const reason = upperValue(document.getElementById("mrqReasonFilter").value);
    const assignedTo = upperValue(document.getElementById("mrqAssignedFilter").value);

    mrqFilteredRows = manualReviewRows.filter(row => {
      const rowSearch = upperValue([
        getRowId(row),
        row.sender_mobile,
        row.sender_name,
        row.classifier_reason,
        row.thread_resolution_status,
        getRawMessage(row),
        getPossiblePassport(row),
        row.linked_queue_id,
        row.assigned_to
      ].join(" "));

      if(search && !rowSearch.includes(search)) return false;
      if(messageType && getMessageType(row) !== messageType) return false;
      if(reason && !upperValue(row.classifier_reason + " " + row.thread_resolution_status + " " + row.parse_status).includes(reason)) return false;
      if(assignedTo && upperValue(row.assigned_to) !== assignedTo) return false;

      if(mrqQuickFilter){
        if(mrqQuickFilter === "ATTACHMENT_ONLY" && getMessageType(row) !== "ATTACHMENT_ONLY") return false;
        if(mrqQuickFilter === "NO_THREAD_FOUND" && getThreadStatus(row) !== "NO_THREAD_FOUND") return false;
        if(mrqQuickFilter === "FAILED" && getParseStatus(row) !== "FAILED") return false;
        if(mrqQuickFilter === "RESOLVED" && getReviewStatus(row) !== "RESOLVED") return false;
      }

      return true;
    });

    renderManualReviewGrid();
  }

  function renderManualReviewGrid(){
    const tbody = document.getElementById("mrqGridBody");
    tbody.innerHTML = "";

    if(!mrqFilteredRows.length){
      tbody.innerHTML = '<tr><td colspan="14" style="text-align:center;">No manual review records found.</td></tr>';
      document.getElementById("mrqShowingText").textContent = "Showing 0 records";
      return;
    }

    mrqFilteredRows.forEach(row => {
      const rowId = getRowId(row);
      const tr = document.createElement("tr");

      tr.innerHTML = `
        <td><input type="checkbox" class="mrq-row-check" data-id="${safeInlineId(rowId)}" ${mrqSelectedIds.has(String(rowId)) ? "checked" : ""}></td>
        <td><a class="mrq-link" onclick="openManualSidePanelById('${safeInlineId(rowId)}')">${htmlValue(rowId)}</a></td>
        <td>${htmlValue(row.received_on || row.created_on)}</td>
        <td>${htmlValue(row.sender_mobile)}</td>
        <td><span class="mrq-badge ${badgeClassForMessageType(getMessageType(row))}">${htmlValue(row.message_type)}</span></td>
        <td><span class="mrq-badge ${badgeClassForParseStatus(getParseStatus(row))}">${htmlValue(row.parse_status)}</span></td>
        <td>${htmlValue(row.classifier_reason)}</td>
        <td>${htmlValue(row.thread_resolution_status)}</td>
        <td>${htmlValue(getAttachmentCount(row))}</td>
        <td>${htmlValue(getPossiblePassport(row))}</td>
        <td>${htmlValue(row.linked_queue_id)}</td>
        <td>${htmlValue(row.assigned_to)}</td>
        <td><div class="mrq-msg-preview">${htmlValue(getRawMessage(row))}</div></td>
        <td>
    <div style="display:flex;align-items:center;gap:8px;justify-content:center;white-space:nowrap;">

        <button class="mrq-btn mrq-btn-primary mrq-btn-sm"
                type="button"
                onclick="openManualSidePanelById('${safeInlineId(rowId)}')">
            Review
        </button>

        <button class="mrq-btn mrq-btn-danger mrq-btn-sm"
                type="button"
                onclick="markAsJunk('${safeInlineId(rowId)}')">
            Mark JUNK
        </button>

    </div>
</td>
      `;

      tbody.appendChild(tr);
    });

    document.querySelectorAll(".mrq-row-check").forEach(cb => {
      cb.addEventListener("change", function(){
        const id = String(this.getAttribute("data-id"));
        if(this.checked) mrqSelectedIds.add(id);
        else mrqSelectedIds.delete(id);
        updateSelectedCount();
      });
    });

    document.getElementById("mrqShowingText").textContent = `Showing ${mrqFilteredRows.length} records`;
    updateSelectedCount();
  }

  function updateSelectedCount(){
    document.getElementById("mrqSelectedCount").textContent = `Selected Records: ${mrqSelectedIds.size}`;
  }

  function toggleManualSelectAll(flag){
    if(flag){
      mrqFilteredRows.forEach(row => mrqSelectedIds.add(String(getRowId(row))));
    }else{
      mrqSelectedIds.clear();
    }
    renderManualReviewGrid();
  }

  function openManualSidePanelById(rowId){
    const row = manualReviewRows.find(r => String(getRowId(r)) === String(rowId));
    if(!row) return;
    mrqCurrentRow = row;
    populateManualSidePanel(row);

    document.getElementById("mrqSideOverlay").classList.add("show");
    document.getElementById("mrqSidePanel").classList.add("open");
  }

  function closeManualSidePanel(){
    closeManualDepartmentDropdown(false);
    document.getElementById("mrqSideOverlay").classList.remove("show");
    document.getElementById("mrqSidePanel").classList.remove("open");
  }

  function populateManualSidePanel(row){
    const rowId = getRowId(row);

    document.getElementById("mrqDetailHeader").textContent = `Message Log #${htmlValue(rowId)}`;
    document.getElementById("mrqDetailParseBadge").textContent = displayValue(row.parse_status);
    document.getElementById("mrqDetailMessageTypeBadge").textContent = displayValue(row.message_type);
    document.getElementById("mrqDetailThreadBadge").textContent = displayValue(row.thread_resolution_status);

    document.getElementById("miniSenderMobile").textContent = displayValue(row.sender_mobile);
    document.getElementById("miniReceivedOn").textContent = displayValue(row.received_on || row.created_on);
    document.getElementById("miniAttachmentCount").textContent = displayValue(getAttachmentCount(row));
    document.getElementById("miniPossiblePassport").textContent = displayValue(getPossiblePassport(row));

    document.getElementById("detail_message_log_id").value = displayValue(rowId);
    document.getElementById("detail_message_type").value = displayValue(row.message_type);
    document.getElementById("detail_parse_status").value = displayValue(row.parse_status);
    document.getElementById("detail_thread_resolution_status").value = displayValue(row.thread_resolution_status);
    document.getElementById("detail_linked_queue_id").value = displayValue(row.linked_queue_id);
    document.getElementById("detail_classifier_reason").value = displayValue(row.classifier_reason);

    document.getElementById("detail_raw_message_text").textContent = displayValue(getRawMessage(row));

    setInputValue("detail_patient_given_name", getFirstValue(row, ["patient_given_name", "patgiven_nm", "possible_patient_given_name"]));
    setInputValue("detail_patient_short_name", getFirstValue(row, ["patient_short_name", "patsur_nm", "possible_patient_short_name"]));
    setInputValue("detail_patient_passport_number", getFirstValue(row, ["possible_passport", "patpassport_no", "patient_passport", "extracted_passport"]));
    setInputValue("detail_patient_passport_expiry_date", getFirstValue(row, ["patient_passport_expiry_date", "patpass_expirydt", "passport_expiry_date"]));
    setInputValue("detail_patient_gender", getFirstValue(row, ["patient_gender", "patgender"]));
    setInputValue("detail_patient_dob", getFirstValue(row, ["patient_dob", "patdob"]));
    setInputValue("detail_patient_age", getFirstValue(row, ["patient_age", "patage"]));
    setInputValue("detail_patient_contact_number", getFirstValue(row, ["patient_contact_number", "patient_contno", "patcontact_no"]));
    setInputValue("detail_patient_address", getFirstValue(row, ["patient_address", "pataddress"]));
    setInputValue("detail_patient_email", getFirstValue(row, ["patient_email", "patemail"]));
    setInputValue("detail_patient_nationality", getFirstValue(row, ["patient_nationality", "patient_country", "nationality"]));

    setInputValue("detail_attender1_given_name", getFirstValue(row, ["attender1_given_name", "attn1_givennm", "attn1given_nm"]));
    setInputValue("detail_attender1_short_name", getFirstValue(row, ["attender1_short_name", "attn1_surnm", "attn1sur_nm"]));
    setInputValue("detail_attender1_passport_number", getFirstValue(row, ["attender1_passport_number", "attn1passport_no"]));
    setInputValue("detail_attender1_passport_expiry_date", getFirstValue(row, ["attender1_passport_expiry_date", "attn1pass_expirydt"]));
    setInputValue("detail_attender1_gender", getFirstValue(row, ["attender1_gender", "attn1_gender"]));
    setInputValue("detail_attender1_dob", getFirstValue(row, ["attender1_dob", "attn1_dob"]));
    setInputValue("detail_attender1_age", getFirstValue(row, ["attender1_age", "attn1_age"]));
    setInputValue("detail_attender1_address", getFirstValue(row, ["attender1_address", "attn1_address"]));
    setInputValue("detail_attender1_nationality", getFirstValue(row, ["attender1_nationality", "attn1_nationality", "attn1_country"]));
    setInputValue("detail_attender1_contact_number", getFirstValue(row, ["attender1_contact_number", "attn1_contno", "attn1contact_no"]));
    setInputValue("detail_attender1_relationship", getFirstValue(row, ["attender1_relationship", "attn1pat_relation"]));

    setInputValue("detail_attender2_given_name", getFirstValue(row, ["attender2_given_name", "attn2_givennm", "attn2given_nm"]));
    setInputValue("detail_attender2_short_name", getFirstValue(row, ["attender2_short_name", "attn2_surnm", "attn2sur_nm"]));
    setInputValue("detail_attender2_passport_number", getFirstValue(row, ["attender2_passport_number", "attn2passport_no"]));
    setInputValue("detail_attender2_passport_expiry_date", getFirstValue(row, ["attender2_passport_expiry_date", "attn2pass_expirydt"]));
    setInputValue("detail_attender2_gender", getFirstValue(row, ["attender2_gender", "attn2_gender"]));
    setInputValue("detail_attender2_dob", getFirstValue(row, ["attender2_dob", "attn2_dob"]));
    setInputValue("detail_attender2_age", getFirstValue(row, ["attender2_age", "attn2_age"]));
    setInputValue("detail_attender2_address", getFirstValue(row, ["attender2_address", "attn2_address"]));
    setInputValue("detail_attender2_nationality", getFirstValue(row, ["attender2_nationality", "attn2_nationality", "attn2_country"]));
    setInputValue("detail_attender2_contact_number", getFirstValue(row, ["attender2_contact_number", "attn2_contno", "attn2contact_no"]));
    setInputValue("detail_attender2_relationship", getFirstValue(row, ["attender2_relationship", "attn2pat_relation"]));

    setInputValue("detail_attender3_given_name", getFirstValue(row, ["attender3_given_name", "attn3_givennm", "attn3given_nm"]));
    setInputValue("detail_attender3_short_name", getFirstValue(row, ["attender3_short_name", "attn3_surnm", "attn3sur_nm"]));
    setInputValue("detail_attender3_passport_number", getFirstValue(row, ["attender3_passport_number", "attn3passport_no"]));
    setInputValue("detail_attender3_passport_expiry_date", getFirstValue(row, ["attender3_passport_expiry_date", "attn3pass_expirydt"]));
    setInputValue("detail_attender3_gender", getFirstValue(row, ["attender3_gender", "attn3_gender"]));
    setInputValue("detail_attender3_dob", getFirstValue(row, ["attender3_dob", "attn3_dob"]));
    setInputValue("detail_attender3_age", getFirstValue(row, ["attender3_age", "attn3_age"]));
    setInputValue("detail_attender3_address", getFirstValue(row, ["attender3_address", "attn3_address"]));
    setInputValue("detail_attender3_nationality", getFirstValue(row, ["attender3_nationality", "attn3_nationality", "attn3_country"]));
    setInputValue("detail_attender3_contact_number", getFirstValue(row, ["attender3_contact_number", "attn3_contno", "attn3contact_no"]));
    setInputValue("detail_attender3_relationship", getFirstValue(row, ["attender3_relationship", "attn3pat_relation"]));

    setInputValue("detail_agent_shrtnm", getFirstValue(row, ["agent_shrtnm", "possible_agency"]));
    const selectedHospitalName = getFirstValue(row, ["possible_hospital", "hospital_name", "hospital_shrtnm"]);
    const selectedDepartmentName = getFirstValue(row, ["possible_department", "department", "hospital_dept"]);
    setInputValue("detail_hospital_name", selectedHospitalName);
    filterDepartmentByHospital(selectedHospitalName, selectedDepartmentName);
    setInputValue("detail_doctor_name", getFirstValue(row, ["possible_doctor", "doctor_name", "doctor_nm"]));
    setInputValue("detail_request_nationality", getFirstValue(row, ["request_nationality", "nationality", "patient_country", "patient_nationality"]));
    setInputValue("detail_embassy", getFirstValue(row, ["embassy", "ivac_center"]));
    setInputValue("detail_intent", getFirstValue(row, ["possible_request_for", "intent", "intent_remarks", "request_for"]));
    setInputValue("detail_approximate_date", getFirstValue(row, ["approximate_date", "appointment_dt", "vil_date"]));
    setInputValue("detail_missing_fields", getFirstValue(row, ["missing_fields", "parse_remarks"]));

    document.getElementById("detail_reviewer_decision").value = "";
    document.getElementById("detail_selected_thread_id").value = safeValue(row.linked_queue_id || row.channel_thread_id);
    document.getElementById("detail_assigned_to").value = safeValue(row.assigned_to);
    document.getElementById("detail_reviewer_remarks").value = safeValue(row.reviewer_remarks);

    resetAttenderSections();
    renderAttachments(row);
    renderPossibleMatches(row);
  }

  function parseJsonArray(value){
    if(!value) return [];
    if(Array.isArray(value)) return value;

    if(typeof value === "object"){
      return [value];
    }

    try{
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [];
    }catch(e){
      console.warn("MANUAL_REVIEW_ATTACHMENT_JSON_ERROR:", e);
      return [];
    }
  }

  function normalizeAttachmentUrl(value){
    let raw = safeValue(value).trim();
    if(!raw) return "";

    raw = raw.replace(/\\/g, "/");
    const lower = raw.toLowerCase();
    const privateMarker = "private/whatsapp_docs/";

    if(lower.startsWith(privateMarker)){
      return "/whatsapp/media/" + raw.substring(privateMarker.length);
    }

    if(lower.startsWith("/whatsapp/media/")){
      return raw;
    }

    // HTTPS links are allowed for transitional remote media only.
    if(lower.startsWith("https://")){
      return raw;
    }

    // Legacy local files remain supported during migration, subject to the
    // server-side /static/uploads access guard.
    if(lower.startsWith("/static/uploads/")){
      return raw;
    }

    if(lower.startsWith("static/uploads/")){
      return "/" + raw.replace(/^\/+/, "");
    }

    const appStaticIndex = lower.lastIndexOf("app/static/uploads/");
    if(appStaticIndex >= 0){
      return "/static/uploads/" + raw.substring(appStaticIndex + "app/static/uploads/".length).replace(/^\/+/, "");
    }

    // Reject javascript:, data:, file:, arbitrary relative paths, and other
    // untrusted schemes/locations instead of injecting them into href/src.
    return "";
  }

  function resolveAttachmentMimeType(att, fileName, fileUrl){
    const explicitMime = safeValue(att.mime_type || att.media_type).trim();
    if(explicitMime) return explicitMime;

    const source = `${safeValue(fileName)} ${safeValue(fileUrl)}`.toLowerCase();
    if(/\.(jpg|jpeg)(?:$|[?#])/.test(source)) return "image/jpeg";
    if(/\.png(?:$|[?#])/.test(source)) return "image/png";
    if(/\.webp(?:$|[?#])/.test(source)) return "image/webp";
    if(/\.gif(?:$|[?#])/.test(source)) return "image/gif";
    if(/\.pdf(?:$|[?#])/.test(source)) return "application/pdf";
    if(/\.mp4(?:$|[?#])/.test(source)) return "video/mp4";
    if(/\.(mp3|mpeg)(?:$|[?#])/.test(source)) return "audio/mpeg";
    if(/\.(ogg|opus)(?:$|[?#])/.test(source)) return "audio/ogg";
    return "application/octet-stream";
  }

  function getRowAttachments(row){
    const candidates = [
      row && row.attachments,
      row && row.attachment_json,
      row && row.attachments_json
    ];

    for(let i = 0; i < candidates.length; i++){
      const parsed = parseJsonArray(candidates[i]);
      if(parsed.length) return parsed;
    }

    return [];
  }

  function renderAttachments(row){
    const attachments = getRowAttachments(row);
    const rail = document.getElementById("mrqAttachmentRail");
    const badge = document.getElementById("mrqAttachmentBadge");

    badge.textContent = `${attachments.length || getAttachmentCount(row)} Files`;
    rail.innerHTML = "";

    clearAttachmentPreview();

    if(!attachments.length){
      rail.innerHTML = '<div class="mrq-empty">No attachment available.</div>';
      return;
    }

    attachments.forEach((att, index) => {
      const fileName = att.filename || att.file_name || att.media_id || `Attachment ${index + 1}`;
      const fileUrl = normalizeAttachmentUrl(att.file_path || att.local_path || att.file_url || att.url);
      const mimeType = resolveAttachmentMimeType(att, fileName, fileUrl);
      const normalizedAttachment = {...att, file_name: fileName, file_url: fileUrl, mime_type: mimeType};

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "mrq-doc-item";
      btn.innerHTML = `
        <div class="mrq-doc-name">${htmlValue(fileName)}</div>
        <div class="mrq-doc-sub">${htmlValue(mimeType)} | ${htmlValue(att.file_size || "")}</div>
      `;
      btn.onclick = () => showAttachmentPreview(normalizedAttachment, btn);
      rail.appendChild(btn);

      if(index === 0){
        setTimeout(() => showAttachmentPreview(normalizedAttachment, btn), 0);
      }
    });
  }

  function clearAttachmentPreview(){
    const frame = document.getElementById("mrqAttachmentFrame");
    const image = document.getElementById("mrqAttachmentImage");

    document.getElementById("mrqAttachmentPreviewName").textContent = "No document selected";
    document.getElementById("mrqAttachmentPreviewSub").textContent = "Select a document to preview.";
    document.getElementById("mrqAttachmentPreviewEmpty").style.display = "block";

    frame.removeAttribute("src");
    frame.style.display = "none";
    image.removeAttribute("src");
    image.style.display = "none";

    document.getElementById("mrqAttachmentActions").innerHTML = '<span class="mrq-badge mrq-badge-unknown">No Action</span>';
  }

  function showAttachmentPreview(att, btn){
    document.querySelectorAll(".mrq-doc-item").forEach(x => x.classList.remove("active"));
    if(btn) btn.classList.add("active");

    const fileName = att.filename || att.file_name || att.media_id || "Attachment";
    const fileUrl = normalizeAttachmentUrl(att.file_path || att.local_path || att.file_url || att.url);
    const mimeType = resolveAttachmentMimeType(att, fileName, fileUrl);

    document.getElementById("mrqAttachmentPreviewName").textContent = displayValue(fileName);
    document.getElementById("mrqAttachmentPreviewSub").textContent = displayValue(mimeType);

    const empty = document.getElementById("mrqAttachmentPreviewEmpty");
    const frame = document.getElementById("mrqAttachmentFrame");
    const image = document.getElementById("mrqAttachmentImage");

    empty.style.display = "none";
    frame.removeAttribute("src");
    frame.style.display = "none";
    image.removeAttribute("src");
    image.style.display = "none";

    if(!fileUrl){
      empty.style.display = "block";
      empty.textContent = safeValue(att.remarks) || "No preview URL available.";
    }else if(mimeType.toLowerCase().startsWith("image/")){
      image.onerror = function(){
        image.style.display = "none";
        empty.style.display = "block";
        empty.textContent = "The image could not be loaded. Check whether the saved file exists under app/static.";
      };
      image.src = fileUrl;
      image.style.display = "block";
    }else{
      frame.src = fileUrl;
      frame.style.display = "block";
    }

    document.getElementById("mrqAttachmentActions").innerHTML = fileUrl
      ? `<a class="mrq-btn mrq-btn-light mrq-btn-sm" href="${escapeHtml(fileUrl)}" target="_blank" rel="noopener noreferrer">Open File</a>`
      : `<span class="mrq-badge mrq-badge-unknown">No File URL</span>`;
  }

  function renderPossibleMatches(row){
    const matchList = document.getElementById("mrqMatchList");
    const matches = parseJsonArray(row.possible_matches_json || row.matching_threads_json);

    matchList.innerHTML = "";

    if(!matches.length){
      matchList.innerHTML = '<div class="mrq-empty">No possible matches available. Click Search Matches to fetch candidates.</div>';
      return;
    }

    matches.forEach(match => {
      const card = document.createElement("div");
      card.className = "mrq-match-card";
      card.innerHTML = `
        <div class="mrq-match-top">
          <div>
            <div class="mrq-match-title">Thread: ${htmlValue(match.channel_thread_id)}</div>
            <div class="mrq-doc-sub">Queue ID: ${htmlValue(match.operation_queue_id || match.queue_id)}</div>
          </div>
          <button class="mrq-btn mrq-btn-primary mrq-btn-sm" type="button"
            onclick="selectMatchingThread('${safeInlineId(match.operation_queue_id || match.queue_id)}','${safeInlineId(match.channel_thread_id)}')">
            Select Thread
          </button>
        </div>
        <div class="mrq-match-meta">
          <div class="mrq-meta-box"><div class="k">Patient</div><div class="v">${htmlValue(match.patient_name)}</div></div>
          <div class="mrq-meta-box"><div class="k">Passport</div><div class="v">${htmlValue(match.patpassport_no)}</div></div>
          <div class="mrq-meta-box"><div class="k">Mobile</div><div class="v">${htmlValue(match.patient_contno)}</div></div>
          <div class="mrq-meta-box"><div class="k">Confidence</div><div class="v">${htmlValue(match.match_confidence || match.confidence)}</div></div>
        </div>
      `;
      matchList.appendChild(card);
    });
  }

  function selectMatchingThread(queueId, threadId){
    document.getElementById("detail_selected_thread_id").value = queueId || threadId || "";
    document.getElementById("detail_reviewer_decision").value = "LINK_EXISTING_THREAD";
  }

  function searchMatchingThreads(){
    alert("Backend endpoint needed: search matching threads by passport/mobile/message_log_id.");
  }

  function getFieldLabel(id){
    const el = document.getElementById(id);
    if(!el) return id;
    const item = el.closest(".mrq-detail-item");
    const label = item ? item.querySelector("label") : null;
    return label ? label.textContent.trim().replace(/\s+\*$/, "") : id;
  }

  function getFieldValue(id){
    const el = document.getElementById(id);
    if(!el) return "";

    if(el.tagName === "SELECT" && el.multiple){
      if(id === "detail_department"){
        return getSelectedManualDepartmentCsv();
      }

      return Array.from(el.selectedOptions)
        .map(option => safeValue(option.value).trim())
        .filter(Boolean)
        .join(",");
    }

    return safeValue(el.value).trim();
  }

  function clearManualValidationErrors(){
    document.querySelectorAll(".mrq-field-error").forEach(el => el.classList.remove("mrq-field-error"));
  }

  function markManualFieldError(id){
    const el = document.getElementById(id);
    if(el) el.classList.add("mrq-field-error");

    if(id === "detail_department"){
      document.getElementById("detail_department_summary")?.classList.add("mrq-field-error");
    }
  }

  function focusFirstManualField(id){
    const el = document.getElementById(id);
    if(!el) return;

    const collapsibleBody = el.closest(".mrq-collapsible-body");
    if(collapsibleBody && collapsibleBody.classList.contains("collapsed")){
      const toggleMap = {
        "attender1Body": "attender1Toggle",
        "attender2Body": "attender2Toggle",
        "attender3Body": "attender3Toggle"
      };
      const btnId = toggleMap[collapsibleBody.id];
      toggleMrqSection(collapsibleBody.id, btnId);
    }

    if(id === "detail_department"){
      const summary = document.getElementById("detail_department_summary");
      if(summary){
        summary.scrollIntoView({behavior:"smooth", block:"center"});
        setTimeout(() => summary.focus(), 250);
        return;
      }
    }

    el.scrollIntoView({behavior:"smooth", block:"center"});
    setTimeout(() => el.focus(), 250);
  }

  function sectionHasAnyData(fieldIds){
    return fieldIds.some(id => getFieldValue(id) !== "");
  }

  const mrqMandatoryPatientFields = [
    "detail_patient_given_name",
    "detail_patient_short_name",
    "detail_patient_passport_number",
    "detail_patient_passport_expiry_date",
    "detail_patient_gender",
    "detail_patient_dob",
    "detail_patient_age",
    "detail_patient_contact_number",
    "detail_patient_email",
    "detail_patient_nationality",
    "detail_patient_address"
  ];

  const mrqMandatoryHospitalRequestFields = [
    "detail_hospital_name",
    "detail_department",
    "detail_doctor_name",
    "detail_request_nationality",
    "detail_embassy",
    "detail_intent",
    "detail_approximate_date"
  ];

  const mrqMandatoryAttender1Fields = [
    "detail_attender1_given_name",
    "detail_attender1_short_name",
    "detail_attender1_passport_number",
    "detail_attender1_passport_expiry_date",
    "detail_attender1_gender",
    "detail_attender1_dob",
    "detail_attender1_age",
    "detail_attender1_nationality",
    "detail_attender1_contact_number",
    "detail_attender1_relationship",
    "detail_attender1_address"
  ];

  const mrqMandatoryAttender2Fields = [
    "detail_attender2_given_name",
    "detail_attender2_short_name",
    "detail_attender2_passport_number",
    "detail_attender2_passport_expiry_date",
    "detail_attender2_gender",
    "detail_attender2_dob",
    "detail_attender2_age",
    "detail_attender2_nationality",
    "detail_attender2_contact_number",
    "detail_attender2_relationship",
    "detail_attender2_address"
  ];

  const mrqMandatoryAttender3Fields = [
    "detail_attender3_given_name",
    "detail_attender3_short_name",
    "detail_attender3_passport_number",
    "detail_attender3_passport_expiry_date",
    "detail_attender3_gender",
    "detail_attender3_dob",
    "detail_attender3_age",
    "detail_attender3_nationality",
    "detail_attender3_contact_number",
    "detail_attender3_relationship",
    "detail_attender3_address"
  ];

  function applyMandatoryMarkers(){
    [
      ...mrqMandatoryPatientFields,
      ...mrqMandatoryHospitalRequestFields,
      ...mrqMandatoryAttender1Fields,
      ...mrqMandatoryAttender2Fields,
      ...mrqMandatoryAttender3Fields
    ].forEach(id => {
      const el = document.getElementById(id);
      const item = el ? el.closest(".mrq-detail-item") : null;
      if(item) item.classList.add("mrq-required");
    });
  }

  function validateMandatoryFieldsBeforeSubmit(){
    clearManualValidationErrors();

    let missing = [];
    let missingIds = [];

    function checkRequiredFields(fieldIds){
      fieldIds.forEach(id => {
        if(!getFieldValue(id)){
          missing.push(getFieldLabel(id));
          missingIds.push(id);
          markManualFieldError(id);
        }
      });
    }

    checkRequiredFields(mrqMandatoryPatientFields);
    checkRequiredFields(mrqMandatoryHospitalRequestFields);

    if(sectionHasAnyData(mrqMandatoryAttender1Fields)){
      checkRequiredFields(mrqMandatoryAttender1Fields);
    }

    if(sectionHasAnyData(mrqMandatoryAttender2Fields)){
      checkRequiredFields(mrqMandatoryAttender2Fields);
    }

    if(sectionHasAnyData(mrqMandatoryAttender3Fields)){
      checkRequiredFields(mrqMandatoryAttender3Fields);
    }

    if(missing.length){
      focusFirstManualField(missingIds[0]);
      alert("Please fill the following mandatory fields before submitting the record:\n\n- " + missing.join("\n- "));
      return false;
    }

    return true;
  }

  function collectManualReviewPayload(){
    return {
      message_log_id: document.getElementById("detail_message_log_id").value,
      classifier_reason: document.getElementById("detail_classifier_reason").value,

      patient_given_name: document.getElementById("detail_patient_given_name").value,
      patient_short_name: document.getElementById("detail_patient_short_name").value,
      patient_passport_number: document.getElementById("detail_patient_passport_number").value,
      patient_passport_expiry_date: document.getElementById("detail_patient_passport_expiry_date").value,
      patient_gender: document.getElementById("detail_patient_gender").value,
      patient_dob: document.getElementById("detail_patient_dob").value,
      patient_age: document.getElementById("detail_patient_age").value,
      patient_contact_number: document.getElementById("detail_patient_contact_number").value,
      patient_address: document.getElementById("detail_patient_address").value,
      patient_email: document.getElementById("detail_patient_email").value,
      patient_nationality: document.getElementById("detail_patient_nationality").value,

      attender1_given_name: document.getElementById("detail_attender1_given_name").value,
      attender1_short_name: document.getElementById("detail_attender1_short_name").value,
      attender1_passport_number: document.getElementById("detail_attender1_passport_number").value,
      attender1_passport_expiry_date: document.getElementById("detail_attender1_passport_expiry_date").value,
      attender1_gender: document.getElementById("detail_attender1_gender").value,
      attender1_dob: document.getElementById("detail_attender1_dob").value,
      attender1_age: document.getElementById("detail_attender1_age").value,
      attender1_address: document.getElementById("detail_attender1_address").value,
      attender1_nationality: document.getElementById("detail_attender1_nationality").value,
      attender1_contact_number: document.getElementById("detail_attender1_contact_number").value,
      attender1_relationship: document.getElementById("detail_attender1_relationship").value,

      attender2_given_name: document.getElementById("detail_attender2_given_name").value,
      attender2_short_name: document.getElementById("detail_attender2_short_name").value,
      attender2_passport_number: document.getElementById("detail_attender2_passport_number").value,
      attender2_passport_expiry_date: document.getElementById("detail_attender2_passport_expiry_date").value,
      attender2_gender: document.getElementById("detail_attender2_gender").value,
      attender2_dob: document.getElementById("detail_attender2_dob").value,
      attender2_age: document.getElementById("detail_attender2_age").value,
      attender2_address: document.getElementById("detail_attender2_address").value,
      attender2_nationality: document.getElementById("detail_attender2_nationality").value,
      attender2_contact_number: document.getElementById("detail_attender2_contact_number").value,
      attender2_relationship: document.getElementById("detail_attender2_relationship").value,

      attender3_given_name: document.getElementById("detail_attender3_given_name").value,
      attender3_short_name: document.getElementById("detail_attender3_short_name").value,
      attender3_passport_number: document.getElementById("detail_attender3_passport_number").value,
      attender3_passport_expiry_date: document.getElementById("detail_attender3_passport_expiry_date").value,
      attender3_gender: document.getElementById("detail_attender3_gender").value,
      attender3_dob: document.getElementById("detail_attender3_dob").value,
      attender3_age: document.getElementById("detail_attender3_age").value,
      attender3_address: document.getElementById("detail_attender3_address").value,
      attender3_nationality: document.getElementById("detail_attender3_nationality").value,
      attender3_contact_number: document.getElementById("detail_attender3_contact_number").value,
      attender3_relationship: document.getElementById("detail_attender3_relationship").value,

      agent_shrtnm: document.getElementById("detail_agent_shrtnm").value,
      hospital_name: document.getElementById("detail_hospital_name").value,
      department: getFieldValue("detail_department"),
      doctor_name: document.getElementById("detail_doctor_name").value,
      request_nationality: document.getElementById("detail_request_nationality").value,
      embassy: document.getElementById("detail_embassy").value,
      intent: document.getElementById("detail_intent").value,
      approximate_date: document.getElementById("detail_approximate_date").value,
      missing_fields: document.getElementById("detail_missing_fields").value,
      reviewer_remarks: document.getElementById("detail_reviewer_remarks").value
    };
  }

  function saveManualReview(){
    if(!validateMandatoryFieldsBeforeSubmit()){
      return;
    }

    const payload = collectManualReviewPayload();
    if(!payload.agent_shrtnm){
      alert("Please select an Agency from Partner Master.");
      document.getElementById("detail_agent_shrtnm").focus();
      return;
    }

    if(!payload.message_log_id){
      alert("Message Log ID is missing.");
      return;
    }

    fetch("/manual-review/save-review", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    })
    .then(response => response.json().then(data => ({status: response.status, data: data})))
    .then(result => {
      if(!result.data || !result.data.success){
        alert((result.data && result.data.message) ? result.data.message : "Unable to save manual review.");
        return;
      }

      alert(result.data.message || "Manual review saved. Parser will re-run.");
      window.location.reload();
    })
    .catch(error => {
      alert("Manual review save failed: " + error);
    });
  }

  function markCurrentAsJunk(){
    if(!mrqCurrentRow){
      alert("Please open a manual review record first.");
      return;
    }

    const messageId = getRowId(mrqCurrentRow);
    markAsJunk(messageId);
  }

  async function markAsJunk(messageId){
    if(!messageId){
      alert("Message Log ID is missing.");
      return;
    }

    if(!confirm("Mark this message as JUNK?")){
      return;
    }

    try{
      const response = await fetch(`/manual-review/mark-junk/${messageId}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        }
      });

      const result = await response.json();

      if(!result || !result.success){
        alert((result && result.message) ? result.message : "Unable to mark as JUNK.");
        return;
      }

      alert(result.message || "Message marked as JUNK successfully.");

      const rowIndex = manualReviewRows.findIndex(
        r => String(getRowId(r)) === String(messageId)
      );

      if(rowIndex !== -1){
        manualReviewRows.splice(rowIndex, 1);
      }

      mrqSelectedIds.delete(String(messageId));

      if(mrqCurrentRow && String(getRowId(mrqCurrentRow)) === String(messageId)){
        mrqCurrentRow = null;
        closeManualSidePanel();
      }

      updateKpiCards();
      applyManualReviewFilters();

    }catch(error){
      console.error("MARK_JUNK_ERROR:", error);
      alert("Unexpected error occurred while marking as JUNK.");
    }
  }

  document.getElementById("mrqMasterCheckbox").addEventListener("change", function(){
    toggleManualSelectAll(this.checked);
  });

  initManualDepartmentMultiSelect();
  applyMandatoryMarkers();
  updateKpiCards();
  applyManualReviewFilters();
