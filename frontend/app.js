// If frontend is served by the backend (recommended), leave API_BASE empty.
const API_BASE = ""; // e.g., "" or "http://<backend-ip>:8000" if separate

const els = {
  year: document.getElementById('year'),
  month: document.getElementById('month'),
  company: document.getElementById('company'),
  query: document.getElementById('query'),        // numeric part only (UI shows fixed EXP-)
  btnSearch: document.getElementById('btnSearch'),
  btnClear: document.getElementById('btnClear'),
  status: document.getElementById('status'),
  parentRows: document.getElementById('parentRows'),
  empty: document.getElementById('empty'),

  // Monthly coverage UI (must exist in index.html for monthly feature)
  btnMonthly: document.getElementById('btnMonthly'),
  monthlyCard: document.getElementById('monthlyCard'),
  monthlyStatus: document.getElementById('monthlyStatus'),
  availTable: document.getElementById('availTable'),
  availBody: document.getElementById('availBody'),
  missingWrap: document.getElementById('missingWrap'),
  missingBadges: document.getElementById('missingBadges'),
};

const PARENTS = [
  "CIPL",
  "DOCK AUDIT REPORT",
  "MTC PACKAGE",
  "PDIR REPORT",
  "PHOTOGRAPH",
  "BL",
  "POD",
];

init();

function init(){
  // Year options 2020..2050
  for(let y = 2020; y <= 2050; y++){
    const opt = document.createElement('option');
    opt.value = String(y);
    opt.textContent = String(y);
    els.year.appendChild(opt);
  }

  els.btnSearch.addEventListener('click', search);
  els.btnClear.addEventListener('click', clearFilters);

  // Table-level click handlers (event delegation)
  els.parentRows.addEventListener('click', onParentTableClick);

  // Monthly: button + available table delegation (only if elements exist)
  if (els.btnMonthly) els.btnMonthly.addEventListener('click', onMonthlyClick);
  if (els.availTable) els.availTable.addEventListener('click', onAvailTableClick);

  // Initial empty state
  showEmpty(true);
}

function clearFilters(){
  els.year.value = "";
  els.month.value = "";
  els.company.value = "";
  els.query.value = "";             // numeric field cleared; EXP- stays in UI prefix
  els.status.textContent = "";
  els.parentRows.innerHTML = "";
  showEmpty(true);

  // Clear monthly section (if present)
  if (els.monthlyCard) {
    els.monthlyStatus.textContent = "";
    els.availBody && (els.availBody.innerHTML = "");
    if (els.missingBadges) els.missingBadges.innerHTML = "";
    if (els.missingWrap) els.missingWrap.classList.add('hidden');
  }
}

function showEmpty(on){
  els.empty.classList.toggle('hidden', !on);
  if(on) els.parentRows.innerHTML = "";
}

async function search(){
  const numberPart = (els.query.value || "").trim();
  if(!numberPart){
    els.status.textContent = "Please enter a document number (e.g., 192).";
    showEmpty(true);
    return;
  }

  // Always prepend EXP-
  const query = `EXP-${numberPart}`;

  const params = new URLSearchParams();
  params.set('query', query);
  if(els.year.value) params.set('year', els.year.value);
  if(els.month.value) params.set('month', els.month.value);
  if(els.company.value.trim()) params.set('company', els.company.value.trim());

  els.status.textContent = "Searching...";
  const t0 = performance.now();

  try{
    const res = await fetch(`${API_BASE}/coverage-rows?${params.toString()}`);
    if(!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();

    renderParentRows(data.rows || []);
    const t1 = performance.now();
    els.status.textContent = `Checked ${PARENTS.length} folders in ${Math.max(1, Math.round(t1 - t0))} ms`;
    showEmpty(false);
  }catch(err){
    console.error(err);
    els.status.textContent = "Error contacting server. Please try again.";
    showEmpty(true);
  }
}

/* Render fixed 7 parent rows; each row can expand to show child items */
function renderParentRows(rows){
  // Map incoming rows by parent; ensure fixed display order
  const byParent = {};
  rows.forEach(r => { byParent[(r.parent || "").toUpperCase()] = r; });

  els.parentRows.innerHTML = "";
  PARENTS.forEach(parent => {
    const r = byParent[parent] || { parent, present:false, found:false, count:0, items:[] };

    // Pale green/red full-row highlight
    const tr = document.createElement('tr');
    tr.classList.add(r.found ? 'row-green' : 'row-red');
    tr.dataset.parent = r.parent;

    // Explicit status text
    const statusPill = r.found
      ? `<span class="status-pill status-ok">Found</span>`
      : `<span class="status-pill status-miss">Missing</span>`;

    const countBadge = `<span class="count-badge">${r.found ? (r.count || 0) : 0}</span>`;

    // Actions: only enable View files if row is green
    const btn = r.found
      ? `<button class="btn btn-view" data-parent="${escapeHtmlAttr(r.parent)}" data-loaded="0" aria-expanded="false">View files</button>`
      : `<button class="btn" disabled>View files</button>`;

    tr.innerHTML = `
      <td><strong>${escapeHtml(r.parent)}</strong></td>
      <td>${statusPill}</td>
      <td>${countBadge}</td>
      <td>${btn}</td>
    `;
    els.parentRows.appendChild(tr);

    // Expansion placeholder (hidden)
    const expand = document.createElement('tr');
    expand.className = "expand-row hidden";
    expand.dataset.parent = r.parent;
    expand.innerHTML = `
      <td colspan="4">
        <div class="expand-wrap">
          <table class="child-table">
            <thead>
              <tr>
                <th>File/Folder</th>
                <th>Parent folder</th>
                <th>Open</th>
                <th>Preview</th>
                <th>Copy</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </td>
    `;
    els.parentRows.appendChild(expand);

    // Cache preloaded items (from backend) to avoid extra call
    if(r.found && Array.isArray(r.items) && r.items.length){
      const btnEl = tr.querySelector('.btn-view');
      btnEl.dataset.preloaded = "1";
      btnEl._preloadedItems = r.items;
    }
  });
}

/* Handle clicks inside the parent results table */
async function onParentTableClick(e){
  const viewBtn = e.target.closest('.btn-view');
  if(viewBtn){
    const parent = viewBtn.getAttribute('data-parent');
    const expanded = viewBtn.getAttribute('aria-expanded') === 'true';
    const expandRow = findExpandRow(parent);
    if(!expandRow) return;

    if(expanded){
      expandRow.classList.add('hidden');
      viewBtn.setAttribute('aria-expanded', 'false');
      viewBtn.textContent = 'View files';
      return;
    }

    // expand
    try{
      let items = [];
      if(viewBtn.dataset.preloaded === "1" && viewBtn._preloadedItems){
        items = viewBtn._preloadedItems;
      }else{
        items = await fetchCoverageFiles(parent);
        viewBtn.dataset.preloaded = "1";
        viewBtn._preloadedItems = items;
      }
      renderChildItems(expandRow, items);
      expandRow.classList.remove('hidden');
      viewBtn.setAttribute('aria-expanded', 'true');
      viewBtn.textContent = 'Hide files';
    }catch(err){
      console.error(err);
      alert('Could not load files for ' + parent);
    }
  }

  // Child-table action: copy path
  const copyBtn = e.target.closest('.act-copy');
  if(copyBtn){
    const path = copyBtn.getAttribute('data-path');
    try{
      await navigator.clipboard.writeText(path);
      copyBtn.textContent = "Copied!";
      setTimeout(()=>{ copyBtn.textContent = "Copy"; }, 1200);
    }catch(_){
      alert("Could not copy. You can copy manually:\n" + path);
    }
  }
}

function findExpandRow(parent){
  return els.parentRows.querySelector(`tr.expand-row[data-parent="${cssEscape(parent)}"]`);
}

async function fetchCoverageFiles(parent){
  const numberPart = (els.query.value || "").trim();
  const query = `EXP-${numberPart}`;

  const params = new URLSearchParams();
  params.set('parent', parent);
  params.set('query', query);
  if(els.year.value) params.set('year', els.year.value);
  if(els.month.value) params.set('month', els.month.value);
  if(els.company.value.trim()) params.set('company', els.company.value.trim());

  const res = await fetch(`${API_BASE}/coverage-files?${params.toString()}`);
  if(!res.ok) throw new Error(`coverage-files ${res.status}`);
  const data = await res.json();
  return data.items || [];
}

/* === FIXED: Preview is enabled for BOTH files and folders === */
function renderChildItems(expandRow, items){
  const tbody = expandRow.querySelector('tbody');
  tbody.innerHTML = "";

  if(!items.length){
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="5" style="color:var(--muted);">No files to display.</td>`;
    tbody.appendChild(tr);
    return;
  }

  items.forEach(row => {
    const kind = (row.kind || "file").toLowerCase();
    const isFile = kind === "file";
    const fullPath = row.full_path || "";
    const safeName = escapeHtml(row.file_name || "");
    const folder = escapeHtml(row.parent_folder || "");

    // OPEN (same tab): files -> /preview, folders -> /browse-ui
    const openCell = isFile
      ? `<a class="btn" href="${API_BASE}/preview?path=${encodeURIComponent(fullPath)}">Open</a>`
      : `<a class="btn" href="${API_BASE}/browse-ui?path=${encodeURIComponent(fullPath)}">Open folder</a>`;

    // PREVIEW: files -> /preview in new tab, folders -> /browse-ui (deep view) in new tab
    const previewCell = isFile
      ? `<a href="${API_BASE}/preview?path=${encodeURIComponent(fullPath)}" target="_blank" rel="noopener">Preview</a>`
      : `<a href="${API_BASE}/browse-ui?path=${encodeURIComponent(fullPath)}&deep=1" target="_blank" rel="noopener">Preview</a>`;

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${safeName} ${!isFile ? "📁" : ""}</td>
      <td>${folder}</td>
      <td>${openCell}</td>
      <td>${previewCell}</td>
      <td><button class="btn act-copy" data-path="${escapeHtmlAttr(fullPath)}">Copy</button></td>
    `;
    tbody.appendChild(tr);
  });
}

/* ===========================
   MONTHLY COVERAGE (NEW)
   =========================== */

// DISPLAY-ONLY normalization (PDO -> POD for labels)
const prettyParent = (s) => (String(s).toUpperCase() === "PDO" ? "POD" : s);

async function onMonthlyClick(){
  if (!els.monthlyCard) return; // safety if markup not present

  const year  = (els.year.value || "").trim();
  const month = (els.month.value || "").trim();
  const company = (els.company.value || "").trim();

  if (!year || !month){
    els.monthlyCard.classList.remove('hidden');
    els.monthlyStatus.textContent = 'Please select both Year and Month for monthly coverage.';
    // clear tables
    if (els.availBody) els.availBody.innerHTML = '';
    if (els.missingBadges) els.missingBadges.innerHTML = '';
    if (els.missingWrap) els.missingWrap.classList.add('hidden');
    return;
  }

  els.monthlyCard.classList.remove('hidden');
  els.monthlyStatus.textContent = 'Fetching monthly coverage…';

  try{
    const params = new URLSearchParams({ year, month });
    if (company) params.set('company', company);

    const res = await fetch(`${API_BASE}/monthly-coverage?${params.toString()}`);
    if (!res.ok) throw new Error(`monthly-coverage ${res.status}`);
    const data = await res.json();

    const periodMonth = (data?.period?.month || month).toString();
    const periodYear  = (data?.period?.year  || year).toString();
    els.monthlyStatus.textContent = `Period: ${capitalize(periodMonth)} ${periodYear}`;

    renderAvailableMonthly(data.available || []);
    renderMissingMonthly(data.missing || []);

    // Scroll into view
    try { els.monthlyCard.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch(_){}
  }catch(err){
    console.error(err);
    els.monthlyStatus.textContent = 'Error fetching monthly coverage. Please try again.';
    if (els.availBody) els.availBody.innerHTML = '';
    if (els.missingBadges) els.missingBadges.innerHTML = '';
    if (els.missingWrap) {
      els.missingWrap.classList.remove('hidden');
      const t = els.missingWrap.querySelector('.empty-text');
      if (t) t.textContent = 'Could not load monthly data.';
    }
  }
}

function renderAvailableMonthly(available){
  if (!els.availBody) return;
  els.availBody.innerHTML = '';
  if (!available.length){
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="3" class="status">No documents found for this period.</td>`;
    els.availBody.appendChild(tr);
    return;
  }

  available.forEach(group => {
    const parent = group.parent || '';
    const count  = group.count || 0;

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><strong>${escapeHtml(prettyParent(parent))}</strong></td>
      <td><span class="count-badge">${count}</span></td>
      <td><button class="btn btn-view-month" data-parent="${escapeHtmlAttr(parent)}" aria-expanded="false">View files</button></td>
    `;
    els.availBody.appendChild(tr);

    const ex = document.createElement('tr');
    ex.className = 'expand-row hidden';
    ex.dataset.parent = parent;
    ex.innerHTML = `
      <td colspan="3">
        <div class="expand-wrap">
          <table class="child-table">
            <thead>
              <tr>
                <th>File/Folder</th>
                <th>Parent folder</th>
                <th>Open</th>
                <th>Preview</th>
                <th>Copy</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </td>
    `;
    els.availBody.appendChild(ex);

    // cache items on the button node
    const btn = tr.querySelector('.btn-view-month');
    btn._items = Array.isArray(group.items) ? group.items : [];
  });
}

function renderMissingMonthly(missing){
  if (!els.missingWrap || !els.missingBadges) return;

  if (!missing.length){
    els.missingBadges.innerHTML = '';
    els.missingWrap.classList.add('hidden');
    return;
  }

  // (kept as-is per your current logic)
  els.missingWrap.classList.add('hidden'); // hide "all good" box
  els.missingBadges.innerHTML = missing
    .map(name => `<span class="status-pill status-miss">${escapeHtml(prettyParent(name))}</span>`)
    .join('');
}

function onAvailTableClick(e){
  const viewBtn = e.target.closest('.btn-view-month');
  if (viewBtn){
    const parent = viewBtn.getAttribute('data-parent');
    let exRow = [...els.availBody.querySelectorAll('.expand-row')]
      .find(r => r.dataset.parent === parent);

    // Fallback: use the row that immediately follows the button's row
    if (!exRow) {
      const row = viewBtn.closest('tr');
      if (row && row.nextElementSibling && row.nextElementSibling.classList.contains('expand-row')) {
        exRow = row.nextElementSibling;
      }
    }
    if (!exRow) return;

    const expanded = viewBtn.getAttribute('aria-expanded') === 'true';
    if (expanded){
      exRow.classList.add('hidden');
      viewBtn.setAttribute('aria-expanded','false');
      viewBtn.textContent = 'View files';
      return;
    }

    const items = viewBtn._items || [];
    const tbody = exRow.querySelector('tbody');
    renderChildItemsMonthly(tbody, items);
    exRow.classList.remove('hidden');
    viewBtn.setAttribute('aria-expanded','true');
    viewBtn.textContent = 'Hide files';
    return;
  }

  const copyBtn = e.target.closest('.act-copy');
  if (copyBtn){
    const path = copyBtn.getAttribute('data-path') || '';
    navigator.clipboard.writeText(path).then(()=>{
      copyBtn.textContent = 'Copied!';
      setTimeout(()=>{ copyBtn.textContent = 'Copy'; }, 1200);
    }).catch(()=>{
      alert('Could not copy. You can copy manually:\n' + path);
    });
  }
}

function renderChildItemsMonthly(tbody, items){
  tbody.innerHTML = '';
  if (!items || !items.length){
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="5" style="color:var(--muted);">No files to display.</td>`;
    tbody.appendChild(tr);
    return;
  }

  items.forEach(row => {
    const isFile = (row.kind || 'file').toLowerCase() === 'file';
    const fullPath = row.full_path || '';
    const name = escapeHtml(row.file_name || '');
    const folder = escapeHtml(row.parent_folder || '');

    const openCell = isFile
      ? `<a class="btn" href="${API_BASE}/preview?path=${encodeURIComponent(fullPath)}">Open</a>`
      : `<a class="btn" href="${API_BASE}/browse-ui?path=${encodeURIComponent(fullPath)}">Open folder</a>`;

    const previewCell = isFile
      ? `<a href="${API_BASE}/preview?path=${encodeURIComponent(fullPath)}" target="_blank" rel="noopener">Preview</a>`
      : `<a href="${API_BASE}/browse-ui?path=${encodeURIComponent(fullPath)}&deep=1" target="_blank" rel="noopener">Preview</a>`;

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${name} ${!isFile ? '📁' : ''}</td>
      <td>${folder}</td>
      <td>${openCell}</td>
      <td>${previewCell}</td>
      <td><button class="btn act-copy" data-path="${escapeHtmlAttr(fullPath)}">Copy</button></td>
    `;
    tbody.appendChild(tr);
  });
}

/* ----- Utils ----- */
async function safeJson(res){ try{ return await res.json(); }catch{ return null; } }
function escapeHtml(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[c])); }
function escapeHtmlAttr(s){ return String(s).replace(/["'&<>]/g, c => ({'"':'&quot;',"'":'&#39;','&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function cssEscape(s){ return s.replace(/["\\]/g, '\\$&'); }
function capitalize(s){ return s ? (s[0].toUpperCase() + s.slice(1)) : s; }
