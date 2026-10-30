(() => {
  const $ = id => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  let channel = 'WHATSAPP';
  let recipients = [];
  let validated = [];
  let campaignId = null;
  let media = {type:'', url:'', name:'', file:null, upload_url:''};
  let whatsappTemplates = [];
  let emailTemplates = [];
  let editorRange = null;
  let professionalEmailLoaded = false;

  const api = (path, options={}) => fetch('/broadcast-management' + path, {
    credentials:'same-origin',
    headers:{'X-CSRFToken':csrf, ...(options.body instanceof FormData ? {} : {'Content-Type':'application/json'}), ...(options.headers||{})},
    ...options
  }).then(async r => {
    const j = await r.json().catch(()=>({success:false,message:'Invalid server response'}));
    if(!r.ok || j.success === false) throw new Error(j.message || `HTTP ${r.status}`);
    return j;
  });

  function toast(msg){ const e=$('bcToast'); e.textContent=msg; e.classList.add('show'); setTimeout(()=>e.classList.remove('show'),2600); }
  function esc(s){ return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
  function text(){ return $('bcEditor')?.innerText.trim() || ''; }
  function richHtml(){ return $('bcEditor')?.innerHTML.trim() || ''; }
  function plainToHtml(value){ return esc(String(value||'')).replace(/\r?\n/g,'<br>'); }
  function saveEditorSelection(){
    const editor=$('bcEditor'), sel=window.getSelection();
    if(!editor || !sel || !sel.rangeCount || !editor.contains(sel.anchorNode)) return;
    editorRange=sel.getRangeAt(0).cloneRange();
  }
  function restoreEditorSelection(){
    const editor=$('bcEditor');
    editor.focus();
    const sel=window.getSelection();
    if(editorRange){
      sel.removeAllRanges(); sel.addRange(editorRange);
      return;
    }
    const range=document.createRange();
    range.selectNodeContents(editor); range.collapse(false);
    sel.removeAllRanges(); sel.addRange(range);
    editorRange=range.cloneRange();
  }
  function applyFontSize(px){
    if (professionalEmailLoaded) return;
    restoreEditorSelection();
    document.execCommand('fontSize',false,'7');
    $('bcEditor').querySelectorAll('font[size="7"]').forEach(node=>{
      node.removeAttribute('size'); node.style.fontSize=`${Number(px)||14}px`;
    });
    saveEditorSelection(); preview();
  }
  function editorCommand(cmd,value=null){
    if (professionalEmailLoaded) return;
    restoreEditorSelection();
    document.execCommand(cmd,false,value);
    saveEditorSelection(); preview();
  }
  function insertEditorText(value){ if (professionalEmailLoaded) return; editorCommand('insertText',String(value||'')); }
  function safeLink(value){
    let url=String(value||'').trim();
    if(!url) return '';
    if(/^(https?:|mailto:|tel:)/i.test(url)) return url;
    if(/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(url)) return 'mailto:'+url;
    return 'https://'+url;
  }
  function selectedTemplate(){ const id=Number($('bcTemplate')?.value||0); return whatsappTemplates.find(t=>t.id===id)||null; }
  function templateRequiresMedia(t){ return ['IMAGE','VIDEO','DOCUMENT'].includes(String(t?.header_type||'').toUpperCase()); }
  function mediaRules(){
    if(channel==='WHATSAPP'){
      const t=selectedTemplate();
      if(!t) return {image:'no',video:'no',gif:'no',url:'limited',note:'Select an approved WhatsApp template first.'};
      const h=String(t.header_type||'NONE').toUpperCase();
      if(['IMAGE','VIDEO','DOCUMENT'].includes(h)) return {image:'no',video:'no',gif:'no',url:'yes',note:`This Meta template requires a ${h.toLowerCase()} header. Use a public HTTPS Media URL in this release.`};
      return {image:'no',video:'no',gif:'no',url:'no',note:'The selected WhatsApp template does not accept extra media.'};
    }
    if(channel==='EMAIL') return {image:'yes',video:'limited',gif:'yes',url:'yes',note:'Email: JPG/PNG/WEBP/GIF can be embedded in the outgoing email. Video is best delivered by URL.'};
    if(channel==='SMS') return {image:'no',video:'no',gif:'no',url:'limited',note:'SMS is text-only; a media/website URL can be included.'};
    return {image:'yes',video:'yes',gif:'yes',url:'yes',note:'Telegram supports image, video, animation and links subject to Bot API limits.'};
  }
  function updateMediaRules(){
    const r=mediaRules(); $('bcMediaRules').textContent=r.note;
    document.querySelectorAll('.bc-media-btn').forEach(b => {
      const dis=r[b.dataset.media]==='no'; b.style.opacity=dis?'.45':'1'; b.style.pointerEvents=dis?'none':'auto';
    });
    const urlBtn=$('bcMediaUrl'); const urlDis=r.url==='no'; urlBtn.disabled=urlDis; urlBtn.style.opacity=urlDis?'.45':'1';
    if(channel==='WHATSAPP'){
      const t=selectedTemplate(); const required=templateRequiresMedia(t);
      $('bcMediaSection').classList.toggle('hidden',!required);
      $('bcMediaRequirement').textContent=required?'Required by Template':'Not required';
      $('chkMedia').textContent=required?(media.url?.startsWith('https://')?'Ready':'Required'):'From Template';
    } else {
      $('bcMediaSection').classList.remove('hidden');
      $('bcMediaRequirement').textContent='Optional';
      $('chkMedia').textContent=!media.type?'Optional':(r[media.type]==='no'?'Not Supported':r[media.type]==='limited'?'Check Rules':'Compatible');
    }
  }
  function isPlainEmail(){
    return channel==='EMAIL' &&
      String($('bcEmailMode')?.value || 'RICH').trim().toUpperCase()==='PLAIN';
  }

  function cta(){
    if(channel==='WHATSAPP' || isPlainEmail()) return '';
    const m=$('bcCtaMode').value, lines=['', $('bcCtaText').value || 'For more information or assistance:'];
    if(m==='both'||m==='web') lines.push('🌐 '+$('bcWebsite').value);
    if(m==='both'||m==='phone') lines.push('📞 Call / WhatsApp: '+$('bcPhone').value);
    return lines.join('\n');
  }
  function ctaPreviewHtml(){
    if(channel!=='EMAIL' || isPlainEmail()) return '';
    const mode=$('bcCtaMode').value;
    const label=esc($('bcCtaText').value || 'For more information or assistance:');
    const website=String($('bcWebsite').value||'').trim();
    const phone=String($('bcPhone').value||'').trim();
    const parts=[`<div class="bc-preview-cta"><div><b>${label}</b></div>`];
    if((mode==='both'||mode==='web') && website){
      const href=/^https?:\/\//i.test(website)?website:'https://'+website;
      parts.push(`<div>🌐 <a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(website)}</a></div>`);
    }
    if((mode==='both'||mode==='phone') && phone){
      const digits=phone.replace(/\D/g,'');
      parts.push(`<div>📞 ${digits?`<a href="tel:+${digits}">${esc(phone)}</a> &nbsp; <span>•</span> &nbsp; <a href="https://wa.me/${digits}" target="_blank" rel="noopener noreferrer">WhatsApp</a>`:esc(phone)}</div>`);
    }
    parts.push('</div>');
    return parts.join('');
  }
  function sampleValue(source){
    const map={recipient_name:'Rahim Ahmed',first_name:'Rahim',reference_no:'HE-1048',country_name:'Bangladesh',language_code:'bn',recipient_tag:'Patient',campaign_name:$('bcName').value.trim()||'Healthcare Awareness',website_url:$('bcWebsite').value||'https://cadupiushealthassist.com',contact_number:$('bcPhone').value||'+91 XXXXX XXXXX'};
    return map[source] || 'Sample';
  }
  function sampleFreeformTokens(value){
    return String(value||'').replace(/{{\s*([A-Za-z0-9_]+)\s*}}/g,(m,key)=>{
      const k=String(key||'').toLowerCase();
      const aliases={name:'recipient_name',country:'country_name',language:'language_code',tag:'recipient_tag',mobile:'contact_number'};
      const source=aliases[k]||k;
      const known=['recipient_name','first_name','reference_no','country_name','language_code','recipient_tag','campaign_name','website_url','contact_number'];
      return known.includes(source)?sampleValue(source):m;
    });
  }
  function sampleFreeformTokensHtml(value){
    return String(value||'').replace(/{{\s*([A-Za-z0-9_]+)\s*}}/g,(m,key)=>{
      const k=String(key||'').toLowerCase();
      const aliases={name:'recipient_name',country:'country_name',language:'language_code',tag:'recipient_tag',mobile:'contact_number'};
      const source=aliases[k]||k;
      const known=['recipient_name','first_name','reference_no','country_name','language_code','recipient_tag','campaign_name','website_url','contact_number'];
      return known.includes(source)?esc(sampleValue(source)):m;
    });
  }
  function templatePreviewText(t){
    if(!t) return '';
    let body=String(t.body_text||'');
    Object.entries(t.variable_mapping||{}).forEach(([key,source])=>{ body=body.replaceAll(`{{${key}}}`, sampleValue(source)); body=body.replaceAll(`{{ ${key} }}`, sampleValue(source)); });
    const parts=[];
    if(t.header_text) parts.push(t.header_text);
    if(body) parts.push(body);
    if(t.footer_text) parts.push(t.footer_text);
    (t.buttons||[]).forEach(b=>parts.push(`[ ${b.text||b.type||'Button'} ]`));
    return parts.join('\n\n');
  }
  function updateTemplateInfo(){
    const t=selectedTemplate(); const info=$('bcTemplateInfo'); const warn=$('bcTemplateWarning');
    if(!t){ info.classList.add('hidden'); warn.classList.remove('hidden'); warn.textContent='WhatsApp broadcasts must use a Meta-approved template. Select a template before sending.'; }
    else {
      info.classList.remove('hidden');
      info.innerHTML=`<div><small>Meta Template</small><b>${esc(t.provider_template_name||t.template_name)}</b></div><div><small>Category</small><b>${esc(t.category||'—')}</b></div><div><small>Language</small><b>${esc(t.language_code||'—')}</b></div><div><small>Header</small><b>${esc(t.header_type||'NONE')}</b></div><div><small>Variable Mapping</small><b>${t.mapping_complete?'Ready':'Incomplete'}</b></div>`;
      warn.classList.toggle('hidden',t.mapping_complete);
      if(!t.mapping_complete){ const reasons=[]; if(t.missing_mapping?.length)reasons.push(`Variable mapping is incomplete for ${t.missing_mapping.map(x=>'{{'+x+'}}').join(', ')}.`); if(t.unsupported_reasons?.length)reasons.push(t.unsupported_reasons.join(' ')); warn.textContent=reasons.join(' ')+' Open Manage Templates → Variables for details.'; }
    }
    $('chkTemplate').textContent=t&&t.status==='APPROVED'&&t.mapping_complete?'Ready':'Pending';
    $('chkMessage').textContent=t?'Ready':'Pending';
    updateMediaRules(); preview();
  }
  function preview(){
    const previewEl=$('bcPreview');
    if(channel==='EMAIL'){
      // Email is composed from the full-width workspace. A duplicate side preview is intentionally disabled.
      previewEl.innerHTML='';
      previewEl.classList.remove('bc-preview-rich');
    } else {
      const s=channel==='WHATSAPP'?templatePreviewText(selectedTemplate()):(sampleFreeformTokens(text())+cta());
      previewEl.textContent=s;
      previewEl.classList.remove('bc-preview-rich');
    }
    $('chkName').textContent=$('bcName').value.trim()?'Ready':'Pending';
    $('chkMessage').textContent=channel==='WHATSAPP'?(selectedTemplate()?'Ready':'Pending'):(text()?'Ready':'Pending');
    $('chkTemplateRow').classList.toggle('hidden',channel!=='WHATSAPP');
    $('chkCta').textContent=channel==='WHATSAPP'?'From Template':(($('bcWebsite').value||$('bcPhone').value)?'Ready':'Missing');
    $('bcPreviewChannel').textContent=channel[0]+channel.slice(1).toLowerCase();
    const pm=$('bcPreviewMedia'); pm.innerHTML=''; pm.classList.add('hidden');
    if(media.url && media.type!=='url'){
      const tag=media.type==='video'?'video':'img'; const el=document.createElement(tag); el.src=media.url;
      if(tag==='video') el.controls=true; pm.appendChild(el); pm.classList.remove('hidden');
    } else if(media.type==='url' && media.url){
      pm.textContent=(channel==='WHATSAPP'?'Template header media: ':'Media: ')+media.name+' — '+media.url; pm.classList.remove('hidden');
    }
    updateCost();
  }
  function updateCost(){
    const n=validated.filter(x=>x.validation_status==='VALID').length;
    let rate=0,note='';
    if(channel==='WHATSAPP'){rate=.85;note='Illustrative only; actual Meta price varies by market/category.';}
    if(channel==='EMAIL'){rate=.016;note='Illustrative only; actual email provider price varies.';}
    if(channel==='SMS'){rate=.18;note='Illustrative only; SMS segment/provider/DLT pricing varies.';}
    if(channel==='TELEGRAM'){rate=0;note='Normal Bot API delivery may have no per-message charge.';}
    $('bcCost').textContent='₹'+(n*rate).toFixed(2); $('bcCostNote').textContent=note;
  }
  async function loadWhatsappTemplates(){
    const select=$('bcTemplate');
    select.innerHTML='<option value="">Loading approved templates...</option>';
    const j=await api('/api/templates?channel=WHATSAPP&status=APPROVED');
    whatsappTemplates=j.templates||[];
    const ready=whatsappTemplates.filter(t=>t.mapping_complete);
    select.innerHTML='<option value="">-- Select approved template --</option>'+whatsappTemplates.map(t=>`<option value="${t.id}" ${!t.mapping_complete?'disabled':''}>${esc(t.template_name)} — ${esc(t.language_code)}${t.mapping_complete?'':' (mapping required)'}</option>`).join('');
    if(!whatsappTemplates.length) select.innerHTML='<option value="">No approved templates — sync in Message Templates</option>';
    const qp=Number(new URLSearchParams(location.search).get('template_id')||0);
    if(qp && ready.some(t=>t.id===qp)) select.value=String(qp);
    updateTemplateInfo();
  }

  async function loadEmailTemplateFromQuery(){
    const qp = Number(new URLSearchParams(location.search).get('email_template_id') || 0);
    if(!qp) return;
    const j = await api('/api/templates?channel=EMAIL');
    emailTemplates = j.templates || [];
    const t = emailTemplates.find(x => x.id === qp);
    if(!t) throw new Error('Selected email template was not found.');

    channel = 'EMAIL';
    document.querySelectorAll('.bc-channel').forEach(x => {
      x.classList.toggle('active', x.dataset.channel === 'EMAIL');
    });
    applyChannelUi();
    $('bcSubject').value = t.email_subject || '';
    const bodyHtml = t.body_html || plainToHtml(t.body_text || '');
    professionalEmailLoaded = /<!--HE-BUILDER:[A-Za-z0-9_-]+-->/.test(bodyHtml);
    $('bcEditor').innerHTML = bodyHtml;
    $('bcEditor').setAttribute('contenteditable', professionalEmailLoaded ? 'false' : 'true');
    $('bcEditor').classList.toggle('bc-template-locked', professionalEmailLoaded);
    const toolbar = $('bcRichToolbar');
    if (toolbar) toolbar.classList.toggle('hidden', professionalEmailLoaded);
    let note = $('bcProfessionalTemplateNote');
    if (professionalEmailLoaded) {
      if (!note) {
        note = document.createElement('div');
        note.id = 'bcProfessionalTemplateNote';
        note.className = 'bc-prof-template-note';
        $('bcEditor').parentNode.insertBefore(note, $('bcEditor'));
      }
      note.innerHTML = '<b>Professional Email Template loaded.</b> The design is protected here so its layout cannot be accidentally broken. Use <b>Manage Templates → Edit</b> for section-by-section font, colour, alignment, emoji and content changes.';
      note.classList.remove('hidden');
    } else if (note) note.classList.add('hidden');
    preview();
    toast(professionalEmailLoaded ? 'Professional Email template loaded safely.' : 'Email template loaded into Broadcast Centre.');
  }

  function receiverRequirement(){
    const el=$('bcReceiverRequirement');
    if(!el) return;
    const msg={
      EMAIL:'For Email broadcasts, Email is required. Name, Mobile, Country and Language are optional but recommended.',
      WHATSAPP:'For WhatsApp broadcasts, Mobile is required in international format, e.g. +8801712345678. Other fields are optional but recommended.',
      SMS:'For SMS broadcasts, Mobile is required in international format, e.g. +8801712345678. Other fields are optional.',
      TELEGRAM:'For Telegram broadcasts, Telegram Chat ID is preferred; Mobile can be used only if your provider mapping supports it.'
    };
    el.textContent=msg[channel]||'Enter one or many receivers. Required destination fields depend on the selected channel.';
  }

  function emptyRecipient(){
    return {name:'',mobile:'',email:'',country:'',language:'English',reference_no:'',tag:'',telegram_chat_id:''};
  }

  function entryRowHtml(r={}, index=0){
    const row={...emptyRecipient(),...r};
    return `<tr class="bc-entry-row">
      <td class="bc-entry-no">${index+1}</td>
      <td><input data-field="name" type="text" maxlength="150" value="${esc(row.name)}" placeholder="Receiver name"></td>
      <td><input data-field="mobile" type="text" maxlength="30" value="${esc(row.mobile)}" placeholder="+8801712345678"></td>
      <td><input data-field="email" type="email" maxlength="254" value="${esc(row.email)}" placeholder="name@example.com"></td>
      <td><input data-field="country" type="text" maxlength="100" value="${esc(row.country)}" placeholder="Bangladesh"></td>
      <td><select data-field="language"><option value="English" ${String(row.language).toLowerCase()==='english'?'selected':''}>English</option><option value="Bengali" ${['bengali','bangla','bn'].includes(String(row.language).toLowerCase())?'selected':''}>Bengali</option><option value="Hindi" ${String(row.language).toLowerCase()==='hindi'?'selected':''}>Hindi</option><option value="Other" ${!['english','bengali','bangla','bn','hindi'].includes(String(row.language).toLowerCase())?'selected':''}>Other</option></select></td>
      <td><input data-field="reference_no" type="text" maxlength="100" value="${esc(row.reference_no)}" placeholder="Optional"></td>
      <td><input data-field="tag" type="text" maxlength="100" value="${esc(row.tag)}" placeholder="Patient / Agent"></td>
      <td><input data-field="telegram_chat_id" type="text" maxlength="100" value="${esc(row.telegram_chat_id)}" placeholder="Optional"></td>
      <td><button type="button" class="bc-row-delete" title="Delete row" aria-label="Delete row"><i class="fa-solid fa-trash"></i></button></td>
    </tr>`;
  }

  function renumberEntryRows(){
    document.querySelectorAll('#bcEntryRows .bc-entry-row').forEach((tr,i)=>{ const n=tr.querySelector('.bc-entry-no'); if(n)n.textContent=String(i+1); });
    const count=document.querySelectorAll('#bcEntryRows .bc-entry-row').length;
    if($('bcEntryCount')) $('bcEntryCount').textContent=`${count} receiver row${count===1?'':'s'}`;
  }

  function bindEntryDeletes(){
    document.querySelectorAll('#bcEntryRows .bc-row-delete').forEach(btn=>btn.onclick=()=>{
      btn.closest('tr')?.remove();
      if(!document.querySelector('#bcEntryRows .bc-entry-row')) addEntryRows(1);
      renumberEntryRows();
    });
  }

  function addEntryRows(count=1, rows=null){
    const body=$('bcEntryRows');
    if(!body) return;
    const data=rows||Array.from({length:count},()=>emptyRecipient());
    const start=body.querySelectorAll('.bc-entry-row').length;
    body.insertAdjacentHTML('beforeend',data.map((r,i)=>entryRowHtml(r,start+i)).join(''));
    bindEntryDeletes(); renumberEntryRows();
  }

  function loadEntryGrid(){
    const body=$('bcEntryRows');
    if(!body) return;
    body.innerHTML='';
    if(recipients.length) addEntryRows(0,recipients);
    else addEntryRows(5);
    receiverRequirement();
  }

  function collectEntryRows(){
    const rows=[];
    document.querySelectorAll('#bcEntryRows .bc-entry-row').forEach(tr=>{
      const row=emptyRecipient();
      tr.querySelectorAll('[data-field]').forEach(el=>row[el.dataset.field]=String(el.value||'').trim());
      if(Object.entries(row).some(([k,v])=>k!=='language' && String(v||'').trim())) rows.push(row);
    });
    return rows;
  }

  function parseDelimitedLine(line, delimiter){
    if(delimiter==='\t') return line.split('\t').map(x=>x.trim());
    const out=[]; let cur=''; let quoted=false;
    for(let i=0;i<line.length;i++){
      const ch=line[i];
      if(ch==='"'){
        if(quoted && line[i+1]==='"'){cur+='"';i++;}
        else quoted=!quoted;
      } else if(ch===delimiter && !quoted){out.push(cur.trim());cur='';}
      else cur+=ch;
    }
    out.push(cur.trim()); return out;
  }

  function parseBulkRecipients(raw){
    const lines=String(raw||'').split(/\r?\n/).map(x=>x.trimEnd()).filter(x=>x.trim());
    if(!lines.length) return [];
    const first=lines[0].toLowerCase();
    const hasHeader=first.includes('name') && (first.includes('mobile')||first.includes('email'));
    const dataLines=hasHeader?lines.slice(1):lines;
    return dataLines.map(line=>{
      const delimiter=line.includes('\t')?'\t':',';
      const [name='',mobile='',email='',country='',language='English',reference_no='',tag='',telegram_chat_id='']=parseDelimitedLine(line,delimiter);
      return {name,mobile,email,country,language:language||'English',reference_no,tag,telegram_chat_id};
    }).filter(r=>Object.entries(r).some(([k,v])=>k!=='language' && String(v||'').trim()));
  }

  async function validate(){
    const j=await api('/api/validate',{method:'POST',body:JSON.stringify({channel,recipients})});
    validated=j.data.rows; render(j.data.counts); return j.data;
  }
  function render(counts){
    const body=$('bcRecipients');
    body.innerHTML=validated.length?validated.map(r=>{
      const reason=String(r.validation_reason||'').trim();
      return `<tr><td>${esc(r.name)}</td><td>${esc(r.mobile)}</td><td>${esc(r.email)}</td><td>${esc(r.country)}</td><td>${esc(r.language)}</td><td>${esc(r.reference_no)}</td><td>${esc(r.tag)}</td><td>${esc(r.telegram_chat_id)}</td><td><b>${esc(r.validation_status)}</b>${reason?`<small class="bc-validation-reason">${esc(reason)}</small>`:''}</td></tr>`;
    }).join(''):'<tr><td colspan="9" class="bc-empty">No receiver list loaded.</td></tr>';
    counts=counts||{total:0,valid:0,duplicate:0,invalid:0};
    $('bcTotal').textContent=counts.total;$('bcValid').textContent=counts.valid;$('bcDup').textContent=counts.duplicate;$('bcInvalid').textContent=counts.invalid;
    $('chkReceivers').textContent=counts.valid?'Ready':'Pending'; updateCost();
  }
  function payload(sendMode){
    const t=selectedTemplate();
    const waMediaType=channel==='WHATSAPP'&&t?String(t.header_type||'').toLowerCase():null;
    return {
      campaign_name:$('bcName').value.trim(),campaign_type:$('bcType').value,channel,
      email_mode:channel==='EMAIL'?String($('bcEmailMode')?.value||'RICH').trim().toUpperCase():'RICH',
      template_id:channel==='WHATSAPP'?(t?.id||null):null,
      email_subject:$('bcSubject').value,message_html:channel==='EMAIL'?richHtml():'',
      message_text:channel==='WHATSAPP'?(t?.body_text||''):(text()+cta()),
      language:channel==='WHATSAPP'?(t?.language_code||''): 'English',website_url:$('bcWebsite').value,contact_number:$('bcPhone').value,
      cta_text:$('bcCtaText').value,cta_mode:$('bcCtaMode').value,
      media_type:channel==='WHATSAPP'?(templateRequiresMedia(t)?waMediaType:null):(media.type||null),
      media_url:media.upload_url||(media.type==='url'?media.url:null),media_name:media.name||null,
      send_mode:sendMode||$('bcSendMode').value,scheduled_on:$('bcSchedule').value||null,recipients
    };
  }
  async function ensureMediaUploaded(){
    if(channel!=='EMAIL' || !media.file) return;
    if(media.upload_url) return;
    if(!['image','gif'].includes(media.type)) throw new Error('For Email video, use Media URL. Picture/GIF files can be embedded directly.');
    const fd=new FormData();
    fd.append('file',media.file);
    const j=await api('/api/media/upload',{method:'POST',body:fd});
    media.upload_url=j.media_url;
    media.name=j.media_name||media.name;
    $('bcMediaSelected').innerHTML=`<b>${esc(media.name)}</b><br><small>EMAIL IMAGE — ready to embed in outgoing email</small>`;
  }

  async function create(sendMode){
    await validate();
    await ensureMediaUploaded();
    const p=payload(sendMode);
    if(!p.campaign_name) throw new Error('Broadcast name is required.');
    if(channel==='WHATSAPP'){
      const t=selectedTemplate();
      if(!t) throw new Error('Select an approved WhatsApp template.');
      if(!t.mapping_complete) throw new Error('Complete the WhatsApp template variable mapping first.');
      if(templateRequiresMedia(t) && !String(p.media_url||'').startsWith('https://')) throw new Error(`The selected template requires a public HTTPS ${String(t.header_type).toLowerCase()} URL.`);
    } else if(!p.message_text) throw new Error('Message content is required.');
    const j=await api('/api/campaigns',{method:'POST',body:JSON.stringify(p)}); campaignId=j.campaign_id; return campaignId;
  }
  async function dispatch(){
    if(!campaignId) await create('NOW');
    $('bcProgressText').textContent='Sending...';
    let loops=0;
    while(loops++<500){
      await api(`/api/campaigns/${campaignId}/dispatch`,{method:'POST',body:'{}'});
      const s=(await api(`/api/campaigns/${campaignId}/status`)).data;
      $('bcQueued').textContent=s.queued_count||0;$('bcSuccess').textContent=s.success_count||0;$('bcFailed').textContent=s.failed_count||0;$('bcSkipped').textContent=s.skipped_count||0;
      const total=Math.max(1,Number(s.valid_count||0)); const done=Number(s.success_count||0)+Number(s.failed_count||0);
      $('bcProgressBar').style.width=Math.min(100,Math.round(done/total*100))+'%';
      if(Number(s.queued_count||0)===0){$('bcProgressText').textContent='Broadcast completed';break;}
    }
  }
  function applyChannelUi(){
    const wa=channel==='WHATSAPP', email=channel==='EMAIL';
    document.querySelector('.bc-page')?.classList.toggle('bc-email-mode', email);
    $('bcWhatsappTemplateWrap').classList.toggle('hidden',!wa);
    $('bcFreeformComposer').classList.toggle('hidden',wa);
    $('bcSubjectWrap').classList.toggle('hidden',!email);
    $('bcCtaSection').classList.toggle('hidden',wa);
    $('bcWhatsappCtaNote').classList.toggle('hidden',!wa);
    $('chkTemplateRow').classList.toggle('hidden',!wa);
    document.querySelectorAll('.bc-email-only').forEach(el=>el.classList.toggle('hidden',!email));
    if($('bcFormattingNote')) $('bcFormattingNote').textContent=email
      ? 'Rich Text is enabled for Email. Formatting is stored as safe HTML and rendered in the outgoing email.'
      : 'This channel is sent as plain text in the current release. Emoji and personalisation tokens remain available.';
    $('bcEditor')?.classList.toggle('bc-plain-editor',!email);
    if(wa) updateTemplateInfo(); else {updateMediaRules();preview();}

    if(email && isPlainEmail()){
      $('bcCtaSection')?.classList.add('hidden');
      $('bcMediaSection')?.classList.add('hidden');
    } else if(email) {
      $('bcCtaSection')?.classList.remove('hidden');
    }

    receiverRequirement();
  }

  $('bcEmailMode')?.addEventListener('change',()=>{
    applyChannelUi();
    preview();
  });

  document.querySelectorAll('.bc-channel').forEach(b=>b.addEventListener('click',async()=>{
    document.querySelectorAll('.bc-channel').forEach(x=>x.classList.remove('active')); b.classList.add('active'); channel=b.dataset.channel;
    applyChannelUi(); if(recipients.length) await validate(); preview();
  }));

  const editor=$('bcEditor');
  ['keyup','mouseup','input','focus'].forEach(evt=>editor?.addEventListener(evt,saveEditorSelection));
  $('bcRichToolbar')?.addEventListener('mousedown',e=>{
    saveEditorSelection();
    if(e.target.closest('button')) e.preventDefault();
  });
  document.querySelectorAll('.bc-toolbar [data-cmd]').forEach(b=>b.addEventListener('click',()=>editorCommand(b.dataset.cmd)));
  $('bcBlockFormat')?.addEventListener('change',e=>editorCommand('formatBlock',e.target.value));
  $('bcFontName')?.addEventListener('change',e=>editorCommand('fontName',e.target.value));
  $('bcFontSize')?.addEventListener('change',e=>applyFontSize(e.target.value));
  $('bcForeColor')?.addEventListener('input',e=>editorCommand('foreColor',e.target.value));
  $('bcBackColor')?.addEventListener('input',e=>{
    restoreEditorSelection();
    if(!document.execCommand('hiliteColor',false,e.target.value)) document.execCommand('backColor',false,e.target.value);
    saveEditorSelection(); preview();
  });
  $('bcAddLink')?.addEventListener('click',()=>{
    restoreEditorSelection();
    const raw=window.prompt('Enter website, email or telephone link:','https://');
    if(raw===null) return;
    const url=safeLink(raw);
    if(!url) return toast('Enter a valid link.');
    const sel=window.getSelection();
    if(sel && !sel.isCollapsed) document.execCommand('createLink',false,url);
    else document.execCommand('insertHTML',false,`<a href="${esc(url)}">${esc(raw.trim()||url)}</a>`);
    saveEditorSelection(); preview();
  });
  document.querySelectorAll('.bc-tokens [data-token]').forEach(b=>b.addEventListener('click',()=>insertEditorText(b.dataset.token)));
  $('bcEmoji').addEventListener('click',()=> $('bcEmojiBox').classList.toggle('hidden'));
  $('bcEmojiBox').addEventListener('click',e=>{
    const b=e.target.closest('[data-emoji]'); if(!b) return;
    insertEditorText(b.dataset.emoji); $('bcEmojiBox').classList.add('hidden');
  });
  ['bcName','bcEditor','bcWebsite','bcPhone','bcCtaText','bcCtaMode','bcSubject'].forEach(id=>$(id)?.addEventListener('input',preview));
  $('bcSendMode').addEventListener('change',()=> $('bcScheduleWrap').classList.toggle('hidden',$('bcSendMode').value!=='SCHEDULE'));
  $('bcTemplate').addEventListener('change',()=>{media={type:'',url:'',name:'',file:null,upload_url:''};$('bcMediaSelected').classList.add('hidden');updateTemplateInfo();});
  $('bcTemplateRefresh').addEventListener('click',()=>loadWhatsappTemplates().then(()=>toast('Approved templates refreshed.')).catch(e=>toast(e.message)));

  $('bcPaste').onclick=()=>{ loadEntryGrid(); $('bcBulkPaste').classList.add('hidden'); $('bcPasteText').value=''; $('bcPasteModal').classList.remove('hidden'); };
  document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>b.closest('.bc-modal').classList.add('hidden'));
  $('bcAddRow').onclick=()=>addEntryRows(1);
  $('bcAdd10').onclick=()=>addEntryRows(10);
  $('bcEntryClear').onclick=()=>{ $('bcEntryRows').innerHTML=''; addEntryRows(5); $('bcPasteText').value=''; };
  $('bcToggleBulk').onclick=()=>{ $('bcBulkPaste').classList.toggle('hidden'); if(!$('bcBulkPaste').classList.contains('hidden')) $('bcPasteText').focus(); };
  $('bcPasteApply').onclick=()=>{
    const rows=parseBulkRecipients($('bcPasteText').value);
    if(!rows.length) return toast('Paste at least one receiver row.');
    const existing=collectEntryRows();
    $('bcEntryRows').innerHTML='';
    if(existing.length) addEntryRows(0,existing);
    addEntryRows(0,rows);
    $('bcPasteText').value='';
    toast(`${rows.length} pasted receiver row(s) added.`);
  };
  $('bcPasteImport').onclick=async()=>{
    const rows=collectEntryRows();
    if(!rows.length) return toast('Enter at least one receiver.');
    const j=await api('/api/validate',{method:'POST',body:JSON.stringify({channel,recipients:rows})});
    recipients=rows;
    validated=j.data.rows;
    render(j.data.counts);
    $('bcPasteModal').classList.add('hidden');
    const c=j.data.counts;
    toast(`Imported ${c.total} receiver(s): ${c.valid} valid, ${c.invalid} invalid, ${c.duplicate} duplicate.`);
  };
  $('bcCsv').onchange=async e=>{
    const input=e.target;
    const f=input.files[0];
    if(!f)return;
    try{
      const fd=new FormData();fd.append('file',f);
      const j=await api('/api/import-csv',{method:'POST',body:fd});
      recipients=j.rows||[];
      const d=await validate();
      toast(`CSV imported: ${d.counts.valid} valid, ${d.counts.invalid} invalid, ${d.counts.duplicate} duplicate.`);
    }catch(err){
      toast(err?.message||'CSV import failed.');
    }finally{
      input.value='';
    }
  };
  $('bcClear').onclick=()=>{recipients=[];validated=[];render();toast('Receiver list cleared.');};

  function chooseFile(input,type){
    input.onchange=()=>{const f=input.files[0];if(!f)return;if(media.url?.startsWith('blob:'))URL.revokeObjectURL(media.url);media={type,url:URL.createObjectURL(f),name:f.name,file:f,upload_url:''};$('bcMediaSelected').classList.remove('hidden');const note=(channel==='EMAIL'&&['image','gif'].includes(type))?'will be embedded in outgoing Email':'preview selected';$('bcMediaSelected').innerHTML=`<b>${esc(f.name)}</b><br><small>${esc(type.toUpperCase())} — ${note}</small>`;updateMediaRules();preview();};
  }
  chooseFile($('bcImage'),'image'); chooseFile($('bcVideo'),'video'); chooseFile($('bcGif'),'gif');
  $('bcMediaUrl').onclick=()=> $('bcUrlModal').classList.remove('hidden');
  $('bcUrlAdd').onclick=()=>{const url=$('bcUrlValue').value.trim(); if(!url.startsWith('https://'))return toast('Media URL must start with https://'); media={type:'url',url,name:$('bcUrlTitle').value.trim()||'Media',file:null,upload_url:''};$('bcUrlModal').classList.add('hidden');$('bcMediaSelected').classList.remove('hidden');$('bcMediaSelected').innerHTML=`<b>${esc(media.name)}</b><br><small>${esc(media.url)}</small>`;updateMediaRules();preview();};

  $('bcValidate').onclick=()=>validate().then(d=>toast(`Validation passed: ${d.counts.valid} valid receiver(s).`)).catch(e=>toast(e.message));
  $('bcSaveDraft').onclick=()=>create('DRAFT').then(id=>toast('Draft saved. Campaign #'+id)).catch(e=>toast(e.message));
  $('bcTest').onclick=()=>{if(channel==='WHATSAPP'&&!selectedTemplate())return toast('Select an approved WhatsApp template first.');if(channel!=='WHATSAPP'&&!text())return toast('Enter a message first.');$('chkTest').textContent='Passed';toast('Test checkpoint marked passed.');};
  $('bcBroadcast').onclick=()=>validate().then(d=>{if(!d.counts.valid)throw new Error('No valid receivers.');const t=selectedTemplate();if(channel==='WHATSAPP'&&!t)throw new Error('Select an approved WhatsApp template.');$('bcReviewText').innerHTML=`<p><b>${esc($('bcName').value)}</b></p><p>${d.counts.valid} valid receiver(s) via ${channel}.</p>${channel==='WHATSAPP'?`<p>Template: <b>${esc(t.template_name)}</b> (${esc(t.language_code)})</p>`:''}<p>Estimated cost: ${esc($('bcCost').textContent)}</p>`;$('bcReviewModal').classList.remove('hidden');}).catch(e=>toast(e.message));
  $('bcConfirm').onclick=()=>{ $('bcReviewModal').classList.add('hidden'); create('NOW').then(dispatch).catch(e=>toast(e.message)); };
  $('bcRetry').onclick=async()=>{if(!campaignId)return toast('No active campaign.');const j=await api(`/api/campaigns/${campaignId}/retry`,{method:'POST',body:'{}'});toast(`${j.retry_count} failed receiver(s) queued for retry.`);};

  $('bcNew').onclick=()=>{location.href=location.pathname;};
  $('bcEditor').innerHTML='Hello {{first_name}} 👋<br><br>Cadupius Healthassist supports international patients by coordinating communication and healthcare-related arrangements with hospitals and healthcare providers in India.<br><br>For more information or assistance, please use the contact details below.<br><br>Regards,<br>Cadupius Healthassist';
  loadWhatsappTemplates()
    .then(()=>loadEmailTemplateFromQuery())
    .catch(e=>toast(e.message));
  applyChannelUi(); updateMediaRules(); preview(); render();
})();

/* TERIQEN Broadcast V1.3.2 — delivery result popup */
(() => {
  'use strict';
  if (window.__heBroadcastDeliveryPopupV132) return;
  window.__heBroadcastDeliveryPopupV132 = true;

  const seen = new Map();

  function number(value) {
    const n = Number(value || 0);
    return Number.isFinite(n) ? Math.max(0, Math.trunc(n)) : 0;
  }

  function ensureStyles() {
    if (document.getElementById('heBroadcastResultStyleV132')) return;
    const style = document.createElement('style');
    style.id = 'heBroadcastResultStyleV132';
    style.textContent = `
      .he-br-result-backdrop{position:fixed;inset:0;z-index:2147483000;background:rgba(15,23,42,.42);display:flex;align-items:center;justify-content:center;padding:24px}
      .he-br-result-card{width:min(520px,calc(100vw - 32px));background:#fff;border-radius:16px;box-shadow:0 24px 70px rgba(15,23,42,.30);overflow:hidden;font-family:Arial,sans-serif}
      .he-br-result-head{padding:22px 24px 18px;border-bottom:1px solid #e5e7eb;display:flex;align-items:flex-start;gap:14px}
      .he-br-result-icon{width:42px;height:42px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:700;flex:0 0 auto}
      .he-br-result-icon.ok{background:#dcfce7;color:#15803d}.he-br-result-icon.warn{background:#fef3c7;color:#b45309}
      .he-br-result-title{font-size:20px;font-weight:700;color:#0f172a;margin:0 0 4px}.he-br-result-sub{font-size:13px;color:#64748b;margin:0}
      .he-br-result-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:20px 24px}
      .he-br-result-stat{border:1px solid #e2e8f0;border-radius:12px;padding:14px 10px;text-align:center;background:#f8fafc}
      .he-br-result-num{font-size:24px;line-height:1;font-weight:800;color:#0f172a}.he-br-result-label{margin-top:7px;font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.04em}
      .he-br-result-actions{padding:0 24px 22px;display:flex;justify-content:flex-end}.he-br-result-btn{border:0;border-radius:9px;background:#1976b9;color:#fff;font-weight:700;padding:10px 22px;cursor:pointer}
      @media(max-width:520px){.he-br-result-grid{grid-template-columns:repeat(2,1fr)}.he-br-result-card{border-radius:12px}}
    `;
    document.head.appendChild(style);
  }

  function show(summary) {
    if (!summary || !summary.complete) return;
    const campaignId = number(summary.campaign_id);
    const success = number(summary.success);
    const failed = number(summary.failed);
    const skipped = number(summary.skipped);
    const valid = number(summary.valid);
    const total = number(summary.total) || valid;
    const channel = String(summary.channel || 'Broadcast').toUpperCase();
    const signature = `${success}|${failed}|${skipped}|${String(summary.status || '')}`;
    if (campaignId && seen.get(campaignId) === signature) return;
    if (campaignId) seen.set(campaignId, signature);

    ensureStyles();
    const existing = document.getElementById('heBroadcastResultBackdropV132');
    if (existing) existing.remove();

    const backdrop = document.createElement('div');
    backdrop.className = 'he-br-result-backdrop';
    backdrop.id = 'heBroadcastResultBackdropV132';
    const clean = failed === 0;
    const label = channel === 'EMAIL' ? 'Email Broadcast' : `${channel.charAt(0) + channel.slice(1).toLowerCase()} Broadcast`;
    backdrop.innerHTML = `
      <div class="he-br-result-card" role="dialog" aria-modal="true" aria-labelledby="heBrResultTitleV132">
        <div class="he-br-result-head">
          <div class="he-br-result-icon ${clean ? 'ok' : 'warn'}">${clean ? '✓' : '!'}</div>
          <div>
            <div class="he-br-result-title" id="heBrResultTitleV132">${label} Completed</div>
            <p class="he-br-result-sub">Delivery processing has finished for this broadcast.</p>
          </div>
        </div>
        <div class="he-br-result-grid">
          <div class="he-br-result-stat"><div class="he-br-result-num">${total}</div><div class="he-br-result-label">Total</div></div>
          <div class="he-br-result-stat"><div class="he-br-result-num">${success}</div><div class="he-br-result-label">Success</div></div>
          <div class="he-br-result-stat"><div class="he-br-result-num">${failed}</div><div class="he-br-result-label">Failed</div></div>
          <div class="he-br-result-stat"><div class="he-br-result-num">${skipped}</div><div class="he-br-result-label">Skipped</div></div>
        </div>
        <div class="he-br-result-actions"><button type="button" class="he-br-result-btn" id="heBrResultOkV132">OK</button></div>
      </div>`;
    document.body.appendChild(backdrop);
    const close = () => backdrop.remove();
    backdrop.querySelector('#heBrResultOkV132')?.addEventListener('click', close);
    backdrop.addEventListener('click', (event) => { if (event.target === backdrop) close(); });
  }

  const originalFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    const isDispatch = method === 'POST' && /\/broadcast-management\/api\/campaigns\/\d+\/dispatch(?:[?#]|$)/i.test(url);
    const isStatus = method === 'GET' && /\/broadcast-management\/api\/campaigns\/\d+\/status(?:[?#]|$)/i.test(url);
    return originalFetch(input, init).then((response) => {
      if ((isDispatch || isStatus) && response.ok) {
        response.clone().json().then((payload) => {
          if (payload && payload.success && payload.summary) show(payload.summary);
        }).catch(() => {});
      }
      return response;
    });
  };
})();
