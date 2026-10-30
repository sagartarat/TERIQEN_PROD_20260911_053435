(() => {
  const page = document.getElementById('btPage');
  if (!page || page.dataset.initialized === '1') return;
  page.dataset.initialized = '1';

  const $ = id => document.getElementById(id);
  const root = (page.dataset.apiRoot || '').replace(/\/$/, '');
  const centreUrl = page.dataset.centreUrl || root + '/';
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const mappingSources = (() => {
    try { return JSON.parse($('btMappingSources')?.textContent || '[]'); }
    catch (_) { return []; }
  })();

  let waTemplates = [];
  let emailTemplates = [];

  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'
  }[c]));
  const dateText = v => v ? String(v).replace('T',' ').replace(/\.\d+$/, '') : '—';

  function toast(msg) {
    const el = $('btToast');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => el.classList.remove('show'), 3200);
  }

  async function api(path, options = {}) {
    const isForm = options.body instanceof FormData;
    const headers = {
      ...(csrf ? {'X-CSRFToken': csrf} : {}),
      ...(isForm ? {} : {'Content-Type':'application/json'}),
      ...(options.headers || {})
    };
    const res = await fetch(root + path, {
      credentials:'same-origin',
      ...options,
      headers
    });
    const data = await res.json().catch(() => ({
      success:false,
      message:`Invalid server response (HTTP ${res.status}).`
    }));
    if (!res.ok || data.success === false) throw new Error(data.message || `Request failed (HTTP ${res.status}).`);
    return data;
  }

  function badge(status) {
    const s = String(status || 'UNKNOWN').toUpperCase();
    const cls = s === 'APPROVED' ? 'approved' :
                s === 'PENDING' ? 'pending' :
                s === 'REJECTED' ? 'rejected' : 'active-badge';
    return `<span class="bt-badge ${cls}"><span></span>${esc(s)}</span>`;
  }

  function mappingBadge(t) {
    if (t.unsupported_reasons?.length) return '<span class="bt-map-missing">Advanced mapping needed</span>';
    if (!t.variable_keys?.length) return '<span class="bt-map-ok">No variables</span>';
    return t.mapping_complete
      ? '<span class="bt-map-ok">Ready</span>'
      : `<span class="bt-map-missing">Map ${Number(t.missing_mapping?.length || 0)}</span>`;
  }

  function updateStats() {
    $('btTotal').textContent = waTemplates.length;
    $('btApproved').textContent = waTemplates.filter(t => t.status === 'APPROVED').length;
    $('btPending').textContent = waTemplates.filter(t => t.status === 'PENDING').length;
    $('btRejected').textContent = waTemplates.filter(t => t.status === 'REJECTED').length;
  }

  function fillLanguages() {
    const select = $('btLanguage');
    const current = select.value;
    const langs = [...new Set(waTemplates.map(t => t.language_code).filter(Boolean))].sort();
    select.innerHTML = '<option value="ALL">All Languages</option>' +
      langs.map(x => `<option value="${esc(x)}">${esc(x)}</option>`).join('');
    if ([...select.options].some(o => o.value === current)) select.value = current;
  }

  function renderWa() {
    const q = $('btSearch').value.trim().toLowerCase();
    const st = $('btStatus').value;
    const lang = $('btLanguage').value;
    const list = waTemplates.filter(t =>
      (!q ||
       String(t.template_name || '').toLowerCase().includes(q) ||
       String(t.provider_template_name || '').toLowerCase().includes(q)) &&
      (st === 'ALL' || t.status === st) &&
      (lang === 'ALL' || t.language_code === lang)
    );

    $('btWaRows').innerHTML = list.length ? list.map(t => `<tr>
      <td><b>${esc(t.template_name)}</b><small class="bt-sub">${esc(t.provider_template_name || '')}</small></td>
      <td>${esc(t.category || '—')}</td>
      <td>${esc(t.language_code || '—')}</td>
      <td>${esc(t.header_type || 'NONE')}</td>
      <td>${badge(t.status)}</td>
      <td>${mappingBadge(t)}</td>
      <td>${esc(dateText(t.last_synced_on))}</td>
      <td><div class="bc-actions">
        <button class="bc-btn" type="button" data-action="preview" data-id="${t.id}">Preview</button>
        <button class="bc-btn" type="button" data-action="mapping" data-id="${t.id}">Variables</button>
        <button class="bc-btn ${t.status === 'APPROVED' && t.mapping_complete ? 'bc-primary' : ''}"
                type="button" data-action="use" data-id="${t.id}"
                ${t.status !== 'APPROVED' || !t.mapping_complete ? 'disabled' : ''}>Use in Broadcast</button>
      </div></td>
    </tr>`).join('') :
      '<tr><td colspan="8" class="bc-empty">No WhatsApp templates match the selected filters.</td></tr>';
  }

  function renderEmail() {
    $('btEmailRows').innerHTML = emailTemplates.length ? emailTemplates.map(t => `<tr>
      <td><b>${esc(t.template_name)}</b></td>
      <td>${esc(t.email_subject || '')}</td>
      <td><span class="bt-badge active-badge"><span></span>${esc(t.status || 'ACTIVE')}</span></td>
      <td>${esc(dateText(t.updated_on))}</td>
      <td><div class="bc-actions">
        <button class="bc-btn" type="button" data-email-action="preview" data-id="${t.id}">Preview</button>
        <button class="bc-btn" type="button" data-email-action="edit" data-id="${t.id}">Edit</button>
        <a class="bc-btn bc-primary" href="${centreUrl}?email_template_id=${t.id}">Use in Broadcast</a>
      </div></td>
    </tr>`).join('') :
      '<tr><td colspan="5" class="bc-empty">No email templates created yet. Click “Create Email Template”.</td></tr>';
  }

  async function loadWhatsApp() {
    const wa = await api('/api/templates?channel=WHATSAPP');
    waTemplates = wa.templates || [];
    updateStats();
    fillLanguages();
    renderWa();
  }

  async function loadEmail() {
    const email = await api('/api/templates?channel=EMAIL');
    emailTemplates = email.templates || [];
    renderEmail();
  }

  async function load() {
    const results = await Promise.allSettled([loadWhatsApp(), loadEmail()]);
    if (results[0].status === 'rejected') {
      $('btWaRows').innerHTML = `<tr><td colspan="8" class="bc-empty">${esc(results[0].reason?.message || 'Unable to load WhatsApp templates.')}</td></tr>`;
    }
    if (results[1].status === 'rejected') {
      $('btEmailRows').innerHTML = `<tr><td colspan="5" class="bc-empty">${esc(results[1].reason?.message || 'Unable to load email templates.')}</td></tr>`;
    }
    if (results.every(r => r.status === 'rejected')) throw results[0].reason;
  }

  function openModal(title, sub, body, footer = '') {
    $('btModalTitle').textContent = title;
    $('btModalSub').textContent = sub || '';
    $('btModalBody').innerHTML = body;
    $('btModalFooter').innerHTML = footer;
    $('btModal').classList.remove('hidden');
  }
  function closeModal() { $('btModal').classList.add('hidden'); }

  function preview(t) {
    const buttons = (t.buttons || []).map(b =>
      `<div class="bt-wa-button">${esc(b.text || b.type || 'Button')}</div>`
    ).join('');
    openModal(
      t.template_name,
      'WhatsApp Template Preview',
      `<div class="bt-preview-grid">
        <div>
          <div class="bt-meta-grid">
            <div><small>Status</small>${badge(t.status)}</div>
            <div><small>Category</small><b>${esc(t.category || '—')}</b></div>
            <div><small>Language</small><b>${esc(t.language_code || '—')}</b></div>
            <div><small>Header</small><b>${esc(t.header_type || 'NONE')}</b></div>
          </div>
          <h4>Variable Mapping</h4>
          <div class="bt-map-list">
            ${t.variable_keys?.length
              ? t.variable_keys.map(k => `<div><code>{{${esc(k)}}}</code><span>${esc(t.variable_mapping?.[k] || 'Not mapped')}</span></div>`).join('')
              : '<p class="bc-help" style="padding:10px">This template has no positional body variables.</p>'}
          </div>
        </div>
        <div class="bt-phone">
          <div class="bt-phone-label">WhatsApp Preview</div>
          <div class="bt-wa-card">
            ${t.header_type === 'IMAGE' ? '<div class="bt-wa-media">IMAGE HEADER</div>' : ''}
            ${t.header_type === 'VIDEO' ? '<div class="bt-wa-media">VIDEO HEADER</div>' : ''}
            ${t.header_type === 'DOCUMENT' ? '<div class="bt-wa-media">DOCUMENT HEADER</div>' : ''}
            ${t.header_text ? `<div class="bt-wa-header">${esc(t.header_text)}</div>` : ''}
            <div class="bt-wa-body">${esc(t.body_text || '')}</div>
            ${t.footer_text ? `<div class="bt-wa-footer">${esc(t.footer_text)}</div>` : ''}
            ${buttons}
          </div>
        </div>
      </div>`,
      t.status === 'APPROVED' && t.mapping_complete
        ? `<a class="bc-btn bc-primary" href="${centreUrl}?template_id=${t.id}">Use in Broadcast</a>`
        : ''
    );
  }

  function mapping(t) {
    const rows = t.variable_keys?.length ? t.variable_keys.map(k => `
      <div class="bt-map-row">
        <code>{{${esc(k)}}}</code>
        <select data-map-key="${esc(k)}">
          <option value="">-- Select TERIQEN field --</option>
          ${mappingSources.map(src => `<option value="${esc(src)}" ${t.variable_mapping?.[k] === src ? 'selected' : ''}>${esc(src)}</option>`).join('')}
        </select>
      </div>`).join('') :
      '<p class="bc-help" style="padding:10px">No variable mapping is required for this template.</p>';

    openModal(
      t.template_name,
      'Variable Mapping',
      `<p class="bc-help">Map each Meta BODY placeholder to a TERIQEN recipient or campaign field. The mapping is reused by Broadcast Centre.</p>
       ${t.unsupported_reasons?.length ? `<div class="bc-note"><b>Not yet supported for sending:</b> ${t.unsupported_reasons.map(esc).join(' ')}</div>` : ''}
       <div class="bt-map-editor">${rows}</div>`,
      t.variable_keys?.length ? '<button class="bc-btn bc-primary" id="btSaveMapping" type="button">Save Mapping</button>' : ''
    );

    $('btSaveMapping')?.addEventListener('click', async () => {
      const map = {};
      document.querySelectorAll('[data-map-key]').forEach(s => {
        if (s.value) map[s.dataset.mapKey] = s.value;
      });
      try {
        await api(`/api/templates/${t.id}/mapping`, {
          method:'POST',
          body:JSON.stringify({mapping:map})
        });
        closeModal();
        await loadWhatsApp();
        toast('Variable mapping saved.');
      } catch (e) { toast(e.message); }
    });
  }

  function whatsappEditor() {
    openModal(
      'Create WhatsApp Template',
      'Submit a new template to Meta for approval',
      `<div class="bt-builder-note">
         TERIQEN will submit this template to your configured Meta WhatsApp Business Account.
         Meta decides whether it is approved. After submission, use <b>Sync from Meta</b> to refresh status.
         This in-app creator currently supports <b>NONE or TEXT headers</b>; IMAGE/VIDEO/DOCUMENT template headers can still be created in Meta and synchronized here.
       </div>
       <div class="bt-builder-grid">
         <label>Template Name *
           <input id="btWaName" maxlength="512" placeholder="e.g. cadupius_health_bn_v1">
           <div class="bt-builder-help">Lowercase letters, numbers and underscores only.</div>
         </label>
         <label>Category *
           <select id="btWaCategory">
             <option value="MARKETING">MARKETING</option>
             <option value="UTILITY">UTILITY</option>
           </select>
         </label>
         <label>Language *
           <select id="btWaLanguage">
             <option value="bn">Bengali (bn)</option>
             <option value="en">English (en)</option>
             <option value="en_US">English US (en_US)</option>
             <option value="hi">Hindi (hi)</option>
           </select>
         </label>
         <label>Header Type
           <select id="btWaHeaderType">
             <option value="NONE">No Header</option>
             <option value="TEXT">Text Header</option>
           </select>
         </label>
         <label class="bt-span-2 hidden" id="btWaHeaderWrap">Header Text
           <input id="btWaHeaderText" maxlength="60" placeholder="Short heading">
           <div class="bt-builder-help">Variables in the text header are intentionally disabled in this release.</div>
         </label>
         <label class="bt-span-2">Body *
           <textarea id="btWaBody" rows="8" maxlength="1024" placeholder="Hello {{1}},&#10;&#10;Your message here..."></textarea>
           <div class="bt-builder-help">Use sequential positional variables such as {{1}}, {{2}}, {{3}}.</div>
         </label>
         <label class="bt-span-2">Footer
           <input id="btWaFooter" maxlength="60" placeholder="Cadupius HealthAssist">
         </label>
         <label>Website Button Text
           <input id="btWaUrlText" maxlength="25" value="Visit Website">
         </label>
         <label>Website URL
           <input id="btWaUrl" type="url" placeholder="https://cadupiushealthassist.com">
         </label>
         <label>Call Button Text
           <input id="btWaPhoneText" maxlength="25" value="Call Us">
         </label>
         <label>Call Number
           <input id="btWaPhone" type="text" placeholder="+91XXXXXXXXXX">
         </label>
       </div>`,
      '<button class="bc-btn" id="btWaCancel" type="button">Cancel</button><button class="bc-btn bc-primary" id="btWaSubmit" type="button">Submit to Meta</button>'
    );

    $('btWaCancel').addEventListener('click', closeModal);
    $('btWaHeaderType').addEventListener('change', () => {
      $('btWaHeaderWrap').classList.toggle('hidden', $('btWaHeaderType').value !== 'TEXT');
    });
    $('btWaSubmit').addEventListener('click', async () => {
      const btn = $('btWaSubmit');
      const payload = {
        template_name:$('btWaName').value.trim(),
        category:$('btWaCategory').value,
        language_code:$('btWaLanguage').value,
        header_type:$('btWaHeaderType').value,
        header_text:$('btWaHeaderText').value.trim(),
        body_text:$('btWaBody').value.trim(),
        footer_text:$('btWaFooter').value.trim(),
        website_button_text:$('btWaUrlText').value.trim(),
        website_url:$('btWaUrl').value.trim(),
        phone_button_text:$('btWaPhoneText').value.trim(),
        phone_number:$('btWaPhone').value.trim()
      };
      btn.disabled = true;
      btn.textContent = 'Submitting...';
      try {
        const result = await api('/api/templates/whatsapp', {
          method:'POST',
          body:JSON.stringify(payload)
        });
        closeModal();
        await loadWhatsApp().catch(() => {});
        const status = result.data?.provider?.status || 'submitted';
        toast(`WhatsApp template ${status}. Meta approval may take time.`);
        $('btSyncNote').classList.remove('hidden');
        $('btSyncNote').textContent = result.data?.sync_warning
          ? `Template submitted to Meta. Sync warning: ${result.data.sync_warning}`
          : 'Template submitted to Meta. Refresh/Sync to check its approval status.';
      } catch (e) {
        toast(e.message);
        btn.disabled = false;
        btn.textContent = 'Submit to Meta';
      }
    });
  }

  function emailEditor(t = null) {
    const existing = t?.body_html || t?.body_text || '';
    openModal(
      t ? 'Edit Email Template' : 'Create Email Template',
      'TERIQEN Email Template',
      `<div class="bt-builder-grid">
         <label>Template Name *
           <input id="btEmailName" maxlength="200" value="${esc(t?.template_name || '')}">
         </label>
         <label>Subject *
           <input id="btEmailSubject" maxlength="300" value="${esc(t?.email_subject || '')}">
         </label>
         <div class="bt-span-2">
           <label>Email Content *</label>
           <div class="bt-editor-shell">
             <div class="bt-editor-toolbar">
               <button type="button" data-email-cmd="bold"><b>B</b></button>
               <button type="button" data-email-cmd="italic"><i>I</i></button>
               <button type="button" data-email-cmd="underline"><u>U</u></button>
               <button type="button" data-email-cmd="insertUnorderedList">• List</button>
               <button type="button" data-email-token="{{recipient_name}}">{{recipient_name}}</button>
               <button type="button" data-email-token="{{reference_no}}">{{reference_no}}</button>
             </div>
             <div id="btEmailBody" class="bt-rich-editor" contenteditable="true">${existing}</div>
           </div>
           <p class="bc-help">HTML/rich content is stored in TERIQEN. Personalisation tokens are resolved when the campaign is sent.</p>
         </div>
       </div>`,
      '<button class="bc-btn" id="btEmailCancel" type="button">Cancel</button><button class="bc-btn bc-primary" id="btSaveEmail" type="button">Save Template</button>'
    );

    $('btEmailCancel').addEventListener('click', closeModal);
    document.querySelectorAll('[data-email-cmd]').forEach(b => b.addEventListener('click', () => {
      document.execCommand(b.dataset.emailCmd, false, null);
      $('btEmailBody').focus();
    }));
    document.querySelectorAll('[data-email-token]').forEach(b => b.addEventListener('click', () => {
      document.execCommand('insertText', false, b.dataset.emailToken);
      $('btEmailBody').focus();
    }));

    $('btSaveEmail').addEventListener('click', async () => {
      const bodyHtml = $('btEmailBody').innerHTML.trim();
      const bodyText = $('btEmailBody').innerText.trim();
      const payload = {
        template_name:$('btEmailName').value.trim(),
        email_subject:$('btEmailSubject').value.trim(),
        body_html:bodyHtml,
        body_text:bodyText
      };
      try {
        const path = t ? `/api/templates/email/${t.id}` : '/api/templates/email';
        await api(path, {method:t ? 'PUT' : 'POST', body:JSON.stringify(payload)});
        closeModal();
        await loadEmail();
        toast('Email template saved.');
      } catch (e) { toast(e.message); }
    });
  }

  $('btWaRows').addEventListener('click', e => {
    const b = e.target.closest('[data-action]');
    if (!b) return;
    const t = waTemplates.find(x => x.id === Number(b.dataset.id));
    if (!t) return;
    if (b.dataset.action === 'preview') preview(t);
    if (b.dataset.action === 'mapping') mapping(t);
    if (b.dataset.action === 'use' && !b.disabled) location.href = centreUrl + '?template_id=' + t.id;
  });

  $('btEmailRows').addEventListener('click', e => {
    const b = e.target.closest('[data-email-action]');
    if (!b) return;
    const t = emailTemplates.find(x => x.id === Number(b.dataset.id));
    if (!t) return;
    if (b.dataset.emailAction === 'preview') {
      openModal(
        t.template_name,
        'Email Preview',
        `<div class="bt-email-preview"><small>SUBJECT</small><h3>${esc(t.email_subject || '')}</h3><div>${t.body_html || esc(t.body_text || '')}</div></div>`
      );
    }
    if (b.dataset.emailAction === 'edit') emailEditor(t);
  });

  ['btSearch','btStatus','btLanguage'].forEach(id =>
    $(id).addEventListener(id === 'btSearch' ? 'input' : 'change', renderWa)
  );

  $('btSync').addEventListener('click', async () => {
    const btn = $('btSync');
    btn.disabled = true;
    $('btSyncNote').classList.remove('hidden');
    $('btSyncNote').textContent = 'Synchronizing templates from Meta...';
    try {
      const j = await api('/api/templates/sync-meta', {method:'POST', body:'{}'});
      $('btSyncNote').textContent = `Sync complete: ${j.data.received} received, ${j.data.added} added, ${j.data.updated} updated.`;
      await loadWhatsApp();
      toast('Meta template synchronization completed.');
    } catch (e) {
      $('btSyncNote').textContent = e.message;
      toast(e.message);
    } finally { btn.disabled = false; }
  });

  $('btRefresh').addEventListener('click', () =>
    loadWhatsApp().then(() => toast('WhatsApp template list refreshed.')).catch(e => toast(e.message))
  );
  $('btCreateWa').addEventListener('click', whatsappEditor);
  $('btCreateEmail').addEventListener('click', () => emailEditor());
  $('btModalClose').addEventListener('click', closeModal);
  $('btModal').addEventListener('click', e => { if (e.target === $('btModal')) closeModal(); });

  function tab(wa) {
    $('btWaTab').classList.toggle('active', wa);
    $('btEmailTab').classList.toggle('active', !wa);
    $('btWaPanel').classList.toggle('hidden', !wa);
    $('btEmailPanel').classList.toggle('hidden', wa);
    $('btSync').classList.toggle('hidden', !wa);
    $('btRefresh').classList.toggle('hidden', !wa);
    $('btCreateWa').classList.toggle('hidden', !wa);
    $('btCreateEmail').classList.toggle('hidden', wa);
    if (!wa) loadEmail().catch(e => toast(e.message));
  }

  $('btWaTab').addEventListener('click', () => tab(true));
  $('btEmailTab').addEventListener('click', () => tab(false));

  load().catch(e => toast(e.message));
})();