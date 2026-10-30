const opqCurrentEmailRow = JSON.parse(document.getElementById('emailQueueRowData').textContent || '{}');
  window.opqCurrentAttachments = JSON.parse(document.getElementById('emailAttachmentsData').textContent || '[]');

  function safeValue(value){ return value === null || value === undefined ? '' : String(value); }
  function setInputValue(id, value){ const el=document.getElementById(id); if(el){ el.value=safeValue(value); } }
  function setTextValue(id, value){ const el=document.getElementById(id); if(el){ el.textContent=safeValue(value) || '—'; } }
  function decodeHtmlEntities(value){ if(value===null || value===undefined){ return ''; } const textarea=document.createElement('textarea'); textarea.innerHTML=String(value); return textarea.value; }
  function escapeHtml(value){ return safeValue(value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#039;'); }
  function convertHtmlEmailToPlainText(value){ let text=safeValue(value); if(!text.trim()) return ''; text=text.replace(/\r\n/g,'\n'); text=text.replace(/<br\s*\/?>/gi,'\n'); text=text.replace(/<\/div>/gi,'\n'); text=text.replace(/<div[^>]*>/gi,''); text=text.replace(/<\/p>/gi,'\n'); text=text.replace(/<p[^>]*>/gi,''); text=text.replace(/<\/li>/gi,'\n'); text=text.replace(/<li[^>]*>/gi,'• '); text=text.replace(/<\/ul>/gi,'\n'); text=text.replace(/<ul[^>]*>/gi,''); text=text.replace(/<\/ol>/gi,'\n'); text=text.replace(/<ol[^>]*>/gi,''); text=text.replace(/<\/h[1-6]>/gi,'\n'); text=text.replace(/<h[1-6][^>]*>/gi,''); text=text.replace(/&nbsp;/gi,' '); text=text.replace(/<[^>]+>/g,''); text=decodeHtmlEntities(text); text=text.replace(/\n{3,}/g,'\n\n'); text=text.replace(/[ \t]+\n/g,'\n'); text=text.replace(/\n[ \t]+/g,'\n'); return text.trim(); }
  function formatDateTime(value){ const raw=safeValue(value); if(!raw) return ''; const normalized=raw.trim().replace('T',' '); const match=normalized.match(/^(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?$/); if(!match) return raw; const year=match[1]; const month=match[2]; const day=match[3]; const hour24=parseInt(match[4] || '0',10); const minute=match[5] || '00'; const second=match[6] || '00'; const ampm=hour24 >= 12 ? 'PM' : 'AM'; let hour12=hour24 % 12; if(hour12 === 0) hour12 = 12; return `${parseInt(month,10)}/${parseInt(day,10)}/${year}, ${hour12}:${minute}:${second} ${ampm}`; }
  function formatFileSize(bytes){ const size=Number(bytes || 0); if(!size || size < 0) return '—'; if(size < 1024) return `${size} B`; if(size < 1024*1024) return `${(size/1024).toFixed(2)} KB`; if(size < 1024*1024*1024) return `${(size/(1024*1024)).toFixed(2)} MB`; return `${(size/(1024*1024*1024)).toFixed(2)} GB`; }

  function closeEmailWindow(){
    try{
      window.close();
    }catch(error){
      console.warn('Window close failed:', error);
    }
  }

  function refreshParentOperationQueue(data={}){
    try{
      if (!window.opener || window.opener.closed) return;

      const queueId = safeValue(data.queue_id || document.getElementById('queue_id')?.value || '').trim();
      const threadId = safeValue(
        data.channel_thread_id ||
        (opqCurrentEmailRow && opqCurrentEmailRow.channel_thread_id) ||
        ''
      ).trim();

      if (typeof window.opener.refreshOperationQueueAfterEmail === 'function'){
        window.opener.refreshOperationQueueAfterEmail({
          queue_id: queueId,
          channel_thread_id: threadId,
          email_sent: true,
          payload: data
        });
        return;
      }

      if (typeof window.opener.loadOperationQueueGrid === 'function'){
        window.opener.loadOperationQueueGrid();
        return;
      }

      if (typeof window.opener.fetchOperationQueueGrid === 'function'){
        window.opener.fetchOperationQueueGrid();
        return;
      }

      if (typeof window.opener.reloadOperationQueueGrid === 'function'){
        window.opener.reloadOperationQueueGrid();
        return;
      }

      if (window.opener.location && typeof window.opener.location.reload === 'function'){
        window.opener.location.reload();
      }
    }catch(error){
      console.warn('Parent refresh failed:', error);
      try{
        if (window.opener && !window.opener.closed && window.opener.location && typeof window.opener.location.reload === 'function'){
          window.opener.location.reload();
        }
      }catch(innerError){
        console.warn('Parent reload fallback failed:', innerError);
      }
    }
  }

  function refreshParentAndClose(data={}){
    refreshParentOperationQueue(data);
    setTimeout(() => {
      closeEmailWindow();
    }, 500);
  }

  function renderEmailAttachmentOptions(attachments){
    const listEl=document.getElementById('opqEmailAttachmentList');
    const badgeEl=document.getElementById('opqEmailSectionBadge');
    if(!listEl) return;
    const attachmentList=Array.isArray(attachments) ? attachments : [];
    if(badgeEl){ badgeEl.textContent = `${attachmentList.length} Thread Document${attachmentList.length === 1 ? '' : 's'}`; }
    if(!attachmentList.length){ listEl.innerHTML='<div class="opq-empty">No thread documents available for email attachment.</div>'; updateEmailAttachmentSummary(); return; }
    listEl.innerHTML = attachmentList.map((attachment,index) => {
      const fileName=safeValue(attachment.file_name) || `Document ${index + 1}`;
      const fileExt=safeValue(attachment.file_ext).toUpperCase() || 'FILE';
      const fileSize=formatFileSize(attachment.file_size);
      const uploadedOn=formatDateTime(attachment.created_on || attachment.updated_on) || '—';
      const attachmentId=safeValue(attachment.id);
      return `<div class="opq-email-attachment-item"><label><input type="checkbox" class="opq-email-attachment-checkbox" data-index="${index}" data-attachment-id="${escapeHtml(attachmentId)}" checked onchange="updateEmailAttachmentSummary()" /><span><div class="opq-email-attachment-name">${escapeHtml(fileName)}</div><div class="opq-email-attachment-meta">${escapeHtml(fileExt)} • ${escapeHtml(fileSize)} • Uploaded ${escapeHtml(uploadedOn)}</div></span></label></div>`;
    }).join('');
    updateEmailAttachmentSummary();
  }

  function getSelectedEmailAttachmentNames(){ const attachments=Array.isArray(window.opqCurrentAttachments)?window.opqCurrentAttachments:[]; const selectedIndexes=Array.from(document.querySelectorAll('.opq-email-attachment-checkbox:checked')).map(el=>Number(el.getAttribute('data-index'))); return selectedIndexes.map(index=>{ const item=attachments[index] || {}; return safeValue(item.file_name || `Document ${index+1}`); }).filter(Boolean); }
  function getSelectedEmailAttachmentIds(){ return Array.from(document.querySelectorAll('.opq-email-attachment-checkbox:checked')).map(el=>safeValue(el.getAttribute('data-attachment-id')).trim()).filter(Boolean); }
  function updateEmailAttachmentSummary(){ const names=getSelectedEmailAttachmentNames(); const helperEl=document.getElementById('opqEmailHelperText'); if(helperEl){ if(names.length){ helperEl.innerHTML = `Selected thread documents: <strong>${escapeHtml(names.join(', '))}</strong>. These selected documents will be sent as real email attachments through the backend email API.`; } else { helperEl.textContent = 'No thread attachment selected. You can still send the email without attachments.'; } } }
  async function fetchHospitalEmail(hospitalShortName){ const safeHospitalShortName=safeValue(hospitalShortName).trim(); if(!safeHospitalShortName){ return {success:false,hospital_email:'',message:'Hospital short name is blank.'}; } try{ const response=await fetch(`/api/operation-queue/hospital-email?hospital_shrtnm=${encodeURIComponent(safeHospitalShortName)}`, {method:'GET', headers:{'X-Requested-With':'XMLHttpRequest'}}); const data=await response.json().catch(()=>({})); if(!response.ok){ return {success:false,hospital_email:'',message:safeValue(data.message) || `HTTP ${response.status}`}; } return {success:!!data.success,hospital_email:safeValue(data.hospital_email),message:safeValue(data.message)}; }catch(error){ console.error('Failed to fetch hospital email:', error); return {success:false,hospital_email:'',message:'Unable to fetch hospital email.'}; } }
  function getCurrentEmailBaseRow(){ return opqCurrentEmailRow && typeof opqCurrentEmailRow === 'object' ? opqCurrentEmailRow : {}; }
  function getCurrentOperationQueueIdForEmail(){ const row=getCurrentEmailBaseRow(); return safeValue(row.id || document.getElementById('queue_id')?.value || '').trim(); }
  function getCurrentHospitalShortNameForEmail(){ const row=getCurrentEmailBaseRow(); return safeValue(row.hospital_shrtnm || row.hospital_short_name || '').trim(); }
  async function resolveHospitalEmailForComposer(forceRefresh=false){ const toEl=document.getElementById('opq_email_to'); if(!toEl) return ''; const existing=safeValue(toEl.value).trim(); if(existing && !forceRefresh) return existing; const hospitalShrtnm=getCurrentHospitalShortNameForEmail(); if(!hospitalShrtnm) return ''; const result=await fetchHospitalEmail(hospitalShrtnm); const email=safeValue(result.hospital_email).trim(); if(email){ toEl.value=email; setTextValue('opqEmailHospitalAddressText', email); const statusEl=document.getElementById('opqEmailStatusText'); if(statusEl) statusEl.textContent='Hospital email found.'; return email; } setTextValue('opqEmailHospitalAddressText','—'); const statusEl=document.getElementById('opqEmailStatusText'); if(statusEl){ statusEl.textContent = hospitalShrtnm ? 'Hospital email is blank. You can still enter an email address manually.' : 'Hospital short name is blank. You can still enter an email address manually.'; } return ''; }
  function clearEmailDraftFields(options={}){ const keepTo=options.keepTo===true; const toEl=document.getElementById('opq_email_to'); const ids=['opq_email_cc','opq_email_bcc','opq_email_subject','opq_email_body']; const tplEl=document.getElementById('opq_email_template'); if(tplEl) tplEl.value=''; if(!keepTo && toEl) toEl.value=''; ids.forEach(id=>{ const el=document.getElementById(id); if(el) el.value=''; }); const statusText=document.getElementById('opqEmailStatusText'); if(statusText) statusText.textContent='Select an Email Template to load the rendered draft from backend.'; const helperText=document.getElementById('opqEmailHelperText'); if(helperText) helperText.innerHTML='Select an Email Template to load the rendered draft from backend.'; setTextValue('opqEmailHospitalAddressText', keepTo && toEl && safeValue(toEl.value).trim() ? toEl.value : '—'); }
  async function populateEmailComposer(row, attachments){ const threadText=safeValue((row||{}).channel_thread_id || (row||{}).channel_message_id || (row||{}).id) || '—'; setTextValue('opqEmailThreadText', threadText); setTextValue('opqEmailQueueText', getCurrentOperationQueueIdForEmail() || '—'); clearEmailDraftFields({keepTo:false}); renderEmailAttachmentOptions(attachments || []); await resolveHospitalEmailForComposer(true); }

  async function applyEmailTemplate(){
    const templateEl=document.getElementById('opq_email_template');
    const subjectEl=document.getElementById('opq_email_subject');
    const bodyEl=document.getElementById('opq_email_body');
    if(!templateEl || !subjectEl || !bodyEl) return;

    const selectedOption = templateEl ? templateEl.options[templateEl.selectedIndex] : null;
    const templateCode = safeValue(selectedOption?.dataset?.templateCode).trim();

    const operationQueueId=getCurrentOperationQueueIdForEmail();
    if(!operationQueueId){ alert('Operation Queue row id is missing.'); return; }

    const templateName=safeValue(templateEl.value).trim();
    if(!templateName){ clearEmailDraftFields({keepTo:true}); await resolveHospitalEmailForComposer(false); return; }

    const toEmail=await resolveHospitalEmailForComposer(false);
    if(!toEmail){ alert('Hospital email is not available.'); return; }

    const ccEmail=safeValue(document.getElementById('opq_email_cc')?.value).trim();
    const bccEmail=safeValue(document.getElementById('opq_email_bcc')?.value).trim();
    const selectedAttachmentIds=getSelectedEmailAttachmentIds();

    try{
      const response=await fetch('/api/operation-queue/apply-email-template', {
        method:'POST',
        headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},
        body:JSON.stringify({
          operation_queue_id:operationQueueId,
          template_name:templateName,
          template_code:templateCode,
          hospital_shrtnm:getCurrentHospitalShortNameForEmail(),
          to_email:toEmail,
          cc_email:ccEmail,
          bcc_email:bccEmail,
          selected_attachment_ids:selectedAttachmentIds
        })
      });

      const data=await response.json().catch(()=>({}));
      if(!response.ok || !data.success) throw new Error(data.message || data.error || `HTTP ${response.status}`);

      subjectEl.value=convertHtmlEmailToPlainText(safeValue(data.rendered_subject));
      bodyEl.value=convertHtmlEmailToPlainText(safeValue(data.rendered_body));

      const statusText=document.getElementById('opqEmailStatusText');
      if(statusText) statusText.textContent=safeValue(data.message || 'Template applied successfully.');

      const helperText=document.getElementById('opqEmailHelperText');
      if(helperText) helperText.innerHTML='Template applied from backend. Review the draft and click <strong>Send Email</strong> to send.';
    }catch(error){
      console.error('Unable to apply email template:', error);
      alert(error.message || 'Unable to apply email template.');
    }
  }

  async function resetEmailDraft(){ clearEmailDraftFields({keepTo:true}); await resolveHospitalEmailForComposer(false); }
  async function copyEmailBody(){ const body=safeValue(document.getElementById('opq_email_body')?.value); if(!body.trim()){ alert('Email body is empty.'); return; } try{ await navigator.clipboard.writeText(body); alert('Email body copied successfully.'); }catch(error){ alert('Unable to copy email body.'); } }

  async function openEmailDraft(){
    const templateEl = document.getElementById('opq_email_template');
    const selectedOption = templateEl ? templateEl.options[templateEl.selectedIndex] : null;

    const templateName = safeValue(templateEl?.value).trim();
    const templateCode = safeValue(selectedOption?.dataset?.templateCode).trim();

    const queueId=safeValue(document.getElementById('queue_id')?.value || getCurrentOperationQueueIdForEmail()).trim();
    const requestId=safeValue(document.getElementById('request_id')?.value).trim();
    const to=safeValue(document.getElementById('opq_email_to')?.value).trim();
    const cc=safeValue(document.getElementById('opq_email_cc')?.value).trim();
    const bcc=safeValue(document.getElementById('opq_email_bcc')?.value).trim();
    const subject=safeValue(document.getElementById('opq_email_subject')?.value).trim();
    const body=safeValue(document.getElementById('opq_email_body')?.value);

    if(!queueId){ alert('Queue id is missing.'); return; }
    if(!requestId){ alert('Request id is missing. Please reopen the email communication window.'); return; }
    if(!to){ alert('Please enter the recipient email address.'); document.getElementById('opq_email_to')?.focus(); return; }
    if(!subject){ alert('Please enter the email subject.'); document.getElementById('opq_email_subject')?.focus(); return; }
    if(!body.trim()){ alert('Please enter the email body.'); document.getElementById('opq_email_body')?.focus(); return; }

    try{
      const response=await fetch('/api/operation-queue/send-email', {
        method:'POST',
        headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},
        body:JSON.stringify({
          queue_id:queueId,
          request_id:requestId,
          to:to,
          cc:cc,
          bcc:bcc,
          subject:subject,
          body:body,
          template_name:templateName,
          template_code:templateCode,
          selected_attachment_ids:getSelectedEmailAttachmentIds()
        })
      });

      const data=await response.json().catch(()=>({}));
      if(!response.ok || !data.success) throw new Error(data.message || data.error || `HTTP ${response.status}`);

      const attachedCount=Number(data.attachment_count || 0);
      const missingCount=Array.isArray(data.missing_files) ? data.missing_files.length : 0;

      if(missingCount > 0){
        alert(`Email sent successfully with ${attachedCount} attachment(s). ${missingCount} attachment file(s) were not found on disk.`);
      } else {
        alert(`Email sent successfully with ${attachedCount} attachment(s).`);
      }

      refreshParentAndClose({
        queue_id: queueId,
        request_id: requestId,
        channel_thread_id: safeValue(data.channel_thread_id || opqCurrentEmailRow?.channel_thread_id),
        attachment_count: attachedCount,
        missing_files: Array.isArray(data.missing_files) ? data.missing_files : [],
        response: data
      });
    }catch(error){
      alert(error.message || 'Unable to send email.');
    }
  }

  document.addEventListener('DOMContentLoaded', async function(){ populateEmailComposer(opqCurrentEmailRow, window.opqCurrentAttachments || []); });
