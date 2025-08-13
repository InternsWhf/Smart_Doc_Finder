// If frontend is served by the backend (recommended), leave API_BASE empty string.
const API_BASE = ""; // e.g., "" or "http://<backend-ip>:8000" if separate

const els = {
  year: document.getElementById('year'),
  month: document.getElementById('month'),
  company: document.getElementById('company'),
  query: document.getElementById('query'),
  btnSearch: document.getElementById('btnSearch'),
  btnClear: document.getElementById('btnClear'),
  status: document.getElementById('status'),
  table: document.getElementById('results'),
  tbody: document.querySelector('#results tbody'),
  empty: document.getElementById('empty'),
  pager: document.getElementById('pager'),
  prev: document.getElementById('prev'),
  next: document.getElementById('next'),
  pageInfo: document.getElementById('pageInfo'),
  pageSize: document.getElementById('pageSize'),
};

let state = {
  page: 1,
  totalPages: 1,
  pageSize: parseInt(els.pageSize?.value || "50", 10) || 50,
};

init();

function init(){
  // years 2022..2050
  if (els.year) {
    for(let y = 2022; y <= 2050; y++){
      const opt = document.createElement('option');
      opt.value = String(y);
      opt.textContent = String(y);
      els.year.appendChild(opt);
    }
  }

  els.btnSearch.addEventListener('click', () => { state.page = 1; search(); });
  els.btnClear.addEventListener('click', clearFilters);
  els.query.addEventListener('keydown', e => { if(e.key === 'Enter'){ state.page = 1; search(); } });
  els.pageSize.addEventListener('change', () => { state.pageSize = parseInt(els.pageSize.value,10); state.page = 1; search(); });
  els.prev.addEventListener('click', () => { if(state.page > 1){ state.page--; search(); } });
  els.next.addEventListener('click', () => { if(state.page < state.totalPages){ state.page++; search(); } });

  // Event delegation for row actions
  els.tbody.addEventListener('click', onTableClick);
}

function clearFilters(){
  els.year.value = "";
  els.month.value = "";
  els.company.value = "";
  els.query.value = "EXP-"; // keep prefix
  state.page = 1;
  state.totalPages = 1;
  els.status.textContent = "";
  els.tbody.innerHTML = "";
  els.table.classList.add('hidden');
  els.empty.classList.add('hidden');
  els.pager.classList.add('hidden');
}

async function search(){
  let query = (els.query.value || "").trim();

  // Treat bare prefix as empty query so month/year can list contents
  const qUpper = query.toUpperCase();
  if (qUpper === "EXP" || qUpper === "EXP-") {
    query = "";
  }

  const params = new URLSearchParams();
  params.set('query', query); // filename-only; backend handles case-insensitive match
  if(els.year.value) params.set('year', els.year.value);
  if(els.month.value) params.set('month', els.month.value);
  if(els.company.value.trim()) params.set('company', els.company.value.trim());
  params.set('page', state.page);
  params.set('page_size', state.pageSize);

  els.status.textContent = "Searching...";
  toggleLoading(true);

  try{
    const t0 = performance.now();
    const res = await fetch(`${API_BASE}/search?${params.toString()}`);
    if(!res.ok) throw new Error(`Server responded ${res.status}`);
    const data = await res.json();
    const t1 = performance.now();

    renderResults(data);
    els.status.textContent = `Found ${data.count} item(s) in ${Math.max(1, Math.round(t1 - t0))} ms`;
  } catch(err){
    console.error(err);
    els.status.textContent = "Error contacting server. Please try again.";
    showEmpty();
  } finally {
    toggleLoading(false);
  }
}

function renderResults(data){
  const { items = [], page=1, total_pages=1 } = data;
  state.page = page || 1;
  state.totalPages = total_pages || 1;

  els.tbody.innerHTML = "";
  if(!items.length){
    els.table.classList.add('hidden');
    showEmpty();
  } else {
    els.empty.classList.add('hidden');
    els.table.classList.remove('hidden');
    items.forEach(row => {
      const tr = document.createElement('tr');
      const safeName = escapeHtml(row.file_name || "");
      const folder = escapeHtml(row.parent_folder || "");
      const fullPath = row.full_path || "";
      const kind = (row.kind || "file").toLowerCase();

      let actions = "";
      if (kind === "file") {
        const previewLink = `${API_BASE}/preview?path=${encodeURIComponent(fullPath)}`;
        actions = `
          <td><button class="btn act-open" data-path="${escapeHtmlAttr(fullPath)}" title="Open in default app">Open</button></td>
          <td><a href="${previewLink}" target="_blank" rel="noopener">Preview</a></td>
        `;
      } else { // folder
        const browseLink = `${API_BASE}/browse?path=${encodeURIComponent(fullPath)}&query=${encodeURIComponent(els.query.value.trim()||"")}`;
        actions = `
          <td><button class="btn act-open-folder" data-path="${escapeHtmlAttr(fullPath)}" title="Open in Explorer">Open folder</button></td>
          <td><a class="act-view-contents" href="${browseLink}" target="_blank" rel="noopener">View contents</a></td>
        `;
      }

      tr.innerHTML = `
        <td>${safeName} ${kind === "folder" ? "📁" : ""}</td>
        <td>${folder}</td>
        ${actions}
        <td><button class="btn copy" data-path="${escapeHtmlAttr(fullPath)}" title="Copy full path">Copy path</button></td>
      `;

      els.tbody.appendChild(tr);
    });
  }

  els.pageInfo.textContent = `Page ${state.page} of ${state.totalPages}`;
  els.prev.disabled = (state.page <= 1);
  els.next.disabled = (state.totalPages <= 1 || state.page >= state.totalPages);
  els.pager.classList.remove('hidden');
}

function showEmpty(){
  els.empty.classList.remove('hidden');
  els.pager.classList.add('hidden');
}

function toggleLoading(on){
  document.body.style.cursor = on ? 'progress' : 'default';
}

// Event delegation for actions inside the table
async function onTableClick(e){
  const btnOpen = e.target.closest(".act-open");
  const btnOpenFolder = e.target.closest(".act-open-folder");
  const btnCopy = e.target.closest(".copy");

  if (btnOpen) {
    const path = btnOpen.getAttribute("data-path");
    await callShellOpen(path);
    return;
  }
  if (btnOpenFolder) {
    const path = btnOpenFolder.getAttribute("data-path");
    await callShellOpenFolder(path);
    return;
  }
  if (btnCopy) {
    const path = btnCopy.getAttribute("data-path");
    try{
      await navigator.clipboard.writeText(path);
      btnCopy.textContent = "Copied!";
      setTimeout(()=>{ btnCopy.textContent = "Copy path"; }, 1200);
    }catch(_){
      alert("Could not copy to clipboard. You can copy manually:\n" + path);
    }
    return;
  }
}

// Backend endpoints to avoid file:// restrictions
async function callShellOpen(path){
  try{
    const res = await fetch(`${API_BASE}/shell/open`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ path })
    });
    if(!res.ok){
      const j = await safeJson(res);
      throw new Error(j?.detail || `Open failed (${res.status})`);
    }
  }catch(err){
    console.error(err);
    alert(`Could not open file.\n${err.message || err}`);
  }
}

async function callShellOpenFolder(path){
  try{
    const res = await fetch(`${API_BASE}/shell/open-folder`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ path })
    });
    if(!res.ok){
      const j = await safeJson(res);
      throw new Error(j?.detail || `Open folder failed (${res.status})`);
    }
  }catch(err){
    console.error(err);
    alert(`Could not open folder.\n${err.message || err}`);
  }
}

// Utils
async function safeJson(res){
  try{ return await res.json(); }catch{ return null; }
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[c]));
}
function escapeHtmlAttr(s){
  return String(s).replace(/["'&<>]/g, c => ({'"':'&quot;',"'":'&#39;','&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
}
