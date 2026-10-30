  const opqQueueRows = JSON.parse(document.getElementById("opqQueueRowsData").textContent || "[]");
  const opqUsersLov = JSON.parse(document.getElementById("opqUsersLovData").textContent || "[]");

  let opqGroupedThreads = [];
  let opqCurrentThread = null;
  let opqRenderedRows = [];
  let opqFilteredRows = [];
  let opqCurrentGridPage = 1;
  let opqGridPageSize = 10;
  let opqSelectedThreadIds = new Set();
  let opqCurrentThreadId = "";
  let opqCurrentGroupRows = [];
  let opqInitialDetailState = "";
  let opqTimelineExpanded = false;
  let opqCurrentEmailRow = null;
  let opqDashboardFilter = "";
  let opqBaseFilteredRows = [];

  // Department multi-select UI state.
  // The database continues to store the selected Departments as one CSV value.
  let opqDepartmentWorkingSelection = new Set();
  let opqDepartmentUnknownValues = [];

  function updateNearBreachCardState(count){
    const nearCard = document.getElementById("opqNearBreachCard");
    if(!nearCard) return;

    const numericCount = Number(count || 0);
    if(numericCount > 0){
      nearCard.classList.add("opq-stat-card-warning");
    }else{
      nearCard.classList.remove("opq-stat-card-warning");
    }
  }

  function normalizeDashboardFilterValue(value){
    return safeValue(value).trim().toUpperCase();
  }

  function getEscalationNumericValue(row){
    const rawValue = safeValue(
      row?.sla_current_escalation_level ??
      row?.current_escalation_level ??
      row?.sla_escalation_level ??
      row?.esc_level
    ).trim();

    const numericValue = Number(rawValue);
    if(!Number.isNaN(numericValue)) return numericValue;

    const digitsOnly = rawValue.replace(/[^0-9.-]/g, "");
    return digitsOnly ? Number(digitsOnly) || 0 : 0;
  }

  function matchesDashboardFilter(summaryRow){
    const activeFilter = normalizeDashboardFilterValue(opqDashboardFilter);
    if(!activeFilter){
      return true;
    }

    const row = summaryRow.display_row || summaryRow.latest_row || {};
    const slaStatus = normalizeDashboardFilterValue(getSlaStatus(row));
    const escalationLevel = getEscalationNumericValue(row);

    if(activeFilter === "NEAR BREACH") return slaStatus === "NEAR BREACH";
    if(activeFilter === "BREACHED") return slaStatus === "BREACHED";
    if(activeFilter === "ESCALATED") return escalationLevel > 0;

    return true;
  }

  function updateDashboardCardsActiveState(){
    const activeFilter = normalizeDashboardFilterValue(opqDashboardFilter);
    document.querySelectorAll(".opq-stat-card-clickable").forEach(card => {
      const cardFilter = normalizeDashboardFilterValue(card.getAttribute("data-dashboard-filter") || "");
      const shouldBeActive = (!activeFilter && !cardFilter) || (!!activeFilter && activeFilter === cardFilter);
      card.classList.toggle("opq-stat-card-active", shouldBeActive);
    });
  }

  function updateDashboardFilterIndicator(){
    const indicator = document.getElementById("opqDashboardFilterIndicator");
    if(!indicator) return;

    const activeFilter = normalizeDashboardFilterValue(opqDashboardFilter);
    if(!activeFilter){
      indicator.style.display = "none";
      indicator.textContent = "Dashboard Filter: All";
      return;
    }

    indicator.style.display = "inline-block";
    indicator.textContent = `Dashboard Filter: ${activeFilter}`;
  }

  function setDashboardFilter(value){
    const normalizedValue = normalizeDashboardFilterValue(value);
    opqDashboardFilter = normalizedValue;
    opqCurrentGridPage = 1;
    updateDashboardCardsActiveState();
    updateDashboardFilterIndicator();
    closeSidePanel();
    renderThreadGrid(false);
  }

  function bindDashboardCardEvents(){
    document.querySelectorAll(".opq-stat-card-clickable").forEach(card => {
      card.addEventListener("click", function(event){
        if(event){
          event.preventDefault();
          event.stopPropagation();
        }

        const filterValue = this.getAttribute("data-dashboard-filter") || "";
        const normalizedValue = normalizeDashboardFilterValue(filterValue);

        if(normalizedValue && opqDashboardFilter === normalizedValue){
          setDashboardFilter("");
          return;
        }

        setDashboardFilter(normalizedValue);
      });
    });

    updateDashboardCardsActiveState();
    updateDashboardFilterIndicator();
  }

  function getUserFullName(userCode){
    const code = safeValue(userCode).trim();
    if(!code){
      return "Unassigned";
    }

    const matched = (opqUsersLov || []).find(user => safeValue(user.user_code).trim().toLowerCase() === code.toLowerCase());
    return matched ? safeValue(matched.user_name || matched.user_code || code) : code;
  }

  function getAssignedDisplayName(rowOrCode){
    if(rowOrCode && typeof rowOrCode === "object") {
      return getUserFullName(rowOrCode.assigned_to);
    }
    return getUserFullName(rowOrCode);
  }

  function safeStr(value){
    return value === null || value === undefined ? "" : String(value);
  }

  function normalizeText(value){
    return safeStr(value).trim();
  }

  function safeValue(value){
    return value === null || value === undefined ? "" : String(value);
  }

  function escapeHtml(value){
    return safeValue(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function decodeHtmlEntities(value){
    if(value === null || value === undefined){
      return "";
    }
    const textarea = document.createElement("textarea");
    textarea.innerHTML = String(value);
    return textarea.value;
  }

  function convertHtmlEmailToPlainText(value){
    let text = safeValue(value);

    if(!text.trim()){
      return "";
    }

    text = text.replace(/\r\n/g, "\n");

    text = text.replace(/<br\s*\/?>/gi, "\n");
    text = text.replace(/<\/div>/gi, "\n");
    text = text.replace(/<div[^>]*>/gi, "");
    text = text.replace(/<\/p>/gi, "\n");
    text = text.replace(/<p[^>]*>/gi, "");
    text = text.replace(/<\/li>/gi, "\n");
    text = text.replace(/<li[^>]*>/gi, "- ");
    text = text.replace(/<\/ul>/gi, "\n");
    text = text.replace(/<ul[^>]*>/gi, "");
    text = text.replace(/<\/ol>/gi, "\n");
    text = text.replace(/<ol[^>]*>/gi, "");
    text = text.replace(/<\/h[1-6]>/gi, "\n");
    text = text.replace(/<h[1-6][^>]*>/gi, "");
    text = text.replace(/&nbsp;/gi, " ");

    text = text.replace(/<[^>]+>/g, "");
    text = decodeHtmlEntities(text);

    text = text.replace(/\n{3,}/g, "\n\n");
    text = text.replace(/[ \t]+\n/g, "\n");
    text = text.replace(/\n[ \t]+/g, "\n");

    return text.trim();
  }

  function setInputValue(id, value){
    const el = document.getElementById(id);
    if(el){ el.value = safeValue(value); }
  }

  function setSelectValue(id, value){
    const el = document.getElementById(id);
    if(!el) return;

    const incoming = safeValue(value).trim();

    if(!incoming){
      el.value = "";
      return;
    }

    el.value = incoming;
    if(el.value === incoming){
      return;
    }

    const normalizedIncoming = incoming.replace(/\s+/g, " ").trim().toLowerCase();

    const matchedOption = Array.from(el.options || []).find(option => {
      const optionValue = safeValue(option.value).replace(/\s+/g, " ").trim().toLowerCase();
      const optionText = safeValue(option.text).replace(/\s+/g, " ").trim().toLowerCase();
      return optionValue === normalizedIncoming || optionText === normalizedIncoming;
    });

    if(matchedOption){
      el.value = matchedOption.value;
      return;
    }

    const dynamicOption = document.createElement("option");
    dynamicOption.value = incoming;
    dynamicOption.text = incoming;
    el.appendChild(dynamicOption);
    el.value = incoming;
  }

  function normalizeDepartmentKey(value){
    return safeValue(value).replace(/\s+/g, " ").trim().toLowerCase();
  }

  function splitDepartmentValues(value){
    const rawItems = Array.isArray(value) ? value : [value];
    const values = [];
    const seen = new Set();

    rawItems.forEach(rawItem => {
      safeValue(rawItem)
        .replace(/\|/g, ",")
        .replace(/;/g, ",")
        .split(",")
        .map(item => item.trim())
        .filter(Boolean)
        .forEach(item => {
          const key = normalizeDepartmentKey(item);
          if(!key || seen.has(key)) return;
          seen.add(key);
          values.push(item);
        });
    });

    return values;
  }

  function getOperationDepartmentSelect(){
    return document.getElementById("detail_hospital_dept");
  }

  function getOperationDepartmentOptions(){
    const select = getOperationDepartmentSelect();
    if(!select) return [];

    return Array.from(select.options || []).filter(option => safeValue(option.value).trim());
  }

  function getSelectedOperationDepartments(){
    const select = getOperationDepartmentSelect();
    if(!select) return [];

    return Array.from(select.selectedOptions || [])
      .map(option => safeValue(option.value).trim())
      .filter(Boolean);
  }

  function getSelectedOperationDepartmentCsv(){
    return getSelectedOperationDepartments().join(",");
  }

  function updateOperationDepartmentWarning(){
    const warning = document.getElementById("detail_hospital_dept_warning");
    if(!warning) return;

    if(!opqDepartmentUnknownValues.length){
      warning.style.display = "none";
      warning.textContent = "";
      return;
    }

    const label = opqDepartmentUnknownValues.length === 1
      ? "Unrecognized / unavailable Department"
      : "Unrecognized / unavailable Departments";

    warning.textContent = `${label}: ${opqDepartmentUnknownValues.join(", ")}`;
    warning.style.display = "block";
  }

  function refreshOperationDepartmentControl(){
    const select = getOperationDepartmentSelect();
    const chips = document.getElementById("detail_hospital_dept_chips");
    const count = document.getElementById("detail_hospital_dept_count");

    if(!select || !chips) return;

    const selectedValues = getSelectedOperationDepartments();
    chips.innerHTML = "";

    if(!selectedValues.length){
      const placeholder = document.createElement("span");
      placeholder.className = "opq-dept-placeholder";
      placeholder.id = "detail_hospital_dept_placeholder";
      placeholder.textContent = "Select Department";
      chips.appendChild(placeholder);
    }else{
      selectedValues.forEach(value => {
        const chip = document.createElement("span");
        chip.className = "opq-dept-chip";
        chip.setAttribute("data-department", value);

        const label = document.createElement("span");
        label.className = "opq-dept-chip-label";
        label.textContent = value;
        chip.appendChild(label);

        const remove = document.createElement("span");
        remove.className = "opq-dept-chip-remove";
        remove.setAttribute("role", "button");
        remove.setAttribute("tabindex", select.disabled ? "-1" : "0");
        remove.setAttribute("aria-label", `Remove ${value}`);
        remove.textContent = "×";

        const removeHandler = event => {
          event.preventDefault();
          event.stopPropagation();
          if(select.disabled) return;
          removeOperationDepartmentSelection(value);
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

    updateOperationDepartmentWarning();
  }

  function setOperationDepartmentSelection(value){
    const select = getOperationDepartmentSelect();
    if(!select) return;

    const incomingValues = splitDepartmentValues(value);
    const optionMap = new Map();

    getOperationDepartmentOptions().forEach(option => {
      option.selected = false;
      optionMap.set(normalizeDepartmentKey(option.value), option);
      optionMap.set(normalizeDepartmentKey(option.text), option);
    });

    const unknownValues = [];
    const unknownSeen = new Set();

    incomingValues.forEach(incomingValue => {
      const key = normalizeDepartmentKey(incomingValue);
      const matchedOption = optionMap.get(key);

      if(matchedOption){
        matchedOption.selected = true;
        return;
      }

      if(key && !unknownSeen.has(key)){
        unknownSeen.add(key);
        unknownValues.push(incomingValue);
      }
    });

    opqDepartmentUnknownValues = unknownValues;
    opqDepartmentWorkingSelection = new Set(
      getSelectedOperationDepartments().map(normalizeDepartmentKey)
    );

    refreshOperationDepartmentControl();
  }

  function renderOperationDepartmentOptions(){
    const container = document.getElementById("detail_hospital_dept_options");
    const search = normalizeDepartmentKey(
      document.getElementById("detail_hospital_dept_search")?.value || ""
    );

    if(!container) return;

    container.innerHTML = "";
    let visibleCount = 0;

    getOperationDepartmentOptions().forEach(option => {
      const value = safeValue(option.value).trim();
      const labelText = safeValue(option.text || option.value).trim();
      const key = normalizeDepartmentKey(value);

      if(search && !normalizeDepartmentKey(labelText).includes(search)){
        return;
      }

      visibleCount += 1;

      const row = document.createElement("label");
      row.className = "opq-dept-option";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "opq-dept-option-checkbox";
      checkbox.value = value;
      checkbox.checked = opqDepartmentWorkingSelection.has(key);
      checkbox.disabled = option.disabled;

      checkbox.addEventListener("change", function(){
        if(this.checked){
          opqDepartmentWorkingSelection.add(key);
        }else{
          opqDepartmentWorkingSelection.delete(key);
        }
      });

      const label = document.createElement("span");
      label.textContent = labelText;

      row.appendChild(checkbox);
      row.appendChild(label);
      container.appendChild(row);
    });

    if(!visibleCount){
      const empty = document.createElement("div");
      empty.className = "opq-dept-option-empty";
      empty.textContent = "No Departments found.";
      container.appendChild(empty);
    }
  }

  function openOperationDepartmentDropdown(){
    const select = getOperationDepartmentSelect();
    const dropdown = document.getElementById("detail_hospital_dept_dropdown");
    const summary = document.getElementById("detail_hospital_dept_summary");
    const search = document.getElementById("detail_hospital_dept_search");

    if(!select || !dropdown || !summary || select.disabled || summary.disabled){
      return;
    }

    opqDepartmentWorkingSelection = new Set(
      getSelectedOperationDepartments().map(normalizeDepartmentKey)
    );

    if(search){
      search.value = "";
    }

    renderOperationDepartmentOptions();
    dropdown.hidden = false;
    summary.setAttribute("aria-expanded", "true");

    if(search){
      setTimeout(() => search.focus(), 0);
    }
  }

  function closeOperationDepartmentDropdown(applyChanges){
    const select = getOperationDepartmentSelect();
    const dropdown = document.getElementById("detail_hospital_dept_dropdown");
    const summary = document.getElementById("detail_hospital_dept_summary");

    if(!select || !dropdown || !summary) return;

    if(applyChanges){
      const before = getSelectedOperationDepartmentCsv();

      getOperationDepartmentOptions().forEach(option => {
        option.selected = opqDepartmentWorkingSelection.has(
          normalizeDepartmentKey(option.value)
        );
      });

      // Applying an amended selection intentionally removes any previously
      // unrecognized incoming Department text instead of creating a fake LOV item.
      opqDepartmentUnknownValues = [];
      refreshOperationDepartmentControl();

      const after = getSelectedOperationDepartmentCsv();
      if(before !== after){
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }else{
      opqDepartmentWorkingSelection = new Set(
        getSelectedOperationDepartments().map(normalizeDepartmentKey)
      );
    }

    dropdown.hidden = true;
    summary.setAttribute("aria-expanded", "false");
  }

  function removeOperationDepartmentSelection(value){
    const select = getOperationDepartmentSelect();
    if(!select || select.disabled) return;

    const targetKey = normalizeDepartmentKey(value);
    let changed = false;

    getOperationDepartmentOptions().forEach(option => {
      if(normalizeDepartmentKey(option.value) === targetKey && option.selected){
        option.selected = false;
        changed = true;
      }
    });

    if(!changed) return;

    opqDepartmentWorkingSelection = new Set(
      getSelectedOperationDepartments().map(normalizeDepartmentKey)
    );
    refreshOperationDepartmentControl();
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function renderDepartmentGridHtml(value){
    const departments = splitDepartmentValues(value);
    if(!departments.length){
      return "-";
    }

    return `
      <div class="opq-grid-dept-wrap">
        ${departments.map(department => `
          <span class="opq-grid-dept-chip">${escapeHtml(department)}</span>
        `).join("")}
      </div>
    `;
  }

  function initOperationDepartmentMultiSelect(){
    const control = document.getElementById("detail_hospital_dept_control");
    const select = getOperationDepartmentSelect();
    const summary = document.getElementById("detail_hospital_dept_summary");
    const dropdown = document.getElementById("detail_hospital_dept_dropdown");
    const search = document.getElementById("detail_hospital_dept_search");
    const applyBtn = document.getElementById("detail_hospital_dept_apply");
    const cancelBtn = document.getElementById("detail_hospital_dept_cancel");

    if(!control || !select || !summary || !dropdown){
      return;
    }

    summary.addEventListener("click", function(event){
      event.preventDefault();
      event.stopPropagation();

      if(dropdown.hidden){
        openOperationDepartmentDropdown();
      }else{
        closeOperationDepartmentDropdown(false);
      }
    });

    dropdown.addEventListener("click", function(event){
      event.stopPropagation();
    });

    if(search){
      search.addEventListener("input", renderOperationDepartmentOptions);
    }

    if(applyBtn){
      applyBtn.addEventListener("click", function(event){
        event.preventDefault();
        event.stopPropagation();
        closeOperationDepartmentDropdown(true);
      });
    }

    if(cancelBtn){
      cancelBtn.addEventListener("click", function(event){
        event.preventDefault();
        event.stopPropagation();
        closeOperationDepartmentDropdown(false);
      });
    }

    select.addEventListener("change", refreshOperationDepartmentControl);

    document.addEventListener("click", function(event){
      if(!dropdown.hidden && !control.contains(event.target)){
        closeOperationDepartmentDropdown(false);
      }
    });

    document.addEventListener("keydown", function(event){
      if(event.key === "Escape" && !dropdown.hidden){
        closeOperationDepartmentDropdown(false);
      }
    });

    refreshOperationDepartmentControl();
  }

  function setTextValue(id, value){
    const el = document.getElementById(id);
    if(el){ el.textContent = safeValue(value) || "-"; }
  }

  async function loadHospitalLov(selectedValue){
    const el = document.getElementById("detail_hospital_shrtnm");
    if(!el) return;

    const selected = safeValue(selectedValue || el.value).trim();
    el.innerHTML = '<option value="">Select Hospital</option>';

    try{
      const response = await fetch("/get-hospitals", {
        method: "GET",
        headers: { "X-Requested-With": "XMLHttpRequest" }
      });

      if(!response.ok){
        throw new Error(`HTTP ${response.status}`);
      }

      const hospitals = await response.json();

      (Array.isArray(hospitals) ? hospitals : []).forEach(item => {
        const value = safeValue(item).trim();
        if(!value) return;

        const opt = document.createElement("option");
        opt.value = value;
        opt.text = value;
        el.appendChild(opt);
      });

      setSelectValue("detail_hospital_shrtnm", selected);
    }catch(error){
      console.error("Failed to load hospital LOV:", error);
      if(selected){
        setSelectValue("detail_hospital_shrtnm", selected);
      }
    }
  }

  function normalizeForCompare(value){
    return safeValue(value).trim().replace(/\s+/g, " ").toLowerCase();
  }

  function getRowDateValue(row){
    return safeValue(
      row?.latest_message_on ||
      row?.received_on ||
      row?.updated_on ||
      row?.created_on
    );
  }

  function getRowDateMs(row){
    const raw = getRowDateValue(row);
    if(!raw) return 0;
    const date = new Date(raw.replace(" ", "T"));
    return isNaN(date.getTime()) ? 0 : date.getTime();
  }

  function formatDateTime(value){
    const raw = safeValue(value);
    if(!raw) return "";

    if(typeof raw !== "string"){
      return String(raw);
    }

    const normalized = raw.trim().replace("T", " ");

    const match = normalized.match(
      /^(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?$/
    );

    if(!match){
      return raw;
    }

    const year = match[1];
    const month = match[2];
    const day = match[3];
    const hour24 = parseInt(match[4] || "0", 10);
    const minute = match[5] || "00";
    const second = match[6] || "00";

    const ampm = hour24 >= 12 ? "PM" : "AM";
    let hour12 = hour24 % 12;
    if(hour12 === 0) hour12 = 12;

    return `${parseInt(month, 10)}/${parseInt(day, 10)}/${year}, ${hour12}:${minute}:${second} ${ampm}`;
  }

  function formatDateOnly(value){
    const raw = safeValue(value);
    if(!raw) return "";
    const date = new Date(raw);
    if(isNaN(date.getTime())) return raw;
    return date.toISOString().slice(0, 10);
  }

  function getRowIdValue(row){
    const rawId = safeValue(row?.id).trim();
    const parsed = Number(rawId);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function chooseLatestRow(rows){
    if(!rows || !rows.length) return null;

    return rows.slice().sort((a, b) => {
      const idDiff = getRowIdValue(b) - getRowIdValue(a);
      if(idDiff !== 0){
        return idDiff;
      }

      const dateDiff = getRowDateMs(b) - getRowDateMs(a);
      if(dateDiff !== 0){
        return dateDiff;
      }

      return 0;
    })[0];
  }

  function getCompletenessScore(row){
    if(!row) return 0;

    const weightedKeys = [
      ["patgiven_nm", 2],
      ["patsur_nm", 1],
      ["patpassport_no", 2],
      ["patient_country", 1],
      ["patient_gender", 1],
      ["patient_dob", 1],
      ["patient_contno", 1],
      ["patient_email", 1],
      ["patient_address", 1],
      ["hospital_shrtnm", 1],
      ["hospital_dept", 1],
      ["doctor_nm", 1],
      ["intent_remarks", 1],
      ["vil_date", 1],
      ["appointment_dt", 1],

      ["attn1_givennm", 3],
      ["attn1_surnm", 2],
      ["attn1passport_no", 3],
      ["attn1pat_relation", 2],
      ["attn1_country", 1],
      ["attn1_dob", 1],
      ["attn1_gender", 1],
      ["attn1_contno", 1],
      ["attn1_address", 1],
      ["attn1_email", 1],
      ["attn1pass_expirydt", 1],

      ["attn2_givennm", 3],
      ["attn2_surnm", 2],
      ["attn2passport_no", 3],
      ["attn2pat_relation", 2],
      ["attn2_country", 1],
      ["attn2_dob", 1],
      ["attn2_gender", 1],
      ["attn2_contno", 1],
      ["attn2_address", 1],
      ["attn2_email", 1],
      ["attn2pass_expirydt", 1],

      ["attn3_givennm", 3],
      ["attn3_surnm", 2],
      ["attn3passport_no", 3],
      ["attn3pat_relation", 2],
      ["attn3_country", 1],
      ["attn3_dob", 1],
      ["attn3_gender", 1],
      ["attn3_contno", 1],
      ["attn3_address", 1],
      ["attn3_email", 1],
      ["attn3pass_expirydt", 1]
    ];

    return weightedKeys.reduce((score, item) => {
      const key = item[0];
      const weight = item[1];
      return score + (safeValue(row[key]).trim() ? weight : 0);
    }, 0);
  }

  function chooseBestRow(rows){
    if(!rows || !rows.length) return null;

    return rows.slice().sort((a, b) => {
      const scoreDiff = getCompletenessScore(b) - getCompletenessScore(a);
      if(scoreDiff !== 0){
        return scoreDiff;
      }
      return getRowDateMs(b) - getRowDateMs(a);
    })[0];
  }

  function mergeThreadDisplayRow(primaryRow, bestRow){
    const base = Object.assign({}, primaryRow || {});
    const source = bestRow || {};

    const mergeKeys = [
      "id",
      "channel_thread_id",
      "channel_message_id",
      "channel_code",
      "patgiven_nm",
      "patsur_nm",
      "patpassport_no",
      "patient_country",
      "patient_gender",
      "patient_dob",
      "patient_contno",
      "patient_email",
      "patient_address",
      "patient_age",
      "patpass_expirydt",
      "attn1_givennm",
      "attn1_surnm",
      "attn1passport_no",
      "attn1pat_relation",
      "attn1_country",
      "attn1_dob",
      "attn1_gender",
      "attn1_contno",
      "attn1_age",
      "attn1_address",
      "attn1_email",
      "attn1pass_expirydt",
      "attn2_givennm",
      "attn2_surnm",
      "attn2passport_no",
      "attn2pat_relation",
      "attn2_country",
      "attn2_dob",
      "attn2_gender",
      "attn2_contno",
      "attn2_age",
      "attn2_address",
      "attn2_email",
      "attn2pass_expirydt",
      "attn3_givennm",
      "attn3_surnm",
      "attn3passport_no",
      "attn3pat_relation",
      "attn3_country",
      "attn3_dob",
      "attn3_gender",
      "attn3_contno",
      "attn3_age",
      "attn3_address",
      "attn3_email",
      "attn3pass_expirydt",
      "ivac_center",
      "hospital_shrtnm",
      "hospital_dept",
      "doctor_nm",
      "intent_remarks",
      "request_for",
      "vil_date",
      "appointment_dt",
      "priority_status",
      "wrkflw_status",
      "assigned_to",
      "assigned_by",
      "assigned_at",
      "parse_status",
      "parse_remarks",
      "agent_shrtnm",
      "latest_message_text",
      "raw_message_text",
      "latest_message_on",
      "received_on",
      "created_on",
      "updated_on"
    ];

    mergeKeys.forEach(key => {
      if(!safeValue(base[key]).trim() && safeValue(source[key]).trim()){
        base[key] = source[key];
      }
    });

    return base;
  }

  function formatSortDateMs(value){
    const raw = safeValue(value);
    if(!raw) return 0;

    const normalized = raw.replace(" ", "T");
    const date = new Date(normalized);
    return isNaN(date.getTime()) ? 0 : date.getTime();
  }

  function chooseLatestAssignedRow(rows){
    if(!rows || !rows.length) return null;

    const rowsWithAssignedAt = rows.filter(row => safeValue(row.assigned_at));

    if(rowsWithAssignedAt.length){
      return rowsWithAssignedAt.slice().sort((a, b) => {
        const idDiff = getRowIdValue(b) - getRowIdValue(a);
        if(idDiff !== 0){
          return idDiff;
        }

        const assignedDiff = formatSortDateMs(b.assigned_at) - formatSortDateMs(a.assigned_at);
        if(assignedDiff !== 0){
          return assignedDiff;
        }

        return 0;
      })[0];
    }

    return chooseLatestRow(rows);
  }

  function groupRowsByThread(rows){
    const grouped = {};
    (rows || []).forEach(row => {
      const threadId = safeValue(row.channel_thread_id || row.channel_message_id || row.id);
      if(!grouped[threadId]) grouped[threadId] = [];
      grouped[threadId].push(row);
    });
    return grouped;
  }

  function getThreadAttachments(groupRows){
    const attachments = [];
    const seen = new Set();

    (groupRows || []).forEach(row => {
      const threadAttachments = Array.isArray(row.thread_attachments) ? row.thread_attachments : [];
      threadAttachments.forEach(att => {
        const uniqueKey = [
          safeValue(att.id),
          safeValue(att.file_name),
          safeValue(att.file_path),
          safeValue(att.created_on)
        ].join("|");

        if(seen.has(uniqueKey)) return;
        seen.add(uniqueKey);
        attachments.push(att);
      });
    });

    attachments.sort((a, b) => {
      return formatSortDateMs(b.created_on || b.updated_on) - formatSortDateMs(a.created_on || a.updated_on);
    });

    return attachments;
  }

  function buildThreadSummaryRows(rows){
    const grouped = groupRowsByThread(rows);
    return Object.keys(grouped).map(threadId => {
      const groupRows = grouped[threadId];
      const latestRow = chooseLatestRow(groupRows) || {};
      const bestRow = chooseBestRow(groupRows) || latestRow || {};
      const latestAssignedRow = chooseLatestAssignedRow(groupRows) || bestRow || latestRow || {};
      const displayRow = mergeThreadDisplayRow(latestRow, bestRow);

      return {
        thread_id: threadId,
        message_count: groupRows.length,
        latest_row: latestRow,
        best_row: bestRow,
        latest_assigned_row: latestAssignedRow,
        display_row: displayRow,
        group_rows: groupRows,
        thread_attachments: getThreadAttachments(groupRows)
      };
    }).sort((a, b) => getRowDateMs(b.latest_row) - getRowDateMs(a.latest_row));
  }

  function normalizeThreadKey(value){
    return normalizeForCompare(value);
  }

  function getThreadGroup(threadId){
    const normalizedThreadId = normalizeThreadKey(threadId);
    if(!normalizedThreadId){
      return null;
    }
    return (opqFilteredRows || []).find(row => normalizeThreadKey(row.thread_id) === normalizedThreadId) || null;
  }

  function getThreadGroupFromAllRows(threadId){
    const normalizedThreadId = normalizeThreadKey(threadId);
    if(!normalizedThreadId){
      return null;
    }

    return buildThreadSummaryRows(opqQueueRows).find(row => normalizeThreadKey(row.thread_id) === normalizedThreadId) || null;
  }

  function getThreadGroupByQueueId(queueId){
    const normalizedQueueId = safeValue(queueId).trim();
    if(!normalizedQueueId){
      return null;
    }

    return buildThreadSummaryRows(opqQueueRows).find(summary => {
      const candidateQueueId = safeValue(
        (summary.best_row || {}).id ||
        (summary.display_row || {}).id ||
        (summary.latest_assigned_row || {}).id ||
        (summary.latest_row || {}).id
      ).trim();
      return candidateQueueId === normalizedQueueId;
    }) || null;
  }

  function focusThreadGroup(summary){
    if(!summary){
      return false;
    }

    const threadId = safeValue(summary.thread_id).trim();
    const normalizedThreadId = normalizeThreadKey(threadId);
    if(!normalizedThreadId){
      return false;
    }

    const filteredIndex = (opqFilteredRows || []).findIndex(item => normalizeThreadKey(item.thread_id) === normalizedThreadId);
    if(filteredIndex >= 0){
      opqCurrentGridPage = Math.floor(filteredIndex / opqGridPageSize) + 1;
      renderThreadGrid(false);
    }

    return openThreadFromGrid(threadId);
  }

  function getContextThreadId(){
    const params = new URLSearchParams(window.location.search || '');
    return safeValue(
      params.get('channel_thread_id') ||
      params.get('thread_id') ||
      document.getElementById('detail_channel_thread_id')?.value ||
      document.getElementById('detail_channel_thread_id_text')?.textContent
    ).trim();
  }

  function openPreferredThread(preferredThreadId, fallbackToFirst){
    const candidateThreadId = safeValue(preferredThreadId).trim();

    if(candidateThreadId){
      const matchedSummary = getThreadGroup(candidateThreadId) || getThreadGroupFromAllRows(candidateThreadId);
      if(matchedSummary){
        return focusThreadGroup(matchedSummary);
      }
    }

    if(fallbackToFirst){
      const firstThread = (opqRenderedRows || [])[0];
      if(firstThread){
        populateDetailedView(
          getPreferredDetailRow(firstThread),
          firstThread.group_rows || [],
          firstThread.thread_attachments || []
        );
        setActiveGridRow(firstThread.thread_id || "");
        return true;
      }
    }

    return false;
  }

  function openThreadFromContext(){
    const params = new URLSearchParams(window.location.search || '');

    const candidateThreadId = getContextThreadId();

    const candidateQueueId = safeValue(
      params.get('queue_id') ||
      document.getElementById('queue_id')?.value
    ).trim();

    let matchedSummary = null;

    if(candidateThreadId){
      matchedSummary = getThreadGroup(candidateThreadId) || getThreadGroupFromAllRows(candidateThreadId);
    }

    if(!matchedSummary && candidateQueueId){
      matchedSummary = getThreadGroupByQueueId(candidateQueueId);
    }

    if(matchedSummary){
      return focusThreadGroup(matchedSummary);
    }

    return false;
  }

  function badgeClassForStatus(status){
    const normalized = normalizeForCompare(status);
    if(normalized === "assigned") return "opq-badge-assigned";
    if(normalized === "open" || normalized === "new") return "opq-badge-open";
    if(normalized === "in progress") return "opq-badge-medium";
    if(normalized === "closed") return "opq-badge-unassigned";
    return "opq-badge-unassigned";
  }

  function getPreferredDetailRow(summary){
    const item = summary && typeof summary === "object" ? summary : {};
    return item.latest_row || item.display_row || item.latest_assigned_row || item.best_row || {};
  }

  function badgeClassForPriority(priority){
    const normalized = normalizeForCompare(priority);
    if(normalized === "high") return "opq-badge-high";
    if(normalized === "medium") return "opq-badge-medium";
    return "opq-badge-unassigned";
  }

  function isClosedWorkflowStatus(status){
    const normalized = normalizeForCompare(status);
    return normalized === "closed";
  }

  function normalizeRequestForValue(value){
    const normalized = normalizeForCompare(value);
    if(!normalized){
      return "";
    }
    if(normalized.includes("appoint")){
      return "appointment";
    }
    if(normalized.includes("vil")){
      return "vil";
    }
    return normalized;
  }

  function isEmailAlreadySentForRow(row){
    const item = row || {};
    const emailStatus = normalizeForCompare(item.email_status);
    const currentRequest = normalizeRequestForValue(item.request_for || item.intent_remarks);
    const sentRequest = normalizeRequestForValue(item.sent_intent_remarks);
    const trackedCurrent = currentRequest === "vil" || currentRequest === "appointment";
    const trackedSent = sentRequest === "vil" || sentRequest === "appointment";
    return emailStatus === "sent" && trackedCurrent && trackedSent && currentRequest === sentRequest;
  }

  function updateEmailLauncherState(rowOrWorkflowStatus, maybeEmailStatus, maybeRequestFor, maybeTemplateName){
    const emailBtn = document.getElementById("opqOpenEmailWindowBtn");
    if(!emailBtn){
      return;
    }

    let workflowStatus = "";
    let emailStatus = "";
    let requestFor = "";
    let sentIntentRemarks = "";
    let templateName = "";

    if(rowOrWorkflowStatus && typeof rowOrWorkflowStatus === "object"){
      workflowStatus = safeValue(rowOrWorkflowStatus.wrkflw_status);
      emailStatus = safeValue(rowOrWorkflowStatus.email_status);
      requestFor = safeValue(rowOrWorkflowStatus.request_for || rowOrWorkflowStatus.intent_remarks);
      sentIntentRemarks = safeValue(rowOrWorkflowStatus.sent_intent_remarks);
      templateName = safeValue(rowOrWorkflowStatus.email_template_name || rowOrWorkflowStatus.email_template_code);
    }else{
      workflowStatus = safeValue(rowOrWorkflowStatus);
      emailStatus = safeValue(maybeEmailStatus);
      requestFor = safeValue(maybeRequestFor);
      templateName = safeValue(maybeTemplateName);
    }

    const closed = isClosedWorkflowStatus(workflowStatus);
    const currentRequest = normalizeRequestForValue(requestFor);
    const sentRequest = normalizeRequestForValue(sentIntentRemarks);
    const alreadySentSameRequest = normalizeForCompare(emailStatus) === "sent"
      && (currentRequest === "vil" || currentRequest === "appointment")
      && currentRequest === sentRequest;
    const disabled = closed || alreadySentSameRequest;

    emailBtn.disabled = disabled;
    emailBtn.style.opacity = disabled ? "0.55" : "";
    emailBtn.style.cursor = disabled ? "not-allowed" : "";
    emailBtn.title = closed
      ? "Email communication is disabled because this channel thread is CLOSED."
      : alreadySentSameRequest
        ? `Email communication is disabled because email is already SENT${templateName ? ` using template ${templateName}` : ""} for this request.`
        : "Open Email Communication Window";

    // Process Without Email is only a valid action for an
    // open case whose Patient / Workflow ingestion is not complete.
    const noEmailBtn = document.getElementById(
      "opqProcessWithoutEmailBtn"
    );

    if(noEmailBtn){
      const flagIsTrue = (value) => {
        const normalized = safeValue(value)
          .trim()
          .toLowerCase();

        return [
          "1",
          "true",
          "yes",
          "y"
        ].includes(normalized);
      };

      let patientCreated = false;
      let workflowCreated = false;
      let converted = false;

      if(
        rowOrWorkflowStatus &&
        typeof rowOrWorkflowStatus === "object"
      ){
        patientCreated = flagIsTrue(
          rowOrWorkflowStatus.patient_created_flag
        );

        workflowCreated = flagIsTrue(
          rowOrWorkflowStatus.workflow_created_flag
        );

        converted = flagIsTrue(
          rowOrWorkflowStatus.converted_flag
        );
      }

      const ingestionCompleted =
        patientCreated && workflowCreated;

      const noEmailDisabled =
        closed ||
        ingestionCompleted ||
        converted;

      noEmailBtn.disabled = noEmailDisabled;

      noEmailBtn.style.opacity =
        noEmailDisabled ? "0.55" : "";

      noEmailBtn.style.cursor =
        noEmailDisabled ? "not-allowed" : "";

      if(closed){
        noEmailBtn.title =
          "Process Without Email is disabled because this case is CLOSED.";
      }
      else if(ingestionCompleted || converted){
        noEmailBtn.title =
          "Process Without Email is disabled because Patient and Workflow ingestion has already been completed.";
      }
      else{
        noEmailBtn.title =
          "Process this case without sending an email.";
      }
    }
  }

  function getSlaStatus(row){
    return safeValue(
      row?.sla_status ||
      row?.status ||
      row?.sla_runtime_status ||
      row?.sla_log_status
    ).trim().toUpperCase();
  }

  function getSlaRuleCode(row){
    return safeValue(
      row?.sla_rule_code ||
      row?.rule_code
    ).trim();
  }

  function getSlaElapsedMins(row){
    const value = safeValue(
      row?.sla_elapsed_mins ??
      row?.elapsed_mins
    ).trim();
    return value || "0";
  }

  function getSlaEscalationLevel(row){
    const level = safeValue(
      row?.sla_current_escalation_level ??
      row?.current_escalation_level ??
      row?.sla_escalation_level
    ).trim();

    if(!level){
      return "-";
    }

    return `L${level}`;
  }

  function getSlaEscalatedTo(row){
    return safeValue(
      row?.sla_escalated_to ||
      row?.escalated_to
    ).trim();
  }

  function getSlaTargetTime(row){
    return safeValue(
      row?.sla_target_time ||
      row?.target_time
    ).trim();
  }

  function getSlaStartTime(row){
    return safeValue(
      row?.sla_start_time ||
      row?.start_time
    ).trim();
  }

  function getSlaRemarks(row){
    return safeValue(
      row?.sla_remarks ||
      row?.remarks
    ).trim();
  }

  function getSlaBadgeClass(status){
    const normalized = safeValue(status).trim().toUpperCase();

    if(normalized === "BREACHED") return "opq-badge-sla-breached";
    if(normalized === "NEAR BREACH") return "opq-badge-sla-near";
    if(normalized === "MET") return "opq-badge-sla-met";
    if(normalized === "RUNNING") return "opq-badge-sla-running";

    return "opq-badge-sla-none";
  }

  function getSlaAlertClass(status){
    const normalized = safeValue(status).trim().toUpperCase();

    if(normalized === "BREACHED") return "opq-sla-alert-breached";
    if(normalized === "NEAR BREACH") return "opq-sla-alert-near";
    if(normalized === "MET") return "opq-sla-alert-met";

    return "opq-sla-alert-running";
  }

  function renderSlaBadge(status){
    const normalized = safeValue(status).trim().toUpperCase() || "-";
    const badgeClass = getSlaBadgeClass(normalized);
    return `<span class="opq-badge ${badgeClass}">${escapeHtml(normalized)}</span>`;
  }

  function updateSlaSummaryCards(rows){
    const allRows = Array.isArray(rows) ? rows : [];

    let nearCount = 0;
    let breachedCount = 0;
    let escalatedCount = 0;

    allRows.forEach(item => {
      const row = item.display_row || item.latest_row || item || {};
      const status = getSlaStatus(row);
      const escLevel = getEscalationNumericValue(row);

      if(status === "NEAR BREACH"){
        nearCount += 1;
      }
      if(status === "BREACHED"){
        breachedCount += 1;
      }
      if(escLevel > 0){
        escalatedCount += 1;
      }
    });

    setTextValue("opqNearBreachCount", String(nearCount));
    updateNearBreachCardState(nearCount);
    setTextValue("opqBreachedCount", String(breachedCount));
    setTextValue("opqEscalatedCount", String(escalatedCount));
  }

  function populateSlaSection(row){
    const sectionEl = document.getElementById("opqSlaAlertSection");
    const pillEl = document.getElementById("opqSlaAlertPill");
    const cardEl = document.getElementById("opqSlaAlertCard");
    const headerBadgeEl = document.getElementById("detailSlaStatusBadge");

    if(!sectionEl || !pillEl || !cardEl || !headerBadgeEl){
      return;
    }

    const status = getSlaStatus(row);
    const hasSla = !!(
      status ||
      getSlaRuleCode(row) ||
      getSlaElapsedMins(row) !== "0" ||
      getSlaTargetTime(row) ||
      getSlaStartTime(row)
    );

    if(!hasSla){
      sectionEl.style.display = "none";
      pillEl.textContent = "No SLA";
      pillEl.className = "opq-badge opq-badge-sla-none";
      headerBadgeEl.textContent = "SLA: -";
      headerBadgeEl.className = "opq-badge opq-badge-sla-none";
      setTextValue("miniSlaStatus", "-");
      return;
    }

    sectionEl.style.display = "";
    const badgeClass = getSlaBadgeClass(status);
    const alertClass = getSlaAlertClass(status);

    pillEl.textContent = status || "RUNNING";
    pillEl.className = `opq-badge ${badgeClass}`;

    headerBadgeEl.textContent = `SLA: ${status || "RUNNING"}`;
    headerBadgeEl.className = `opq-badge ${badgeClass}`;

    cardEl.className = `opq-sla-alert-card ${alertClass}`;

    setTextValue("miniSlaStatus", status || "RUNNING");

    setInputValue("detail_sla_rule_code", getSlaRuleCode(row));
    setInputValue("detail_sla_status", status || "RUNNING");
    setInputValue("detail_sla_escalation_level", getSlaEscalationLevel(row));
    setInputValue("detail_sla_start_time", formatDateTime(getSlaStartTime(row)));
    setInputValue("detail_sla_target_time", formatDateTime(getSlaTargetTime(row)));
    setInputValue("detail_sla_elapsed_mins", getSlaElapsedMins(row));
    setInputValue("detail_sla_escalated_to", getSlaEscalatedTo(row) || "-");
    setInputValue("detail_sla_remarks", getSlaRemarks(row) || "-");

    const noteEl = document.getElementById("detail_sla_note");
    if(noteEl){
      noteEl.textContent =
        status === "BREACHED"
          ? "This thread has breached its configured SLA and needs attention."
          : status === "NEAR BREACH"
          ? "This thread is nearing SLA breach. Action is recommended."
          : status === "MET"
          ? "This SLA has been closed/met for the selected thread."
          : "This SLA is currently running for the selected thread.";
    }
  }

  function getDisplayPatientName(row){
    const given = safeValue(row.patgiven_nm);
    const sur = safeValue(row.patsur_nm);
    return (given + " " + sur).trim() || "-";
  }

  function getLatestMessageText(row){
    return safeValue(row.latest_message_text || row.raw_message_text || row.intent_remarks || "");
  }

  function buildGridRowHtml(summaryRow){
    const latestRow = summaryRow.latest_row || {};
    const displayRow = summaryRow.display_row || latestRow || {};
    const assignedRow = summaryRow.latest_assigned_row || displayRow || latestRow || {};
    const threadId = safeValue(summaryRow.thread_id);
    const queueId = safeValue(
      (summaryRow.best_row || {}).id ||
      (summaryRow.display_row || {}).id ||
      (summaryRow.latest_assigned_row || {}).id ||
      (summaryRow.latest_row || {}).id
    );
    const patientName = getDisplayPatientName(displayRow);
    const patientPassport = safeValue(displayRow.patpassport_no);
    const nationality = safeValue(displayRow.patient_country);
    const hospital = safeValue(displayRow.hospital_shrtnm);
    const department = safeValue(displayRow.hospital_dept);
    const doctor = safeValue(displayRow.doctor_nm);
    const requestFor = safeValue(displayRow.request_for || displayRow.intent_remarks);
    const agentName = safeValue(displayRow.agent_shrtnm);
    const latestMessage = getLatestMessageText(latestRow);
    const lastMessageOn = formatDateTime(getRowDateValue(latestRow));
    const assignedTo = getAssignedDisplayName(assignedRow);
    const priority = safeValue(displayRow.priority_status || "NORMAL");
    const status = safeValue(displayRow.wrkflw_status || "OPEN");
    const emailStatus = safeValue(displayRow.email_status || "NOT_SENT");
    const templateUsed = safeValue(displayRow.email_template_name || displayRow.email_template_code || "-");
    const remarks = safeValue(displayRow.parse_remarks);
    const slaStatus = getSlaStatus(displayRow);
    const slaElapsed = getSlaElapsedMins(displayRow);
    const slaEscLevel = getSlaEscalationLevel(displayRow);

    return `
      <tr data-thread-id="${escapeHtml(threadId)}" data-queue-id="${escapeHtml(queueId)}">
        <td class="opq-grid-select">
          <input type="checkbox" class="opq-row-checkbox" data-thread-id="${escapeHtml(threadId)}" ${opqSelectedThreadIds.has(threadId) ? "checked" : ""} />
        </td>
        <td>
          <div class="opq-thread-meta">
            <a href="/operation-queue?channel_thread_id=${encodeURIComponent(threadId)}" class="opq-queue-link" data-thread-id="${escapeHtml(threadId)}">${escapeHtml(threadId)}</a>
            <div class="opq-thread-sub">Open grouped conversation</div>
          </div>
        </td>
        <td>${escapeHtml(summaryRow.message_count)}</td>
        <td>${escapeHtml(patientPassport || "-")}</td>
        <td>${escapeHtml(patientName)}</td>
        <td>${escapeHtml(nationality || "-")}</td>
        <td>${escapeHtml(hospital || "-")}</td>
        <td>${renderDepartmentGridHtml(department)}</td>
        <td>${escapeHtml(doctor || "-")}</td>
        <td>${escapeHtml(requestFor || "-")}</td>
        <td>${escapeHtml(agentName || "-")}</td>
        <td>${escapeHtml(lastMessageOn || "-")}</td>
        <td>${escapeHtml(assignedTo)}</td>
        <td><span class="opq-badge ${badgeClassForPriority(priority)}">${escapeHtml(priority)}</span></td>
        <td><span class="opq-badge ${badgeClassForStatus(status)}">${escapeHtml(status)}</span></td>
        <td class="opq-sla-grid-cell">${renderSlaBadge(slaStatus || "-")}</td>
        <td class="opq-sla-grid-cell"><span class="opq-sla-grid-number">${escapeHtml(slaElapsed)}</span></td>
        <td class="opq-sla-grid-cell">${escapeHtml(slaEscLevel)}</td>
        <td><span class="opq-badge ${normalizeForCompare(emailStatus) === "sent" ? "opq-badge-assigned" : "opq-badge-unassigned"}">${escapeHtml(emailStatus)}</span></td>
        <td>${escapeHtml(templateUsed)}</td>
        <td class="opq-grid-remarks">
          <input type="text" data-thread-id="${escapeHtml(threadId)}" value="${escapeHtml(remarks)}" />
        </td>
      </tr>
    `;
  }

  function getTotalGridPages(totalRows, pageSize){
    const safeSize = Number(pageSize || 10);
    if(!totalRows || safeSize <= 0) return 1;
    return Math.max(1, Math.ceil(totalRows / safeSize));
  }

  function getPaginatedRows(rows){
    const allRows = Array.isArray(rows) ? rows : [];
    const totalPages = getTotalGridPages(allRows.length, opqGridPageSize);

    if(opqCurrentGridPage > totalPages){
      opqCurrentGridPage = totalPages;
    }
    if(opqCurrentGridPage < 1){
      opqCurrentGridPage = 1;
    }

    const startIndex = (opqCurrentGridPage - 1) * opqGridPageSize;
    const endIndex = startIndex + opqGridPageSize;

    return allRows.slice(startIndex, endIndex);
  }

  function renderPagination(){
    const summaryEl = document.getElementById("opqPaginationSummary");
    const buttonsEl = document.getElementById("opqPaginationButtons");

    if(!summaryEl || !buttonsEl){
      return;
    }

    const totalRows = (opqFilteredRows || []).length;
    const totalPages = getTotalGridPages(totalRows, opqGridPageSize);

    if(totalRows === 0){
      summaryEl.textContent = "Showing 0 to 0 of 0";
      buttonsEl.innerHTML = "";
      return;
    }

    const startRow = ((opqCurrentGridPage - 1) * opqGridPageSize) + 1;
    const endRow = Math.min(opqCurrentGridPage * opqGridPageSize, totalRows);

    summaryEl.textContent = `Showing ${startRow} to ${endRow} of ${totalRows}`;

    let html = `
      <button type="button" class="opq-page-btn" onclick="goToGridPage(1)" ${opqCurrentGridPage === 1 ? "disabled" : ""}>First</button>
      <button type="button" class="opq-page-btn" onclick="goToGridPage(${opqCurrentGridPage - 1})" ${opqCurrentGridPage === 1 ? "disabled" : ""}>Prev</button>
    `;

    let startPage = Math.max(1, opqCurrentGridPage - 2);
    let endPage = Math.min(totalPages, opqCurrentGridPage + 2);

    if(opqCurrentGridPage <= 3){
      endPage = Math.min(totalPages, 5);
    }
    if(opqCurrentGridPage >= totalPages - 2){
      startPage = Math.max(1, totalPages - 4);
    }

    for(let page = startPage; page <= endPage; page++){
      html += `
        <button
          type="button"
          class="opq-page-btn ${page === opqCurrentGridPage ? "active" : ""}"
          onclick="goToGridPage(${page})"
        >
          ${page}
        </button>
      `;
    }

    html += `
      <button type="button" class="opq-page-btn" onclick="goToGridPage(${opqCurrentGridPage + 1})" ${opqCurrentGridPage === totalPages ? "disabled" : ""}>Next</button>
      <button type="button" class="opq-page-btn" onclick="goToGridPage(${totalPages})" ${opqCurrentGridPage === totalPages ? "disabled" : ""}>Last</button>
    `;

    buttonsEl.innerHTML = html;
  }

  function goToGridPage(pageNumber){
    const totalPages = getTotalGridPages((opqFilteredRows || []).length, opqGridPageSize);
    opqCurrentGridPage = Math.min(Math.max(1, Number(pageNumber || 1)), totalPages);
    renderThreadGrid(false);
  }

  function isSidePanelOpen(){
    const panel = document.getElementById("opqSidePanel");
    return !!(panel && panel.classList.contains("open"));
  }

  function applyGridFilters(){
    opqCurrentGridPage = 1;
    renderThreadGrid(false);

    /*
      Do not auto-open the side panel after Apply.
      Existing flow is preserved: if the user already has the side panel open,
      the same selected thread is refreshed only when it still exists after filtering.
    */
    if(isSidePanelOpen() && opqCurrentThreadId){
      openPreferredThread(opqCurrentThreadId, false);
    }
  }

  function renderThreadGrid(resetPage){
    if(resetPage === true){
      opqCurrentGridPage = 1;
    }

    const searchValue = normalizeForCompare(document.getElementById("opqSearchInput")?.value || "");
    const statusValue = normalizeForCompare(document.getElementById("opqStatusFilter")?.value || "");
    const priorityValue = normalizeForCompare(document.getElementById("opqPriorityFilter")?.value || "");
    const assignedValue = normalizeForCompare(document.getElementById("opqAssignedFilter")?.value || "");
    const deptValue = normalizeForCompare(document.getElementById("opqDeptFilter")?.value || "");

    const baseFilteredRows = buildThreadSummaryRows(opqQueueRows).filter(item => {
      const row = item.display_row || item.latest_row || {};
      const latestRow = item.latest_row || row || {};
      const compositeText = [
        item.thread_id,
        row.patpassport_no,
        row.patgiven_nm,
        row.patsur_nm,
        row.patient_country,
        row.hospital_shrtnm,
        row.hospital_dept,
        row.doctor_nm,
        row.agent_shrtnm,
        getLatestMessageText(latestRow),
        getLatestMessageText(row)
      ].map(safeValue).join(" ").toLowerCase();

      const matchesSearch = !searchValue || compositeText.includes(searchValue);
      const matchesStatus = !statusValue || normalizeForCompare(row.wrkflw_status).includes(statusValue);
      const matchesPriority = !priorityValue || normalizeForCompare(row.priority_status).includes(priorityValue);
      const matchesAssigned = !assignedValue || normalizeForCompare(row.assigned_to).includes(assignedValue) || normalizeForCompare(getAssignedDisplayName(row)).includes(assignedValue);
      const rowDepartmentValues = splitDepartmentValues(row.hospital_dept)
        .map(normalizeDepartmentKey);
      const matchesDept = !deptValue || rowDepartmentValues.includes(deptValue);

      return matchesSearch && matchesStatus && matchesPriority && matchesAssigned && matchesDept;
    });

    opqBaseFilteredRows = baseFilteredRows;

    const filteredRows = baseFilteredRows.filter(item => matchesDashboardFilter(item));

    opqFilteredRows = filteredRows;

    const paginatedRows = getPaginatedRows(filteredRows);
    opqRenderedRows = paginatedRows;

    const tbody = document.getElementById("opqGridBody");
    if(!tbody) return;

    if(!paginatedRows.length){
      tbody.innerHTML = `<tr><td colspan="21" style="text-align:center;">No records found.</td></tr>`;
    }else{
      tbody.innerHTML = paginatedRows.map(buildGridRowHtml).join("");
    }

    const totalRows = filteredRows.length;
    const totalPages = getTotalGridPages(totalRows, opqGridPageSize);

    document.getElementById("opqGridShowingText").textContent = totalRows
      ? `Showing ${paginatedRows.length} thread groups on page ${opqCurrentGridPage} of ${totalPages} (${totalRows} total)`
      : "Showing 0 thread groups";

    document.getElementById("opqThreadCount").textContent = String(baseFilteredRows.length);
    updateSlaSummaryCards(baseFilteredRows);
    updateDashboardCardsActiveState();
    updateDashboardFilterIndicator();

    bindGridEvents();
    updateSelectedCount();
    refreshMasterCheckbox();
    renderPagination();

    if(opqCurrentThreadId){
      setActiveGridRow(opqCurrentThreadId);
    }
  }

  function openThreadFromGrid(threadId){
    const normalizedThreadId = safeValue(threadId).trim();
    if(!normalizedThreadId){
      return false;
    }

    opqCurrentThreadId = normalizedThreadId;

    const matchedSummary = getThreadGroup(normalizedThreadId) || getThreadGroupFromAllRows(normalizedThreadId);
    if(!matchedSummary){
      console.warn("Thread summary not found for:", normalizedThreadId);
      return false;
    }

    try{
      setActiveGridRow(normalizedThreadId);
      openSidePanel();

      populateDetailedView(
        getPreferredDetailRow(matchedSummary),
        matchedSummary.group_rows || [],
        matchedSummary.thread_attachments || []
      );

      const sideBody = document.querySelector(".opq-side-body");
      if(sideBody){
        sideBody.scrollTop = 0;
      }
    }catch(error){
      console.error("Failed to open thread details:", error);
    }

    return false;
  }

  function bindGridEvents(){
    document.querySelectorAll("#opqGridBody .opq-queue-link").forEach(link => {
      link.addEventListener("click", function(event){
        event.preventDefault();
        event.stopPropagation();
        const threadId = this.dataset.threadId || this.getAttribute("data-thread-id") || "";
        openThreadFromGrid(threadId);
      });
    });

    document.querySelectorAll("#opqGridBody tr[data-thread-id]").forEach(row => {
      row.addEventListener("click", function(event){
        if(event.target.closest(".opq-row-checkbox") || event.target.closest("textarea")){
          return;
        }
        const threadId = this.dataset.threadId || this.getAttribute("data-thread-id") || "";
        if(threadId){
          openThreadFromGrid(threadId);
        }
      });
    });

    document.querySelectorAll(".opq-row-checkbox").forEach(checkbox => {
      checkbox.addEventListener("change", function(){
        const threadId = this.getAttribute("data-thread-id") || "";
        if(this.checked){
          opqSelectedThreadIds.add(threadId);
        }else{
          opqSelectedThreadIds.delete(threadId);
        }
        updateSelectedCount();
        refreshMasterCheckbox();
      });

      checkbox.addEventListener("click", function(event){
        event.stopPropagation();
      });
    });

    document.querySelectorAll(".opq-grid-remarks input").forEach(input => {
      input.addEventListener("click", function(event){
        event.stopPropagation();
      });
    });
  }

  function toggleSelectAll(checked){
    document.querySelectorAll(".opq-row-checkbox").forEach(checkbox => {
      checkbox.checked = checked;
      const threadId = checkbox.getAttribute("data-thread-id") || "";
      if(checked){
        opqSelectedThreadIds.add(threadId);
      }else{
        opqSelectedThreadIds.delete(threadId);
      }
    });
    updateSelectedCount();
    refreshMasterCheckbox();
  }

  function updateSelectedCount(){
    const el = document.getElementById("opqSelectedCount");
    if(el){
      el.textContent = `Selected Records: ${opqSelectedThreadIds.size}`;
    }
  }

  function refreshMasterCheckbox(){
    const master = document.getElementById("opqMasterCheckbox");
    const all = Array.from(document.querySelectorAll(".opq-row-checkbox"));
    if(!master) return;
    if(!all.length){
      master.checked = false;
      master.indeterminate = false;
      return;
    }
    const checked = all.filter(x => x.checked).length;
    master.checked = checked === all.length;
    master.indeterminate = checked > 0 && checked < all.length;
  }

  function openSidePanel(){
    document.getElementById("opqSidePanel").classList.add("open");
    document.getElementById("opqSideOverlay").classList.add("show");
    document.body.style.overflow = "hidden";
  }

  function closeSidePanel(){
    closeOperationDepartmentDropdown(false);
    document.getElementById("opqSidePanel").classList.remove("open");
    document.getElementById("opqSideOverlay").classList.remove("show");
    document.body.style.overflow = "";
  }

  function setActiveGridRow(threadId){
    document.querySelectorAll(".opq-table tbody tr").forEach(row => {
      if((row.getAttribute("data-thread-id") || "") === (threadId || "")){
        row.classList.add("opq-active");
      } else {
        row.classList.remove("opq-active");
      }
    });
  }

  function buildAttenderSummary(givenName, surName, passportNo){
    const text = [safeValue(givenName), safeValue(surName)].join(" ").trim();
    const passport = safeValue(passportNo);
    return [text, passport].filter(Boolean).join(" | ");
  }

  function toggleAttenderSection(buttonId, bodyId){
    const btn = document.getElementById(buttonId);
    const body = document.getElementById(bodyId);
    if(!btn || !body) return;
    btn.addEventListener("click", function(){
      const isOpen = body.style.display !== "none";
      body.style.display = isOpen ? "none" : "block";
      btn.textContent = isOpen ? "+" : "-";
    });
  }

  function updateAttenderVisibility(){
    const att1Available = !!(
      safeValue(document.getElementById("detail_attn1_givennm")?.value) ||
      safeValue(document.getElementById("detail_attn1_surnm")?.value) ||
      safeValue(document.getElementById("detail_attn1passport_no")?.value)
    );
    const att2Available = !!(
      safeValue(document.getElementById("detail_attn2_givennm")?.value) ||
      safeValue(document.getElementById("detail_attn2_surnm")?.value) ||
      safeValue(document.getElementById("detail_attn2passport_no")?.value)
    );
    const att3Available = !!(
      safeValue(document.getElementById("detail_attn3_givennm")?.value) ||
      safeValue(document.getElementById("detail_attn3_surnm")?.value) ||
      safeValue(document.getElementById("detail_attn3passport_no")?.value)
    );

    document.getElementById("opqAttender1Section").style.display = att1Available ? "block" : "none";
    document.getElementById("opqAttender2Section").style.display = att2Available ? "block" : "none";
    document.getElementById("opqAttender3Section").style.display = att3Available ? "block" : "none";

    if(!att1Available){
      document.getElementById("bodyAttender1").style.display = "none";
      document.getElementById("toggleAttender1").textContent = "+";
    }
    if(!att2Available){
      document.getElementById("bodyAttender2").style.display = "none";
      document.getElementById("toggleAttender2").textContent = "+";
    }
    if(!att3Available){
      document.getElementById("bodyAttender3").style.display = "none";
      document.getElementById("toggleAttender3").textContent = "+";
    }
  }

  function getChangedFields(curr, prev){
    const compareFields = [
      ["Patient Given Name", "patgiven_nm"],
      ["Patient Sur Name", "patsur_nm"],
      ["Patient Passport", "patpassport_no"],
      ["Patient Passport Expiry Date", "patpass_expirydt"],
      ["Patient Country", "patient_country"],
      ["Patient DOB", "patient_dob"],
      ["Patient Gender", "patient_gender"],
      ["Patient Address", "patient_address"],
      ["Patient Contact", "patient_contno"],
      ["Patient Email", "patient_email"],
      ["Attender1 Given Name", "attn1_givennm"],
      ["Attender1 Sur Name", "attn1_surnm"],
      ["Attender1 Passport", "attn1passport_no"],
      ["Attender1 Relation", "attn1pat_relation"],
      ["Attender1 Country", "attn1_country"],
      ["Attender1 DOB", "attn1_dob"],
      ["Attender1 Gender", "attn1_gender"],
      ["Attender1 Address", "attn1_address"],
      ["Attender1 Contact", "attn1_contno"],
      ["Attender2 Given Name", "attn2_givennm"],
      ["Attender2 Sur Name", "attn2_surnm"],
      ["Attender2 Passport", "attn2passport_no"],
      ["Attender2 Relation", "attn2pat_relation"],
      ["Attender2 Country", "attn2_country"],
      ["Attender2 DOB", "attn2_dob"],
      ["Attender2 Gender", "attn2_gender"],
      ["Attender2 Address", "attn2_address"],
      ["Attender2 Contact", "attn2_contno"],
      ["Attender3 Given Name", "attn3_givennm"],
      ["Attender3 Sur Name", "attn3_surnm"],
      ["Attender3 Passport", "attn3passport_no"],
      ["Attender3 Relation", "attn3pat_relation"],
      ["Attender3 Country", "attn3_country"],
      ["Attender3 DOB", "attn3_dob"],
      ["Attender3 Gender", "attn3_gender"],
      ["Attender3 Address", "attn3_address"],
      ["Attender3 Contact", "attn3_contno"],
      ["Embassy (IVAC)", "ivac_center"],
      ["Hospital", "hospital_shrtnm"],
      ["Department", "hospital_dept"],
      ["Doctor", "doctor_nm"],
      ["Request For", "request_for"],
      ["Priority Status", "priority_status"],
      ["VIL Date", "vil_date"],
      ["Appointment Date", "appointment_dt"],
      ["Workflow Status", "wrkflw_status"],
      ["Assigned To", "assigned_to"],
      ["Assigned On", "assigned_at"],
      ["Agent Name", "agent_shrtnm"],
      ["Operation Remarks", "parse_remarks"]
    ];

    return compareFields
      .map(([label, key]) => {
        const previousValue = safeValue(prev[key]);
        const currentValue = safeValue(curr[key]);

        if(normalizeForCompare(previousValue) !== normalizeForCompare(currentValue)){
          return {
            label,
            prevValue: previousValue,
            currValue: currentValue
          };
        }
        return null;
      })
      .filter(Boolean);
  }

  function getOrdinalLabel(number){
    const mod100 = number % 100;
    if(mod100 >= 11 && mod100 <= 13){
      return `${number}th`;
    }
    const mod10 = number % 10;
    if(mod10 === 1) return `${number}st`;
    if(mod10 === 2) return `${number}nd`;
    if(mod10 === 3) return `${number}rd`;
    return `${number}th`;
  }

  function formatFileSize(bytes){
    const size = Number(bytes || 0);
    if(!size || size < 0) return "-";
    if(size < 1024) return `${size} B`;
    if(size < 1024 * 1024) return `${(size / 1024).toFixed(2)} KB`;
    if(size < 1024 * 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(2)} MB`;
    return `${(size / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  }

  function resolveAttachmentUrl(attachment){
    if(!attachment || typeof attachment !== "object"){
      return "";
    }

    const directCandidates = [
      attachment.open_url,
      attachment.file_url,
      attachment.download_url,
      attachment.url,
      attachment.view_url
    ];

    for(const candidate of directCandidates){
      const value = safeValue(candidate).trim();
      if(!value){
        continue;
      }

      if(/^https?:\/\//i.test(value)){
        return value;
      }

      if(value.startsWith("/")){
        return value;
      }

      if(!/^[a-zA-Z]:\\/.test(value) && !value.startsWith("\\\\")){
        return "/" + value.replace(/^\/+/, "").replace(/\\/g, "/");
      }
    }

    const rawPath = safeValue(attachment.file_path).trim();
    if(!rawPath){
      return "";
    }

    const normalizedPath = rawPath.replace(/\\/g, "/");

    if(/^https?:\/\//i.test(normalizedPath)){
      return normalizedPath;
    }

    if(normalizedPath.startsWith("/")){
      return normalizedPath;
    }

    const uploadsIndex = normalizedPath.toLowerCase().lastIndexOf("/uploads/");
    if(uploadsIndex >= 0){
      return normalizedPath.substring(uploadsIndex);
    }

    if(normalizedPath.toLowerCase().startsWith("uploads/")){
      return "/" + normalizedPath;
    }

    const staticIndex = normalizedPath.toLowerCase().lastIndexOf("/static/");
    if(staticIndex >= 0){
      return normalizedPath.substring(staticIndex);
    }

    if(normalizedPath.toLowerCase().startsWith("static/")){
      return "/" + normalizedPath;
    }

    return "/" + normalizedPath.replace(/^\/+/, "");
  }

  function isPreviewableImage(attachment){
    const fileExt = safeValue(attachment?.file_ext).toLowerCase();
    const mimeType = safeValue(attachment?.mime_type).toLowerCase();
    return ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].includes(fileExt)
      || mimeType.startsWith("image/");
  }

  function buildAttachmentRailHtml(attachment, index, isActive){
    const fileName = safeValue(attachment.file_name) || `Document ${index + 1}`;
    const fileExt = safeValue(attachment.file_ext) || "FILE";
    const uploadedOn = formatDateTime(attachment.created_on || attachment.updated_on) || "-";
    const fileSize = formatFileSize(attachment.file_size);

    return `
      <button type="button" class="opq-doc-rail-item ${isActive ? "active" : ""}" onclick="selectAttachmentPreview(${index})">
        <div class="opq-doc-rail-title">${escapeHtml(fileName)}</div>
        <div class="opq-doc-rail-meta">
          <span>${escapeHtml(fileExt.toUpperCase())}</span>
          <span>${escapeHtml(fileSize)}</span>
        </div>
        <div class="opq-doc-rail-meta" style="margin-top:6px;">
          <span>Uploaded</span>
          <span>${escapeHtml(uploadedOn)}</span>
        </div>
      </button>
    `;
  }

  function renderAttachmentPreview(index){
    const attachments = Array.isArray(window.opqCurrentAttachments) ? window.opqCurrentAttachments : [];
    const attachment = attachments[index];

    const rail = document.getElementById("opqAttachmentRail");
    const emptyEl = document.getElementById("opqAttachmentPreviewEmpty");
    const frameEl = document.getElementById("opqAttachmentPreviewFrame");
    const imageEl = document.getElementById("opqAttachmentPreviewImage");
    const nameEl = document.getElementById("opqAttachmentPreviewName");
    const subEl = document.getElementById("opqAttachmentPreviewSub");
    const actionsEl = document.getElementById("opqAttachmentPreviewActions");
    const metaEl = document.getElementById("opqAttachmentMeta");
    if(!rail || !emptyEl || !frameEl || !imageEl || !nameEl || !subEl || !actionsEl || !metaEl){
      return;
    }

    if(!attachment){
      rail.innerHTML = `<div class="opq-empty">No documents available.</div>`;
      nameEl.textContent = "No document selected";
      subEl.textContent = "Select a document from the left panel to preview it here.";
      emptyEl.style.display = "block";
      frameEl.style.display = "none";
      imageEl.style.display = "none";
      frameEl.removeAttribute("src");
      imageEl.removeAttribute("src");
      actionsEl.innerHTML = `<span class="opq-attachment-link" style="cursor:default;color:#6c7f99;">No Action Available</span>`;
      metaEl.innerHTML = `
        <div class="opq-doc-meta-card"><div class="k">File Type</div><div class="v">-</div></div>
        <div class="opq-doc-meta-card"><div class="k">File Size</div><div class="v">-</div></div>
        <div class="opq-doc-meta-card"><div class="k">Uploaded On</div><div class="v">-</div></div>
        <div class="opq-doc-meta-card"><div class="k">Remarks</div><div class="v">-</div></div>
      `;
      return;
    }

    window.opqCurrentAttachmentIndex = index;
    rail.innerHTML = attachments.map((item, idx) => buildAttachmentRailHtml(item, idx, idx === index)).join("");

    const fileName = safeValue(attachment.file_name) || `Document ${index + 1}`;
    const fileExt = safeValue(attachment.file_ext) || "FILE";
    const mimeType = safeValue(attachment.mime_type) || "-";
    const fileSize = formatFileSize(attachment.file_size);
    const uploadedOn = formatDateTime(attachment.created_on || attachment.updated_on) || "-";
    const remarks = safeValue(attachment.remarks) || "-";
    const fileUrl = resolveAttachmentUrl(attachment);

    nameEl.textContent = fileName;
    subEl.textContent = `Document ${index + 1} of ${attachments.length} - ${fileExt.toUpperCase()} - ${uploadedOn}`;

    if(fileUrl){
      actionsEl.innerHTML = `<a class="opq-attachment-link" href="${escapeHtml(fileUrl)}" target="_blank" rel="noopener noreferrer" onclick="return openAttachmentDocument(event, ${index})">Open Document</a>`;
    }else{
      actionsEl.innerHTML = `<span class="opq-attachment-link" style="cursor:default;color:#6c7f99;">File Link Not Available</span>`;
    }

    metaEl.innerHTML = `
      <div class="opq-doc-meta-card"><div class="k">File Type</div><div class="v">${escapeHtml(fileExt.toUpperCase())}</div></div>
      <div class="opq-doc-meta-card"><div class="k">Mime Type</div><div class="v">${escapeHtml(mimeType)}</div></div>
      <div class="opq-doc-meta-card"><div class="k">File Size</div><div class="v">${escapeHtml(fileSize)}</div></div>
      <div class="opq-doc-meta-card"><div class="k">Uploaded On</div><div class="v">${escapeHtml(uploadedOn)}</div></div>
      <div class="opq-doc-meta-card" style="grid-column:1 / -1;"><div class="k">Remarks</div><div class="v">${escapeHtml(remarks)}</div></div>
    `;

    if(!fileUrl){
      emptyEl.style.display = "block";
      emptyEl.textContent = "Preview not available because no file link is stored for this document.";
      frameEl.style.display = "none";
      imageEl.style.display = "none";
      frameEl.removeAttribute("src");
      imageEl.removeAttribute("src");
      return;
    }

    emptyEl.style.display = "none";
    if(isPreviewableImage(attachment)){
      imageEl.src = fileUrl;
      imageEl.style.display = "block";
      frameEl.style.display = "none";
      frameEl.removeAttribute("src");
    }else{
      frameEl.src = fileUrl;
      frameEl.style.display = "block";
      imageEl.style.display = "none";
      imageEl.removeAttribute("src");
    }
  }

  function selectAttachmentPreview(index){
    renderAttachmentPreview(index);
  }

  function openAttachmentDocument(event, index){
    if(event){
      event.preventDefault();
    }

    const attachments = Array.isArray(window.opqCurrentAttachments) ? window.opqCurrentAttachments : [];
    const attachment = attachments[index];

    if(!attachment){
      alert("Document record not found.");
      return false;
    }

    const fileUrl = resolveAttachmentUrl(attachment);

    if(!fileUrl){
      alert("File link not available.");
      return false;
    }

    window.open(fileUrl, "_blank", "noopener,noreferrer");
    return false;
  }

  function buildAttachmentHtml(attachment, index){
    const fileName = safeValue(attachment.file_name) || `Document ${index + 1}`;
    const fileExt = safeValue(attachment.file_ext) || "FILE";
    const mimeType = safeValue(attachment.mime_type) || "-";
    const fileSize = formatFileSize(attachment.file_size);
    const uploadedOn = formatDateTime(attachment.created_on || attachment.updated_on);
    const remarks = safeValue(attachment.remarks) || "-";
    const fileUrl = resolveAttachmentUrl(attachment);

    const actionHtml = fileUrl
      ? `<a class="opq-attachment-link" href="${escapeHtml(fileUrl)}" target="_blank" rel="noopener noreferrer" onclick="return openAttachmentDocument(event, ${index})">Open Document</a>`
      : `<span class="opq-attachment-link" style="cursor:default;color:#6c7f99;">File Link Not Available</span>`;

    return `
      <div class="opq-attachment-item">
        <div class="opq-attachment-top">
          <div class="opq-attachment-name">${escapeHtml(fileName)}</div>
          <div class="opq-attachment-ext">${escapeHtml(fileExt)}</div>
        </div>
        <div class="opq-attachment-meta">
          <div class="opq-attachment-meta-box">
            <div class="k">Mime Type</div>
            <div class="v">${escapeHtml(mimeType)}</div>
          </div>
          <div class="opq-attachment-meta-box">
            <div class="k">File Size</div>
            <div class="v">${escapeHtml(fileSize)}</div>
          </div>
          <div class="opq-attachment-meta-box">
            <div class="k">Uploaded On</div>
            <div class="v">${escapeHtml(uploadedOn || "-")}</div>
          </div>
          <div class="opq-attachment-meta-box">
            <div class="k">Remarks</div>
            <div class="v">${escapeHtml(remarks)}</div>
          </div>
        </div>
        <div class="opq-attachment-actions">
          ${actionHtml}
        </div>
      </div>
    `;
  }

  function getSelectedQueueId() {
    const activeRow = document.querySelector("[data-queue-id].opq-active");
    if (activeRow) {
      return activeRow.getAttribute("data-queue-id");
    }

    const activeThreadRow = document.querySelector(".opq-table tbody tr.opq-active");
    if (activeThreadRow) {
      return activeThreadRow.getAttribute("data-queue-id");
    }

    const detailRowId = document.getElementById("detail_row_id");
    if (detailRowId && detailRowId.value) {
      return detailRowId.value;
    }

    const queueIdInput = document.getElementById("queue_id");
    if (queueIdInput && queueIdInput.value) {
      return queueIdInput.value;
    }

    return null;
  }

  async function openEmailCommunicationWindow(queueId) {
    const currentRow = (opqCurrentThread && typeof opqCurrentThread === "object") ? opqCurrentThread : {};
    const currentWorkflowStatus = safeValue(
      currentRow.wrkflw_status || document.getElementById("detail_wrkflw_status")?.value
    ).trim();

    if (isClosedWorkflowStatus(currentWorkflowStatus)) {
      alert("Open Email Communication Window is disabled because the workflow status is CLOSED.");
      return;
    }

    if (isEmailAlreadySentForRow(currentRow)) {
      alert("Open Email Communication Window is disabled because email is already SENT for this request.");
      return;
    }

    if (!queueId) {
      alert("Please select a queue row first.");
      return;
    }

    try {
      const response = await fetch("/api/operation-queue/start-email", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest"
        },
        body: JSON.stringify({
          queue_id: queueId
        })
      });

      const data = await response.json().catch(() => ({}));

      if (!response.ok || !data.success) {
        alert(data.message || "Unable to start email communication.");
        return;
      }

      if (!data.open_url) {
        alert("Popup URL not received from server.");
        return;
      }

      const requestIdEl = document.getElementById("request_id");
      if (requestIdEl) {
        requestIdEl.value = data.request_id || "";
      }

      const queueIdEl = document.getElementById("queue_id");
      if (queueIdEl) {
        queueIdEl.value = String(queueId);
      }

      window.open(
        data.open_url,
        "operationQueueEmailWindow",
        "width=1200,height=850,resizable=yes,scrollbars=yes"
      );
    } catch (error) {
      console.error("start-email error:", error);
      alert("Something went wrong while opening email communication window.");
    }
  }

  function renderThreadAttachments(attachments){
    const section = document.getElementById("opqAttachmentSection");
    const container = document.getElementById("opqAttachmentContainer");
    const rail = document.getElementById("opqAttachmentRail");
    const countBadge = document.getElementById("opqAttachmentCountBadge");

    if(!section || !container || !rail) return;

    const attachmentList = Array.isArray(attachments) ? attachments : [];
    window.opqCurrentAttachments = attachmentList;
    window.opqCurrentAttachmentIndex = 0;

    if(countBadge){
      countBadge.textContent = `${attachmentList.length} Document${attachmentList.length === 1 ? "" : "s"}`;
    }

    if(!attachmentList.length){
      section.style.display = "none";
      container.innerHTML = `<div class="opq-empty">No documents available.</div>`;
      rail.innerHTML = `<div class="opq-empty">No documents available.</div>`;
      renderAttachmentPreview(-1);
      return;
    }

    section.style.display = "block";
    container.innerHTML = attachmentList.map((attachment, index) => buildAttachmentHtml(attachment, index)).join("");
    renderAttachmentPreview(0);
  }

  function toggleTimelineExpanded(){
    opqTimelineExpanded = !opqTimelineExpanded;
    renderTimeline(opqCurrentGroupRows || []);
  }

  function renderTimeline(rows){
    const section = document.getElementById("opqTimelineSection");
    const container = document.getElementById("opqTimelineContainer");
    const summaryText = document.getElementById("opqTimelineSummaryText");
    const toggleBtn = document.getElementById("opqTimelineToggleBtn");
    if(!section || !container || !summaryText || !toggleBtn) return;

    const orderedRows = (rows || []).slice().sort((a, b) => getRowDateMs(b) - getRowDateMs(a));

    if(orderedRows.length <= 1){
      section.style.display = "none";
      container.innerHTML = `<div class="opq-empty">For the 1st message, no past communication timeline is shown.</div>`;
      summaryText.textContent = "No timeline available.";
      toggleBtn.style.display = "none";
      return;
    }

    section.style.display = "block";

    const timelineItems = [];
    const totalMessages = orderedRows.length;

    for(let i = 0; i < orderedRows.length - 1; i++){
      const curr = orderedRows[i];
      const prev = orderedRows[i + 1];
      const changes = getChangedFields(curr, prev);

      if(!changes.length){
        continue;
      }

      const messageNumber = totalMessages - i;

      const changeHtml = changes.map(change => `
        <div class="opq-change-item">
          <div class="opq-change-field">${escapeHtml(change.label)}</div>
          <div class="opq-change-values">
            <strong>Current:</strong> ${escapeHtml(change.currValue || "-")}<br>
            <strong>Previous:</strong> ${escapeHtml(change.prevValue || "-")}
          </div>
        </div>
      `).join("");

      timelineItems.push(`
        <div class="opq-timeline-item">
          <div class="opq-timeline-dot"></div>
          <div class="opq-timeline-card">
            <div class="opq-timeline-top">
              <div class="opq-timeline-title">${escapeHtml(getOrdinalLabel(messageNumber))} Message</div>
              <div class="opq-badge opq-badge-msg">${escapeHtml(formatDateTime(getRowDateValue(curr)) || "-")}</div>
            </div>
            <div class="opq-timeline-msg">${escapeHtml(getLatestMessageText(curr) || "-")}</div>
            <div class="opq-timeline-meta">
              <div class="opq-timeline-meta-box">
                <div class="k">Channel Message ID</div>
                <div class="v">${escapeHtml(curr.channel_message_id || "-")}</div>
              </div>
              <div class="opq-timeline-meta-box">
                <div class="k">Workflow</div>
                <div class="v">${escapeHtml(curr.wrkflw_status || "-")}</div>
              </div>
              <div class="opq-timeline-meta-box">
                <div class="k">Priority</div>
                <div class="v">${escapeHtml(curr.priority_status || "-")}</div>
              </div>
              <div class="opq-timeline-meta-box">
                <div class="k">Operational Notes</div>
                <div class="v">${escapeHtml(curr.parse_remarks || "-")}</div>
              </div>
            </div>
            <div class="opq-change-list">${changeHtml}</div>
          </div>
        </div>
      `);
    }

    if(!timelineItems.length){
      container.innerHTML = `<div class="opq-empty">No changed fields found across previous messages.</div>`;
      summaryText.textContent = "No changed fields found across previous messages.";
      toggleBtn.style.display = "none";
      return;
    }

    const visibleItems = opqTimelineExpanded ? timelineItems : timelineItems.slice(0, 3);
    container.innerHTML = visibleItems.join("");

    summaryText.textContent = opqTimelineExpanded
      ? `Showing all ${timelineItems.length} timeline item${timelineItems.length === 1 ? "" : "s"}.`
      : `Showing latest ${Math.min(3, timelineItems.length)} of ${timelineItems.length} timeline item${timelineItems.length === 1 ? "" : "s"}.`;

    if(timelineItems.length > 3){
      toggleBtn.style.display = "inline-flex";
      toggleBtn.textContent = opqTimelineExpanded ? "Show Latest Only" : "View Full Timeline";
    }else{
      toggleBtn.style.display = "none";
    }
  }

  function applyDetailFieldEditability(){
    const readonlyFieldIds = [
      "detail_patgiven_nm",
      "detail_patsur_nm",
      "detail_patpassport_no",
      "detail_patpass_expirydt",
      "detail_patient_country",
      "detail_patient_dob",
      "detail_patient_gender",
      "detail_patient_address",
      "detail_patient_contno",
      "detail_patient_email",
      "detail_attn1_givennm",
      "detail_attn1_surnm",
      "detail_attn1passport_no",
      "detail_attn1pat_relation",
      "detail_attn1_country",
      "detail_attn1_dob",
      "detail_attn1_gender",
      "detail_attn1_address",
      "detail_attn1_contno",
      "detail_attn2_givennm",
      "detail_attn2_surnm",
      "detail_attn2passport_no",
      "detail_attn2pat_relation",
      "detail_attn2_country",
      "detail_attn2_dob",
      "detail_attn2_gender",
      "detail_attn2_address",
      "detail_attn2_contno",
      "detail_attn3_givennm",
      "detail_attn3_surnm",
      "detail_attn3passport_no",
      "detail_attn3pat_relation",
      "detail_attn3_country",
      "detail_attn3_dob",
      "detail_attn3_gender",
      "detail_attn3_address",
      "detail_attn3_contno",
      "detail_wrkflw_status",
      "detail_assigned_at"
    ];

    readonlyFieldIds.forEach(id => {
      const el = document.getElementById(id);
      if(!el) return;

      if(el.tagName === "SELECT"){
        el.disabled = true;
        el.setAttribute("disabled", "disabled");
      }else{
        el.readOnly = true;
      }

      el.classList.add("opq-force-readonly");

      if((el.type || "").toLowerCase() === "date"){
        el.classList.add("opq-force-readonly-date");
        el.disabled = true;
        el.setAttribute("disabled", "disabled");
      }
    });

    const statusEl = document.getElementById("detail_wrkflw_status");
    const rawStatus = statusEl ? statusEl.value : "";
    const currentStatus = normalizeForCompare(rawStatus);
    const isClosed = currentStatus === "closed";

    const requestWorkflowSection = document.getElementById("opqRequestWorkflowSection");
    if(requestWorkflowSection){
      requestWorkflowSection.classList.toggle("opq-section-closed", isClosed);

      const controls = requestWorkflowSection.querySelectorAll("input, select, textarea, button");
      controls.forEach(el => {
        if(el.id === "detail_wrkflw_status" || el.id === "detail_assigned_at"){
          return;
        }

        const type = (el.type || "").toLowerCase();

        if(isClosed){
          el.disabled = true;
          el.setAttribute("disabled", "disabled");
          if(el.tagName !== "SELECT"){
            el.readOnly = true;
          }
          el.classList.add("opq-force-readonly");
          if(type === "date"){
            el.classList.add("opq-force-readonly-date");
          }
        }else{
          if(el.tagName === "SELECT"){
            el.disabled = false;
            el.removeAttribute("disabled");
          }else{
            el.disabled = false;
            el.removeAttribute("disabled");
            el.readOnly = false;
          }
          el.classList.remove("opq-force-readonly");
          el.classList.remove("opq-force-readonly-date");
        }

      });
    }

    const agencySelect = document.getElementById("detail_agent_shrtnm");
    if(agencySelect){
      agencySelect.disabled = isClosed;
      agencySelect.classList.toggle("opq-force-readonly", isClosed);
    }

    const saveBtn = document.getElementById("opqSaveBtn");
    const resolveBtn = document.getElementById("opqResolveBtn");

    if(saveBtn){
      saveBtn.disabled = isClosed;
      if(isClosed){ saveBtn.setAttribute("disabled", "disabled"); } else { saveBtn.removeAttribute("disabled"); }
      saveBtn.style.opacity = isClosed ? "0.55" : "";
      saveBtn.style.cursor = isClosed ? "not-allowed" : "";
    }

    if(resolveBtn){
      resolveBtn.disabled = isClosed;
      if(isClosed){ resolveBtn.setAttribute("disabled", "disabled"); } else { resolveBtn.removeAttribute("disabled"); }
      resolveBtn.style.opacity = isClosed ? "0.55" : "";
      resolveBtn.style.cursor = isClosed ? "not-allowed" : "";
    }
  }

  function captureDetailState(){
    const fields = Array.from(document.querySelectorAll(
      '.opq-side-body input:not(.opq-email-ignore), .opq-side-body select:not(.opq-email-ignore), .opq-side-body textarea:not(.opq-email-ignore)'
    ))
      .filter(el => !el.closest(".opq-dept-dropdown"))
      .map(el => {
        if(el.tagName === "SELECT" && el.multiple){
          const selectedValues = Array.from(el.selectedOptions || [])
            .map(option => safeValue(option.value).trim())
            .filter(Boolean)
            .join(",");
          return `${el.id}:${selectedValues}`;
        }
        return `${el.id}:${safeValue(el.value)}`;
      })
      .join("|");

    return fields;
  }

  function updateUnsavedBadge(){
    const badge = document.getElementById("opqUnsavedBadge");
    if(!badge) return;
    const current = captureDetailState();
    badge.style.display = current !== opqInitialDetailState ? "inline-block" : "none";
  }

  function populateDetailedView(row, groupRows, attachments){
    row = row || {};
    groupRows = groupRows || [];
    const resolvedRow = mergeThreadDisplayRow(row || {}, chooseBestRow(groupRows || []) || {});
    row = resolvedRow;
    opqCurrentThread = resolvedRow;
    opqCurrentThreadId = safeValue(row.channel_thread_id || row.channel_message_id || row.id);
    opqCurrentGroupRows = groupRows;
    opqCurrentEmailRow = resolvedRow;

    setTextValue("detailHeaderThreadId", opqCurrentThreadId || "No Record");
    setTextValue("detail_channel_thread_id_text", opqCurrentThreadId || "-");
    setInputValue("detail_channel_thread_id", opqCurrentThreadId);
    setInputValue("detail_row_id", row.id || "");

    document.getElementById("detailWorkflowBadge").textContent = `Workflow: ${safeValue(row.wrkflw_status || "OPEN")}`;
    document.getElementById("detailWorkflowBadge").className = `opq-badge ${badgeClassForStatus(row.wrkflw_status || "OPEN")}`;

    document.getElementById("detailPriorityBadge").textContent = `Priority: ${safeValue(row.priority_status || "NORMAL")}`;
    document.getElementById("detailPriorityBadge").className = `opq-badge ${badgeClassForPriority(row.priority_status || "NORMAL")}`;

    document.getElementById("detailMetaAssignedBadge").textContent = `Assigned: ${getAssignedDisplayName(row)}`;
    document.getElementById("detailMsgCountBadge").textContent = `Messages: ${safeValue(groupRows.length || 0)}`;

    setTextValue("miniPatientName", getDisplayPatientName(row));
    setTextValue("miniPassport", row.patpassport_no || "-");
    setTextValue("miniLastMessageOn", formatDateTime(getRowDateValue(row)) || "-");
    setTextValue("miniAssignedTo", getAssignedDisplayName(row));
    populateSlaSection(row);

    setInputValue("detail_updated_on_core", formatDateTime(row.updated_on || row.created_on || row.received_on));
    setInputValue("detail_latest_message_on", formatDateTime(getRowDateValue(row)));
    setInputValue("detail_message_count", groupRows.length);

    setInputValue("detail_patgiven_nm", row.patgiven_nm);
    setInputValue("detail_patsur_nm", row.patsur_nm);
    setInputValue("detail_patpassport_no", row.patpassport_no);
    setInputValue("detail_patpass_expirydt", formatDateOnly(row.patpass_expirydt));
    setInputValue("detail_patient_country", row.patient_country);
    setInputValue("detail_patient_dob", formatDateOnly(row.patient_dob));
    setInputValue("detail_patient_gender", row.patient_gender);
    setInputValue("detail_patient_address", row.patient_address);
    setInputValue("detail_patient_contno", row.patient_contno);
    setInputValue("detail_patient_email", row.patient_email);

    setInputValue("detail_attn1_givennm", row.attn1_givennm);
    setInputValue("detail_attn1_surnm", row.attn1_surnm);
    setInputValue("detail_attn1passport_no", row.attn1passport_no);
    setInputValue("detail_attn1pat_relation", row.attn1pat_relation);
    setInputValue("detail_attn1_country", row.attn1_country);
    setInputValue("detail_attn1_dob", formatDateOnly(row.attn1_dob));
    setInputValue("detail_attn1_gender", row.attn1_gender);
    setInputValue("detail_attn1_address", row.attn1_address);
    setInputValue("detail_attn1_contno", row.attn1_contno);

    setInputValue("detail_attn2_givennm", row.attn2_givennm);
    setInputValue("detail_attn2_surnm", row.attn2_surnm);
    setInputValue("detail_attn2passport_no", row.attn2passport_no);
    setInputValue("detail_attn2pat_relation", row.attn2pat_relation);
    setInputValue("detail_attn2_country", row.attn2_country);
    setInputValue("detail_attn2_dob", formatDateOnly(row.attn2_dob));
    setInputValue("detail_attn2_gender", row.attn2_gender);
    setInputValue("detail_attn2_address", row.attn2_address);
    setInputValue("detail_attn2_contno", row.attn2_contno);

    setInputValue("detail_attn3_givennm", row.attn3_givennm);
    setInputValue("detail_attn3_surnm", row.attn3_surnm);
    setInputValue("detail_attn3passport_no", row.attn3passport_no);
    setInputValue("detail_attn3pat_relation", row.attn3pat_relation);
    setInputValue("detail_attn3_country", row.attn3_country);
    setInputValue("detail_attn3_dob", formatDateOnly(row.attn3_dob));
    setInputValue("detail_attn3_gender", row.attn3_gender);
    setInputValue("detail_attn3_address", row.attn3_address);
    setInputValue("detail_attn3_contno", row.attn3_contno);

    setSelectValue("detail_ivac_center", row.ivac_center);
    loadHospitalLov(row.hospital_shrtnm);
    setOperationDepartmentSelection(row.hospital_dept_values || row.hospital_dept);
    setInputValue("detail_doctor_nm", row.doctor_nm);
    setSelectValue("detail_request_for", row.request_for || row.intent_remarks);
    setInputValue("detail_priority_status", row.priority_status);
    setInputValue("detail_vil_date", formatDateOnly(row.vil_date));
    setInputValue("detail_appointment_dt", formatDateOnly(row.appointment_dt));
    setInputValue("detail_wrkflw_status", row.wrkflw_status);
    updateEmailLauncherState(row);
    setSelectValue("detail_assigned_to", row.assigned_to);
    setInputValue("detail_assigned_at", formatDateTime(row.assigned_at));
    setSelectValue("detail_agent_shrtnm", row.agent_shrtnm);
    setInputValue("detail_parse_remarks", row.parse_remarks);

    setTextValue("detail_latest_message_title", "Last message in this thread");
    setTextValue("detail_latest_message_meta", `Thread: ${opqCurrentThreadId || "-"}`);
    setTextValue("detail_latest_message_date_badge", formatDateTime(getRowDateValue(row)) || "-");
    setTextValue("detail_latest_message_channel_badge", row.channel_code || "Channel Message");
    setTextValue("detail_latest_message_preview", getLatestMessageText(row) || "-");

    setTextValue("summary_attender1", buildAttenderSummary(row.attn1_givennm, row.attn1_surnm, row.attn1passport_no));
    setTextValue("summary_attender2", buildAttenderSummary(row.attn2_givennm, row.attn2_surnm, row.attn2passport_no));
    setTextValue("summary_attender3", buildAttenderSummary(row.attn3_givennm, row.attn3_surnm, row.attn3passport_no));

    updateAttenderVisibility();
    opqTimelineExpanded = false;
    renderThreadAttachments(attachments || []);
    renderTimeline(groupRows);
    if (typeof populateEmailComposer === "function") {
      populateEmailComposer(row, attachments || []);
    }

    applyDetailFieldEditability();

    setTimeout(() => {
      applyDetailFieldEditability();
    }, 400);

    opqInitialDetailState = captureDetailState();
    updateUnsavedBadge();
  }

  function getDetailPayload(){
    return {
      row_id: safeValue(document.getElementById("detail_row_id")?.value),
      channel_thread_id: safeValue(document.getElementById("detail_channel_thread_id")?.value),
      ivac_center: safeValue(document.getElementById("detail_ivac_center")?.value),
      hospital_shrtnm: safeValue(document.getElementById("detail_hospital_shrtnm")?.value),
      hospital_dept: getSelectedOperationDepartmentCsv(),
      doctor_nm: safeValue(document.getElementById("detail_doctor_nm")?.value),
      intent_remarks: safeValue(document.getElementById("detail_request_for")?.value),
      priority_status: safeValue(document.getElementById("detail_priority_status")?.value),
      vil_date: safeValue(document.getElementById("detail_vil_date")?.value),
      appointment_dt: safeValue(document.getElementById("detail_appointment_dt")?.value),
      assigned_to: safeValue(document.getElementById("detail_assigned_to")?.value),
      agent_shrtnm: safeValue(document.getElementById("detail_agent_shrtnm")?.value),
      parse_remarks: safeValue(document.getElementById("detail_parse_remarks")?.value)
    };
  }

  function validateOperationRemarks(payload){
    if(!normalizeText(payload.parse_remarks)){
      alert("Operation Remarks is mandatory.");
      const remarksEl = document.getElementById("detail_parse_remarks");
      if(remarksEl){ remarksEl.focus(); }
      return false;
    }
    return true;
  }

  async function postOperationAction(url, payload){
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest"
      },
      body: JSON.stringify(payload || {})
    });

    const data = await response.json().catch(() => ({}));
    if(!response.ok || !data.success){
      throw new Error(data.message || data.error || `HTTP ${response.status}`);
    }
    return data;
  }

  async function saveCurrentThread(){
    const payload = getDetailPayload();
    if(!validateOperationRemarks(payload)) return;

    try{
      await postOperationAction("/operation-queue/save", payload);
      alert("Saved successfully.");
      window.location.reload();
    }catch(error){
      alert(error.message || "Unable to save record.");
    }
  }

  async function resolveCurrentThread(){
    const payload = getDetailPayload();
    if(!validateOperationRemarks(payload)) return;

    try{
      await postOperationAction("/operation-queue/resolve", payload);
      alert("Query resolved successfully.");
      window.location.reload();
    }catch(error){
      alert(error.message || "Unable to resolve record.");
    }
  }

  function getGridRemarkByThreadId(threadId){
    const el = document.querySelector(`.opq-grid-remarks input[data-thread-id="${threadId}"]`);
    return safeValue(el?.value);
  }

  async function bulkActionSelected(url, successMessage){
    const selectedThreadIds = Array.from(opqSelectedThreadIds || []);
    if(!selectedThreadIds.length){
      alert("Please select at least one record.");
      return;
    }

    const payloadRows = [];
    for(const threadId of selectedThreadIds){
      const group = getThreadGroup(threadId);
      const latestRow = group?.latest_row || {};
      const remarks = getGridRemarkByThreadId(threadId);

      if(!safeValue(latestRow.id)){
        alert(`Unable to process thread ${threadId}. Row id is missing.`);
        return;
      }

      if(!normalizeText(remarks)){
        alert(`Operation Remarks is mandatory for thread ${threadId}.`);
        return;
      }

      payloadRows.push({
        row_id: safeValue(latestRow.id),
        channel_thread_id: threadId,
        parse_remarks: remarks
      });
    }

    try{
      const response = await postOperationAction(url, { rows: payloadRows });
      alert(response.message || successMessage);
      window.location.reload();
    }catch(error){
      alert(error.message || "Bulk action failed.");
    }
  }

  async function saveSelectedThreads(){
    await bulkActionSelected("/operation-queue/save-selected", "Selected records saved successfully.");
  }

  async function resolveSelectedThreads(){
    await bulkActionSelected("/operation-queue/resolve-selected", "Selected records resolved successfully.");
  }

  document.addEventListener("DOMContentLoaded", async function(){
    initOperationDepartmentMultiSelect();
    applyDetailFieldEditability();
    toggleAttenderSection("toggleAttender1", "bodyAttender1");
    toggleAttenderSection("toggleAttender2", "bodyAttender2");
    toggleAttenderSection("toggleAttender3", "bodyAttender3");

    loadHospitalLov();
    bindDashboardCardEvents();
    renderThreadGrid();

    const pageSizeSelect = document.getElementById("opqPageSizeSelect");
    if(pageSizeSelect){
      pageSizeSelect.value = String(opqGridPageSize);
      pageSizeSelect.addEventListener("change", function(){
        opqGridPageSize = Number(this.value || 10);
        opqCurrentGridPage = 1;
        renderThreadGrid(false);
        if(isSidePanelOpen() && opqCurrentThreadId){
          openPreferredThread(opqCurrentThreadId, false);
        }
      });
    }

    const openedFromContext = openThreadFromContext();

    if(!openedFromContext){
      /*
        Normal Operation Queue load should not auto-open the first grid record.
        Side panel will open only by explicit user action or URL context.
      */
    }

    const masterCheckbox = document.getElementById("opqMasterCheckbox");
    if(masterCheckbox){
      masterCheckbox.addEventListener("change", function(){
        toggleSelectAll(this.checked);
      });
    }

    const watchSelectors = Array.from(document.querySelectorAll(
      '.opq-side-body input:not(.opq-email-ignore), .opq-side-body select:not(.opq-email-ignore), .opq-side-body textarea:not(.opq-email-ignore)'
    )).filter(el => !el.closest(".opq-dept-dropdown"));

    watchSelectors.forEach(el => {
      el.addEventListener("input", updateUnsavedBadge);
      el.addEventListener("change", updateUnsavedBadge);
    });

    const searchInput = document.getElementById("opqSearchInput");
    if(searchInput){
      searchInput.addEventListener("keydown", function(e){
        if(e.key === "Enter"){
          applyGridFilters();
        }
      });
    }
  });

  function populateEmailComposer(){}

window.processWithoutEmail = async function(queueId) {
  if (!queueId) {
    alert("Please select a queue row first.");
    return;
  }

  const confirmed = window.confirm(
    "Process Without Email?\n\n" +
    "No email will be sent. CADUPIUS will validate the case and proceed " +
    "with Patient, Lead and Workflow creation."
  );

  if (!confirmed) {
    return;
  }

  const button = document.getElementById("opqProcessWithoutEmailBtn");
  const originalText = button ? button.textContent : "";

  if (button) {
    button.disabled = true;
    button.textContent = "Processing...";
  }

  try {
    const requestIdEl = document.getElementById("request_id");

    const requestId =
      (requestIdEl && requestIdEl.value
        ? String(requestIdEl.value).trim()
        : "") ||
      `NOEMAIL-${queueId}-${Date.now()}`;

    const response = await fetch(
      "/api/operation-queue/process-without-email",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest"
        },
        body: JSON.stringify({
          queue_id: queueId,
          request_id: requestId
        })
      }
    );

    const data = await response.json().catch(() => ({}));

    if (!response.ok || !data.success) {
      alert(
        data.message ||
        "Unable to process the case without email."
      );
      return;
    }

    alert(
      data.message ||
      "Case processed successfully. Patient, Lead and Workflow processing completed. Email communication was skipped."
    );

    window.location.reload();

  } catch (error) {
    console.error("PROCESS_WITHOUT_EMAIL_ERROR", error);

    alert(
      "Unable to process the case without email. Please try again."
    );

  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalText || "Process Without Email";
    }
  }
};
