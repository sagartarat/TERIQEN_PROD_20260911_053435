(() => {
  const $ = id => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
  const api = (path, options={}) => fetch('/broadcast-management' + path, {
    credentials:'same-origin',
    ...options,
    headers:{'X-CSRFToken':csrf,'Content-Type':'application/json',...(options.headers||{})}
  }).then(async r => {
    const j=await r.json().catch(()=>({success:false,message:'Invalid server response'}));
    if(!r.ok || j.success===false) throw new Error(j.message||`HTTP ${r.status}`);
    return j;
  });

  let currentCampaignId = null;
  let currentCampaignStatus = '';
  let detailPage = 1;
  let detailPages = 1;
  let detailPageSize = 50;

  function toast(msg){const e=$('bcToast');if(!e)return;e.textContent=msg;e.classList.add('show');setTimeout(()=>e.classList.remove('show'),3500);}
  function statusClass(status){const s=String(status||'').toUpperCase();return s==='SUCCESS'?'success':s==='FAILED'?'failed':(s==='SKIPPED'||s==='UNSUBSCRIBED')?'skipped':'queued';}
  function destination(r, channel){
    channel=String(channel||'').toUpperCase();
    if(channel==='EMAIL') return r.email_id||'—';
    if(channel==='TELEGRAM') return r.telegram_chat_id||r.mobile_no||'—';
    return r.mobile_no||'—';
  }
  function whenText(r){return r.sent_on||r.updated_on||'—';}
  function canDelete(status){return !['QUEUED','IN_PROGRESS','SCHEDULED'].includes(String(status||'').toUpperCase());}

  function updateDetailPager(p){
    detailPage = Number(p.page||1);
    detailPages = Number(p.pages||1);
    $('bcHistoryPageInfo').textContent=`Page ${detailPage} of ${detailPages}`;
    $('bcHistoryPrev').disabled=detailPage<=1;
    $('bcHistoryNext').disabled=detailPage>=detailPages;
    const total=Number(p.total||0), start=Number(p.start||0), end=Number(p.end||0);
    $('bcHistoryCount').textContent=total?`Showing ${start}–${end} of ${total} recipient records`:'0 recipient records';
  }

  async function openCampaign(id, page=1){
    currentCampaignId=Number(id);
    detailPage=Number(page)||1;
    $('bcHistoryRecipients').innerHTML='<tr><td colspan="8" class="bc-empty">Loading delivery details...</td></tr>';
    $('bcHistoryModal').classList.remove('hidden');
    try{
      const j=await api(`/api/history/${id}?page=${detailPage}&page_size=${detailPageSize}`);
      const c=j.campaign||{}, rows=j.recipients||[], p=j.pagination||{};
      currentCampaignStatus=String(c.campaign_status||'').toUpperCase();
      const isDraft=currentCampaignStatus==='DRAFT';
      $('bcHistoryTitle').textContent=`Broadcast #${c.id} — ${c.campaign_name||''}`;
      $('bcHistorySubtitle').textContent=`${c.channel_code||''} • ${c.campaign_status||''} • Created ${c.created_on||'—'}`;
      $('bcHistorySummary').innerHTML=`
        <div><b>${Number(c.total_count||0)}</b><span>Total</span></div>
        <div><b>${Number(c.success_count||0)}</b><span>Success</span></div>
        <div><b>${Number(c.failed_count||0)}</b><span>Failed</span></div>
        <div><b>${isDraft?Number(c.valid_count||0):Number(c.skipped_count||0)}</b><span>${isDraft?'Valid':'Skipped'}</span></div>`;
      $('bcHistoryNote').innerHTML=isDraft
        ? '<b>Draft:</b> These recipients are saved only. They are not in the delivery queue until you click <b>Send Now</b>.'
        : '<b>Failure Reason:</b> TERIQEN records the provider/SMTP/API error returned for each failed receiver.';
      const startIndex=Math.max(0,Number(p.start||1)-1);
      $('bcHistoryRecipients').innerHTML=rows.length?rows.map((r,i)=>{
        const status=String(r.delivery_status||'').toUpperCase();
        const error=status==='FAILED'?(r.last_error||'No provider error was recorded.'):(r.last_error||'—');
        return `<tr>
          <td>${startIndex+i+1}</td>
          <td><b>${esc(r.receiver_name||'—')}</b><small class="bc-cell-sub">${esc(r.country_name||'')}${r.language_code?' • '+esc(r.language_code):''}</small></td>
          <td>${esc(destination(r,c.channel_code))}</td>
          <td><span class="bc-delivery-badge ${statusClass(status)}">${esc(status||'—')}</span></td>
          <td>${Number(r.attempt_count||0)}</td>
          <td>${esc(r.provider_reference||'—')}</td>
          <td class="bc-error-cell ${status==='FAILED'?'has-error':''}">${esc(error)}</td>
          <td>${esc(whenText(r))}</td>
        </tr>`;
      }).join(''):'<tr><td colspan="8" class="bc-empty">No recipient records found for this broadcast.</td></tr>';
      updateDetailPager(p);
      $('bcHistoryDelete').classList.toggle('hidden',!canDelete(currentCampaignStatus));
    }catch(e){
      $('bcHistoryRecipients').innerHTML='<tr><td colspan="8" class="bc-empty">Unable to load delivery details.</td></tr>';
      toast(e.message);
    }
  }

  async function sendCampaign(id, btn){
    if(!confirm('Send this broadcast now?')) return;
    const original=btn.innerHTML;
    btn.disabled=true; btn.innerHTML='<i class="fa-solid fa-spinner fa-spin"></i> Sending...';
    try{
      await api(`/api/campaigns/${id}/activate`,{method:'POST',body:'{}'});
      let loops=0, status={};
      while(loops++<500){
        await api(`/api/campaigns/${id}/dispatch`,{method:'POST',body:'{}'});
        status=(await api(`/api/campaigns/${id}/status`)).data||{};
        if(Number(status.queued_count||0)===0) break;
      }
      if(Number(status.queued_count||0)>0) throw new Error('Some recipients remain queued. Use Resume Sending to continue.');
      toast(`Broadcast #${id} completed. Success: ${Number(status.success_count||0)}, Failed: ${Number(status.failed_count||0)}.`);
      setTimeout(()=>location.reload(),900);
    }catch(e){
      toast(e.message);
      btn.disabled=false; btn.innerHTML=original;
    }
  }

  async function deleteCampaign(id, status){
    if(!canDelete(status)) { toast('Active/queued broadcasts cannot be deleted.'); return; }
    const noun=String(status||'').toUpperCase()==='DRAFT'?'draft':'broadcast history record';
    if(!confirm(`Delete this ${noun} #${id} and all of its recipient records?\n\nThis action cannot be undone.`)) return;
    try{
      const j=await api(`/api/history/${id}`,{method:'DELETE',body:'{}'});
      toast(`Deleted broadcast #${id}${j.deleted_recipients!=null?` and ${Number(j.deleted_recipients)} recipient record${Number(j.deleted_recipients)===1?'':'s'}`:''}.`);
      $('bcHistoryModal')?.classList.add('hidden');
      setTimeout(()=>location.reload(),650);
    }catch(e){toast(e.message);}
  }

  document.querySelectorAll('.bc-view-history').forEach(btn=>btn.addEventListener('click',()=>openCampaign(btn.dataset.campaignId,1)));
  document.querySelectorAll('.bc-send-campaign').forEach(btn=>btn.addEventListener('click',()=>sendCampaign(btn.dataset.campaignId,btn)));
  document.querySelectorAll('.bc-delete-history').forEach(btn=>btn.addEventListener('click',()=>deleteCampaign(btn.dataset.campaignId,btn.dataset.status)));
  document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>b.closest('.bc-modal')?.classList.add('hidden')));
  $('bcHistoryModal')?.addEventListener('click',e=>{if(e.target===$('bcHistoryModal'))$('bcHistoryModal').classList.add('hidden');});
  $('bcHistoryPrev')?.addEventListener('click',()=>{if(currentCampaignId&&detailPage>1)openCampaign(currentCampaignId,detailPage-1);});
  $('bcHistoryNext')?.addEventListener('click',()=>{if(currentCampaignId&&detailPage<detailPages)openCampaign(currentCampaignId,detailPage+1);});
  $('bcHistoryPageSize')?.addEventListener('change',e=>{detailPageSize=Number(e.target.value)||50;if(currentCampaignId)openCampaign(currentCampaignId,1);});
  $('bcHistoryDelete')?.addEventListener('click',()=>{if(currentCampaignId)deleteCampaign(currentCampaignId,currentCampaignStatus);});
})();

/* TERIQEN Broadcast V1.3.1 — bulk history delete */
(() => {
  function initBulkDeleteV131() {
  const page = document.querySelector('.bc-history-page');
  if (!page || page.dataset.bulkDeleteV131 === '1') return;
  page.dataset.bulkDeleteV131 = '1';

  const tableWrap = page.querySelector('.bc-history-paged-table') || page.querySelector('.bc-table-wrap');
  const table = tableWrap?.querySelector('table');
  const theadRow = table?.querySelector('thead tr');
  const tbody = table?.querySelector('tbody');
  if (!tableWrap || !table || !theadRow || !tbody) return;

  const headers = Array.from(theadRow.cells).map(cell => String(cell.textContent || '').trim().toLowerCase());
  const idIndex = Math.max(0, headers.findIndex(text => text === 'id' || text.startsWith('id ')));
  const statusIndexRaw = headers.findIndex(text => text === 'status' || text.includes('status'));
  const statusIndex = statusIndexRaw >= 0 ? statusIndexRaw : 3;
  const protectedStatuses = new Set(['QUEUED', 'IN_PROGRESS', 'SCHEDULED', 'ACTIVE', 'SENDING']);

  const selectHead = document.createElement('th');
  selectHead.className = 'bc-history-select-cell';
  selectHead.title = 'Select all deletable records on this page';
  selectHead.innerHTML = '<input type="checkbox" id="bcHistorySelectPageHead" aria-label="Select all deletable records on this page">';
  theadRow.insertBefore(selectHead, theadRow.firstChild);

  const selectable = [];
  Array.from(tbody.rows).forEach(row => {
    const originalCells = Array.from(row.cells);
    if (!originalCells.length) return;
    const idText = String(originalCells[idIndex]?.textContent || '').trim();
    if (!/^\d+$/.test(idText)) return;

    const status = String(originalCells[statusIndex]?.textContent || '')
      .trim().toUpperCase().replace(/[\s-]+/g, '_');
    const isProtected = protectedStatuses.has(status);
    const cell = document.createElement('td');
    cell.className = 'bc-history-select-cell';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.className = 'bc-history-row-select';
    input.value = idText;
    input.dataset.campaignId = idText;
    input.setAttribute('aria-label', `Select broadcast ${idText}`);
    if (isProtected) {
      input.disabled = true;
      input.title = `${status.replaceAll('_', ' ')} broadcasts cannot be deleted`;
    } else {
      selectable.push(input);
    }
    cell.appendChild(input);
    row.insertBefore(cell, row.firstChild);
  });

  const bar = document.createElement('div');
  bar.className = 'bc-history-bulkbar';
  bar.innerHTML = `
    <div class="bc-history-bulkbar-left">
      <label><input type="checkbox" id="bcHistorySelectPage"> Select Page</label>
      <span class="bc-history-bulk-count" id="bcHistoryBulkCount">0 selected</span>
    </div>
    <button type="button" class="bc-btn bc-history-bulk-delete" id="bcHistoryBulkDelete" disabled>
      <i class="fa-solid fa-trash-can"></i> Delete Selected
    </button>`;
  tableWrap.parentNode.insertBefore(bar, tableWrap);

  const selectPage = document.getElementById('bcHistorySelectPage');
  const selectPageHead = document.getElementById('bcHistorySelectPageHead');
  const count = document.getElementById('bcHistoryBulkCount');
  const deleteButton = document.getElementById('bcHistoryBulkDelete');

  function selectedInputs() {
    return selectable.filter(input => input.checked && !input.disabled);
  }

  function refreshSelectionState() {
    const chosen = selectedInputs().length;
    const enabled = selectable.length;
    count.textContent = `${chosen} selected`;
    deleteButton.disabled = chosen === 0;
    const all = enabled > 0 && chosen === enabled;
    const partial = chosen > 0 && chosen < enabled;
    selectPage.checked = all;
    selectPage.indeterminate = partial;
    selectPageHead.checked = all;
    selectPageHead.indeterminate = partial;
    selectPage.disabled = enabled === 0;
    selectPageHead.disabled = enabled === 0;
  }

  function setAll(checked) {
    selectable.forEach(input => { input.checked = checked; });
    refreshSelectionState();
  }

  selectPage.addEventListener('change', () => setAll(selectPage.checked));
  selectPageHead.addEventListener('change', () => setAll(selectPageHead.checked));
  selectable.forEach(input => input.addEventListener('change', refreshSelectionState));
  refreshSelectionState();

  deleteButton.addEventListener('click', async () => {
    const campaignIds = selectedInputs().map(input => Number(input.dataset.campaignId)).filter(Number.isInteger);
    if (!campaignIds.length) return;
    const noun = campaignIds.length === 1 ? 'record' : 'records';
    if (!window.confirm(`Permanently delete ${campaignIds.length} selected Broadcast/Draft ${noun}? Recipient rows belonging to the selected records will also be deleted.`)) return;

    deleteButton.disabled = true;
    const oldText = deleteButton.innerHTML;
    deleteButton.textContent = 'Deleting...';
    try {
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
      const base = window.location.pathname.replace(/\/history\/?$/, '');
      const response = await fetch(`${base}/api/history/bulk`, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          ...(csrf ? {'X-CSRFToken': csrf} : {})
        },
        body: JSON.stringify({campaign_ids: campaignIds})
      });
      const data = await response.json().catch(() => ({success:false, message:`Invalid server response (HTTP ${response.status}).`}));
      if (!response.ok || data.success === false) throw new Error(data.message || `Bulk delete failed (HTTP ${response.status}).`);

      const skipped = Number(data.protected_ids?.length || 0);
      const missing = Number(data.missing_ids?.length || 0);
      let message = `${Number(data.deleted_campaigns || 0)} record(s) deleted successfully.`;
      if (skipped) message += ` ${skipped} active/queued/scheduled record(s) were protected and not deleted.`;
      if (missing) message += ` ${missing} record(s) no longer existed.`;
      window.alert(message);
      window.location.reload();
    } catch (error) {
      window.alert(error?.message || 'Unable to delete the selected records.');
      deleteButton.innerHTML = oldText;
      refreshSelectionState();
    }
  });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initBulkDeleteV131, {once:true});
  } else {
    initBulkDeleteV131();
  }
})();
