const searchData = JSON.parse(document.getElementById("patients-json").textContent || "[]");
const hospitalsData = JSON.parse(document.getElementById("hospitals-json").textContent || "[]");
const agentsData = JSON.parse(document.getElementById("agents-json").textContent || "[]");
const ivacsData = JSON.parse(document.getElementById("ivacs-json").textContent || "[]");
const flashMessages = JSON.parse(document.getElementById("flash-messages-json").textContent || "[]");

function showFlashPopups(){
  if(!Array.isArray(flashMessages) || flashMessages.length === 0) return;

  const messages = flashMessages
    .map(item => Array.isArray(item) ? String(item[1] || "").trim() : "")
    .filter(Boolean);

  if(messages.length === 0) return;

  setTimeout(() => {
    alert(messages.join("\n\n"));
  }, 100);
}

function normShortName(x){
  if(x == null) return "";
  if(typeof x === "string") return x.trim();
  if(typeof x === "object") return String(x.short_name || x.name || x.value || "").trim();
  return String(x).trim();
}
function normIvacCentre(x){
  if(x == null) return "";
  if(typeof x === "string") return x.trim();
  if(typeof x === "object") return String(x.ivac_centre || x.centre || x.value || "").trim();
  return String(x).trim();
}

const hospitalCityMap = new Map();
(hospitalsData || []).forEach(h => {
  const name = String(h.short_name || "").trim();
  const city = String(h.partner_city || "").trim();
  if(name) hospitalCityMap.set(name, city);
});

function initHospitalLov(){
  const hospitalSel = document.getElementById("hospital");
  const agentSel = document.getElementById("agent");
  const ivacSel = document.getElementById("ivac");

  hospitalSel.innerHTML = `<option value="">Select Hospital</option>`;
  (hospitalsData || []).forEach(h => {
    const name = String(h.short_name || "").trim();
    if(!name) return;
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    hospitalSel.appendChild(opt);
  });

  agentSel.innerHTML = `<option value="">Select Agent</option>`;
  (agentsData || []).forEach(a => {
    const name = normShortName(a);
    if(!name) return;
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    agentSel.appendChild(opt);
  });

  ivacSel.innerHTML = `<option value="">Select IVAC</option>`;
  (ivacsData || []).forEach(v => {
    const centre = normIvacCentre(v);
    if(!centre) return;
    const opt = document.createElement("option");
    opt.value = centre;
    opt.textContent = centre;
    ivacSel.appendChild(opt);
  });
}

async function populateCity(){
  const hosp = document.getElementById("hospital").value || "";
  const input = document.getElementById("hospitalCity");
  if(!hosp){ input.value = ""; return; }

  const fromMap = hospitalCityMap.get(hosp);
  if(fromMap){
    input.value = fromMap;
    return;
  }

  try{
    const res = await fetch(`/api/hospital-city?hospital=${encodeURIComponent(hosp)}`);
    const data = await res.json();
    input.value = (data && data.partner_city) ? String(data.partner_city) : "";
  }catch(e){
    input.value = "";
  }
}

async function loadHospitalDepts(){
  const hosp = document.getElementById("hospital").value || "";
  const sel = document.getElementById("hospitalDept");
  sel.innerHTML = "";

  if(!hosp) return;

  try{
    const res = await fetch(`/api/hospital-depts?hospital=${encodeURIComponent(hosp)}`);
    const rows = await res.json();
    if(!Array.isArray(rows)) return;

    rows.forEach(v => {
      const opt = document.createElement("option");
      opt.value = String(v || "").trim();
      opt.textContent = String(v || "").trim();
      sel.appendChild(opt);
    });
  }catch(e){
    sel.innerHTML = "";
  }
}

let step = 1;
let lead = { patient: null, attenders: [], hospital: {} };

let patientPage = 1, patientPageSize = 10, patientFiltered = [];
let attPage = 1, attPageSize = 10, attFiltered = [];

const MAX_DOC_BYTES = 10 * 1024 * 1024;
let selectedDocs = [];

function bytesToHuman(bytes){
  const b = Number(bytes || 0);
  if(b < 1024) return `${b} B`;
  const kb = b / 1024;
  if(kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if(mb < 1024) return `${mb.toFixed(2)} MB`;
  const gb = mb / 1024;
  return `${gb.toFixed(2)} GB`;
}

function syncDocInput(){
  const dt = new DataTransfer();
  selectedDocs.forEach(f => dt.items.add(f));
  document.getElementById("docFiles").files = dt.files;
}

function renderDocs(){
  const total = selectedDocs.reduce((s,f)=>s + (f.size||0), 0);
  document.getElementById("docTotalText").textContent = bytesToHuman(total);
  document.getElementById("docCountText").textContent = String(selectedDocs.length);

  const badge = document.getElementById("docTotalBadge");
  badge.classList.remove("danger");
  if(total > MAX_DOC_BYTES) badge.classList.add("danger");

  const body = document.getElementById("docListBody");
  const hint = document.getElementById("docHint");

  body.innerHTML = "";
  if(selectedDocs.length === 0){
    hint.textContent = "No documents selected";
    return;
  }
  hint.textContent = "";

  selectedDocs.forEach((f, idx) => {
    const row = document.createElement("div");
    row.className = "doc-row";
    row.innerHTML = `
      <div class="name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</div>
      <div class="right">
        <span>${bytesToHuman(f.size||0)}</span>
        <button type="button" class="icon-btn danger" onclick="removeDoc(${idx})">
          <i class="fa-solid fa-trash"></i> Remove
        </button>
      </div>
    `;
    body.appendChild(row);
  });
}

function removeDoc(idx){
  selectedDocs.splice(idx, 1);
  syncDocInput();
  renderDocs();
}

function clearAllDocs(){
  selectedDocs = [];
  syncDocInput();
  renderDocs();
}

document.getElementById("docFiles").addEventListener("change", function(){
  const incoming = Array.from(this.files || []);
  if(incoming.length === 0) return;

  const key = f => `${f.name}__${f.size}__${f.lastModified}`;
  const existingKeys = new Set(selectedDocs.map(key));
  const toAdd = incoming.filter(f => !existingKeys.has(key(f)));

  const currentTotal = selectedDocs.reduce((s,f)=>s + (f.size||0), 0);
  const addTotal = toAdd.reduce((s,f)=>s + (f.size||0), 0);

  if(currentTotal + addTotal > MAX_DOC_BYTES){
    alert("Total upload size exceeds 10MB. Please remove some files or choose smaller files.");
    syncDocInput();
    renderDocs();
    return;
  }

  selectedDocs = selectedDocs.concat(toAdd);
  syncDocInput();
  renderDocs();
});

function calculateAge(dob){
  if(!dob) return "";
  const d = new Date(dob);
  if(String(d) === "Invalid Date") return "";
  const today = new Date();
  let age = today.getFullYear() - d.getFullYear();
  const m = today.getMonth() - d.getMonth();
  if(m < 0 || (m === 0 && today.getDate() < d.getDate())) age--;
  return age < 0 ? "" : String(age);
}
function val(id){ return (document.getElementById(id).value || "").trim(); }
function escapeHtml(str){
  return String(str||"")
    .replaceAll("&","&amp;").replaceAll("<","&lt;")
    .replaceAll(">","&gt;").replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}
function validateAttenderRelationships(showPopup=true){
  for(let i=0; i<lead.attenders.length; i++){
    const rel = String(lead.attenders[i].relationship || "").trim();
    if(!rel){
      if(showPopup){
        alert(`Relationship is mandatory for Attender ${i+1}. Please edit and add the relationship.`);
      }
      return false;
    }
  }
  return true;
}

function scrollToDetails(){ document.getElementById("patientDetailsBox").scrollIntoView({behavior:"smooth", block:"start"}); }
function scrollToGrid(){ document.getElementById("patientGridBox").scrollIntoView({behavior:"smooth", block:"start"}); }

function getExcludedPassportsForPatientGrid(){
  const s = new Set();
  (lead.attenders || []).forEach(a => { if(a && a.passport) s.add(String(a.passport)); });
  return s;
}
function getExcludedPassportsForAttenderGrid(){
  const s = new Set();
  if(lead.patient && lead.patient.passport) s.add(String(lead.patient.passport));
  (lead.attenders || []).forEach(a => { if(a && a.passport) s.add(String(a.passport)); });
  return s;
}

function setStep(n){
  step = n;
  document.getElementById("panel1").style.display = (step===1) ? "block" : "none";
  document.getElementById("panel2").style.display = (step===2) ? "block" : "none";
  document.getElementById("panel3").style.display = (step===3) ? "block" : "none";

  const s1 = document.getElementById("step1");
  const s2 = document.getElementById("step2");
  const s3 = document.getElementById("step3");
  [s1,s2,s3].forEach(s=>s.classList.remove("active","done"));

  if(step===1){ s1.classList.add("active"); }
  if(step===2){ s1.classList.add("done"); s2.classList.add("active"); }
  if(step===3){ s1.classList.add("done"); s2.classList.add("done"); s3.classList.add("active"); }

  document.getElementById("submitBtn").style.display = (step===3) ? "inline-flex" : "none";
  document.getElementById("nextBtn").style.display = (step===3) ? "none" : "inline-flex";

  document.getElementById("statusText").textContent =
    step===1 ? "Step 1 of 3 • Patient" :
    step===2 ? "Step 2 of 3 • Attenders (Optional)" :
               "Step 3 of 3 • Hospital + Documents";
}

function goBack(){
  if(step<=1) return;
  setStep(step-1);
}

function goNext(){
  if(step===1){
    const p = readPatient();
    if(!p.passport || !p.given){
      alert("Please enter Patient Passport and Given Name.");
      return;
    }
    if(lead.attenders.some(a => String(a.passport) === String(p.passport))){
      alert("This passport is already selected as an Attender. Remove it from Attenders first.");
      return;
    }
    lead.patient = p;
  }

  if(step===2){
    if(!validateAttenderRelationships(true)) return;
  }

  if(step===3) return;
  setStep(step+1);
  refreshChips();
  renderPatientGrid();
}

function readPatient(){
  const dob = document.getElementById("patient_dob").value;
  return {
    passport: val("patient_passport"),
    given: val("patient_given"),
    sur: val("patient_sur"),
    dob,
    passport_expiry: (document.getElementById("patient_passport_expiry").value || ""),
    age: calculateAge(dob),
    gender: val("patient_gender"),
    country: val("patient_country"),
    contact: val("patient_contact"),
    email: val("patient_email"),
    address: val("patient_address")
  };
}
document.getElementById("patient_dob").addEventListener("change", function(){
  document.getElementById("patient_age").value = calculateAge(this.value);
});

function clearPatientForm(){
  ["passport","given","sur","dob","age","country","contact","email","address"].forEach(k=>{
    const el = document.getElementById("patient_"+k);
    if(el) el.value = "";
  });
  const pe = document.getElementById("patient_passport_expiry");
  if(pe) pe.value = "";
  document.getElementById("patient_gender").value = "Male";

  lead.patient = null;
  renderPatientGrid();
}

function setPatientPageSize(size){ patientPageSize = parseInt(size,10) || 10; patientPage = 1; renderPatientGrid(); }
function patientPrevPage(){ if(patientPage>1){ patientPage--; renderPatientGrid(); } }
function patientNextPage(){
  const totalPages = Math.max(1, Math.ceil(patientFiltered.length / patientPageSize));
  if(patientPage<totalPages){ patientPage++; renderPatientGrid(); }
}

function renderPatientGrid(){
  const q = (document.getElementById("patientSearch").value || "").toLowerCase();
  const excluded = getExcludedPassportsForPatientGrid();

  patientFiltered = (searchData || []).filter(d => {
    const pno = String(d.passport||"");
    if(excluded.has(pno)) return false;
    return (
      pno.toLowerCase().includes(q) ||
      String(d.given||"").toLowerCase().includes(q) ||
      String(d.sur||"").toLowerCase().includes(q) ||
      String(d.contact||"").toLowerCase().includes(q) ||
      String(d.email||"").toLowerCase().includes(q) ||
      String(d.address||"").toLowerCase().includes(q)
    );
  });

  const total = patientFiltered.length;
  const totalPages = Math.max(1, Math.ceil(total / patientPageSize));
  if(patientPage > totalPages) patientPage = totalPages;

  const start = (patientPage-1)*patientPageSize;
  const end = start + patientPageSize;
  const pageRows = patientFiltered.slice(start, end);

  const tbody = document.getElementById("patientTable");
  tbody.innerHTML = "";

  if(pageRows.length === 0){
    tbody.innerHTML = `<tr><td colspan="10" style="padding:16px;color:#6b7a90;font-weight:900;">No patients found.</td></tr>`;
  }else{
    pageRows.forEach(d=>{
      const pno = String(d.passport||"");
      const exp = d.passport_expiry || "";
      tbody.innerHTML += `
        <tr>
          <td><span class="link" onclick="pickPatient('${escapeHtml(pno)}')">${escapeHtml(pno)}</span></td>
          <td>${escapeHtml(d.given||"")}</td>
          <td>${escapeHtml(d.sur||"")}</td>
          <td>${escapeHtml(d.dob||"")}</td>
          <td>${escapeHtml(d.country||"")}</td>
          <td>${escapeHtml(d.gender||"")}</td>
          <td>${escapeHtml(exp)}</td>
          <td>${escapeHtml(d.contact||"")}</td>
          <td>${escapeHtml(d.email||"")}</td>
          <td title="${escapeHtml(d.address||"")}">${escapeHtml(d.address||"")}</td>
        </tr>`;
    });
  }

  document.getElementById("patientPrev").disabled = (patientPage<=1);
  document.getElementById("patientNext").disabled = (patientPage>=totalPages);

  const shownFrom = total ? (start+1) : 0;
  const shownTo = Math.min(end, total);
  document.getElementById("patientMeta").textContent =
    `Page ${patientPage}/${totalPages} • Showing ${shownFrom}-${shownTo} of ${total}`;
}
document.getElementById("patientSearch").addEventListener("input", ()=>{ patientPage = 1; renderPatientGrid(); });

function pickPatient(passport){
  const pno = String(passport || "");
  if(lead.attenders.some(a => String(a.passport) === pno)){
    alert("This passport is already selected as an Attender. Remove it from Attenders first.");
    return;
  }

  const r = (searchData || []).find(x=>String(x.passport||"")===pno);
  if(!r) return;

  document.getElementById("patient_passport").value = r.passport || "";
  document.getElementById("patient_given").value = r.given || "";
  document.getElementById("patient_sur").value = r.sur || "";
  document.getElementById("patient_dob").value = r.dob || "";
  document.getElementById("patient_age").value = calculateAge(r.dob || "");
  document.getElementById("patient_gender").value = r.gender || "Male";
  document.getElementById("patient_country").value = r.country || "";
  document.getElementById("patient_passport_expiry").value = r.passport_expiry || "";
  document.getElementById("patient_contact").value = r.contact || "";
  document.getElementById("patient_email").value = r.email || "";
  document.getElementById("patient_address").value = r.address || "";

  lead.patient = readPatient();
  scrollToDetails();
}

function refreshChips(){
  const wrap = document.getElementById("attenderChips");
  wrap.innerHTML = "";
  if(lead.attenders.length===0){
    wrap.innerHTML = `<div class="muted" style="font-weight:900;">No attenders added.</div>`;
    return;
  }
  lead.attenders.forEach((a,idx)=>{
    wrap.innerHTML += `
      <span class="chip">
        Attender ${idx+1}: ${escapeHtml(a.given)} (${escapeHtml(a.passport)}) • ${escapeHtml(a.relationship || "—")}
        <span class="x" title="Remove" onclick="removeAttender(${idx})"><i class="fa-solid fa-xmark"></i></span>
      </span>`;
  });
}
function removeAttender(i){
  lead.attenders.splice(i,1);
  refreshChips();
  syncWorkflowHiddenFields();
  renderPatientGrid();
}
function removeAllAttenders(){
  lead.attenders=[];
  refreshChips();
  syncWorkflowHiddenFields();
  renderPatientGrid();
}

function openAttenderModal(){
  if(!lead.patient && !val("patient_passport")){
    alert("Please complete Patient (Step 1) first.");
    return;
  }
  if(lead.attenders.length>=3){
    alert("Maximum 3 attenders allowed.");
    return;
  }
  lead.patient = readPatient();
  document.getElementById("modalOverlay").style.display = "flex";
  attPage = 1;
  renderAttGrid();
}
function closeAttenderModal(e){
  if(e && e.target !== document.getElementById("modalOverlay")) return;
  document.getElementById("modalOverlay").style.display = "none";
}

function setAttPageSize(size){ attPageSize = parseInt(size,10) || 10; attPage=1; renderAttGrid(); }
function attPrevPage(){ if(attPage>1){ attPage--; renderAttGrid(); } }
function attNextPage(){
  const totalPages = Math.max(1, Math.ceil(attFiltered.length / attPageSize));
  if(attPage<totalPages){ attPage++; renderAttGrid(); }
}
document.getElementById("attSearch").addEventListener("input", ()=>{ attPage=1; renderAttGrid(); });

function renderAttGrid(){
  const q = (document.getElementById("attSearch").value || "").toLowerCase();
  const excluded = getExcludedPassportsForAttenderGrid();

  attFiltered = (searchData || []).filter(d => {
    const pno = String(d.passport||"");
    if(excluded.has(pno)) return false;
    return (
      pno.toLowerCase().includes(q) ||
      String(d.given||"").toLowerCase().includes(q) ||
      String(d.sur||"").toLowerCase().includes(q) ||
      String(d.contact||"").toLowerCase().includes(q) ||
      String(d.email||"").toLowerCase().includes(q) ||
      String(d.address||"").toLowerCase().includes(q)
    );
  });

  const total = attFiltered.length;
  const totalPages = Math.max(1, Math.ceil(total / attPageSize));
  if(attPage > totalPages) attPage = totalPages;

  const start = (attPage-1)*attPageSize;
  const end = start + attPageSize;
  const pageRows = attFiltered.slice(start, end);

  const tbody = document.getElementById("attTable");
  tbody.innerHTML = "";

  if(pageRows.length === 0){
    tbody.innerHTML = `<tr><td colspan="10" style="padding:16px;color:#6b7a90;font-weight:900;">No records found.</td></tr>`;
  }else{
    pageRows.forEach(d=>{
      const pno = String(d.passport||"");
      tbody.innerHTML += `
        <tr>
          <td><span class="link" onclick="pickAttender('${escapeHtml(pno)}')">${escapeHtml(pno)}</span></td>
          <td>${escapeHtml(d.given||"")}</td>
          <td>${escapeHtml(d.sur||"")}</td>
          <td>${escapeHtml(d.dob||"")}</td>
          <td>${escapeHtml(d.country||"")}</td>
          <td>${escapeHtml(d.gender||"")}</td>
          <td>${escapeHtml(d.passport_expiry||"")}</td>
          <td>${escapeHtml(d.contact||"")}</td>
          <td>${escapeHtml(d.email||"")}</td>
          <td title="${escapeHtml(d.address||"")}">${escapeHtml(d.address||"")}</td>
        </tr>`;
    });
  }

  document.getElementById("attPrev").disabled = (attPage<=1);
  document.getElementById("attNext").disabled = (attPage>=totalPages);

  const shownFrom = total ? (start+1) : 0;
  const shownTo = Math.min(end, total);
  document.getElementById("attMeta").textContent = `Page ${attPage}/${totalPages} • Showing ${shownFrom}-${shownTo} of ${total}`;
}

function pickAttender(passport){
  const pno = String(passport || "");
  if(lead.patient && String(lead.patient.passport) === pno){
    alert("Patient cannot be selected as an Attender.");
    return;
  }
  if(lead.attenders.some(a => String(a.passport) === pno)){
    alert("This attender is already added.");
    return;
  }

  const r = (searchData || []).find(x=>String(x.passport||"")===pno);
  if(!r) return;

  document.getElementById("att_passport").value = r.passport || "";
  document.getElementById("att_given").value = r.given || "";
  document.getElementById("att_sur").value = r.sur || "";
  document.getElementById("att_dob").value = r.dob || "";
  document.getElementById("att_age").value = calculateAge(r.dob || "");
  document.getElementById("att_country").value = r.country || "";
  document.getElementById("att_gender").value = r.gender || "Male";
  document.getElementById("att_passport_expiry").value = r.passport_expiry || "";
  document.getElementById("att_contact").value = r.contact || "";
  document.getElementById("att_email").value = r.email || "";
  document.getElementById("att_address").value = r.address || "";
}

document.getElementById("att_dob").addEventListener("change", function(){
  document.getElementById("att_age").value = calculateAge(this.value);
});

function clearAttenderForm(){
  ["passport","given","sur","dob","age","country","passport_expiry","relationship","contact","email","address"].forEach(k=>{
    const el = document.getElementById("att_"+k);
    if(el) el.value = "";
  });
  document.getElementById("att_gender").value = "Male";
}

function saveAttenderFromForm(){
  const dob = (document.getElementById("att_dob").value || "");
  const a = {
    passport: val("att_passport"),
    given: val("att_given"),
    sur: val("att_sur"),
    dob,
    age: calculateAge(dob),
    gender: val("att_gender"),
    country: val("att_country"),
    passport_expiry: (document.getElementById("att_passport_expiry").value || ""),
    relationship: val("att_relationship"),
    contact: val("att_contact"),
    email: val("att_email"),
    address: val("att_address")
  };
  if(!a.passport || !a.given){ alert("Enter Attender Passport and Given Name."); return; }
  if(!a.relationship){ alert("Relationship is mandatory for Attender."); return; }

  lead.patient = readPatient();

  if(lead.patient && String(lead.patient.passport) === String(a.passport)){
    alert("Patient cannot be selected as an Attender.");
    return;
  }
  if(lead.attenders.some(x=>String(x.passport)===String(a.passport))){
    alert("This attender is already added.");
    return;
  }

  lead.attenders.push(a);
  refreshChips();
  syncWorkflowHiddenFields();
  closeAttenderModal();
  clearAttenderForm();
  document.getElementById("attSearch").value = "";
  renderPatientGrid();
}

function syncOneAttender(prefix, a){
  document.getElementById(`h_${prefix}passport_no`).value = a ? (a.passport || "") : "";
  document.getElementById(`h_${prefix}pat_relation`).value = a ? (a.relationship || "") : "";
  document.getElementById(`h_${prefix}_givennm`).value = a ? (a.given || "") : "";
  document.getElementById(`h_${prefix}_surnm`).value = a ? (a.sur || "") : "";
  document.getElementById(`h_${prefix}_dob`).value = a ? (a.dob || "") : "";
  document.getElementById(`h_${prefix}_country`).value = a ? (a.country || "") : "";
  document.getElementById(`h_${prefix}_gender`).value = a ? (a.gender || "") : "";
  document.getElementById(`h_${prefix}_contno`).value = a ? (a.contact || "") : "";
  document.getElementById(`h_${prefix}_age`).value = a ? (a.age || "") : "";
  document.getElementById(`h_${prefix}pass_expirydt`).value = a ? (a.passport_expiry || "") : "";
  document.getElementById(`h_${prefix}_address`).value = a ? (a.address || "") : "";
  document.getElementById(`h_${prefix}_email`).value = a ? (a.email || "") : "";
}

function syncWorkflowHiddenFields(){
  syncOneAttender("attn1", lead.attenders[0] || null);
  syncOneAttender("attn2", lead.attenders[1] || null);
  syncOneAttender("attn3", lead.attenders[2] || null);

  const age = calculateAge(document.getElementById("patient_dob").value || "");
  document.getElementById("patient_age").value = age;
}

function openPreview(){
  if(!validateAttenderRelationships(true)) return;
  syncWorkflowHiddenFields();
  buildPreview();
  document.getElementById("drawerOverlay").style.display = "flex";
}
function closePreview(e){
  if(!e || e.target===document.getElementById("drawerOverlay")){
    document.getElementById("drawerOverlay").style.display = "none";
  }
}
function buildPreview(){
  const kv = document.getElementById("previewKV");
  const ppl = document.getElementById("previewPeople");
  const docs = document.getElementById("previewDocs");
  kv.innerHTML = ""; ppl.innerHTML = ""; docs.innerHTML = "";

  const p = readPatient();
  lead.patient = p;

  const hospName = document.getElementById("hospital").value || "";
  const hospCity = document.getElementById("hospitalCity").value || "";
  const hospitalDeptValues = Array.from(document.getElementById("hospitalDept").selectedOptions || []).map(o => o.value).filter(Boolean);
  const agent = document.getElementById("agent").value || "";
  const ivac = document.getElementById("ivac").value || "";
  const doctor = document.getElementById("doctor").value || "";
  const vilDate = document.getElementById("vilDate").value || "";
  const apptDate = document.getElementById("apptDate").value || "";
  const remarks = document.getElementById("remarks").value || "";
  const shortRemarks = remarks ? (remarks.length > 160 ? remarks.slice(0,160)+"…" : remarks) : "";
  const totalDocBytes = selectedDocs.reduce((s,f)=>s + (f.size||0), 0);
  const likelyStatus = vilDate ? "VIL REQUESTED" : "LEAD GENERATED";

  kv.innerHTML += `<div class="k">Patient</div><div class="v">${p.passport ? `${escapeHtml((p.given+" "+p.sur).trim())} (${escapeHtml(p.passport)})` : "Not set"}</div>`;
  kv.innerHTML += `<div class="k">Passport Expiry</div><div class="v">${escapeHtml(p.passport_expiry || "—")}</div>`;
  kv.innerHTML += `<div class="k">Attenders</div><div class="v">${lead.attenders.length}</div>`;
  kv.innerHTML += `<div class="k">Workflow Status</div><div class="v">${escapeHtml(likelyStatus)}</div>`;
  kv.innerHTML += `<div class="k">Hospital</div><div class="v">${hospName ? escapeHtml(hospName) : "Not set"}</div>`;
  kv.innerHTML += `<div class="k">City</div><div class="v">${escapeHtml(hospCity || "—")}</div>`;
  kv.innerHTML += `<div class="k">Hospital Dept</div><div class="v">${hospitalDeptValues.length ? escapeHtml(hospitalDeptValues.join(", ")) : "—"}</div>`;
  kv.innerHTML += `<div class="k">Agent</div><div class="v">${escapeHtml(agent || "—")}</div>`;
  kv.innerHTML += `<div class="k">IVAC</div><div class="v">${escapeHtml(ivac || "—")}</div>`;
  kv.innerHTML += `<div class="k">Doctor</div><div class="v">${escapeHtml(doctor || "—")}</div>`;
  kv.innerHTML += `<div class="k">VIL Date</div><div class="v">${escapeHtml(vilDate || "—")}</div>`;
  kv.innerHTML += `<div class="k">Appointment Date</div><div class="v">${escapeHtml(apptDate || "—")}</div>`;
  kv.innerHTML += `<div class="k">Remarks</div><div class="v">${escapeHtml(shortRemarks || "—")}</div>`;
  kv.innerHTML += `<div class="k">Documents</div><div class="v">${selectedDocs.length} file(s) • ${escapeHtml(bytesToHuman(totalDocBytes))}</div>`;

  if(p.passport){
    ppl.innerHTML += `<tr><td>Patient</td><td>—</td><td>${escapeHtml(p.passport)}</td><td>${escapeHtml((p.given+" "+p.sur).trim())}</td><td>${escapeHtml(p.contact||"")}</td></tr>`;
  }
  lead.attenders.forEach((a,idx)=>{
    ppl.innerHTML += `<tr><td>Attender ${idx+1}</td><td>${escapeHtml(a.relationship || "—")}</td><td>${escapeHtml(a.passport)}</td><td>${escapeHtml((a.given+" "+a.sur).trim())}</td><td>${escapeHtml(a.contact||"")}</td></tr>`;
  });

  if(selectedDocs.length === 0){
    docs.innerHTML = `<tr><td colspan="3" style="padding:14px;color:#6b7a90;font-weight:900;">No documents selected</td></tr>`;
  }else{
    selectedDocs.forEach((f, idx)=>{
      docs.innerHTML += `<tr><td>${idx+1}</td><td>${escapeHtml(f.name)}</td><td>${escapeHtml(bytesToHuman(f.size||0))}</td></tr>`;
    });
  }
}

function getFormValueByNames(...names){
  const form = document.getElementById("leadForm");

  for(const name of names){
    const elByName = form ? form.querySelector(`[name="${name}"]`) : null;
    if(elByName && String(elByName.value || "").trim()){
      return String(elByName.value || "").trim();
    }

    const elById = document.getElementById(name);
    if(elById && String(elById.value || "").trim()){
      return String(elById.value || "").trim();
    }
  }

  return "";
}

function validateBeforeSubmit(){
  lead.patient = readPatient();
  syncWorkflowHiddenFields();

  if(!lead.patient.passport || !lead.patient.given){
    alert("Patient Passport No and Given Name are required.");
    setStep(1);
    return false;
  }
  if(!lead.patient.dob || !lead.patient.country || !lead.patient.gender){
    alert("Please complete mandatory Patient fields: DOB, Country and Gender.");
    setStep(1);
    return false;
  }
  if(!validateAttenderRelationships(true)){
    setStep(2);
    return false;
  }

  const hosp = getFormValueByNames("hospital_shrtnm", "hospital");
  const ivac = getFormValueByNames("ivac_center", "ivac");
  const agent = getFormValueByNames("agent_shrtnm", "agent");
  const doctor = getFormValueByNames("doctor_nm", "doctor");

  if(!hosp || !ivac || !agent || !doctor){
    alert("Please complete mandatory Hospital fields: Hospital, IVAC, Agent and Doctor.");
    setStep(3);
    return false;
  }

  const totalDocBytes = selectedDocs.reduce((s,f)=>s + (f.size||0), 0);
  if(totalDocBytes > MAX_DOC_BYTES){
    alert("Total document size exceeds 10MB. Please remove files.");
    setStep(3);
    return false;
  }

  return true;
}

async function submitForm(){
  if(!validateBeforeSubmit()) return;

  const passport = val("patient_passport");
  if(!passport){
    alert("Patient Passport No is required.");
    setStep(1);
    return;
  }

  try{
    const res = await fetch(`/api/check-patient-workflow-duplicate?passport=${encodeURIComponent(passport)}`);
    const data = await res.json();

    if(!data.allowed){
      alert(data.message || "An existing active request is available for this patient. A new request can be created only when the current workflow status permits creation of a new case.");
      return;
    }
  }catch(e){
    alert("Unable to validate duplicate workflow record.");
    return;
  }

  document.getElementById("leadForm").submit();
}

function resetAll(){
  if(!confirm("Reset everything?")) return;

  lead = { patient: null, attenders: [], hospital: {} };
  clearPatientForm();
  removeAllAttenders();

  ["hospital","hospitalCity","agent","ivac","doctor","vilDate","apptDate","remarks"].forEach(id=>{
    const el = document.getElementById(id);
    if(el) el.value = "";
  });

  const dept = document.getElementById("hospitalDept");
  if(dept) dept.innerHTML = "";

  document.getElementById("patientSearch").value = "";
  patientPage = 1;
  clearAllDocs();
  syncWorkflowHiddenFields();

  renderPatientGrid();
  setStep(1);
  refreshChips();
}

async function openDocsViewer(role){
  let passport = "";
  if(role === "patient"){
    passport = (document.getElementById("patient_passport").value || "").trim();
    document.getElementById("docsTitle").textContent = passport ? `Patient Passport: ${passport}` : "Patient Passport not selected";
  }else{
    passport = (document.getElementById("att_passport").value || "").trim();
    document.getElementById("docsTitle").textContent = passport ? `Attender Passport: ${passport}` : "Attender Passport not selected";
  }

  const tbody = document.getElementById("docsTable");
  tbody.innerHTML = "";

  document.getElementById("docsOverlay").style.display = "flex";

  if(!passport){
    tbody.innerHTML = `<tr><td colspan="5" style="padding:14px;color:#6b7a90;font-weight:900;">Select a passport first.</td></tr>`;
    return;
  }

  try{
    const res = await fetch(`/api/patient-docs?passport=${encodeURIComponent(passport)}`);
    const docs = await res.json();

    if(!Array.isArray(docs) || docs.length === 0){
      tbody.innerHTML = `<tr><td colspan="5" style="padding:14px;color:#6b7a90;font-weight:900;">No documents found for this passport.</td></tr>`;
      return;
    }

    docs.forEach((d, i) => {
      const url = d.doc_url || "";
      tbody.innerHTML += `
        <tr>
          <td>${i+1}</td>
          <td title="${escapeHtml(d.file_name||"")}">${escapeHtml(d.file_name||"")}</td>
          <td>${escapeHtml(d.file_type||"")}</td>
          <td>${escapeHtml(bytesToHuman(d.file_size||0))}</td>
          <td>
            ${url ? `<a class="link" href="${url}" target="_blank" rel="noopener">View</a>` : "—"}
          </td>
        </tr>
      `;
    });

  }catch(e){
    tbody.innerHTML = `<tr><td colspan="5" style="padding:14px;color:#b42318;font-weight:900;">Failed to load documents.</td></tr>`;
  }
}

function closeDocsViewer(e){
  if(!e || e.target===document.getElementById("docsOverlay")){
    document.getElementById("docsOverlay").style.display = "none";
  }
}

initHospitalLov();
setStep(1);
renderPatientGrid();
refreshChips();
renderDocs();
syncWorkflowHiddenFields();
// Flash messages are rendered inline above; no browser popup needed.
