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
  let modalBackdropCloseEnabled = true;
  let modalBeforeClose = null;

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
        <button class="bc-btn" type="button" data-action="delete" data-id="${t.id}" style="border-color:#dc2626;color:#b91c1c;">Delete</button>
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
        <button class="bc-btn" type="button" data-email-action="copy" data-id="${t.id}">Copy</button>
        <button class="bc-btn" type="button" data-email-action="delete" data-id="${t.id}" style="border-color:#dc2626;color:#b91c1c;">Delete</button>
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

  function resetModalMode() {
    const modal = $('btModal');
    const box = document.querySelector('#btModal .bt-modal-box');
    modal?.classList.remove('bt-editor-fullscreen');
    box?.classList.remove('bt-pro-modal', 'bt-pro-modal-fullscreen', 'bt-wa-meta-modal');
    document.body.classList.remove('bt-editor-open');
    modalBackdropCloseEnabled = true;
    modalBeforeClose = null;
  }

  function openModal(title, sub, body, footer = '') {
    resetModalMode();
    $('btModalTitle').textContent = title;
    $('btModalSub').textContent = sub || '';
    $('btModalBody').innerHTML = body;
    $('btModalFooter').innerHTML = footer;
    $('btModal').classList.remove('hidden');
  }

  function closeModal(force = false) {
    if (!force && typeof modalBeforeClose === 'function' && modalBeforeClose() === false) return false;
    $('btModal').classList.add('hidden');
    resetModalMode();
    return true;
  }

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
            ${t.header_type === 'IMAGE'
              ? (t.header_preview_url
                  ? `<img src="${esc(t.header_preview_url)}" alt="WhatsApp template header" style="display:block;width:100%;max-height:250px;object-fit:cover;border-radius:8px 8px 0 0;background:#eef2f5;">`
                  : '<div class="bt-wa-media">IMAGE HEADER</div>')
              : ''}
            ${t.header_type === 'VIDEO' ? '<div class="bt-wa-media">VIDEO HEADER</div>' : ''}
            ${t.header_type === 'DOCUMENT' ? '<div class="bt-wa-media">DOCUMENT HEADER</div>' : ''}
            ${t.header_text ? `<div class="bt-wa-header">${esc(t.header_text)}</div>` : ''}
            <div class="bt-wa-body">${esc(t.body_text || '')}</div>
            ${t.footer_text ? `<div class="bt-wa-footer">${esc(t.footer_text)}</div>` : ''}
            ${buttons}
          </div>
        </div>
      </div>`,
      `<div class="bc-actions">
        <button class="bc-btn" id="btDeleteWaTemplate" type="button" style="border-color:#dc2626;color:#b91c1c;">Delete Template</button>
        ${t.status === 'APPROVED' && t.mapping_complete ? `<a class="bc-btn bc-primary" href="${centreUrl}?template_id=${t.id}">Use in Broadcast</a>` : ''}
      </div>`
    );
    $('btDeleteWaTemplate')?.addEventListener('click', () => deleteWhatsAppTemplate(t, true));
  }

  async function deleteWhatsAppTemplate(t, fromModal = false) {
    if (!t) return;
    const warning = `Delete WhatsApp template “${t.template_name}”?\n\nThis will delete this exact template from Meta and remove it from the active TERIQEN Message Templates list. Existing Broadcast History is preserved.`;
    if (!window.confirm(warning)) return;
    try {
      await api(`/api/templates/whatsapp/${t.id}`, {method:'DELETE', body:'{}'});
      if (fromModal) closeModal(true);
      await loadWhatsApp();
      toast('WhatsApp template deleted from Meta and TERIQEN.');
    } catch (e) {
      toast(e.message);
    }
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
    const sourceLabels = {
      recipient_name:'Recipient Name',
      first_name:'First Name',
      reference_no:'Reference Number',
      country_name:'Country',
      language_code:'Language Code',
      recipient_tag:'Recipient Tag',
      campaign_name:'Campaign Name',
      website_url:'Website URL',
      contact_number:'Contact Number'
    };
    const sourceSamples = {
      recipient_name:'Rahim Ahmed',
      first_name:'Rahim',
      reference_no:'HE-20260830-001',
      country_name:'Bangladesh',
      language_code:'bn',
      recipient_tag:'Medical Tourism',
      campaign_name:'Cadupius HealthAssist',
      website_url:'https://cadupiushealthassist.com',
      contact_number:'+919876543210'
    };

    openModal(
      'Create WhatsApp Template',
      'Meta-aligned TERIQEN template builder',
      `<div class="bt-wa-meta-builder">
        <div class="bt-wa-builder-main">
          <div class="bt-builder-note bt-wa-meta-note">
            This builder submits directly to the configured Meta WhatsApp Business Account. Media headers upload a real review sample to Meta before template creation. Meta remains the final approval authority.
          </div>

          <div id="btWaActionStatus" class="bt-wa-action-status info">
            <b>Ready to create template</b>
            <span>Complete the fields below, validate, then submit to Meta.</span>
          </div>

          <section class="bt-wa-section">
            <h4>1. Template setup</h4>
            <div class="bt-wa-form-grid">
              <label class="bt-wa-field"><span>Template Name *</span>
                <input id="btWaName" maxlength="512" placeholder="e.g. cadupius_health_bn_v1" autocomplete="off">
                <div class="bt-builder-help">Automatically kept lowercase. Use letters, numbers and underscores only.</div>
              </label>
              <label class="bt-wa-field"><span>Category *</span>
                <select id="btWaCategory">
                  <option value="MARKETING">MARKETING</option>
                  <option value="UTILITY">UTILITY</option>
                </select>
                <div class="bt-builder-help">Authentication templates use a different Meta structure and are intentionally excluded here.</div>
              </label>
              <label class="bt-wa-field"><span>Language *</span>
                <select id="btWaLanguage">
                  <option value="bn">Bengali (bn)</option>
                  <option value="en">English (en)</option>
                  <option value="en_US">English US (en_US)</option>
                  <option value="en_GB">English UK (en_GB)</option>
                  <option value="hi">Hindi (hi)</option>
                </select>
              </label>
              <label class="bt-wa-field"><span>Placeholder Format</span>
                <input value="Named placeholders — recommended" readonly aria-readonly="true">
                <div class="bt-builder-help">Use descriptive placeholders such as <code>{{recipient_name}}</code>. Numbered placeholders are not available for new TERIQEN templates.</div>
              </label>
              <label class="bt-wa-field"><span>Header Type</span>
                <select id="btWaHeaderType">
                  <option value="NONE">No Header</option>
                  <option value="TEXT">Text</option>
                  <option value="IMAGE">Image / Photo</option>
                  <option value="VIDEO">Video (MP4)</option>
                  <option value="GIF">GIF (auto-convert to MP4)</option>
                  <option value="DOCUMENT">Document (PDF)</option>
                </select>
              </label>
            </div>
          </section>

          <section class="bt-wa-section" id="btWaHeaderSection">
            <h4>2. Header / Media</h4>
            <label class="bt-wa-field hidden" id="btWaHeaderWrap"><span>Header Text</span>
              <input id="btWaHeaderText" maxlength="60" placeholder="Short heading">
              <div class="bt-builder-help">Maximum 60 characters. Header variables stay disabled because the current TERIQEN sender maps BODY variables only.</div>
            </label>
            <div class="hidden" id="btWaMediaWrap">
              <label class="bt-wa-field"><span>Sample Media *</span>
                <input id="btWaMedia" type="file">
              </label>
              <div class="bt-wa-media-guidance" id="btWaMediaGuidance"></div>
              <div class="bt-wa-media-file hidden" id="btWaMediaFile"></div>
            </div>
            <div class="bc-help" id="btWaNoHeaderNote">No header selected.</div>
          </section>

          <section class="bt-wa-section">
            <h4>3. Body</h4>
            <label class="bt-wa-field bt-wa-body-field"><span>Message Body *</span>
              <textarea id="btWaBody" rows="9" maxlength="1024" placeholder="Hello {{recipient_name}},&#10;&#10;Your message here..."></textarea>
            </label>
            <div class="bt-wa-placeholder-tools">
              <label class="bt-wa-field bt-wa-placeholder-select"><span>Placeholder / TERIQEN Field</span>
                <select id="btWaPlaceholderSource">
                  ${mappingSources.map(src => `<option value="${esc(src)}">${esc(sourceLabels[src] || src)}</option>`).join('')}
                </select>
              </label>
              <button class="bc-btn" id="btWaAddVariable" type="button">+ Insert Placeholder</button>
              <span class="bt-char-count" id="btWaBodyCount">0 / 1024</span>
            </div>
            <div class="bt-builder-help" id="btWaVariableHint">Named placeholders are easier to understand and maintain, for example <code>{{recipient_name}}</code>.</div>
            <div id="btWaExamples" class="bt-wa-examples"></div>
          </section>

          <section class="bt-wa-section">
            <h4>4. Footer</h4>
            <label class="bt-wa-field"><span>Footer</span>
              <input id="btWaFooter" maxlength="60" placeholder="Cadupius HealthAssist">
            </label>
            <div class="bt-char-count" id="btWaFooterCount">0 / 60</div>
          </section>

          <section class="bt-wa-section">
            <h4>5. Call-to-action buttons</h4>
            <div class="bt-wa-form-grid">
              <label class="bt-wa-field"><span>Website Button Text</span>
                <input id="btWaUrlText" maxlength="25" value="Visit Website">
              </label>
              <label class="bt-wa-field"><span>Website URL</span>
                <input id="btWaUrl" type="url" placeholder="https://cadupiushealthassist.com">
              </label>
              <label class="bt-wa-field"><span>Call Button Text</span>
                <input id="btWaPhoneText" maxlength="25" value="Call Us">
              </label>
              <label class="bt-wa-field"><span>Call Number</span>
                <input id="btWaPhone" type="text" placeholder="+919876543210">
              </label>
            </div>
          </section>

          <section class="bt-wa-section">
            <h4>6. Pre-submission validation</h4>
            <div id="btWaValidation" class="bt-wa-validation">Complete the template to run validation.</div>
          </section>
        </div>

        <aside class="bt-wa-preview-pane">
          <div class="bt-wa-preview-title"><b>WhatsApp Preview</b><span>Live</span></div>
          <div class="bt-wa-preview-phone">
            <div class="bt-wa-preview-top"><div class="bt-wa-avatar">C</div><div><b>Cadupius HealthAssist</b><small>business account</small></div></div>
            <div class="bt-wa-preview-chat">
              <div class="bt-wa-preview-bubble">
                <div id="btWaPreviewMedia" class="bt-wa-preview-media hidden"></div>
                <div id="btWaPreviewHeader" class="bt-wa-preview-header hidden"></div>
                <div id="btWaPreviewBody" class="bt-wa-preview-body">Your message preview will appear here.</div>
                <div id="btWaPreviewFooter" class="bt-wa-preview-footer hidden"></div>
                <div class="bt-wa-preview-time">10:32</div>
                <div id="btWaPreviewButtons"></div>
              </div>
            </div>
          </div>
          <div class="bt-builder-help" style="margin-top:8px">Final rendering, categorisation and approval are controlled by Meta.</div>
        </aside>
      </div>`,
      '<button class="bc-btn" id="btWaCancel" type="button">Cancel</button><button class="bc-btn" id="btWaValidate" type="button">Validate</button><button class="bc-btn bc-primary" id="btWaSubmit" type="button">Submit to Meta</button>'
    );

    const modal = $('btModal');
    const modalBox = document.querySelector('#btModal .bt-modal-box');
    modal?.classList.add('bt-editor-fullscreen');
    modalBox?.classList.add('bt-pro-modal-fullscreen', 'bt-wa-meta-modal');
    document.body.classList.add('bt-editor-open');
    modalBackdropCloseEnabled = false;

    const exampleValues = new Map();
    const variableMapping = new Map();
    let objectUrl = '';

    const mediaConfig = {
      IMAGE:{accept:'image/jpeg,image/png', help:'Upload the actual JPEG or PNG sample that Meta should review with this template.'},
      VIDEO:{accept:'video/mp4', help:'Upload an MP4 sample. TERIQEN validates the file before Meta submission.'},
      GIF:{accept:'image/gif', help:'Upload an animated GIF. TERIQEN converts it to MP4 before Meta submission; ffmpeg must be installed on the server.'},
      DOCUMENT:{accept:'application/pdf,.pdf', help:'Upload the actual PDF sample Meta should review with this template.'}
    };

    const placeholderMatches = () => [...String($('btWaBody').value || '').matchAll(/\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g)].map(m => m[1]);
    const vars = () => {
      const raw = placeholderMatches();
      const seen = new Set();
      return raw.filter(x => !/^\d+$/.test(x) && !seen.has(x) && seen.add(x));
    };

    const sourceOptions = selected => '<option value="">-- Select TERIQEN field --</option>' + mappingSources.map(src =>
      `<option value="${esc(src)}" ${selected === src ? 'selected' : ''}>${esc(sourceLabels[src] || src)}</option>`
    ).join('');

    const setActionStatus = (kind, title, detail='') => {
      const box = $('btWaActionStatus');
      if (!box) return;
      box.className = `bt-wa-action-status ${kind}`;
      box.innerHTML = `<b>${esc(title)}</b>${detail ? `<span>${esc(detail)}</span>` : ''}`;
    };

    const rebuildExamples = () => {
      document.querySelectorAll('[data-wa-example]').forEach(el => exampleValues.set(String(el.dataset.waExample), el.value));
      document.querySelectorAll('[data-wa-source]').forEach(el => variableMapping.set(String(el.dataset.waSource), el.value));
      const keys = vars();
      const box = $('btWaExamples');
      if (!keys.length) {
        box.innerHTML = '<div class="bt-builder-help">No BODY placeholders detected.</div>';
        return;
      }
      box.innerHTML = `<div class="bt-wa-example-title">Placeholder mapping and Meta review examples</div>
        <div class="bt-wa-variable-head"><span>Placeholder</span><span>TERIQEN Field</span><span>Meta Review Sample</span></div>` + keys.map(k => {
          const defaultSource = variableMapping.get(k) || (mappingSources.includes(k) ? k : '');
          if (defaultSource) variableMapping.set(k, defaultSource);
          const sample = exampleValues.get(k) || sourceSamples[defaultSource] || (k === '1' ? 'Rahim Ahmed' : 'Sample value');
          return `<div class="bt-wa-variable-row">
            <code>{{${esc(k)}}}</code>
            <select data-wa-source="${esc(k)}">${sourceOptions(defaultSource)}</select>
            <input data-wa-example="${esc(k)}" maxlength="200" value="${esc(sample)}" placeholder="Example shown to Meta reviewer">
          </div>`;
        }).join('');
      document.querySelectorAll('[data-wa-example]').forEach(el => el.addEventListener('input', () => {
        exampleValues.set(String(el.dataset.waExample), el.value); renderPreview();
      }));
      document.querySelectorAll('[data-wa-source]').forEach(el => el.addEventListener('change', () => {
        const key = String(el.dataset.waSource);
        variableMapping.set(key, el.value);
        const sample = document.querySelector(`[data-wa-example="${CSS.escape(key)}"]`);
        if (sample && (!sample.value.trim() || sample.value === 'Sample value')) sample.value = sourceSamples[el.value] || 'Sample value';
        renderPreview();
      }));
    };

    const sampleBody = text => String(text || '').replace(/\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g, (_, key) => {
      const val = document.querySelector(`[data-wa-example="${CSS.escape(String(key))}"]`)?.value || exampleValues.get(String(key));
      return val || `{{${key}}}`;
    });

    const renderPreview = () => {
      $('btWaBodyCount').textContent = `${$('btWaBody').value.length} / 1024`;
      $('btWaFooterCount').textContent = `${$('btWaFooter').value.length} / 60`;
      const ht = $('btWaHeaderType').value;
      const head = $('btWaHeaderText').value.trim();
      $('btWaPreviewHeader').classList.toggle('hidden', !(ht === 'TEXT' && head));
      $('btWaPreviewHeader').textContent = head;
      $('btWaPreviewBody').textContent = sampleBody($('btWaBody').value) || 'Your message preview will appear here.';
      const foot = $('btWaFooter').value.trim();
      $('btWaPreviewFooter').classList.toggle('hidden', !foot);
      $('btWaPreviewFooter').textContent = foot;
      const buttons = [];
      if ($('btWaUrl').value.trim()) buttons.push('↗ ' + ($('btWaUrlText').value.trim() || 'Visit Website'));
      if ($('btWaPhone').value.trim()) buttons.push('☎ ' + ($('btWaPhoneText').value.trim() || 'Call Us'));
      $('btWaPreviewButtons').innerHTML = buttons.map(x => `<div class="bt-wa-preview-button">${esc(x)}</div>`).join('');
    };

    const updateHeader = () => {
      const type = $('btWaHeaderType').value;
      const isText = type === 'TEXT';
      const isMedia = !!mediaConfig[type];
      $('btWaHeaderWrap').classList.toggle('hidden', !isText);
      $('btWaMediaWrap').classList.toggle('hidden', !isMedia);
      $('btWaNoHeaderNote').classList.toggle('hidden', type !== 'NONE');
      $('btWaPreviewMedia').classList.toggle('hidden', !isMedia);
      if (isMedia) {
        $('btWaMedia').accept = mediaConfig[type].accept;
        $('btWaMediaGuidance').textContent = mediaConfig[type].help;
      } else {
        $('btWaMedia').value = '';
        $('btWaMediaFile').classList.add('hidden');
        $('btWaPreviewMedia').innerHTML = '';
        if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = ''; }
      }
      renderPreview();
    };

    const validate = (showStatus = false) => {
      const issues = [];
      const name = $('btWaName').value.trim();
      if (!/^[a-z0-9_]{1,512}$/.test(name)) issues.push('Template name must use lowercase letters, numbers and underscores only.');
      if (!$('btWaBody').value.trim()) issues.push('Body is required.');
      const all = placeholderMatches();
      if (all.some(x => /^\d+$/.test(x))) issues.push('Numbered placeholders are not supported for new templates. Use descriptive placeholders such as {{recipient_name}}.');
      if (all.some(x => !/^\d+$/.test(x) && !/^[a-z][a-z0-9_]{0,59}$/.test(x))) issues.push('Named placeholders must start with a lowercase letter and use lowercase letters, numbers or underscores only.');
      const keys = vars();
      keys.forEach(k => {
        if (!(document.querySelector(`[data-wa-example="${CSS.escape(k)}"]`)?.value || '').trim()) issues.push(`Sample value for {{${k}}} is required.`);
        if (!(document.querySelector(`[data-wa-source="${CSS.escape(k)}"]`)?.value || '').trim()) issues.push(`Select a TERIQEN field for {{${k}}}.`);
      });
      const ht = $('btWaHeaderType').value;
      if (ht === 'TEXT' && !$('btWaHeaderText').value.trim()) issues.push('Header text is required.');
      if (ht === 'TEXT' && /\{\{/.test($('btWaHeaderText').value)) issues.push('Header variables are disabled in this TERIQEN release.');
      if (mediaConfig[ht] && !$('btWaMedia').files?.[0]) issues.push('Choose sample media for the selected header type.');
      const url = $('btWaUrl').value.trim();
      if (url && !/^https:\/\//i.test(url)) issues.push('Website URL must start with https://');
      const phone = $('btWaPhone').value.replace(/[\s()-]/g, '');
      if (phone && !/^\+\d{7,15}$/.test(phone)) issues.push('Call number must use international format such as +919876543210.');
      $('btWaValidation').className = 'bt-wa-validation ' + (issues.length ? 'has-errors' : 'is-ready');
      $('btWaValidation').innerHTML = issues.length
        ? `<b>Fix ${issues.length} item(s) before submission:</b><br>${issues.map(x => '• ' + esc(x)).join('<br>')}`
        : '<b>Structure validation passed.</b> TERIQEN will upload any media sample to Meta and then submit the template.';
      if (showStatus) {
        if (issues.length) setActionStatus('error', 'Validation failed', `${issues.length} item(s) need attention. See section 6 for details.`);
        else setActionStatus('success', 'Validation successful', 'The template structure is ready to submit to Meta for review.');
        $('btWaValidation').scrollIntoView({behavior:'smooth', block:'center'});
      }
      return issues;
    };

    $('btWaCancel').addEventListener('click', () => closeModal(true));
    $('btWaValidate').addEventListener('click', () => validate(true));
    $('btWaHeaderType').addEventListener('change', updateHeader);
    $('btWaName').addEventListener('input', e => {
      const clean = e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/_+/g, '_');
      if (e.target.value !== clean) e.target.value = clean;
    });
    $('btWaAddVariable').addEventListener('click', () => {
      const ta = $('btWaBody');
      const source = $('btWaPlaceholderSource').value;
      const key = source;
      const token = `{{${key}}}`;
      const a = ta.selectionStart ?? ta.value.length;
      const b = ta.selectionEnd ?? ta.value.length;
      ta.value = ta.value.slice(0,a) + token + ta.value.slice(b);
      variableMapping.set(key, source);
      if (!exampleValues.get(key)) exampleValues.set(key, sourceSamples[source] || 'Sample value');
      ta.focus(); ta.selectionStart = ta.selectionEnd = a + token.length;
      rebuildExamples(); renderPreview();
    });
    $('btWaBody').addEventListener('input', () => { rebuildExamples(); renderPreview(); });
    ['btWaHeaderText','btWaFooter','btWaUrlText','btWaUrl','btWaPhoneText','btWaPhone'].forEach(id => $(id).addEventListener('input', renderPreview));
    $('btWaMedia').addEventListener('change', () => {
      const file = $('btWaMedia').files?.[0];
      if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = ''; }
      if (!file) { $('btWaMediaFile').classList.add('hidden'); $('btWaPreviewMedia').innerHTML=''; return; }
      $('btWaMediaFile').classList.remove('hidden');
      $('btWaMediaFile').textContent = `${file.name} • ${Math.max(1, Math.round(file.size/1024))} KB`;
      objectUrl = URL.createObjectURL(file);
      const type = $('btWaHeaderType').value;
      if (type === 'IMAGE' || type === 'GIF') $('btWaPreviewMedia').innerHTML = `<img src="${objectUrl}" alt="Header sample">`;
      else if (type === 'VIDEO') $('btWaPreviewMedia').innerHTML = `<video src="${objectUrl}" controls muted playsinline></video>`;
      else $('btWaPreviewMedia').innerHTML = `<div class="bt-wa-document-preview">📄 ${esc(file.name)}</div>`;
      renderPreview();
    });

    $('btWaSubmit').addEventListener('click', async () => {
      const issues = validate(false);
      if (issues.length) {
        setActionStatus('error', 'Submission blocked', `${issues.length} validation item(s) must be fixed before Meta submission.`);
        $('btWaValidation').scrollIntoView({behavior:'smooth', block:'center'});
        return;
      }
      const btn = $('btWaSubmit');
      const keys = vars();
      const mapping = {};
      keys.forEach(k => { mapping[k] = document.querySelector(`[data-wa-source="${CSS.escape(k)}"]`)?.value || ''; });
      const form = new FormData();
      form.append('template_name', $('btWaName').value.trim());
      form.append('category', $('btWaCategory').value);
      form.append('language_code', $('btWaLanguage').value);
      form.append('parameter_format', 'NAMED');
      form.append('header_type', $('btWaHeaderType').value);
      form.append('header_text', $('btWaHeaderText').value.trim());
      form.append('body_text', $('btWaBody').value.trim());
      form.append('body_examples', JSON.stringify(keys.map(k => (document.querySelector(`[data-wa-example="${CSS.escape(k)}"]`)?.value || '').trim())));
      form.append('body_mapping', JSON.stringify(mapping));
      form.append('footer_text', $('btWaFooter').value.trim());
      form.append('website_button_text', $('btWaUrlText').value.trim());
      form.append('website_url', $('btWaUrl').value.trim());
      form.append('phone_button_text', $('btWaPhoneText').value.trim());
      form.append('phone_number', $('btWaPhone').value.trim());
      const media = $('btWaMedia').files?.[0];
      if (media) form.append('header_media', media, media.name);

      btn.disabled = true;
      btn.textContent = media ? 'Uploading & Submitting...' : 'Submitting...';
      setActionStatus('working', media ? 'Uploading media and submitting to Meta…' : 'Submitting template to Meta…', 'Please wait for the Meta API response.');
      try {
        const result = await api('/api/templates/whatsapp', {method:'POST', body:form});
        await loadWhatsApp().catch(() => {});
        const provider = result.data?.provider || {};
        const status = String(provider.status || 'PENDING').toUpperCase();
        const metaId = String(provider.id || 'Not returned');
        const converted = result.data?.requested_header_type === 'GIF' && result.data?.header_type === 'VIDEO';
        setActionStatus('success', 'Submitted to Meta successfully', `Status: ${status} • Meta Template ID: ${metaId}${converted ? ' • GIF submitted as VIDEO' : ''}`);
        $('btWaValidation').className = 'bt-wa-validation is-ready';
        $('btWaValidation').innerHTML = `<b>Submission successful.</b><br>Template: ${esc(result.data?.template_name || $('btWaName').value)}<br>Meta status: ${esc(status)}<br>Meta Template ID: ${esc(metaId)}${result.data?.sync_warning ? `<br>Sync warning: ${esc(result.data.sync_warning)}` : '<br>Use Sync from Meta to refresh the approval status.'}`;
        btn.textContent = 'Submitted';
        toast(`WhatsApp template submitted: ${status}.`);
        if ($('btSyncNote')) {
          $('btSyncNote').classList.remove('hidden');
          $('btSyncNote').textContent = result.data?.sync_warning
            ? `Template submitted to Meta. Sync warning: ${result.data.sync_warning}`
            : 'Template submitted to Meta. Refresh/Sync to check its approval status.';
        }
      } catch (e) {
        const message = e?.message || 'Unknown Meta submission error.';
        setActionStatus('error', 'Submission failed', message);
        $('btWaValidation').className = 'bt-wa-validation has-errors';
        $('btWaValidation').innerHTML = `<b>Meta submission failed.</b><br>${esc(message)}`;
        btn.disabled = false;
        btn.textContent = 'Submit to Meta';
        toast('Meta submission failed. See the error shown in the builder.');
      }
    });

    rebuildExamples();
    updateHeader();
    renderPreview();
  }

  function emailEditor(t = null) {
    const existing = t?.body_html || esc(t?.body_text || '').replace(/\r?\n/g, '<br>');
    const modalBox = document.querySelector('#btModal .bt-modal-box');
    let editorDirty = false;
    const uid = () => 'b' + String(Date.now()) + String(Math.floor(Math.random() * 100000));
    const CURRENT_BUILDER_VERSION = 2;
    const MAX_BLOCKS = 40;
    const MAX_FEATURES = 8;
    const MAX_OFFERS = 6;
    const MAX_PROMOS = 4;
    const allowedTypes = new Set([
      'header','hero','image','richtext','featuregrid','offergrid','promo','cta','social','spacer','divider','footer',
      // Legacy block types are accepted only so existing V1.2 templates can be upgraded safely.
      'cards2','cards3','services'
    ]);

    const encodeConfig = value => {
      const bytes = new TextEncoder().encode(JSON.stringify(value));
      let binary = '';
      bytes.forEach(x => { binary += String.fromCharCode(x); });
      return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
    };
    const decodeConfig = value => {
      let raw = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
      while (raw.length % 4) raw += '=';
      const binary = atob(raw);
      const bytes = Uint8Array.from(binary, ch => ch.charCodeAt(0));
      return JSON.parse(new TextDecoder().decode(bytes));
    };
    const cleanHex = (value, fallback = '#ffffff') => /^#[0-9a-f]{6}$/i.test(String(value || '')) ? String(value) : fallback;
    const cleanText = (value, max = 2000) => String(value ?? '').slice(0, max);
    const clone = value => JSON.parse(JSON.stringify(value));

    const migrateLegacyBlock = raw => {
      const b = raw && typeof raw === 'object' ? clone(raw) : null;
      if (!b || !allowedTypes.has(b.type)) return null;
      b.id = cleanText(b.id || uid(), 80);
      if (b.type === 'cards2' || b.type === 'cards3') {
        const legacyCards = Array.isArray(b.cards) ? b.cards : [];
        return {
          id:b.id,
          type:'featuregrid',
          title:cleanText(b.title || 'Featured Highlights', 300),
          subtitle:'',
          bg:cleanHex(b.bg, '#ffffff'),
          columns:b.type === 'cards2' ? 2 : 3,
          mobile_columns:1,
          items:legacyCards.slice(0, MAX_FEATURES).map(c => ({
            icon:cleanText(c?.icon || '✓', 30),
            title:cleanText(c?.title || '', 300),
            text:cleanText(c?.text || '', 1200),
            url:'',
            bg:cleanHex(c?.bg, '#ffffff'),
            border:cleanHex(c?.border, '#dce5ea'),
            icon_style:c?.icon_style || {},
            title_style:c?.title_style || {},
            text_style:c?.text_style || {}
          })),
          title_style:b.title_style || {}
        };
      }
      if (b.type === 'services') {
        const items = String(b.items || '').split(/\r?\n/).map(x => x.trim()).filter(Boolean).slice(0, MAX_FEATURES);
        return {
          id:b.id,
          type:'featuregrid',
          title:cleanText(b.title || 'Our Services', 300),
          subtitle:'',
          bg:cleanHex(b.bg, '#EEF5F8'),
          columns:Math.min(4, Math.max(2, items.length || 4)),
          mobile_columns:2,
          items:items.map(x => {
            const firstSpace = x.indexOf(' ');
            return {
              icon:firstSpace > 0 ? cleanText(x.slice(0, firstSpace), 30) : '✓',
              title:firstSpace > 0 ? cleanText(x.slice(firstSpace + 1), 300) : cleanText(x, 300),
              text:'', url:'', bg:'#ffffff', border:'#dce5ea'
            };
          }),
          title_style:b.title_style || {}
        };
      }
      return b;
    };

    const readBuilderConfig = html => {
      const m = String(html || '').match(/<!--HE-BUILDER:([A-Za-z0-9_-]+)-->/);
      if (!m) return null;
      try {
        const parsed = decodeConfig(m[1]);
        if (!parsed || ![1,2].includes(Number(parsed.version)) || !Array.isArray(parsed.blocks)) return null;
        const blocks = parsed.blocks.slice(0, MAX_BLOCKS).map(migrateLegacyBlock).filter(Boolean);
        if (!blocks.length) return null;
        return {
          blocks,
          background:cleanHex(parsed.background, '#EEF2F5')
        };
      } catch (_) { return null; }
    };

    const safeUrl = value => {
      const url = String(value || '').trim();
      if (!url) return '';
      if (/^https:\/\/[^\s<>"']+$/i.test(url)) return url;
      if (/^mailto:[^\s@]+@[^\s@]+\.[^\s@]+$/i.test(url)) return url;
      if (/^tel:\+?[0-9() .-]{5,30}$/i.test(url)) return url;
      if (/^#[A-Za-z0-9_-]{1,80}$/.test(url)) return url;
      if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(url)) return 'mailto:' + url;
      if (/^\+?[0-9() .-]{5,30}$/.test(url)) return 'tel:' + url.replace(/[^0-9+]/g, '');
      if (/^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:\/[^\s]*)?$/.test(url)) return 'https://' + url;
      return '';
    };
    const safeImageUrl = (value, forPreview = false) => {
      const url = String(value || '').trim();
      if (/^https:\/\/[^\s<>"']+$/i.test(url)) return url;
      const match = url.match(/^local:\/\/broadcast_media\/([A-Za-z0-9._-]+)$/i);
      if (!match) return '';
      return forPreview ? `${root}/api/media/${encodeURIComponent(match[1])}` : url;
    };
    const safeRichHtml = value => {
      const tpl = document.createElement('template');
      tpl.innerHTML = String(value || '').slice(0, 50000);
      const allowed = new Set(['P','DIV','BR','SPAN','STRONG','B','EM','I','U','S','STRIKE','UL','OL','LI','H1','H2','H3','H4','BLOCKQUOTE','A','FONT','HR']);
      [...tpl.content.querySelectorAll('*')].forEach(node => {
        if (!allowed.has(node.tagName)) {
          node.replaceWith(...node.childNodes);
          return;
        }
        [...node.attributes].forEach(attr => {
          const n = attr.name.toLowerCase();
          const v = attr.value || '';
          if (n.startsWith('on') || ['srcdoc','id','class'].includes(n)) node.removeAttribute(attr.name);
          else if (n === 'href') {
            const safe = safeUrl(v);
            if (safe) node.setAttribute('href', safe); else node.removeAttribute('href');
          } else if (n === 'style' && /(url\s*\(|expression|javascript:|data:|@import|behavior:)/i.test(v)) node.removeAttribute('style');
          else if (!['href','title','target','rel','style','align','color','face','size'].includes(n)) node.removeAttribute(attr.name);
        });
      });
      return tpl.innerHTML;
    };
    const sampleTokens = html => String(html || '')
      .replaceAll('{{recipient_name}}', 'Rahman Ahmed')
      .replaceAll('{{first_name}}', 'Rahman')
      .replaceAll('{{country}}', 'Bangladesh')
      .replaceAll('{{reference_no}}', 'HE-20260826-001')
      .replaceAll('{{website_url}}', 'https://cadupiushealthassist.com')
      .replaceAll('{{contact_number}}', '+91 98765 43210');

    const emojiChoices = ['😊','👍','❤️','🙏','✨','🌐','📞','💬','🏥','👨‍⚕️','✈️','✅'];
    const emojiLibrary = [
      '😀','😃','😄','😁','😊','🙂','😉','😍','🥰','🤩','😎','🤗','🤝','🙏','👏','👍','👎','👌','✌️','🤞',
      '❤️','🩷','🧡','💛','💚','💙','💜','🤍','💯','✅','☑️','⭐','🌟','✨','🎉','🎊','🎯','🏆','💡','📌',
      '📞','☎️','📱','💬','📧','✉️','🌐','🔗','📍','🗓️','⏰','🚨','ℹ️','❗','❓','➡️','⬅️','⬆️','⬇️','🔔',
      '🏥','👨‍⚕️','👩‍⚕️','🩺','💊','💉','🩹','❤️‍🩹','🧬','🧪','🧠','🫀','🦷','👁️','🦴','🧘','🚑','♿','🛏️','📋',
      '✈️','🛫','🛬','🧳','🛂','🏨','🚕','🚗','🚌','🗺️','🏠','🏢','💳','💰','🎁','🛡️','🔒','📄','📝','📊'
    ];
    const fontChoices = ['Arial','Calibri','Verdana','Georgia','Times New Roman','Tahoma','Trebuchet MS'];
    const alignChoices = ['left','center','right','justify'];
    const styleDefaults = (overrides = {}) => ({font:'Arial',size:14,color:'#1f2937',bold:false,italic:false,underline:false,strike:false,align:'left',...overrides});
    const safeFont = value => fontChoices.includes(String(value || '')) ? String(value) : 'Arial';
    const safeAlign = value => alignChoices.includes(String(value || '')) ? String(value) : 'left';
    const safeSize = value => Math.max(8, Math.min(36, Number(value) || 14));
    const styleValue = (target, key, defaults = {}) => styleDefaults({...defaults,...(target?.[key] || {})});
    const textCss = (target, key, defaults = {}) => {
      const st = styleValue(target,key,defaults);
      const decorations = [st.underline ? 'underline' : '', st.strike ? 'line-through' : ''].filter(Boolean).join(' ') || 'none';
      return `font-family:${safeFont(st.font)},sans-serif;font-size:${safeSize(st.size)}px;font-weight:${st.bold?'bold':'normal'};font-style:${st.italic?'italic':'normal'};text-decoration:${decorations};text-align:${safeAlign(st.align)};color:${cleanHex(st.color, defaults.color || '#1f2937')}`;
    };
    const emojiRow = (key, itemIndex = null) => `<div class="bt-pro-emoji-row" aria-label="Emoji shortcuts">${emojiChoices.map(x => `<button type="button" data-pro-emoji="${esc(x)}" data-emoji-key="${esc(key)}" ${itemIndex===null?'':`data-item-index="${itemIndex}"`} title="Insert ${esc(x)}">${esc(x)}</button>`).join('')}<details class="bt-pro-emoji-more"><summary>More 😊</summary><div class="bt-pro-emoji-grid">${emojiLibrary.map(x => `<button type="button" data-pro-emoji="${esc(x)}" data-emoji-key="${esc(key)}" ${itemIndex===null?'':`data-item-index="${itemIndex}"`} title="Insert ${esc(x)}">${esc(x)}</button>`).join('')}</div></details></div>`;
    const subjectEmojiRow = () => `<div class="bt-pro-subject-emojis">${emojiChoices.map(x=>`<button type="button" data-subject-emoji="${esc(x)}" title="Insert ${esc(x)}">${esc(x)}</button>`).join('')}<details class="bt-pro-emoji-more"><summary>More 😊</summary><div class="bt-pro-emoji-grid">${emojiLibrary.map(x=>`<button type="button" data-subject-emoji="${esc(x)}" title="Insert ${esc(x)}">${esc(x)}</button>`).join('')}</div></details></div>`;
    const richEmojiRow = () => `<div class="bt-pro-emoji-row">${emojiChoices.map(x=>`<button type="button" data-pro-token=" ${esc(x)} ">${esc(x)}</button>`).join('')}<details class="bt-pro-emoji-more"><summary>More 😊</summary><div class="bt-pro-emoji-grid">${emojiLibrary.map(x=>`<button type="button" data-pro-token=" ${esc(x)} ">${esc(x)}</button>`).join('')}</div></details></div>`;
    const styleToolbar = (styleKey, current, defaults = {}, itemIndex = null) => {
      const st = styleDefaults({...defaults,...(current || {})});
      return `<div class="bt-pro-text-style" aria-label="Text formatting"><span class="bt-pro-style-label">Format</span><select data-style-key="${esc(styleKey)}" data-style-prop="font" ${itemIndex===null?'':`data-item-index="${itemIndex}"`}>${fontChoices.map(x=>`<option value="${esc(x)}" ${st.font===x?'selected':''}>${esc(x)}</option>`).join('')}</select><select data-style-key="${esc(styleKey)}" data-style-prop="size" ${itemIndex===null?'':`data-item-index="${itemIndex}"`}>${[8,9,10,11,12,13,14,16,18,20,22,24,28,32,36].map(x=>`<option value="${x}" ${Number(st.size)===x?'selected':''}>${x}px</option>`).join('')}</select><input type="color" value="${cleanHex(st.color,defaults.color||'#1f2937')}" data-style-key="${esc(styleKey)}" data-style-prop="color" ${itemIndex===null?'':`data-item-index="${itemIndex}"`} title="Text colour"><button type="button" class="${st.bold?'active':''}" data-style-toggle="bold" data-style-key="${esc(styleKey)}" ${itemIndex===null?'':`data-item-index="${itemIndex}"`} title="Bold"><b>B</b></button><button type="button" class="${st.italic?'active':''}" data-style-toggle="italic" data-style-key="${esc(styleKey)}" ${itemIndex===null?'':`data-item-index="${itemIndex}"`} title="Italic"><i>I</i></button><button type="button" class="${st.underline?'active':''}" data-style-toggle="underline" data-style-key="${esc(styleKey)}" ${itemIndex===null?'':`data-item-index="${itemIndex}"`} title="Underline"><u>U</u></button><button type="button" class="${st.strike?'active':''}" data-style-toggle="strike" data-style-key="${esc(styleKey)}" ${itemIndex===null?'':`data-item-index="${itemIndex}"`} title="Strikethrough"><s>S</s></button><select data-style-key="${esc(styleKey)}" data-style-prop="align" ${itemIndex===null?'':`data-item-index="${itemIndex}"`} title="Alignment">${alignChoices.map(x=>`<option value="${x}" ${st.align===x?'selected':''}>${x[0].toUpperCase()+x.slice(1)}</option>`).join('')}</select></div>`;
    };

    const defaultBlocks = () => [
      {id:uid(),type:'header',brand:'CADUPIUS HEALTH ASSIST',tagline:'Medical Treatment Coordination in India',logo_url:'',bg:'#ffffff',color:'#0B6E99'},
      {id:uid(),type:'hero',eyebrow:'INTERNATIONAL PATIENT ASSISTANCE',heading:'Your Treatment Journey, Coordinated with Care',text:'Coordinate with suitable hospitals and specialists in India with end-to-end assistance for your treatment journey.',button_text:'EXPLORE SUPPORT',button_url:'https://cadupiushealthassist.com',button_bg:'#ffffff',bg:'#0B6E99',color:'#ffffff'},
      {id:uid(),type:'richtext',html:'<p>Dear <strong>{{recipient_name}}</strong>,</p><p>Looking for quality medical treatment in India?</p><p><strong>Cadupius Health Assist</strong> helps international patients coordinate with suitable hospitals and specialists for their treatment requirements.</p>',bg:'#ffffff',padding:28},
      {id:uid(),type:'featuregrid',title:'Cadupius HealthAssist',subtitle:'Supporting Your Treatment Journey in India',bg:'#EEF5F8',columns:5,mobile_columns:2,items:[
        {icon:'🏥',title:'HOSPITALS',text:'Coordination',url:'',bg:'#ffffff',border:'#e0e8ed'},
        {icon:'👨‍⚕️',title:'SPECIALISTS',text:'Appointments',url:'',bg:'#ffffff',border:'#e0e8ed'},
        {icon:'📋',title:'TREATMENT',text:'Planning',url:'',bg:'#ffffff',border:'#e0e8ed'},
        {icon:'🛂',title:'MEDICAL VISA',text:'Assistance',url:'',bg:'#ffffff',border:'#e0e8ed'},
        {icon:'✈️',title:'TRAVEL',text:'Support',url:'',bg:'#ffffff',border:'#e0e8ed'}
      ]},
      {id:uid(),type:'offergrid',title:'Featured Services',subtitle:'Flexible cards — add, remove and reorder as needed.',bg:'#ffffff',columns:2,items:[
        {image_url:'',icon:'🏥',title:'Hospital Selection',text:'Coordination with suitable hospitals and specialists.',code:'SERVICE: HOSPITAL',button_text:'KNOW MORE',button_url:'https://cadupiushealthassist.com',bg:'#ffffff',border:'#d7e1e7'},
        {image_url:'',icon:'🛂',title:'Medical Visa',text:'Guidance for treatment-related travel documentation.',code:'SERVICE: VISA',button_text:'KNOW MORE',button_url:'https://cadupiushealthassist.com',bg:'#ffffff',border:'#d7e1e7'}
      ]},
      {id:uid(),type:'promo',title:'Make your treatment journey easier with:',bg:'#EEF5F8',columns:2,items:[
        {image_url:'',icon:'✈️',title:'Travel & Accommodation Support',text:'Airport, hotel and local coordination assistance.',url:'',bg:'#ffffff'},
        {image_url:'',icon:'💬',title:'Direct Assistance',text:'Call or WhatsApp our coordination team for more information.',url:'',bg:'#ffffff'}
      ]},
      {id:uid(),type:'cta',heading:'Ready to know more?',text:'Our coordination team can help you understand the next steps.',button_text:'VIEW DETAILS',button_url:'https://cadupiushealthassist.com',button_bg:'#0B6E99',bg:'#ffffff'},
      {id:uid(),type:'social',title:'Follow Us & Contact',website_url:'https://cadupiushealthassist.com',whatsapp_number:'',phone_number:'',facebook_url:'',instagram_url:'',youtube_url:'',linkedin_url:'',bg:'#ffffff'},
      {id:uid(),type:'footer',brand:'Cadupius Health Assist',text:'Medical treatment coordination support for international patients travelling to India.',contact_url:'https://cadupiushealthassist.com/contact',privacy_url:'https://cadupiushealthassist.com/privacy-policy',bg:'#F7F9FA'}
    ];
    const blankBlocks = () => [{id:uid(),type:'richtext',html:'<p>Dear <strong>{{recipient_name}}</strong>,</p><p>Start writing your email here.</p>',bg:'#ffffff',padding:28}];

    const loaded = readBuilderConfig(existing);
    let blocks = loaded ? loaded.blocks.map(b => ({...b,id:b.id || uid()})) : (existing ? [
      {id:uid(),type:'richtext',html:existing.replace(/<!--HE-BUILDER:[A-Za-z0-9_-]+-->/g,''),bg:'#ffffff',padding:28}
    ] : defaultBlocks());
    let emailBackground = loaded?.background || '#EEF2F5';
    let selectedId = blocks[0]?.id || null;
    let mobilePreview = false;
    let cleanPreview = false;
    let dragPayload = null;

    openModal(
      t ? 'Edit Email Template' : 'Create Email Template',
      'Professional drag-and-drop Email Marketing designer',
      `<div class="bt-pro-builder bt-pro-builder-v130">
        <div class="bt-pro-meta">
          <label>Template Name *<input id="btEmailName" maxlength="200" value="${esc(t?.template_name || '')}"></label>
          <label>Email Subject *<input id="btEmailSubject" maxlength="300" value="${esc(t?.email_subject || '')}">${subjectEmojiRow()}</label>
          <label class="bt-pro-bg-control">Email Background<input id="btEmailBackground" type="color" value="${esc(emailBackground)}"><small>Outer email background</small></label>
          <label class="bt-pro-preset-control">Starter Layout<select id="btProPreset"><option value="medical">Medical Tourism Marketing</option><option value="blank">Blank Email</option></select><button type="button" class="bc-btn" id="btApplyPreset">Apply</button></label>
        </div>
        <div class="bt-pro-layout">
          <aside class="bt-pro-palette">
            <div class="bt-pro-side-title">Drag & Drop Content</div>
            <div class="bt-pro-palette-group">Basic</div>
            <button type="button" draggable="true" data-add-block="header"><i class="fa-solid fa-heading"></i><span>Logo / Header</span></button>
            <button type="button" draggable="true" data-add-block="hero"><i class="fa-solid fa-star"></i><span>Hero Section</span></button>
            <button type="button" draggable="true" data-add-block="image"><i class="fa-regular fa-image"></i><span>Image Banner</span></button>
            <button type="button" draggable="true" data-add-block="richtext"><i class="fa-solid fa-paragraph"></i><span>Rich Text</span></button>
            <button type="button" draggable="true" data-add-block="cta"><i class="fa-solid fa-arrow-pointer"></i><span>CTA Banner</span></button>
            <div class="bt-pro-palette-group">Marketing</div>
            <button type="button" draggable="true" data-add-block="featuregrid"><i class="fa-solid fa-icons"></i><span>Feature / Powering Strip</span></button>
            <button type="button" draggable="true" data-add-block="offergrid"><i class="fa-solid fa-tags"></i><span>Flexible Offer Cards</span></button>
            <button type="button" draggable="true" data-add-block="promo"><i class="fa-solid fa-gift"></i><span>Promotion / Reward</span></button>
            <button type="button" draggable="true" data-add-block="social"><i class="fa-solid fa-share-nodes"></i><span>Social / Contact</span></button>
            <div class="bt-pro-palette-group">Layout</div>
            <button type="button" draggable="true" data-add-block="spacer"><i class="fa-solid fa-arrows-up-down"></i><span>Spacer</span></button>
            <button type="button" draggable="true" data-add-block="divider"><i class="fa-solid fa-minus"></i><span>Divider</span></button>
            <button type="button" draggable="true" data-add-block="footer"><i class="fa-solid fa-shoe-prints"></i><span>Footer</span></button>
            <div class="bt-pro-side-note"><b>Images:</b> upload JPG/PNG/GIF/WEBP from your computer for the safest delivery path. Public <b>HTTPS</b> image URLs are also supported. Uploaded images are embedded inline in recipient email; PDF/file attachments are not created.</div>
            <div class="bt-pro-side-note"><b>Security:</b> content is sanitized again on the server. Script/style tags, event handlers, unsafe URLs, data URLs and unapproved CSS are removed before storage.</div>
          </aside>

          <section class="bt-pro-workspace">
            <div class="bt-pro-previewbar">
              <div><b>Live Email Canvas</b><small id="btProSubjectPreview">${esc(t?.email_subject || 'Email subject')}</small></div>
              <div class="bc-actions">
                <button type="button" class="bc-btn" id="btProCleanPreview"><i class="fa-solid fa-eye"></i> Clean Preview</button>
                <button type="button" class="bc-btn bc-primary" id="btProDesktop"><i class="fa-solid fa-desktop"></i> Desktop</button>
                <button type="button" class="bc-btn" id="btProMobile"><i class="fa-solid fa-mobile-screen"></i> Mobile</button>
              </div>
            </div>
            <div class="bt-pro-stage"><div id="btProCanvas" class="bt-pro-email-canvas"></div></div>
          </section>

          <aside class="bt-pro-properties">
            <div class="bt-pro-side-title">Block Properties</div>
            <div id="btProNoSelection" class="bt-pro-side-note">Select a block in the email canvas to edit it.</div>
            <div id="btProPropertyPanel"></div>
          </aside>
        </div>
        <div class="bt-builder-note"><b>Email safety:</b> the builder stores only sanitized email HTML and a validated internal HE-BUILDER configuration comment. Internal configuration is removed before recipient delivery. Marketing Email unsubscribe is inserted as a signed, database-backed suppression link at delivery.</div>
      </div>`,
      '<button class="bc-btn" id="btEmailCancel" type="button">Cancel</button><button class="bc-btn bc-primary" id="btSaveEmail" type="button">Save Template</button>'
    );
    modalBox?.classList.add('bt-pro-modal', 'bt-pro-modal-fullscreen');
    $('btModal').classList.add('bt-editor-fullscreen');
    document.body.classList.add('bt-editor-open');
    modalBackdropCloseEnabled = false;
    modalBeforeClose = () => {
      if (!editorDirty) return true;
      return window.confirm('You have unsaved Email Template changes. Close the designer and discard them?');
    };

    const canvas = $('btProCanvas');
    const propPanel = $('btProPropertyPanel');
    const builderRoot = document.querySelector('#btModal .bt-pro-builder-v130');
    const markEditorDirty = () => { editorDirty = true; };
    builderRoot?.addEventListener('input', markEditorDirty);
    builderRoot?.addEventListener('change', markEditorDirty);
    builderRoot?.addEventListener('drop', markEditorDirty);
    builderRoot?.addEventListener('click', e => {
      if (e.target.closest('[data-add-block],[data-block-act],[data-prop-action],[data-item-action],[data-add-item],[data-style-toggle],[data-pro-emoji],[data-subject-emoji],#btApplyPreset')) {
        markEditorDirty();
      }
    });
    const selected = () => blocks.find(b => b.id === selectedId) || null;
    const hex = (value, fallback) => cleanHex(value, fallback);
    const pad = value => Math.max(0, Math.min(60, Number(value) || 0));
    const cellText = value => esc(cleanText(value, 5000)).replace(/\n/g, '<br>');
    const clampInt = (value,min,max,fallback) => Math.max(min,Math.min(max,Number.parseInt(value,10)||fallback));
    const itemWidth = columns => `${(100 / clampInt(columns,1,6,1)).toFixed(4)}%`;
    const splitRows = (items, columns) => {
      const out=[]; const n=clampInt(columns,1,6,1);
      for(let i=0;i<items.length;i+=n) out.push(items.slice(i,i+n));
      return out;
    };

    const buttonHtml = (text, url, bg = '#0B6E99', color = '#ffffff', styleObj = {}) => {
      if (!String(text || '').trim()) return '';
      const safe = safeUrl(url);
      const st = styleDefaults({font:'Arial',size:14,bold:true,color,align:'center',...(styleObj || {})});
      const decorations = [st.underline ? 'underline' : '', st.strike ? 'line-through' : ''].filter(Boolean).join(' ') || 'none';
      const inner = `<span style="color:${hex(st.color,color)}">${esc(cleanText(text,120))}</span>`;
      if (!safe) return `<span class="he-mobile-button" style="display:inline-block;background-color:${hex(bg,'#0B6E99')};color:${hex(st.color,color)};font-family:${safeFont(st.font)},sans-serif;font-size:${safeSize(st.size)}px;font-weight:${st.bold?'bold':'normal'};font-style:${st.italic?'italic':'normal'};text-decoration:${decorations};padding:12px 20px;border-radius:5px;text-align:${safeAlign(st.align)}">${inner}</span>`;
      return `<a class="he-mobile-button" href="${esc(safe)}" style="display:inline-block;background-color:${hex(bg,'#0B6E99')};color:${hex(st.color,color)};font-family:${safeFont(st.font)},sans-serif;font-size:${safeSize(st.size)}px;font-weight:${st.bold?'bold':'normal'};font-style:${st.italic?'italic':'normal'};text-decoration:${decorations};padding:12px 20px;border-radius:5px;text-align:${safeAlign(st.align)}">${inner}</a>`;
    };

    function renderBlock(b, preview = false) {
      const bg = hex(b.bg, '#ffffff');
      const isMobilePreview = Boolean(preview && mobilePreview);
      if (b.type === 'header') {
        const logo = safeImageUrl(b.logo_url, preview);
        return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${bg};border-collapse:collapse"><tr><td class="he-mobile-section-pad" align="center" style="padding:${pad(b.padding ?? 22)}px 24px;text-align:center">${logo ? `<img src="${esc(logo)}" alt="${esc(cleanText(b.brand || 'Logo',180))}" width="${clampInt(b.logo_width,80,360,180)}" style="display:block;width:${clampInt(b.logo_width,80,360,180)}px;max-width:100%;height:auto;margin:0 auto;border:0">` : `<div class="he-mobile-brand" style="${textCss(b,'brand_style',{font:'Arial',size:24,bold:true,color:hex(b.color,'#0B6E99'),align:'center'})}">${esc(cleanText(b.brand || 'Brand',300))}</div>`}<div class="he-mobile-copy" style="${textCss(b,'tagline_style',{font:'Arial',size:12,color:'#667788',align:'center'})};line-height:18px;margin:5px 0 0">${esc(cleanText(b.tagline || '',500))}</div></td></tr></table>`;
      }
      if (b.type === 'hero') {
        return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${bg};border-collapse:collapse"><tr><td class="he-mobile-section-pad" align="center" style="padding:${pad(b.padding ?? 42)}px 30px;text-align:center;color:${hex(b.color,'#ffffff')}"><div class="he-mobile-eyebrow" style="${textCss(b,'eyebrow_style',{font:'Arial',size:11,bold:true,color:hex(b.color,'#ffffff'),align:'center'})};line-height:16px">${esc(cleanText(b.eyebrow || '',300))}</div><div class="he-mobile-hero-heading" style="${textCss(b,'heading_style',{font:'Arial',size:30,bold:true,color:hex(b.color,'#ffffff'),align:'center'})};line-height:1.2;margin:8px 0 0">${esc(cleanText(b.heading || '',500))}</div><div class="he-mobile-copy" style="${textCss(b,'text_style',{font:'Arial',size:15,color:hex(b.color,'#ffffff'),align:'center'})};line-height:1.5;margin:14px 0 0">${cellText(b.text || '')}</div><div style="text-align:${safeAlign(styleValue(b,'button_style',{align:'center'}).align)};margin:22px 0 0">${buttonHtml(b.button_text,b.button_url,b.button_bg||'#ffffff','#0B6E99',b.button_style)}</div></td></tr></table>`;
      }
      if (b.type === 'image') {
        const src = safeImageUrl(b.src, preview);
        if (!src) return preview ? `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${bg};border-collapse:collapse"><tr><td align="center" style="padding:42px 20px;text-align:center;color:#77838d;border:1px dashed #b9cad6">Choose an image file or enter a public HTTPS image URL</td></tr></table>` : '';
        const img = `<img src="${esc(src)}" alt="${esc(cleanText(b.alt || 'Campaign banner',300))}" width="650" style="display:block;width:100%;max-width:650px;height:auto;margin:0 auto;border:0">`;
        const link = safeUrl(b.link);
        const linked = link ? `<a href="${esc(link)}" style="text-decoration:none">${img}</a>` : img;
        return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${bg};border-collapse:collapse"><tr><td align="center" style="padding:${pad(b.padding ?? 0)}px;text-align:center">${linked}</td></tr></table>`;
      }
      if (b.type === 'richtext') {
        return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${bg};border-collapse:collapse"><tr><td class="he-mobile-section-pad he-mobile-rich" style="padding:${pad(b.padding ?? 28)}px;font-family:Arial,sans-serif;font-size:14px;line-height:22px;color:#1f2937;text-align:left">${safeRichHtml(b.html || '')}</td></tr></table>`;
      }
      if (b.type === 'featuregrid') {
        const items = (Array.isArray(b.items) ? b.items : []).slice(0,MAX_FEATURES);
        const desktopColumns = clampInt(b.columns,1,6,Math.min(5,Math.max(1,items.length || 1)));
        const mobileColumns = clampInt(b.mobile_columns,1,2,2);
        const columns = isMobilePreview ? mobileColumns : desktopColumns;
        const rows = splitRows(items,columns);
        const width = itemWidth(columns);
        const mobileClass = mobileColumns === 1 ? 'he-feature-cell-one' : 'he-feature-cell-two';
        const cells = rows.map(row => `<tr>${row.map(item => {
          const url = safeUrl(item.url);
          const content = `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${hex(item.bg,'#ffffff')};border:1px solid ${hex(item.border,'#e0e8ed')};border-radius:8px;border-collapse:separate;box-sizing:border-box"><tr><td align="center" style="padding:13px 8px;text-align:center;vertical-align:top"><div style="${textCss(item,'icon_style',{font:'Arial',size:25,color:'#154f75',align:'center'})};line-height:1.2">${esc(cleanText(item.icon || '✓',30))}</div><div style="${textCss(item,'title_style',{font:'Arial',size:11,bold:true,color:'#123d5a',align:'center'})};line-height:1.3;margin:7px 0 0;white-space:normal;overflow-wrap:break-word;word-break:normal">${esc(cleanText(item.title || '',300))}</div><div style="${textCss(item,'text_style',{font:'Arial',size:10,color:'#667788',align:'center'})};line-height:1.35;margin:4px 0 0;white-space:normal;overflow-wrap:break-word;word-break:normal">${cellText(item.text || '')}</div></td></tr></table>`;
          return `<td class="he-feature-cell ${mobileClass}" width="${width}" valign="top" style="width:${width};padding:5px;vertical-align:top;box-sizing:border-box">${url?`<a href="${esc(url)}" style="text-decoration:none;color:inherit">${content}</a>`:content}</td>`;
        }).join('')}</tr>`).join('');
        return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${bg};border-collapse:collapse"><tr><td class="he-grid-title" align="${safeAlign(styleValue(b,'title_style',{align:'center'}).align)}" style="padding:24px 18px 3px;${textCss(b,'title_style',{font:'Arial',size:18,bold:true,color:'#1f2937',align:'center'})};line-height:1.3">${esc(cleanText(b.title || '',400))}</td></tr><tr><td align="center" style="padding:0 18px 12px;${textCss(b,'subtitle_style',{font:'Arial',size:12,color:'#667788',align:'center'})};line-height:1.4">${esc(cleanText(b.subtitle || '',800))}</td></tr><tr><td class="he-grid-pad" style="padding:0 14px 22px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;table-layout:fixed">${cells}</table></td></tr></table>`;
      }
      if (b.type === 'offergrid') {
        const items = (Array.isArray(b.items) ? b.items : []).slice(0,MAX_OFFERS);
        const desktopColumns = clampInt(b.columns,1,3,2);
        const columns = isMobilePreview ? 1 : desktopColumns;
        const rows = splitRows(items,columns);
        const width = itemWidth(columns);
        const cards = rows.map(row => `<tr>${row.map(item => {
          const src = safeImageUrl(item.image_url, preview);
          const image = src ? `<img src="${esc(src)}" alt="${esc(cleanText(item.alt || item.title || 'Offer image',300))}" width="300" style="display:block;width:100%;max-width:300px;height:auto;border:0;border-radius:10px 10px 0 0">` : `<div style="height:120px;background-color:#EAF3F7;text-align:center;font-family:Arial,sans-serif;font-size:36px;line-height:120px;border-radius:10px 10px 0 0">${esc(cleanText(item.icon || '🏥',30))}</div>`;
          const code = String(item.code || '').trim() ? `<div style="display:inline-block;background-color:#eef2f5;padding:7px 9px;margin:9px 0 0;font-family:Arial,sans-serif;font-size:10px;color:#374151;border-radius:4px">${esc(cleanText(item.code,120))}</div>` : '';
          return `<td class="he-stack-cell" width="${width}" valign="top" style="width:${width};padding:8px;vertical-align:top;box-sizing:border-box"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${hex(item.bg,'#ffffff')};border:1px solid ${hex(item.border,'#d7e1e7')};border-radius:10px;border-collapse:separate;box-sizing:border-box"><tr><td style="padding:0">${image}</td></tr><tr><td style="padding:15px 15px 17px;text-align:left"><div style="${textCss(item,'title_style',{font:'Arial',size:18,bold:true,color:'#1f2937',align:'left'})};line-height:1.25;white-space:normal;overflow-wrap:break-word;word-break:normal">${esc(cleanText(item.title || '',400))}</div><div style="${textCss(item,'text_style',{font:'Arial',size:12,color:'#667788',align:'left'})};line-height:1.5;margin:6px 0 0;white-space:normal;overflow-wrap:break-word;word-break:normal">${cellText(item.text || '')}</div>${code}<div style="margin:12px 0 0">${buttonHtml(item.button_text,item.button_url,item.button_bg||'#ffffff','#0B6E99',item.button_style)}</div></td></tr></table></td>`;
        }).join('')}</tr>`).join('');
        return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${bg};border-collapse:collapse"><tr><td class="he-grid-title" align="center" style="padding:26px 20px 3px;${textCss(b,'title_style',{font:'Arial',size:22,bold:true,color:'#1f2937',align:'center'})};line-height:1.3">${esc(cleanText(b.title || '',400))}</td></tr><tr><td align="center" style="padding:0 20px 10px;${textCss(b,'subtitle_style',{font:'Arial',size:12,color:'#667788',align:'center'})};line-height:1.4">${esc(cleanText(b.subtitle || '',800))}</td></tr><tr><td class="he-grid-pad" style="padding:0 14px 24px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;table-layout:fixed">${cards}</table></td></tr></table>`;
      }
      if (b.type === 'promo') {
        const items = (Array.isArray(b.items) ? b.items : []).slice(0,MAX_PROMOS);
        const desktopColumns = clampInt(b.columns,1,2,2);
        const columns = isMobilePreview ? 1 : desktopColumns;
        const width = itemWidth(columns);
        const rows = splitRows(items,columns).map(row => `<tr>${row.map(item => {
          const src = safeImageUrl(item.image_url, preview);
          const link = safeUrl(item.url);
          const media = src ? `<img src="${esc(src)}" alt="${esc(cleanText(item.title || 'Promotion',300))}" width="260" style="display:block;width:100%;max-width:260px;height:auto;border:0;border-radius:6px">` : `<div style="font-family:Arial,sans-serif;font-size:28px;line-height:1.2">${esc(cleanText(item.icon || '🎁',30))}</div>`;
          const content = `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${hex(item.bg,'#ffffff')};border-collapse:separate;border-radius:7px"><tr><td style="padding:15px;vertical-align:top">${media}<div style="${textCss(item,'title_style',{font:'Arial',size:14,bold:true,color:'#1f2937',align:'left'})};line-height:1.3;margin:8px 0 0">${esc(cleanText(item.title || '',350))}</div><div style="${textCss(item,'text_style',{font:'Arial',size:11,color:'#667788',align:'left'})};line-height:1.45;margin:5px 0 0">${cellText(item.text || '')}</div></td></tr></table>`;
          return `<td class="he-stack-cell" width="${width}" valign="top" style="width:${width};padding:6px;vertical-align:top;box-sizing:border-box">${link?`<a href="${esc(link)}" style="text-decoration:none;color:inherit">${content}</a>`:content}</td>`;
        }).join('')}</tr>`).join('');
        return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${bg};border-collapse:collapse"><tr><td class="he-grid-title" align="center" style="padding:24px 20px 10px;${textCss(b,'title_style',{font:'Arial',size:18,bold:true,color:'#1f2937',align:'center'})};line-height:1.3">${esc(cleanText(b.title || '',400))}</td></tr><tr><td class="he-grid-pad" style="padding:0 16px 22px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;table-layout:fixed">${rows}</table></td></tr></table>`;
      }
      if (b.type === 'cta') {
        return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${bg};border-collapse:collapse"><tr><td class="he-mobile-section-pad" align="center" style="padding:${pad(b.padding ?? 28)}px;text-align:center"><div class="he-mobile-cta-heading" style="${textCss(b,'heading_style',{font:'Arial',size:20,bold:true,color:'#1f2937',align:'center'})};line-height:1.35">${esc(cleanText(b.heading || '',500))}</div><div class="he-mobile-copy" style="${textCss(b,'text_style',{font:'Arial',size:13,color:'#667788',align:'center'})};line-height:1.5;margin:8px 0 0">${cellText(b.text || '')}</div><div style="text-align:${safeAlign(styleValue(b,'button_style',{align:'center'}).align)};margin:18px 0 0">${buttonHtml(b.button_text,b.button_url,b.button_bg||'#0B6E99','#ffffff',b.button_style)}</div></td></tr></table>`;
      }
      if (b.type === 'social') {
        const waDigits = String(b.whatsapp_number || '').replace(/\D/g, '').slice(0,18);
        const phoneDigits = String(b.phone_number || '').replace(/[^\d+]/g, '').slice(0,20);
        const links = [
          safeUrl(b.website_url) ? ['🌐','Website',safeUrl(b.website_url)] : null,
          waDigits ? ['💬','WhatsApp',`https://wa.me/${waDigits}`] : null,
          phoneDigits ? ['📞','Call',`tel:${phoneDigits}`] : null,
          safeUrl(b.instagram_url) ? ['◎','Instagram',safeUrl(b.instagram_url)] : null,
          safeUrl(b.facebook_url) ? ['f','Facebook',safeUrl(b.facebook_url)] : null,
          safeUrl(b.youtube_url) ? ['▶','YouTube',safeUrl(b.youtube_url)] : null,
          safeUrl(b.linkedin_url) ? ['in','LinkedIn',safeUrl(b.linkedin_url)] : null
        ].filter(Boolean);
        return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${bg};border-collapse:collapse"><tr><td class="he-mobile-section-pad" align="center" style="padding:20px 24px;text-align:center"><div style="${textCss(b,'title_style',{font:'Arial',size:14,bold:true,color:'#1f2937',align:'center'})};line-height:1.4">${esc(cleanText(b.title || '',300))}</div><div style="font-family:Arial,sans-serif;font-size:11px;line-height:20px;text-align:center;margin:9px 0 0">${links.length ? links.map(x=>`<a href="${esc(x[2])}" style="display:inline-block;color:#0B6E99;text-decoration:none;padding:5px 7px">${esc(x[0])} ${esc(x[1])}</a>`).join('') : 'Add Website / WhatsApp / Call / Social links'}</div></td></tr></table>`;
      }
      if (b.type === 'spacer') {
        return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${bg};border-collapse:collapse"><tr><td height="${clampInt(b.height,4,120,24)}" style="height:${clampInt(b.height,4,120,24)}px;font-size:1px;line-height:1px">&nbsp;</td></tr></table>`;
      }
      if (b.type === 'divider') {
        return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${bg};border-collapse:collapse"><tr><td style="padding:${pad(b.padding ?? 8)}px 24px"><div style="border-top:${clampInt(b.thickness,1,6,1)}px solid ${hex(b.color,'#d9e1e6')};height:0"></div></td></tr></table>`;
      }
      if (b.type === 'footer') {
        const contact = safeUrl(b.contact_url);
        const privacy = safeUrl(b.privacy_url);
        const unsubscribeHref = preview ? '#' : '{{unsubscribe_url}}';
        const unsubscribe = `<a href="${unsubscribeHref}" style="color:#0B6E99;text-decoration:none">Unsubscribe</a>`;
        const links = [contact ? `<a href="${esc(contact)}" style="color:#0B6E99;text-decoration:none">Contact</a>` : '',privacy ? `<a href="${esc(privacy)}" style="color:#0B6E99;text-decoration:none">Privacy</a>` : '',unsubscribe].filter(Boolean).join(' &nbsp; | &nbsp; ');
        return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${bg};border-collapse:collapse"><tr><td class="he-mobile-section-pad" align="center" style="padding:22px 24px;text-align:center"><div class="he-mobile-brand" style="${textCss(b,'brand_style',{font:'Arial',size:13,bold:true,color:'#263746',align:'center'})};line-height:1.4">${esc(cleanText(b.brand || '',300))}</div><div style="${textCss(b,'text_style',{font:'Arial',size:11,color:'#687782',align:'center'})};line-height:1.55;margin:6px 0 0">${cellText(b.text || '')}</div><div style="font-family:Arial,sans-serif;font-size:10px;line-height:18px;margin:8px 0 0">${links}</div></td></tr></table>`;
      }
      return '';
    }

    function emailHtml() {
      const cleanBlocks = blocks.slice(0,MAX_BLOCKS).map(b => clone(b));
      const config = encodeConfig({version:CURRENT_BUILDER_VERSION,background:emailBackground,blocks:cleanBlocks});
      const content = cleanBlocks.map(b => renderBlock(b,false)).join('');
      const html = `<!--HE-BUILDER:${config}--><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background-color:${hex(emailBackground,'#EEF2F5')};border-collapse:collapse"><tr><td align="center" style="padding:0;text-align:center"><table class="he-email-inner" role="presentation" width="650" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:650px;background-color:#ffffff;border-collapse:collapse;margin:0 auto"><tr><td style="padding:0">${content}</td></tr></table></td></tr></table>`;
      return html;
    }

    function renderCanvas() {
      canvas.classList.toggle('mobile', mobilePreview);
      canvas.classList.toggle('clean-preview', cleanPreview);
      const subject = $('btEmailSubject')?.value || 'Email subject';
      $('btProSubjectPreview').textContent = sampleTokens(subject);
      const blocksHtml = blocks.map((b,index) => {
        const selectedClass = b.id === selectedId ? ' selected' : '';
        const tools = `<div class="bt-pro-block-tools"><span class="bt-pro-drag-handle" title="Drag to reorder">⠿</span><button type="button" data-block-act="up">↑</button><button type="button" data-block-act="down">↓</button><button type="button" data-block-act="copy">Copy</button><button type="button" data-block-act="delete">Delete</button></div>`;
        return `<div class="bt-pro-dropzone" data-drop-index="${index}"><span>Drop here</span></div><div class="bt-pro-block${selectedClass}" draggable="true" data-block-id="${esc(b.id)}">${tools}${renderBlock(b,true)}</div>`;
      }).join('') + `<div class="bt-pro-dropzone bt-pro-dropzone-last" data-drop-index="${blocks.length}"><span>Drag a block here</span></div>`;
      canvas.innerHTML = `<div class="bt-pro-mail-surface" style="background-color:${hex(emailBackground,'#EEF2F5')}"><div class="bt-pro-mail-inner">${blocksHtml}</div></div>`;
    }

    const inputField = (label,key,value,type='text',attrs='') => `<div class="bt-pro-field"><label>${label}<input type="${type}" data-prop="${esc(key)}" value="${esc(value ?? '')}" ${attrs}></label></div>`;
    const textareaField = (label,key,value,rows=3) => `<div class="bt-pro-field"><label>${label}<textarea data-prop="${esc(key)}" rows="${rows}">${esc(value ?? '')}</textarea></label></div>`;
    const colorField = (label,key,value) => `<div class="bt-pro-field"><label>${label}<input type="color" data-prop="${esc(key)}" value="${hex(value,'#ffffff')}"></label></div>`;
    const selectField = (label,key,value,options) => `<div class="bt-pro-field"><label>${label}<select data-prop="${esc(key)}">${options.map(x=>`<option value="${esc(String(x[0]))}" ${String(value)===String(x[0])?'selected':''}>${esc(String(x[1]))}</option>`).join('')}</select></label></div>`;
    const imagePickerField = (label,key,value,itemIndex=null,required=false) => {
      const itemAttr = itemIndex===null ? '' : `data-item-index="${itemIndex}"`;
      const propAttr = itemIndex===null ? `data-prop="${esc(key)}"` : `data-item-prop="${esc(key)}" data-item-index="${itemIndex}"`;
      return `<div class="bt-pro-field bt-pro-image-picker"><label>${label}<input type="text" ${propAttr} value="${esc(value ?? '')}" placeholder="https://... or choose a file below"></label><label class="bt-pro-file-label"><span>Choose image file${required?' *':''}</span><input type="file" accept="image/jpeg,image/png,image/gif,image/webp" data-image-upload-key="${esc(key)}" ${itemAttr}></label><small>JPG, PNG, GIF or WEBP up to 5 MB. Server validates the file signature and image dimensions. Uploaded files are embedded inline in outgoing email.</small></div>`;
    };
    const styledField = (label,key,value,styleKey,target,defaults={},rows=0,itemIndex=null) => {
      if (!target[styleKey] || typeof target[styleKey] !== 'object') target[styleKey] = styleDefaults(defaults);
      const attr = itemIndex===null ? `data-prop="${esc(key)}"` : `data-item-index="${itemIndex}" data-item-prop="${esc(key)}"`;
      const control = rows > 0 ? `<textarea ${attr} rows="${rows}">${esc(value ?? '')}</textarea>` : `<input type="text" ${attr} value="${esc(value ?? '')}">`;
      return `<div class="bt-pro-field"><label>${label}${control}</label>${styleToolbar(styleKey,target?.[styleKey],defaults,itemIndex)}${emojiRow(key,itemIndex)}</div>`;
    };
    const itemActions = (index, canAddLabel='') => `<div class="bt-pro-item-actions"><button type="button" class="bc-btn" data-item-action="up" data-item-index="${index}">↑</button><button type="button" class="bc-btn" data-item-action="down" data-item-index="${index}">↓</button><button type="button" class="bc-btn" data-item-action="copy" data-item-index="${index}">Copy</button><button type="button" class="bc-btn" data-item-action="delete" data-item-index="${index}">Delete</button>${canAddLabel?`<span>${esc(canAddLabel)}</span>`:''}</div>`;

    function renderProperties() {
      const b = selected();
      $('btProNoSelection').classList.toggle('hidden', !!b);
      if (!b) { propPanel.innerHTML = ''; return; }
      let fields = `<div class="bt-pro-selected-type">${esc(String(b.type || '').toUpperCase())}</div><div class="bt-pro-prop-actions"><button class="bc-btn" type="button" data-prop-action="up">↑</button><button class="bc-btn" type="button" data-prop-action="down">↓</button><button class="bc-btn" type="button" data-prop-action="copy">Copy</button><button class="bc-btn" type="button" data-prop-action="delete">Delete</button></div>`;
      if (b.type === 'header') {
        fields += imagePickerField('Logo Image URL (optional)','logo_url',b.logo_url)
          + inputField('Logo Width (px)','logo_width',b.logo_width || 180,'number','min="80" max="360"')
          + styledField('Brand Name','brand',b.brand,'brand_style',b,{font:'Arial',size:24,bold:true,color:hex(b.color,'#0B6E99'),align:'center'})
          + styledField('Tagline','tagline',b.tagline,'tagline_style',b,{font:'Arial',size:12,color:'#667788',align:'center'})
          + colorField('Background','bg',b.bg)
          + inputField('Padding','padding',b.padding ?? 22,'number','min="0" max="60"');
      }
      if (b.type === 'hero') {
        fields += styledField('Eyebrow','eyebrow',b.eyebrow,'eyebrow_style',b,{font:'Arial',size:11,bold:true,color:hex(b.color,'#ffffff'),align:'center'})
          + styledField('Headline','heading',b.heading,'heading_style',b,{font:'Arial',size:30,bold:true,color:hex(b.color,'#ffffff'),align:'center'},2)
          + styledField('Supporting Text','text',b.text,'text_style',b,{font:'Arial',size:15,color:hex(b.color,'#ffffff'),align:'center'},4)
          + styledField('CTA Text','button_text',b.button_text,'button_style',b,{font:'Arial',size:14,bold:true,color:'#0B6E99',align:'center'})
          + inputField('CTA URL','button_url',b.button_url)
          + colorField('CTA Button Background','button_bg',b.button_bg || '#ffffff')
          + colorField('Section Background','bg',b.bg)
          + inputField('Padding','padding',b.padding ?? 42,'number','min="0" max="60"');
      }
      if (b.type === 'image') fields += imagePickerField('Banner Image URL *','src',b.src || '',null,true) + inputField('Alt Text','alt',b.alt || '') + inputField('Click-through URL','link',b.link || '') + colorField('Background','bg',b.bg) + inputField('Padding','padding',b.padding ?? 0,'number','min="0" max="60"');
      if (b.type === 'richtext') {
        fields += `<div class="bt-pro-field"><label>Rich Text Content</label><div class="bt-pro-rich-toolbar" id="btProRichToolbar"><select id="btProFormat"><option value="P">Paragraph</option><option value="H1">Heading 1</option><option value="H2">Heading 2</option><option value="H3">Heading 3</option></select><select id="btProFont"><option>Arial</option><option>Calibri</option><option>Verdana</option><option>Georgia</option><option>Times New Roman</option><option>Tahoma</option><option>Trebuchet MS</option></select><select id="btProSize">${[8,9,10,11,12,13,14,16,18,20,22,24,28,32,36].map(x=>`<option value="${x}" ${x===14?'selected':''}>${x}</option>`).join('')}</select><button type="button" data-rich-cmd="bold" title="Bold"><b>B</b></button><button type="button" data-rich-cmd="italic" title="Italic"><i>I</i></button><button type="button" data-rich-cmd="underline" title="Underline"><u>U</u></button><button type="button" data-rich-cmd="strikeThrough" title="Strikethrough"><s>S</s></button><button type="button" data-rich-cmd="justifyLeft" title="Left"><i class="fa-solid fa-align-left"></i></button><button type="button" data-rich-cmd="justifyCenter" title="Center"><i class="fa-solid fa-align-center"></i></button><button type="button" data-rich-cmd="justifyRight" title="Right"><i class="fa-solid fa-align-right"></i></button><button type="button" data-rich-cmd="justifyFull" title="Justify"><i class="fa-solid fa-align-justify"></i></button><button type="button" data-rich-cmd="insertUnorderedList" title="Bullets"><i class="fa-solid fa-list-ul"></i></button><button type="button" data-rich-cmd="insertOrderedList" title="Numbering"><i class="fa-solid fa-list-ol"></i></button><button type="button" id="btProLink" title="Link"><i class="fa-solid fa-link"></i></button><input id="btProTextColor" type="color" value="#1f2937" title="Font colour"><input id="btProHighlightColor" type="color" value="#fff59d" title="Highlight colour"></div><div id="btProRichEditor" class="bt-pro-rich-editor" contenteditable="true">${safeRichHtml(b.html || '')}</div><div class="bc-tokens bt-pro-tokens">Insert: <button type="button" data-pro-token="{{recipient_name}}">{{recipient_name}}</button><button type="button" data-pro-token="{{first_name}}">{{first_name}}</button><button type="button" data-pro-token="{{country}}">{{country}}</button><button type="button" data-pro-token="{{reference_no}}">{{reference_no}}</button><button type="button" data-pro-token="{{website_url}}">{{website_url}}</button><button type="button" data-pro-token="{{contact_number}}">{{contact_number}}</button></div>${richEmojiRow()}</div>` + colorField('Block Background','bg',b.bg) + inputField('Padding','padding',b.padding ?? 28,'number','min="0" max="60"');
      }
      if (b.type === 'featuregrid') {
        if (!Array.isArray(b.items)) b.items=[];
        fields += styledField('Section Title','title',b.title,'title_style',b,{font:'Arial',size:18,bold:true,color:'#1f2937',align:'center'})
          + styledField('Subtitle','subtitle',b.subtitle,'subtitle_style',b,{font:'Arial',size:12,color:'#667788',align:'center'},2)
          + selectField('Desktop Columns','columns',String(b.columns || 4),[[1,'1'],[2,'2'],[3,'3'],[4,'4'],[5,'5'],[6,'6']])
          + selectField('Mobile Columns','mobile_columns',String(b.mobile_columns || 2),[[1,'1 per row'],[2,'2 per row']])
          + colorField('Section Background','bg',b.bg)
          + `<button type="button" class="bc-btn bc-primary bt-pro-add-item" data-add-item="feature">+ Add Feature</button>`;
        b.items.slice(0,MAX_FEATURES).forEach((item,i)=>{
          fields += `<div class="bt-pro-item-card"><b>Feature ${i+1}</b>${itemActions(i)}${styledField('Icon / Emoji','icon',item.icon,'icon_style',item,{font:'Arial',size:25,color:'#154f75',align:'center'},0,i)}${styledField('Title','title',item.title,'title_style',item,{font:'Arial',size:11,bold:true,color:'#123d5a',align:'center'},0,i)}${styledField('Text','text',item.text,'text_style',item,{font:'Arial',size:10,color:'#667788',align:'center'},2,i)}${inputFieldForItem('Optional Link','url',item.url,i)}${itemColorField('Card Background','bg',item.bg,i)}${itemColorField('Border Colour','border',item.border,i)}</div>`;
        });
      }
      if (b.type === 'offergrid') {
        if (!Array.isArray(b.items)) b.items=[];
        fields += styledField('Section Title','title',b.title,'title_style',b,{font:'Arial',size:22,bold:true,color:'#1f2937',align:'center'})
          + styledField('Subtitle','subtitle',b.subtitle,'subtitle_style',b,{font:'Arial',size:12,color:'#667788',align:'center'},2)
          + selectField('Desktop Columns','columns',String(b.columns || 2),[[1,'1'],[2,'2'],[3,'3']])
          + colorField('Section Background','bg',b.bg)
          + `<button type="button" class="bc-btn bc-primary bt-pro-add-item" data-add-item="offer">+ Add Offer Card</button>`;
        b.items.slice(0,MAX_OFFERS).forEach((item,i)=>{
          fields += `<div class="bt-pro-item-card"><b>Offer ${i+1}</b>${itemActions(i)}${imagePickerField('Card Image','image_url',item.image_url || '',i)}${styledField('Fallback Icon','icon',item.icon,'icon_style',item,{font:'Arial',size:26,color:'#1f2937',align:'center'},0,i)}${styledField('Title','title',item.title,'title_style',item,{font:'Arial',size:18,bold:true,color:'#1f2937',align:'left'},0,i)}${styledField('Text','text',item.text,'text_style',item,{font:'Arial',size:12,color:'#667788',align:'left'},3,i)}${inputFieldForItem('Promo / Service Code','code',item.code,i)}${styledField('Button Text','button_text',item.button_text,'button_style',item,{font:'Arial',size:12,bold:true,color:'#0B6E99',align:'center'},0,i)}${inputFieldForItem('Button URL','button_url',item.button_url,i)}${itemColorField('Button Background','button_bg',item.button_bg || '#ffffff',i)}${itemColorField('Card Background','bg',item.bg,i)}${itemColorField('Border Colour','border',item.border,i)}</div>`;
        });
      }
      if (b.type === 'promo') {
        if (!Array.isArray(b.items)) b.items=[];
        fields += styledField('Section Title','title',b.title,'title_style',b,{font:'Arial',size:18,bold:true,color:'#1f2937',align:'center'})
          + selectField('Desktop Columns','columns',String(b.columns || 2),[[1,'1'],[2,'2']])
          + colorField('Section Background','bg',b.bg)
          + `<button type="button" class="bc-btn bc-primary bt-pro-add-item" data-add-item="promo">+ Add Promotion</button>`;
        b.items.slice(0,MAX_PROMOS).forEach((item,i)=>{
          fields += `<div class="bt-pro-item-card"><b>Promotion ${i+1}</b>${itemActions(i)}${imagePickerField('Promotion Image','image_url',item.image_url || '',i)}${styledField('Fallback Icon','icon',item.icon,'icon_style',item,{font:'Arial',size:26,color:'#1f2937',align:'left'},0,i)}${styledField('Title','title',item.title,'title_style',item,{font:'Arial',size:14,bold:true,color:'#1f2937',align:'left'},0,i)}${styledField('Text','text',item.text,'text_style',item,{font:'Arial',size:11,color:'#667788',align:'left'},3,i)}${inputFieldForItem('Optional Link','url',item.url,i)}${itemColorField('Card Background','bg',item.bg,i)}</div>`;
        });
      }
      if (b.type === 'cta') fields += styledField('Heading','heading',b.heading,'heading_style',b,{font:'Arial',size:20,bold:true,color:'#1f2937',align:'center'}) + styledField('Supporting Text','text',b.text,'text_style',b,{font:'Arial',size:13,color:'#667788',align:'center'},3) + styledField('Button Text','button_text',b.button_text,'button_style',b,{font:'Arial',size:14,bold:true,color:'#ffffff',align:'center'}) + inputField('Button URL','button_url',b.button_url) + colorField('Button Background','button_bg',b.button_bg || '#0B6E99') + colorField('Section Background','bg',b.bg) + inputField('Padding','padding',b.padding ?? 28,'number','min="0" max="60"');
      if (b.type === 'social') fields += styledField('Section Title','title',b.title,'title_style',b,{font:'Arial',size:14,bold:true,color:'#1f2937',align:'center'}) + inputField('Website URL','website_url',b.website_url) + inputField('WhatsApp Number','whatsapp_number',b.whatsapp_number) + inputField('Call Number','phone_number',b.phone_number) + inputField('Instagram URL','instagram_url',b.instagram_url) + inputField('Facebook URL','facebook_url',b.facebook_url) + inputField('YouTube URL','youtube_url',b.youtube_url) + inputField('LinkedIn URL','linkedin_url',b.linkedin_url) + colorField('Background','bg',b.bg);
      if (b.type === 'spacer') fields += inputField('Height (px)','height',b.height || 24,'number','min="4" max="120"') + colorField('Background','bg',b.bg);
      if (b.type === 'divider') fields += colorField('Background','bg',b.bg) + colorField('Divider Colour','color',b.color || '#d9e1e6') + inputField('Thickness (px)','thickness',b.thickness || 1,'number','min="1" max="6"') + inputField('Padding','padding',b.padding ?? 8,'number','min="0" max="60"');
      if (b.type === 'footer') fields += styledField('Brand','brand',b.brand,'brand_style',b,{font:'Arial',size:13,bold:true,color:'#263746',align:'center'}) + styledField('Footer Text','text',b.text,'text_style',b,{font:'Arial',size:11,color:'#687782',align:'center'},4) + inputField('Contact URL','contact_url',b.contact_url) + inputField('Privacy Policy URL','privacy_url',b.privacy_url) + colorField('Background','bg',b.bg) + '<div class="bt-pro-side-note"><b>Unsubscribe:</b> TERIQEN automatically includes a signed unsubscribe link for Marketing Email and suppresses future marketing delivery after opt-out.</div>';
      propPanel.innerHTML = fields;
      if (b.type === 'richtext') setupRichText(b);
    }

    function inputFieldForItem(label,key,value,index,type='text') {
      return `<div class="bt-pro-field"><label>${label}<input type="${type}" data-item-index="${index}" data-item-prop="${esc(key)}" value="${esc(value ?? '')}"></label></div>`;
    }
    function itemColorField(label,key,value,index) {
      return `<div class="bt-pro-field"><label>${label}<input type="color" data-item-index="${index}" data-item-prop="${esc(key)}" value="${hex(value,'#ffffff')}"></label></div>`;
    }

    function setupRichText(b) {
      const editor = $('btProRichEditor');
      if (!editor) return;
      let savedRange = null;
      const saveSelection = () => {
        const sel = window.getSelection();
        if (!sel || !sel.rangeCount || !editor.contains(sel.anchorNode)) return;
        savedRange = sel.getRangeAt(0).cloneRange();
      };
      const restore = () => {
        editor.focus();
        const sel = window.getSelection();
        if (savedRange) { sel.removeAllRanges(); sel.addRange(savedRange); return; }
        const r = document.createRange(); r.selectNodeContents(editor); r.collapse(false); sel.removeAllRanges(); sel.addRange(r); savedRange = r.cloneRange();
      };
      const sync = () => { b.html = safeRichHtml(editor.innerHTML); renderCanvas(); };
      const cmd = (name, value = null) => { restore(); document.execCommand(name,false,value); saveSelection(); sync(); };
      ['keyup','mouseup','focus'].forEach(evt => editor.addEventListener(evt, saveSelection));
      editor.addEventListener('input', sync);
      $('btProRichToolbar')?.addEventListener('mousedown', e => { saveSelection(); if (e.target.closest('button')) e.preventDefault(); });
      document.querySelectorAll('[data-rich-cmd]').forEach(btn => btn.addEventListener('click', () => cmd(btn.dataset.richCmd)));
      $('btProFormat')?.addEventListener('change', e => cmd('formatBlock', e.target.value));
      $('btProFont')?.addEventListener('change', e => cmd('fontName', e.target.value));
      $('btProSize')?.addEventListener('change', e => { restore(); document.execCommand('fontSize',false,'7'); editor.querySelectorAll('font[size="7"]').forEach(n => {n.removeAttribute('size');n.style.fontSize=(Number(e.target.value)||14)+'px';}); saveSelection(); sync(); });
      $('btProTextColor')?.addEventListener('input', e => cmd('foreColor', e.target.value));
      $('btProHighlightColor')?.addEventListener('input', e => { restore(); if(!document.execCommand('hiliteColor',false,e.target.value)) document.execCommand('backColor',false,e.target.value); saveSelection(); sync(); });
      $('btProLink')?.addEventListener('click', () => { restore(); const raw=window.prompt('Enter HTTPS website, email or telephone link:','https://'); if(raw===null)return; const url=safeUrl(raw); if(!url)return toast('Enter a valid HTTPS, email or telephone link.'); const sel=window.getSelection(); if(sel&&!sel.isCollapsed)document.execCommand('createLink',false,url); else document.execCommand('insertHTML',false,`<a href="${esc(url)}">${esc(raw.trim()||url)}</a>`); saveSelection(); sync(); });
      document.querySelectorAll('[data-pro-token]').forEach(btn => btn.addEventListener('click', () => cmd('insertText', btn.dataset.proToken)));
    }

    function newBlock(type) {
      const common = {id:uid(),type};
      if (type === 'header') return {...common,brand:'CADUPIUS HEALTH ASSIST',tagline:'Medical Treatment Coordination in India',logo_url:'',logo_width:180,padding:22,bg:'#ffffff',color:'#0B6E99'};
      if (type === 'hero') return {...common,eyebrow:'CAMPAIGN HIGHLIGHT',heading:'Your Campaign Headline',text:'Add a clear supporting message for your recipients.',button_text:'LEARN MORE',button_url:'https://cadupiushealthassist.com',button_bg:'#ffffff',padding:42,bg:'#0B6E99',color:'#ffffff'};
      if (type === 'image') return {...common,src:'',alt:'Campaign banner',link:'',padding:0,bg:'#ffffff'};
      if (type === 'richtext') return {...common,html:'<p>Dear <strong>{{recipient_name}}</strong>,</p><p>Start writing your email content here.</p>',bg:'#ffffff',padding:28};
      if (type === 'featuregrid') return {...common,title:'Why Choose Us',subtitle:'Add, remove or reorder features freely.',bg:'#EEF5F8',columns:4,mobile_columns:2,items:[{icon:'🏥',title:'Hospitals',text:'Coordination',url:'',bg:'#ffffff',border:'#e0e8ed'},{icon:'👨‍⚕️',title:'Specialists',text:'Appointments',url:'',bg:'#ffffff',border:'#e0e8ed'},{icon:'📋',title:'Planning',text:'Treatment coordination',url:'',bg:'#ffffff',border:'#e0e8ed'},{icon:'✈️',title:'Travel',text:'Support',url:'',bg:'#ffffff',border:'#e0e8ed'}]};
      if (type === 'offergrid') return {...common,title:'Featured Offers / Services',subtitle:'Flexible marketing cards.',bg:'#ffffff',columns:2,items:[{image_url:'',icon:'🏥',title:'Offer / Service One',text:'Add supporting information.',code:'',button_text:'KNOW MORE',button_url:'https://cadupiushealthassist.com',button_bg:'#ffffff',bg:'#ffffff',border:'#d7e1e7'},{image_url:'',icon:'✈️',title:'Offer / Service Two',text:'Add supporting information.',code:'',button_text:'KNOW MORE',button_url:'https://cadupiushealthassist.com',button_bg:'#ffffff',bg:'#ffffff',border:'#d7e1e7'}]};
      if (type === 'promo') return {...common,title:'Make your journey easier with:',bg:'#EEF5F8',columns:2,items:[{image_url:'',icon:'🎁',title:'Promotion One',text:'Add supporting information.',url:'',bg:'#ffffff'},{image_url:'',icon:'✨',title:'Promotion Two',text:'Add supporting information.',url:'',bg:'#ffffff'}]};
      if (type === 'cta') return {...common,heading:'Ready to know more?',text:'Add a short call-to-action message.',button_text:'VIEW DETAILS',button_url:'https://cadupiushealthassist.com',button_bg:'#0B6E99',padding:28,bg:'#ffffff'};
      if (type === 'social') return {...common,title:'Follow Us & Contact',website_url:'https://cadupiushealthassist.com',whatsapp_number:'',phone_number:'',instagram_url:'',facebook_url:'',youtube_url:'',linkedin_url:'',bg:'#ffffff'};
      if (type === 'spacer') return {...common,height:24,bg:'#ffffff'};
      if (type === 'divider') return {...common,bg:'#ffffff',color:'#d9e1e6',thickness:1,padding:8};
      return {...common,brand:'Cadupius Health Assist',text:'Medical treatment coordination support for international patients.',contact_url:'https://cadupiushealthassist.com/contact',privacy_url:'https://cadupiushealthassist.com/privacy-policy',bg:'#F7F9FA'};
    }

    function act(action, id = selectedId) {
      const i = blocks.findIndex(b => b.id === id);
      if (i < 0) return;
      selectedId = id;
      if (action === 'up' && i > 0) [blocks[i-1],blocks[i]] = [blocks[i],blocks[i-1]];
      if (action === 'down' && i < blocks.length - 1) [blocks[i+1],blocks[i]] = [blocks[i],blocks[i+1]];
      if (action === 'copy') {
        if (blocks.length >= MAX_BLOCKS) return toast(`Maximum ${MAX_BLOCKS} blocks per email.`);
        const copy = clone(blocks[i]); copy.id = uid(); blocks.splice(i+1,0,copy); selectedId = copy.id;
      }
      if (action === 'delete') { blocks.splice(i,1); selectedId = blocks[Math.min(i,blocks.length-1)]?.id || null; }
      renderCanvas(); renderProperties();
    }
    function insertBlock(type,index=blocks.length) {
      if (!allowedTypes.has(type) || ['cards2','cards3','services'].includes(type)) return;
      if (blocks.length >= MAX_BLOCKS) return toast(`Maximum ${MAX_BLOCKS} blocks per email.`);
      const b = newBlock(type); const at=Math.max(0,Math.min(blocks.length,Number(index)||0));
      blocks.splice(at,0,b); selectedId=b.id; cleanPreview=false; renderCanvas(); renderProperties();
    }
    function moveBlock(id,index) {
      const from=blocks.findIndex(b=>b.id===id); if(from<0)return;
      let at=Math.max(0,Math.min(blocks.length,Number(index)||0));
      const [item]=blocks.splice(from,1); if(from<at) at-=1; blocks.splice(at,0,item); selectedId=id; renderCanvas(); renderProperties();
    }

    document.querySelectorAll('[data-add-block]').forEach(btn => {
      btn.addEventListener('click', () => insertBlock(btn.dataset.addBlock));
      btn.addEventListener('dragstart', e => {
        dragPayload={kind:'new',type:btn.dataset.addBlock};
        try { e.dataTransfer.effectAllowed='copy'; e.dataTransfer.setData('text/plain',btn.dataset.addBlock); } catch (_) {}
      });
    });
    canvas.addEventListener('dragstart', e => {
      const block=e.target.closest('[data-block-id]'); if(!block)return;
      dragPayload={kind:'move',id:block.dataset.blockId};
      try { e.dataTransfer.effectAllowed='move'; e.dataTransfer.setData('text/plain',block.dataset.blockId); } catch (_) {}
      block.classList.add('dragging');
    });
    canvas.addEventListener('dragend', e => {e.target.closest('[data-block-id]')?.classList.remove('dragging');canvas.querySelectorAll('.drag-over').forEach(x=>x.classList.remove('drag-over'));dragPayload=null;});
    canvas.addEventListener('dragover', e => {
      const zone=e.target.closest('[data-drop-index]'); if(!zone)return;
      e.preventDefault(); canvas.querySelectorAll('.drag-over').forEach(x=>x.classList.remove('drag-over')); zone.classList.add('drag-over');
      try { e.dataTransfer.dropEffect=dragPayload?.kind==='move'?'move':'copy'; } catch (_) {}
    });
    canvas.addEventListener('drop', e => {
      const zone=e.target.closest('[data-drop-index]'); if(!zone||!dragPayload)return;
      e.preventDefault(); const index=Number(zone.dataset.dropIndex)||0;
      if(dragPayload.kind==='new') insertBlock(dragPayload.type,index); else moveBlock(dragPayload.id,index);
      dragPayload=null;
    });
    canvas.addEventListener('click', e => {
      const block = e.target.closest('[data-block-id]'); if (!block) return;
      selectedId = block.dataset.blockId; cleanPreview=false;
      const action = e.target.closest('[data-block-act]')?.dataset.blockAct;
      if (action) act(action, selectedId); else { renderCanvas(); renderProperties(); }
    });

    const styleOwner = (el,b) => {
      const styleKey=el.dataset.styleKey; if(!styleKey||!b)return null;
      const itemIndex=el.dataset.itemIndex;
      const target=itemIndex!==undefined&&Array.isArray(b.items)?b.items[Number(itemIndex)]:b;
      if(!target)return null;
      if(!target[styleKey]||typeof target[styleKey]!=='object') target[styleKey]={};
      return target[styleKey];
    };
    const insertEmoji = (btn,b) => {
      const key=btn.dataset.emojiKey; const itemIndex=btn.dataset.itemIndex;
      const target=itemIndex!==undefined&&Array.isArray(b.items)?b.items[Number(itemIndex)]:b;
      if(!target||!key)return;
      const selector=itemIndex!==undefined?`[data-item-index="${itemIndex}"][data-item-prop="${CSS.escape(key)}"]`:`[data-prop="${CSS.escape(key)}"]`;
      const field=propPanel.querySelector(selector); const emoji=btn.dataset.proEmoji||''; if(!field||!emoji)return;
      const value=String(field.value||''); const start=Number.isInteger(field.selectionStart)?field.selectionStart:value.length; const end=Number.isInteger(field.selectionEnd)?field.selectionEnd:start;
      field.value=value.slice(0,start)+emoji+value.slice(end); target[key]=field.value; renderCanvas(); field.focus(); try{field.setSelectionRange(start+emoji.length,start+emoji.length)}catch(_){}
    };
    const maxItemsFor = b => b?.type==='featuregrid'?MAX_FEATURES:b?.type==='offergrid'?MAX_OFFERS:b?.type==='promo'?MAX_PROMOS:0;
    const newItemFor = b => {
      if(b.type==='featuregrid') return {icon:'✓',title:'New Feature',text:'Add supporting information.',url:'',bg:'#ffffff',border:'#e0e8ed'};
      if(b.type==='offergrid') return {image_url:'',icon:'🏥',title:'New Offer / Service',text:'Add supporting information.',code:'',button_text:'KNOW MORE',button_url:'https://cadupiushealthassist.com',button_bg:'#ffffff',bg:'#ffffff',border:'#d7e1e7'};
      return {image_url:'',icon:'🎁',title:'New Promotion',text:'Add supporting information.',url:'',bg:'#ffffff'};
    };
    function itemAct(action,index){
      const b=selected(); if(!b||!Array.isArray(b.items))return; const i=Number(index); if(!Number.isInteger(i)||i<0||i>=b.items.length)return;
      if(action==='up'&&i>0)[b.items[i-1],b.items[i]]=[b.items[i],b.items[i-1]];
      if(action==='down'&&i<b.items.length-1)[b.items[i+1],b.items[i]]=[b.items[i],b.items[i+1]];
      if(action==='copy'){if(b.items.length>=maxItemsFor(b))return toast('Maximum items reached for this section.');b.items.splice(i+1,0,clone(b.items[i]));}
      if(action==='delete')b.items.splice(i,1);
      renderCanvas();renderProperties();
    }
    propPanel.addEventListener('click', e => {
      const action=e.target.closest('[data-prop-action]')?.dataset.propAction; if(action){act(action);return;}
      const add=e.target.closest('[data-add-item]'); if(add){const b=selected();if(!b||!Array.isArray(b.items))return;if(b.items.length>=maxItemsFor(b))return toast('Maximum items reached for this section.');b.items.push(newItemFor(b));renderCanvas();renderProperties();return;}
      const itemAction=e.target.closest('[data-item-action]'); if(itemAction){itemAct(itemAction.dataset.itemAction,itemAction.dataset.itemIndex);return;}
      const toggle=e.target.closest('[data-style-toggle]'); if(toggle){const b=selected();const st=styleOwner(toggle,b);if(!st)return;const key=toggle.dataset.styleToggle;st[key]=!Boolean(st[key]);renderCanvas();renderProperties();return;}
      const emoji=e.target.closest('[data-pro-emoji]'); if(emoji){const b=selected();if(b)insertEmoji(emoji,b);}
    });

    const updateProp = e => {
      const b=selected(); if(!b)return;
      const itemIndex=e.target.dataset.itemIndex;
      const item=itemIndex!==undefined&&Array.isArray(b.items)?b.items[Number(itemIndex)]:null;
      const styleProp=e.target.dataset.styleProp;
      if(styleProp){const st=styleOwner(e.target,b);if(!st)return;let value=e.target.value;if(styleProp==='size')value=safeSize(value);if(styleProp==='font')value=safeFont(value);if(styleProp==='align')value=safeAlign(value);if(styleProp==='color')value=hex(value,'#1f2937');st[styleProp]=value;renderCanvas();return;}
      const itemKey=e.target.dataset.itemProp;
      if(item&&itemKey){let value=e.target.value;if(['bg','border','button_bg'].includes(itemKey))value=hex(value,itemKey==='border'?'#dce5ea':'#ffffff');item[itemKey]=cleanText(value,itemKey==='text'?5000:2000);renderCanvas();return;}
      const key=e.target.dataset.prop; if(!key)return; let value=e.target.value;
      if(['bg','color','button_bg'].includes(key)) value=hex(value,key==='color'?'#d9e1e6':'#ffffff');
      if(['padding','height','thickness','logo_width','columns','mobile_columns'].includes(key)) value=Number(value)||0;
      b[key]=typeof value==='string'?cleanText(value,key==='text'||key==='subtitle'?5000:2000):value;
      renderCanvas();
    };
    propPanel.addEventListener('change', async e => {
      const key=e.target.dataset.imageUploadKey;
      if(key&&e.target.files?.length){
        const b=selected(); if(!b)return;
        const file=e.target.files[0];
        if(file.size>5*1024*1024)return toast('Image must be 5 MB or smaller.');
        if(!/^image\/(jpeg|png|gif|webp)$/i.test(file.type||'')) return toast('Choose a JPG, PNG, GIF or WEBP image.');
        const form=new FormData();form.append('file',file);e.target.disabled=true;toast('Uploading and validating image...');
        try{
          const result=await api('/api/media/upload',{method:'POST',body:form});
          const idx=e.target.dataset.itemIndex;
          if(idx!==undefined&&Array.isArray(b.items)&&b.items[Number(idx)]) b.items[Number(idx)][key]=result.media_url||''; else b[key]=result.media_url||'';
          renderCanvas();renderProperties();toast(`Image uploaded: ${result.media_name||file.name}`);
        }catch(err){e.target.disabled=false;toast(err.message)}
        return;
      }
      updateProp(e);
    });
    propPanel.addEventListener('input', updateProp);

    $('btEmailSubject').addEventListener('input', renderCanvas);
    $('btEmailBackground').addEventListener('input', e => {emailBackground=hex(e.target.value,'#EEF2F5');renderCanvas();});
    document.querySelectorAll('[data-subject-emoji]').forEach(btn => btn.addEventListener('click', () => {const input=$('btEmailSubject');const emoji=btn.dataset.subjectEmoji||'';const value=String(input.value||'');const start=Number.isInteger(input.selectionStart)?input.selectionStart:value.length;const end=Number.isInteger(input.selectionEnd)?input.selectionEnd:start;input.value=value.slice(0,start)+emoji+value.slice(end);input.focus();try{input.setSelectionRange(start+emoji.length,start+emoji.length)}catch(_){}renderCanvas();}));
    $('btProDesktop').addEventListener('click',()=>{mobilePreview=false;$('btProDesktop').classList.add('bc-primary');$('btProMobile').classList.remove('bc-primary');renderCanvas();});
    $('btProMobile').addEventListener('click',()=>{mobilePreview=true;$('btProMobile').classList.add('bc-primary');$('btProDesktop').classList.remove('bc-primary');renderCanvas();});
    $('btProCleanPreview').addEventListener('click',()=>{cleanPreview=!cleanPreview;if(cleanPreview)selectedId=null;$('btProCleanPreview').classList.toggle('bc-primary',cleanPreview);renderCanvas();renderProperties();});
    $('btApplyPreset').addEventListener('click',()=>{if(blocks.length>1&&!window.confirm('Replace the current canvas with the selected starter layout?'))return;blocks=$('btProPreset').value==='blank'?blankBlocks():defaultBlocks();selectedId=blocks[0]?.id||null;renderCanvas();renderProperties();toast('Starter layout applied.');});
    $('btEmailCancel').addEventListener('click', closeModal);

    renderCanvas(); renderProperties();

    $('btSaveEmail').addEventListener('click', async () => {
      const bodyHtml=emailHtml().trim();
      const metaMatch=bodyHtml.match(/<!--HE-BUILDER:([A-Za-z0-9_-]+)-->/);
      if(metaMatch&&metaMatch[1].length>170000)return toast('Template configuration is too large. Remove some blocks or text before saving.');
      if(bodyHtml.length>230000)return toast('Template is too large. Remove some blocks/images/text before saving.');
      const tmp=document.createElement('div');tmp.innerHTML=bodyHtml;
      const payload={template_name:$('btEmailName').value.trim(),email_subject:$('btEmailSubject').value.trim(),body_html:bodyHtml,body_text:tmp.innerText.trim()};
      if(!payload.template_name||!payload.email_subject)return toast('Template Name and Email Subject are required.');
      if(!blocks.length)return toast('Add at least one email content block.');
      const btn=$('btSaveEmail');btn.disabled=true;btn.textContent='Saving...';
      try{
        const path=t?`/api/templates/email/${t.id}`:'/api/templates/email';
        await api(path,{method:t?'PUT':'POST',body:JSON.stringify(payload)});
        editorDirty=false;closeModal(true);await loadEmail();toast('Professional email template saved successfully.');
      }catch(e){toast(e.message);btn.disabled=false;btn.textContent='Save Template';}
    });
  }
  $('btWaRows').addEventListener('click', e => {
    const b = e.target.closest('[data-action]');
    if (!b) return;
    const t = waTemplates.find(x => x.id === Number(b.dataset.id));
    if (!t) return;
    if (b.dataset.action === 'preview') preview(t);
    if (b.dataset.action === 'mapping') mapping(t);
    if (b.dataset.action === 'delete') deleteWhatsAppTemplate(t);
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
        `<div class="bt-email-preview"><small>SUBJECT</small><h3>${esc(t.email_subject || '')}</h3><div>${t.body_html || esc(t.body_text || '')}</div></div>`,
        `<button class="bc-btn" id="btDeleteEmailPreview" type="button" style="border-color:#dc2626;color:#b91c1c;">Delete Template</button>`
      );
      $('btDeleteEmailPreview')?.addEventListener('click', async () => {
        if (!window.confirm(`Delete email template “${t.template_name}”? Existing Broadcast History will not be deleted.`)) return;
        try {
          await api(`/api/templates/email/${t.id}`, {method:'DELETE', body:'{}'});
          closeModal(true);
          await loadEmail();
          toast('Email template deleted.');
        } catch (e) {
          toast(e.message);
        }
      });
    }
    if (b.dataset.emailAction === 'edit') emailEditor(t);
    if (b.dataset.emailAction === 'copy') {
      const payload = {
        template_name:(String(t.template_name || 'Email Template') + ' - Copy').slice(0,200),
        email_subject:t.email_subject || '',
        body_html:t.body_html || '',
        body_text:t.body_text || ''
      };
      api('/api/templates/email', {method:'POST', body:JSON.stringify(payload)})
        .then(loadEmail)
        .then(() => toast('Email template copied.'))
        .catch(e => toast(e.message));
    }
    if (b.dataset.emailAction === 'delete') {
      if (!window.confirm(`Delete email template “${t.template_name}”? Existing Broadcast History will not be deleted.`)) return;
      api(`/api/templates/email/${t.id}`, {method:'DELETE', body:'{}'})
        .then(loadEmail)
        .then(() => toast('Email template deleted.'))
        .catch(e => toast(e.message));
    }
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
  $('btModal').addEventListener('click', e => { if (e.target === $('btModal') && modalBackdropCloseEnabled) closeModal(); });

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