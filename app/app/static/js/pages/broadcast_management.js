(() => {
  const $ = id => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  let channel = 'WHATSAPP';
  let recipients = [];
  let validated = [];
  let campaignId = null;
  let media = {type:'', url:'', name:'', file:null};
  let whatsappTemplates = [];
  let emailTemplates = [];

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
    if(channel==='EMAIL') return {image:'yes',video:'limited',gif:'yes',url:'yes',note:'Email: image/GIF supported. Video is best delivered as a thumbnail/link.'};
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
  function cta(){
    if(channel==='WHATSAPP') return '';
    const m=$('bcCtaMode').value, lines=['', $('bcCtaText').value || 'For more information or assistance:'];
    if(m==='both'||m==='web') lines.push('🌐 '+$('bcWebsite').value);
    if(m==='both'||m==='phone') lines.push('📞 Call / WhatsApp: '+$('bcPhone').value);
    return lines.join('\n');
  }
  function sampleValue(source){
    const map={recipient_name:'Rahim Ahmed',first_name:'Rahim',reference_no:'HE-1048',country_name:'Bangladesh',language_code:'bn',recipient_tag:'Patient',campaign_name:$('bcName').value.trim()||'Healthcare Awareness',website_url:$('bcWebsite').value||'https://cadupiushealthassist.com',contact_number:$('bcPhone').value||'+91 XXXXX XXXXX'};
    return map[source] || 'Sample';
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
    let s='';
    if(channel==='WHATSAPP') s=templatePreviewText(selectedTemplate());
    else s=text().replaceAll('{{first_name}}','Rahim').replaceAll('{{country}}','Bangladesh').replaceAll('{{reference_no}}','HE-1048') + cta();
    $('bcPreview').textContent=s;
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
    $('bcEditor').innerHTML = t.body_html || esc(t.body_text || '');
    preview();
    toast('Email template loaded into Broadcast Centre.');
  }

  async function validate(){
    const j=await api('/api/validate',{method:'POST',body:JSON.stringify({channel,recipients})});
    validated=j.data.rows; render(j.data.counts); return j.data;
  }
  function render(counts){
    const body=$('bcRecipients');
    body.innerHTML=validated.length?validated.map(r=>`<tr><td>${esc(r.name)}</td><td>${esc(r.mobile)}</td><td>${esc(r.email)}</td><td>${esc(r.country)}</td><td>${esc(r.language)}</td><td>${esc(r.validation_status)}</td></tr>`).join(''):'<tr><td colspan="6" class="bc-empty">No receiver list loaded.</td></tr>';
    counts=counts||{total:0,valid:0,duplicate:0,invalid:0};
    $('bcTotal').textContent=counts.total;$('bcValid').textContent=counts.valid;$('bcDup').textContent=counts.duplicate;$('bcInvalid').textContent=counts.invalid;
    $('chkReceivers').textContent=counts.valid?'Ready':'Pending'; updateCost();
  }
  function payload(sendMode){
    const t=selectedTemplate();
    const waMediaType=channel==='WHATSAPP'&&t?String(t.header_type||'').toLowerCase():null;
    return {
      campaign_name:$('bcName').value.trim(),campaign_type:$('bcType').value,email_mode:channel==='EMAIL'?$('bcEmailMode').value:'RICH',channel,
      template_id:channel==='WHATSAPP'?(t?.id||null):null,
      email_subject:$('bcSubject').value,message_html:$('bcEditor').innerHTML,
      message_text:channel==='WHATSAPP'?(t?.body_text||''):(text()+cta()),
      language:channel==='WHATSAPP'?(t?.language_code||''): 'English',website_url:$('bcWebsite').value,contact_number:$('bcPhone').value,
      cta_text:$('bcCtaText').value,cta_mode:$('bcCtaMode').value,
      media_type:channel==='WHATSAPP'?(templateRequiresMedia(t)?waMediaType:null):(media.type||null),
      media_url:media.type==='url'?media.url:null,media_name:media.name||null,
      send_mode:sendMode||$('bcSendMode').value,scheduled_on:$('bcSchedule').value||null,recipients
    };
  }
  async function create(sendMode){
    await validate();
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
    const wa=channel==='WHATSAPP';
    $('bcWhatsappTemplateWrap').classList.toggle('hidden',!wa);
    $('bcFreeformComposer').classList.toggle('hidden',wa);
    $('bcSubjectWrap').classList.toggle('hidden',channel!=='EMAIL');
    $('bcCtaSection').classList.toggle('hidden',wa);
    $('bcWhatsappCtaNote').classList.toggle('hidden',!wa);
    $('chkTemplateRow').classList.toggle('hidden',!wa);
    if(wa) updateTemplateInfo(); else {updateMediaRules();preview();}
  }

  document.querySelectorAll('.bc-channel').forEach(b=>b.addEventListener('click',async()=>{
    document.querySelectorAll('.bc-channel').forEach(x=>x.classList.remove('active')); b.classList.add('active'); channel=b.dataset.channel;
    applyChannelUi(); if(recipients.length) await validate(); preview();
  }));
  document.querySelectorAll('.bc-toolbar [data-cmd]').forEach(b=>b.addEventListener('click',()=>{document.execCommand(b.dataset.cmd,false,null);$('bcEditor').focus();preview();}));
  document.querySelectorAll('.bc-tokens [data-token]').forEach(b=>b.addEventListener('click',()=>{document.execCommand('insertText',false,b.dataset.token);preview();}));
  $('bcEmoji').addEventListener('click',()=> $('bcEmojiBox').classList.toggle('hidden'));
  $('bcEmojiBox').style.cursor='pointer';
  $('bcEmojiBox').addEventListener('mouseup',()=>{const sel=window.getSelection(); const word=sel?.toString()?.trim(); if(word){document.execCommand('insertText',false,word);preview();}});
  ['bcName','bcEditor','bcWebsite','bcPhone','bcCtaText','bcCtaMode','bcSubject'].forEach(id=>$(id)?.addEventListener('input',preview));
  $('bcSendMode').addEventListener('change',()=> $('bcScheduleWrap').classList.toggle('hidden',$('bcSendMode').value!=='SCHEDULE'));
  $('bcTemplate').addEventListener('change',()=>{media={type:'',url:'',name:'',file:null};$('bcMediaSelected').classList.add('hidden');updateTemplateInfo();});
  $('bcTemplateRefresh').addEventListener('click',()=>loadWhatsappTemplates().then(()=>toast('Approved templates refreshed.')).catch(e=>toast(e.message)));

  $('bcPaste').onclick=()=> $('bcPasteModal').classList.remove('hidden');
  document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>b.closest('.bc-modal').classList.add('hidden'));
  $('bcPasteImport').onclick=async()=>{
    recipients=$('bcPasteText').value.trim().split(/\n+/).filter(Boolean).map(line=>{const [name='',mobile='',email='',country='',language='English',reference_no='',tag='',telegram_chat_id='']=line.split(',').map(x=>x.trim());return{name,mobile,email,country,language,reference_no,tag,telegram_chat_id};});
    $('bcPasteModal').classList.add('hidden'); await validate(); toast('Receiver list imported.');
  };
  $('bcCsv').onchange=async e=>{
    const f=e.target.files[0];if(!f)return;const fd=new FormData();fd.append('file',f);
    const j=await api('/api/import-csv',{method:'POST',body:fd});recipients=j.rows;await validate();toast('CSV imported.');
  };
  $('bcClear').onclick=()=>{recipients=[];validated=[];render();toast('Receiver list cleared.');};

  function chooseFile(input,type){
    input.onchange=()=>{const f=input.files[0];if(!f)return;if(media.url?.startsWith('blob:'))URL.revokeObjectURL(media.url);media={type,url:URL.createObjectURL(f),name:f.name,file:f};$('bcMediaSelected').classList.remove('hidden');$('bcMediaSelected').innerHTML=`<b>${esc(f.name)}</b><br><small>${esc(type.toUpperCase())} — preview only until provider upload is configured</small>`;updateMediaRules();preview();};
  }
  chooseFile($('bcImage'),'image'); chooseFile($('bcVideo'),'video'); chooseFile($('bcGif'),'gif');
  $('bcMediaUrl').onclick=()=> $('bcUrlModal').classList.remove('hidden');
  $('bcUrlAdd').onclick=()=>{const url=$('bcUrlValue').value.trim(); if(!url.startsWith('https://'))return toast('Media URL must start with https://'); media={type:'url',url,name:$('bcUrlTitle').value.trim()||'Media',file:null};$('bcUrlModal').classList.add('hidden');$('bcMediaSelected').classList.remove('hidden');$('bcMediaSelected').innerHTML=`<b>${esc(media.name)}</b><br><small>${esc(media.url)}</small>`;updateMediaRules();preview();};

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
