const etbSampleData = JSON.parse(
    document.getElementById("etbPreviewValuesJson").textContent || "{}"
  );

  let currentPage = 1;
  const rowsPerPage = 10;
  let etbLastFocusedField = "body";
  let etbIsSaving = false;
  let etbIsDeleting = false;

  function etbLiteral(fieldName){
    return String.fromCharCode(123, 123) + fieldName + String.fromCharCode(125, 125);
  }

  function getEtbDefaultSubject(){
    return "";
  }

  function getEtbDefaultBody(){
    return "";
  }

  function normalizeStatus(value){
    const raw = String(value || "").trim().toUpperCase();
    if(raw === "DRAFT"){
      return "Draft";
    }
    if(raw === "ACTIVE"){
      return "Active";
    }
    if(raw === "INACTIVE"){
      return "Inactive";
    }
    return String(value || "").trim();
  }

  function replacePlaceholders(content){
    let output = String(content || "");

    Object.keys(etbSampleData).forEach(function(key){
      const escapedKey = String(key).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const literalKey = etbLiteral(key).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

      output = output.replace(new RegExp(literalKey, "g"), etbSampleData[key]);
      output = output.replace(new RegExp(escapedKey, "g"), etbSampleData[key]);
    });

    return output;
  }

  function getEditorText(){
    const editorEl = document.getElementById("etb_editor");
    return editorEl ? editorEl.innerText : "";
  }

  function getEditorHtml(){
    const editorEl = document.getElementById("etb_editor");
    return editorEl ? editorEl.innerHTML : "";
  }

  function renderSubjectInlinePreview(subject){
    const subjectInlinePreview = document.getElementById("etbSubjectInlinePreview");
    if(!subjectInlinePreview){
      return;
    }

    const rawSubject = String(subject || "");
    const tokenRegex = /(\{\{[^}]+\}\})/g;
    const parts = rawSubject.split(tokenRegex).filter(function(item){
      return item !== "";
    });

    if(parts.length === 0){
      subjectInlinePreview.innerHTML = '<span class="etb-inline-text"></span>';
      return;
    }

    subjectInlinePreview.innerHTML = parts.map(function(part){
      if(/^\{\{[^}]+\}\}$/.test(part)){
        return '<span class="etb-inline-chip">' + part.replace(/</g, "&lt;").replace(/>/g, "&gt;") + '</span>';
      }
      return '<span class="etb-inline-text">' + part.replace(/</g, "&lt;").replace(/>/g, "&gt;") + '</span>';
    }).join("");
  }

  function openPreviewDrawer(){
    const overlay = document.getElementById("etbPreviewOverlay");
    const drawer = document.getElementById("etbPreviewDrawer");

    if(overlay){
      overlay.classList.add("open");
    }

    if(drawer){
      drawer.classList.add("open");
      drawer.setAttribute("aria-hidden", "false");
    }

    document.body.style.overflow = "hidden";
  }

  function closePreviewDrawer(){
    const overlay = document.getElementById("etbPreviewOverlay");
    const drawer = document.getElementById("etbPreviewDrawer");

    if(overlay){
      overlay.classList.remove("open");
    }

    if(drawer){
      drawer.classList.remove("open");
      drawer.setAttribute("aria-hidden", "true");
    }

    document.body.style.overflow = "";
  }

  function renderTemplatePreview(){
    const subjectEl = document.getElementById("etb_subject");
    const templateCodeEl = document.getElementById("etb_template_code");
    const statusEl = document.getElementById("etb_template_status");

    const subject = subjectEl ? subjectEl.value : "";
    const renderedSubject = replacePlaceholders(subject);
    const rawBodyHtml = getEditorHtml();
    const renderedBodyHtml = replacePlaceholders(rawBodyHtml);

    renderSubjectInlinePreview(subject);

    const previewTemplateCode = document.getElementById("etbPreviewTemplateCode");
    const previewStatus = document.getElementById("etbPreviewStatus");
    const previewSubject = document.getElementById("etbPreviewSubject");
    const previewBody = document.getElementById("etbPreviewBody");

    if(previewTemplateCode){
      previewTemplateCode.textContent = templateCodeEl ? templateCodeEl.value : "";
    }

    if(previewStatus){
      previewStatus.textContent = statusEl ? statusEl.value : "";
    }

    if(previewSubject){
      previewSubject.textContent = renderedSubject;
    }

    if(previewBody){
      previewBody.innerHTML = renderedBodyHtml;
    }

    openPreviewDrawer();
  }

  function setInputReadOnly(el, shouldLock){
    if(!el){
      return;
    }
    el.readOnly = !!shouldLock;
  }

  function setTextareaReadOnly(el, shouldLock){
    if(!el){
      return;
    }
    el.readOnly = !!shouldLock;
  }

  function setSelectDisabled(el, shouldLock){
    if(!el){
      return;
    }
    el.disabled = !!shouldLock;
  }

  function applyEditRules(){
    const isExisting = (document.getElementById("etb_is_existing_record")?.value || "0") === "1";
    const currentStatus = normalizeStatus(document.getElementById("etb_template_status")?.value || "");
    const isInactive = isExisting && currentStatus.toUpperCase() === "INACTIVE";

    const templateCodeEl = document.getElementById("etb_template_code");
    const templateNameEl = document.getElementById("etb_template_name");
    const templateCategoryEl = document.getElementById("etb_template_category");
    const templateStatusEl = document.getElementById("etb_template_status");
    const templateLanguageEl = document.getElementById("etb_template_language");
    const templateChannelEl = document.getElementById("etb_template_channel");
    const templateDescriptionEl = document.getElementById("etb_template_description");
    const templateSubjectEl = document.getElementById("etb_subject");
    const editorEl = document.getElementById("etb_editor");
    const editorToolbar = document.getElementById("etbEditorToolbar");
    const noteEl = document.getElementById("etbFormLockNote");

    setInputReadOnly(templateCodeEl, isExisting);
    setSelectDisabled(templateLanguageEl, isExisting);
    setInputReadOnly(templateChannelEl, true);

    setInputReadOnly(templateNameEl, isInactive);
    setSelectDisabled(templateCategoryEl, isInactive);
    setTextareaReadOnly(templateDescriptionEl, isInactive);
    setInputReadOnly(templateSubjectEl, isInactive);

    if(templateStatusEl){
      templateStatusEl.disabled = false;
    }

    if(editorEl){
      editorEl.contentEditable = isInactive ? "false" : "true";
      editorEl.classList.toggle("etb-readonly", isInactive);
    }

    if(editorToolbar){
      editorToolbar.style.opacity = isInactive ? "0.55" : "1";
      editorToolbar.style.pointerEvents = isInactive ? "none" : "auto";
    }

    if(noteEl){
      if(isInactive){
        noteEl.style.display = "block";
        noteEl.textContent = "Inactive templates allow editing of Status only. Template Code, Channel, and Language are locked for existing records.";
      } else if(isExisting){
        noteEl.style.display = "block";
        noteEl.textContent = "For existing records, Template Code, Channel, and Language are locked. Other fields are editable for Draft and Active templates.";
      } else {
        noteEl.style.display = "none";
      }
    }
  }

  function resetTemplateBuilder(){
    const templateId = document.getElementById("etb_template_id");
    const isExisting = document.getElementById("etb_is_existing_record");
    const loadedOriginalStatus = document.getElementById("etb_loaded_original_status");
    const templateCode = document.getElementById("etb_template_code");
    const templateName = document.getElementById("etb_template_name");
    const templateDescription = document.getElementById("etb_template_description");
    const subject = document.getElementById("etb_subject");
    const editor = document.getElementById("etb_editor");
    const templateCategory = document.getElementById("etb_template_category");
    const templateStatus = document.getElementById("etb_template_status");
    const templateLanguage = document.getElementById("etb_template_language");
    const templateChannel = document.getElementById("etb_template_channel");

    if(templateId) templateId.value = "";
    if(isExisting) isExisting.value = "0";
    if(loadedOriginalStatus) loadedOriginalStatus.value = "";

    if(templateCode) templateCode.value = "";
    if(templateName) templateName.value = "";
    if(templateDescription) templateDescription.value = "";
    if(subject) subject.value = getEtbDefaultSubject();
    if(editor) editor.innerHTML = getEtbDefaultBody();

    if(templateCategory){
      if(templateCategory.options.length > 0){
        templateCategory.selectedIndex = 0;
      } else {
        templateCategory.value = "";
      }
    }

    if(templateStatus){
      if(Array.from(templateStatus.options).some(function(option){ return option.value === "Draft"; })){
        templateStatus.value = "Draft";
      } else if(Array.from(templateStatus.options).some(function(option){ return option.value === "DRAFT"; })){
        templateStatus.value = "DRAFT";
      } else {
        templateStatus.selectedIndex = 0;
      }
    }

    if(templateLanguage){
      if(Array.from(templateLanguage.options).some(function(option){ return option.value === "English"; })){
        templateLanguage.value = "English";
      } else {
        templateLanguage.selectedIndex = 0;
      }
    }

    if(templateChannel) templateChannel.value = "EMAIL";

    applyEditRules();
    renderSubjectInlinePreview(subject ? subject.value : "");
    closePreviewDrawer();
  }

  function paginateTable() {
    const tableBody = document.getElementById("placeholderTableBody");
    if(!tableBody){
      return;
    }

    const rows = Array.from(tableBody.querySelectorAll("tr.etb-mapping-row"));
    const totalRows = rows.length;
    const totalPages = Math.max(1, Math.ceil(totalRows / rowsPerPage));

    if(currentPage > totalPages){
      currentPage = totalPages;
    }

    const start = (currentPage - 1) * rowsPerPage;
    const end = start + rowsPerPage;

    rows.forEach(function(row, index){
      row.style.display = (index >= start && index < end) ? "" : "none";
    });

    const pageNumberEl = document.getElementById("etbPageNumber");
    const pageStartEl = document.getElementById("etbPageStart");
    const pageEndEl = document.getElementById("etbPageEnd");
    const pageTotalEl = document.getElementById("etbPageTotal");

    if(pageNumberEl){
      pageNumberEl.textContent = "Page " + currentPage + " of " + totalPages;
    }

    if(pageStartEl){
      pageStartEl.textContent = totalRows ? (start + 1) : 0;
    }

    if(pageEndEl){
      pageEndEl.textContent = Math.min(end, totalRows);
    }

    if(pageTotalEl){
      pageTotalEl.textContent = totalRows;
    }
  }

  function nextPage() {
    const tableBody = document.getElementById("placeholderTableBody");
    if(!tableBody){
      return;
    }

    const totalRows = tableBody.querySelectorAll("tr.etb-mapping-row").length;
    const totalPages = Math.max(1, Math.ceil(totalRows / rowsPerPage));

    if(currentPage < totalPages){
      currentPage++;
      paginateTable();
    }
  }

  function prevPage() {
    if(currentPage > 1){
      currentPage--;
      paginateTable();
    }
  }

  function formatEditor(command){
    const editorEl = document.getElementById("etb_editor");
    if(!editorEl || editorEl.contentEditable === "false"){
      return;
    }

    editorEl.focus();
    document.execCommand(command, false, null);
  }

  function insertTextAtCursor(text){
    const selection = window.getSelection();

    if(selection && selection.rangeCount > 0){
      const range = selection.getRangeAt(0);
      range.deleteContents();
      range.insertNode(document.createTextNode(text));
      range.collapse(false);
      selection.removeAllRanges();
      selection.addRange(range);
    }
  }

  function insertPlaceholder(token){
    const subjectEl = document.getElementById("etb_subject");
    const editorEl = document.getElementById("etb_editor");

    if(subjectEl && subjectEl.readOnly && (!editorEl || editorEl.contentEditable === "false")){
      return;
    }

    if(etbLastFocusedField === "subject" && subjectEl && !subjectEl.readOnly){
      const startPos = subjectEl.selectionStart || 0;
      const endPos = subjectEl.selectionEnd || 0;
      const currentValue = subjectEl.value || "";

      subjectEl.value = currentValue.substring(0, startPos) + token + currentValue.substring(endPos);
      subjectEl.focus();
      subjectEl.selectionStart = subjectEl.selectionEnd = startPos + token.length;
      renderSubjectInlinePreview(subjectEl.value);
    } else if(editorEl && editorEl.contentEditable !== "false"){
      editorEl.focus();
      insertTextAtCursor(token);
    }
  }

  function loadTemplateIntoForm(linkEl){
    const row = linkEl.closest("tr");
    if(!row){
      return;
    }

    const templateId = row.getAttribute("data-template-id") || "";
    const templateCode = row.getAttribute("data-template-code") || "";
    const templateName = row.getAttribute("data-template-name") || "";
    const templateCategory = row.getAttribute("data-template-category") || "";
    const templateStatus = normalizeStatus(row.getAttribute("data-template-status") || "");
    const templateLanguage = row.getAttribute("data-template-language") || "";
    const templateChannel = row.getAttribute("data-template-channel") || "EMAIL";
    const templateDescription = row.getAttribute("data-template-description") || "";
    const templateSubject = row.getAttribute("data-template-subject") || "";
    const templateBody = row.getAttribute("data-template-body") || "";

    const templateIdEl = document.getElementById("etb_template_id");
    const isExistingEl = document.getElementById("etb_is_existing_record");
    const originalStatusEl = document.getElementById("etb_loaded_original_status");

    const templateCodeEl = document.getElementById("etb_template_code");
    const templateNameEl = document.getElementById("etb_template_name");
    const templateCategoryEl = document.getElementById("etb_template_category");
    const templateStatusEl = document.getElementById("etb_template_status");
    const templateLanguageEl = document.getElementById("etb_template_language");
    const templateChannelEl = document.getElementById("etb_template_channel");
    const templateDescriptionEl = document.getElementById("etb_template_description");
    const templateSubjectEl = document.getElementById("etb_subject");
    const editorEl = document.getElementById("etb_editor");

    if(templateIdEl) templateIdEl.value = templateId;
    if(isExistingEl) isExistingEl.value = "1";
    if(originalStatusEl) originalStatusEl.value = templateStatus;

    if(templateCodeEl) templateCodeEl.value = templateCode;
    if(templateNameEl) templateNameEl.value = templateName;
    if(templateCategoryEl) templateCategoryEl.value = templateCategory;
    if(templateStatusEl) templateStatusEl.value = templateStatus;
    if(templateLanguageEl) templateLanguageEl.value = templateLanguage;
    if(templateChannelEl) templateChannelEl.value = templateChannel;
    if(templateDescriptionEl) templateDescriptionEl.value = templateDescription;
    if(templateSubjectEl) templateSubjectEl.value = templateSubject;
    if(editorEl) editorEl.innerHTML = templateBody;

    applyEditRules();
    renderSubjectInlinePreview(templateSubject);

    const addTemplatePanel = document.getElementById("etb_template_code");
    if(addTemplatePanel){
      addTemplatePanel.scrollIntoView({
        behavior: "smooth",
        block: "center"
      });
      addTemplatePanel.focus();
    }
  }

  function getTemplatePayload(actionType){
    const templateStatusEl = document.getElementById("etb_template_status");
    const currentStatus = normalizeStatus(templateStatusEl ? templateStatusEl.value : "");
    const originalStatus = normalizeStatus(document.getElementById("etb_loaded_original_status")?.value || "");
    const isExistingRecord = (document.getElementById("etb_is_existing_record")?.value || "0") === "1";

    let finalStatus = currentStatus;

    return {
      id: (document.getElementById("etb_template_id")?.value || "").trim(),
      template_code: (document.getElementById("etb_template_code")?.value || "").trim(),
      template_name: (document.getElementById("etb_template_name")?.value || "").trim(),
      template_category: (document.getElementById("etb_template_category")?.value || "").trim(),
      template_status: finalStatus,
      template_language: (document.getElementById("etb_template_language")?.value || "").trim(),
      template_channel: (document.getElementById("etb_template_channel")?.value || "EMAIL").trim(),
      template_description: (document.getElementById("etb_template_description")?.value || "").trim(),
      template_subject: (document.getElementById("etb_subject")?.value || "").trim(),
      template_body: getEditorHtml(),
      version_no: 1,
      is_default: 0,
      remarks: "",
      is_existing_record: isExistingRecord ? 1 : 0,
      original_status: originalStatus,
      save_action: actionType
    };
  }

  function validateTemplatePayload(payload){
    const errors = [];
    const isExisting = payload.is_existing_record === 1 || payload.is_existing_record === "1";
    const currentStatus = normalizeStatus(payload.template_status);

    if(!payload.template_code){
      errors.push("Template Code is required.");
    }
    if(!payload.template_name){
      errors.push("Template Name is required.");
    }
    if(!payload.template_category){
      errors.push("Category is required.");
    }
    if(!payload.template_status){
      errors.push("Status is required.");
    }
    if(!payload.template_language){
      errors.push("Language is required.");
    }
    if(!payload.template_channel){
      errors.push("Channel is required.");
    }
    if(!payload.template_subject){
      errors.push("Email Subject is required.");
    }

    const bodyText = (document.getElementById("etb_editor")?.innerText || "").trim();
    if(!bodyText){
      errors.push("Email Body is required.");
    }

    if(isExisting && currentStatus.toUpperCase() === "INACTIVE"){
      const templateNameEl = document.getElementById("etb_template_name");
      const templateCategoryEl = document.getElementById("etb_template_category");
      const templateDescriptionEl = document.getElementById("etb_template_description");
      const templateSubjectEl = document.getElementById("etb_subject");
      const editorEl = document.getElementById("etb_editor");

      if(templateNameEl && !templateNameEl.readOnly){
        errors.push("Inactive records allow modification only for Status field.");
      }
      if(templateCategoryEl && !templateCategoryEl.disabled){
        errors.push("Inactive records allow modification only for Status field.");
      }
      if(templateDescriptionEl && !templateDescriptionEl.readOnly){
        errors.push("Inactive records allow modification only for Status field.");
      }
      if(templateSubjectEl && !templateSubjectEl.readOnly){
        errors.push("Inactive records allow modification only for Status field.");
      }
      if(editorEl && editorEl.contentEditable !== "false"){
        errors.push("Inactive records allow modification only for Status field.");
      }
    }

    return Array.from(new Set(errors));
  }

  // EMAIL_TEMPLATE_DELETE_V1:
  // Delete only the selected saved row after an explicit user confirmation.
  async function deleteEmailTemplate(buttonEl){
    if(etbIsDeleting){
      return;
    }

    const row = buttonEl ? buttonEl.closest("tr.etb-saved-template-row") : null;
    if(!row){
      alert("Unable to identify the selected email template.");
      return;
    }

    const templateId = (row.getAttribute("data-template-id") || "").trim();
    const templateCode = (row.getAttribute("data-template-code") || "").trim();
    const templateName = (row.getAttribute("data-template-name") || "").trim();
    const templateStatus = normalizeStatus(row.getAttribute("data-template-status") || "");

    if(!templateId){
      alert("Unable to identify the selected email template.");
      return;
    }

    let message = "Permanently delete this email template?\n\n" +
      "Template Code: " + (templateCode || "-") + "\n" +
      "Template Name: " + (templateName || "-") + "\n" +
      "Status: " + (templateStatus || "-") + "\n\n";

    if(String(templateStatus).toUpperCase() === "ACTIVE"){
      message += "WARNING: This template is ACTIVE and may currently be used by email or alert flows.\n\n";
    }

    message += "This deletion cannot be undone. Continue?";

    if(!window.confirm(message)){
      return;
    }

    etbIsDeleting = true;
    if(buttonEl){
      buttonEl.disabled = true;
    }

    try {
      const response = await fetch("/api/email-template-builder/delete", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          id: templateId,
          template_code: templateCode
        })
      });

      const result = await response.json();

      if(!response.ok || !result.success){
        alert(result.message || "Unable to delete email template.");
        return;
      }

      alert(result.message || "Email template deleted successfully.");
      window.location.reload();
    } catch (error) {
      alert("Error while deleting template: " + error.message);
    } finally {
      etbIsDeleting = false;
      if(buttonEl){
        buttonEl.disabled = false;
      }
    }
  }

  async function saveEmailTemplate(actionType){
    if(etbIsSaving){
      return;
    }

    const payload = getTemplatePayload(actionType);
    const errors = validateTemplatePayload(payload);

    if(errors.length){
      alert(errors.join("\n"));
      return;
    }

    etbIsSaving = true;

    try {
      const response = await fetch("/api/email-template-builder/save", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(payload)
      });

      const result = await response.json();

      if(!response.ok || !result.success){
        const errorMessage = Array.isArray(result.errors) && result.errors.length
          ? result.errors.join("\n")
          : (result.message || "Unable to save template.");
        alert(errorMessage);
        return;
      }

      alert(result.message || "Email template saved successfully.");
      window.location.reload();
    } catch (error) {
      alert("Error while saving template: " + error.message);
    } finally {
      etbIsSaving = false;
    }
  }

  function applySavedTemplateGridFilters(){
    const searchValue = (document.getElementById("etbSearchInput")?.value || "").trim().toLowerCase();
    const categoryValue = (document.getElementById("etbCategory")?.value || "").trim().toLowerCase();
    const statusValue = (document.getElementById("etbStatus")?.value || "").trim().toLowerCase();
    const languageValue = (document.getElementById("etbLanguage")?.value || "").trim().toLowerCase();

    const rows = Array.from(document.querySelectorAll("#savedTemplateGridBody tr.etb-saved-template-row"));
    let visibleCount = 0;

    rows.forEach(function(row){
      const rowSearch = row.getAttribute("data-search") || "";
      const rowCategory = row.getAttribute("data-category") || "";
      const rowStatus = row.getAttribute("data-status") || "";
      const rowLanguage = row.getAttribute("data-language") || "";

      const matchesSearch = !searchValue || rowSearch.includes(searchValue);
      const matchesCategory = !categoryValue || rowCategory === categoryValue;
      const matchesStatus = !statusValue || rowStatus === statusValue;
      const matchesLanguage = !languageValue || rowLanguage === languageValue;

      const shouldShow = matchesSearch && matchesCategory && matchesStatus && matchesLanguage;
      row.style.display = shouldShow ? "" : "none";

      if(shouldShow){
        visibleCount += 1;
      }
    });

    const existingNoMatchRow = document.getElementById("savedTemplateGridNoMatchRow");
    if(existingNoMatchRow){
      existingNoMatchRow.remove();
    }

    const emptyDefaultRow = document.getElementById("savedTemplateGridEmptyRow");
    if(emptyDefaultRow){
      emptyDefaultRow.style.display = visibleCount === 0 && rows.length === 0 ? "" : "none";
    }

    if(visibleCount === 0 && rows.length > 0){
      const tbody = document.getElementById("savedTemplateGridBody");
      const tr = document.createElement("tr");
      tr.id = "savedTemplateGridNoMatchRow";
      tr.innerHTML = '<td colspan="14" class="etb-empty">No saved templates match the selected search criteria.</td>';
      tbody.appendChild(tr);
    }
  }

  document.addEventListener("DOMContentLoaded", function(){
    paginateTable();
    applySavedTemplateGridFilters();
    applyEditRules();
    renderSubjectInlinePreview(document.getElementById("etb_subject")?.value || "");

    const watchIds = [
      "etb_template_code",
      "etb_template_name",
      "etb_template_category",
      "etb_template_status",
      "etb_template_language",
      "etb_template_description",
      "etb_template_channel",
      "etb_subject"
    ];

    watchIds.forEach(function(id){
      const el = document.getElementById(id);
      if(el){
        el.addEventListener("input", function(){
          if(id === "etb_subject"){
            renderSubjectInlinePreview(el.value);
          }
        });

        el.addEventListener("change", function(){
          if(id === "etb_template_status"){
            applyEditRules();
          }
          if(id === "etb_subject"){
            renderSubjectInlinePreview(el.value);
          }
        });
      }
    });

    const filterIds = [
      "etbSearchInput",
      "etbCategory",
      "etbStatus",
      "etbLanguage"
    ];

    filterIds.forEach(function(id){
      const el = document.getElementById(id);
      if(el){
        el.addEventListener("input", applySavedTemplateGridFilters);
        el.addEventListener("change", applySavedTemplateGridFilters);
      }
    });

    const subjectEl = document.getElementById("etb_subject");
    if(subjectEl){
      subjectEl.addEventListener("focus", function(){
        etbLastFocusedField = "subject";
      });
      subjectEl.addEventListener("click", function(){
        etbLastFocusedField = "subject";
      });
      subjectEl.addEventListener("keyup", function(){
        etbLastFocusedField = "subject";
      });
      subjectEl.addEventListener("input", function(){
        renderSubjectInlinePreview(subjectEl.value);
      });
    }

    const editorEl = document.getElementById("etb_editor");
    if(editorEl){
      editorEl.addEventListener("focus", function(){
        etbLastFocusedField = "body";
      });
      editorEl.addEventListener("click", function(){
        etbLastFocusedField = "body";
      });
      editorEl.addEventListener("keyup", function(){
        etbLastFocusedField = "body";
      });
    }

    document.addEventListener("keydown", function(event){
      if(event.key === "Escape"){
        closePreviewDrawer();
      }
    });
  });
