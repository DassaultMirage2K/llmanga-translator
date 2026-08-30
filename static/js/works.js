// Works list + work detail views (Phase 3).
import { api } from './api.js';
import { el, toast } from './state.js';

function fmtDate(s) {
  if (!s) return '';
  const d = new Date(s.replace(' ', 'T'));
  return isNaN(d.getTime()) ? String(s) : d.toLocaleString();
}

/** Accept images and .zip archives (MIME type for zips is often empty). */
function isImportableFile(f) {
  return f.type.startsWith('image/') || /\.zip$/i.test(f.name);
}

// --- modal helper -----------------------------------------------------------
// Encapsulates the overlay → append → close-callback pattern so individual
// modals only supply their content and save logic.

let activeModalClose = null;

function closeModal() {
  if (activeModalClose) { activeModalClose(); activeModalClose = null; }
}

/**
 * Show a modal overlay. Returns `{ root, close }`.
 */
function showModal({ title, subtitle, body = [], onSave }) {
  closeModal();

  const saveBtn = el('button', { class: 'btn primary', text: 'Save' });
  const cancelBtn = el('button', { class: 'btn', text: 'Cancel' });

  const children = [el('h3', { text: title })];
  if (subtitle) children.push(el('p', { class: 'muted', text: subtitle }));
  children.push(...body, el('div', { class: 'row' }, [saveBtn, cancelBtn]));

  const root = el('div', { class: 'modal-overlay' }, [el('div', { class: 'modal' }, children)]);
  document.body.appendChild(root);

  const close = () => {
    root.remove();
    if (activeModalClose === close) activeModalClose = null;
  };
  activeModalClose = close;

  cancelBtn.addEventListener('click', close);
  saveBtn.addEventListener('click', async () => {
    try { await onSave(); close(); } catch (e) { toast(e.message, 'error'); }
  });

  return { root, close };
}

// --- cover thumbnail --------------------------------------------------------
function workThumb(w) {
  const placeholder = () => {
    const box = el('div', { class: 'work-thumb work-thumb--empty' });
    box.innerHTML =
      '<svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#5b6178" '
      + 'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
      + '<rect x="3" y="3" width="18" height="18" rx="2"/>'
      + '<circle cx="8.5" cy="8.5" r="1.5"/>'
      + '<path d="M21 15l-5-5L5 21"/></svg>';
    return box;
  };
  if (!w.cover_image_id) return placeholder();

  const img = el('img', {
    class: 'work-thumb',
    src: api.imageUrl(w.cover_image_id),
    alt: w.name,
    loading: 'lazy',
  });
  // A missing file shouldn't show a broken-image icon -- fall back to the placeholder.
  img.addEventListener('error', () => { const p = placeholder(); img.replaceWith(p); });
  return img;
}

// ===========================================================================
// Works list view
// ===========================================================================
export function renderWorksList(container) {
  let disposed = false;
  container.innerHTML = '';

  const grid = el('div', { class: 'works-grid' });
  const newBtn = el('button', { class: 'btn primary', text: '+ New work' });
  const formWrap = el('div');
  const header = el('div', { class: 'view-header' }, [el('h1', { text: 'Works' }), newBtn]);

  function showForm() {
    const nameInput = el('input', { type: 'text', placeholder: 'Work title' });
    const descArea = el('textarea', { placeholder: 'Description (optional)', rows: '3' });
    const saveBtn = el('button', { class: 'btn primary', text: 'Create' });
    const cancelBtn = el('button', { class: 'btn', text: 'Cancel' });
    const form = el('form', { class: 'work-form' }, [
      el('label', { text: 'Title' }), nameInput,
      el('label', { text: 'Description' }), descArea,
      el('div', { class: 'row' }, [saveBtn, cancelBtn]),
    ]);
    formWrap.replaceChildren(form);
    saveBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      const name = nameInput.value.trim();
      if (!name) { toast('Title is required', 'error'); return; }
      try {
        const work = await api.createWork({ name, description: descArea.value.trim() || null });
        if (disposed) return;
        location.hash = `#/${work.id}`;
      } catch (err) { toast(err.message, 'error'); }
    });
    cancelBtn.addEventListener('click', () => formWrap.replaceChildren());
  }
  newBtn.addEventListener('click', showForm);

  async function load() {
    if (disposed) return;
    let works;
    try { works = await api.listWorks(); } catch (e) { toast(e.message, 'error'); return; }
    if (disposed) return;
    grid.replaceChildren();
    if (!works.length) {
      grid.appendChild(el('p', { class: 'muted', text: 'No works yet. Create one to get started.' }));
    }
    for (const w of works) {
      const delBtn = el('button', { class: 'btn danger small', text: 'Delete' });
      delBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete work "${w.name}"? This cannot be undone.`)) return;
        try { await api.deleteWork(w.id); toast('Deleted', 'success'); load(); }
        catch (err) { toast(err.message, 'error'); }
      });
      const card = el('div', { class: 'work-card' }, [
        workThumb(w),
        el('h2', { text: w.name }),
        w.description ? el('p', { class: 'muted', text: w.description }) : null,
        el('div', { class: 'meta' }, [`${w.image_count || 0} images `, ` updated ${fmtDate(w.updated_at)}`]),
        delBtn,
      ]);
      card.addEventListener('click', () => { location.hash = `#/${w.id}`; });
      grid.appendChild(card);
    }
    container.replaceChildren(header, formWrap, grid);
  }

  container.replaceChildren(el('p', { class: 'muted', text: 'Loading…' }));
  load();
  return () => { disposed = true; };
}

// ===========================================================================
// Work detail view (upload / reorder / context / translate)
// ===========================================================================
export function renderWorkDetail(container, params) {
  const workId = Number(params.id);
  if (!Number.isInteger(workId) || workId <= 0) {
    container.replaceChildren(el('p', { text: 'Invalid work id.' }));
    return;
  }

  let disposed = false;
  let images = [];
  let ws = null;
  let pollTimer = null;      // REST polling fallback while the WebSocket is down
  let jobFinished = false;
  let translating = false;   // a translation job is running → lock add-images + reorder
  let activeJobId = null;    // in-flight job id that drives the polling fallback
  let reconnectTimer = null; // auto-reconnect backoff timer
  let wsGraceTimer = null;   // grace period for the WS to open before REST fallback
  let reconnectAttempts = 0;
  let wsWarned = false;
  const doneIds = new Set(); // pages with a result: restored from server + live events

  // --- chrome ---------------------------------------------------------------
  const backLink = el('a', { class: 'back', href: '#/', text: '← All works' });
  const titleEl = el('h1', { text: 'Loading…' });
  const editBtn = el('button', { class: 'btn', text: 'Edit work' });
  const addBtn = el('button', { class: 'btn', text: 'Add images' });
  const translateBtn = el('button', { class: 'btn primary', text: 'Start translation' });
  const fileInput = el('input', { type: 'file', accept: 'image/*,.zip,application/zip', multiple: '', hidden: '' });

  const progressBar = el('div', { class: 'progress-bar' });
  const statusText = el('span', { class: 'status-text', text: '' });
  const progressWrap = el('div', { class: 'progress-wrap hidden' }, [
    el('div', { class: 'progress-track' }, [progressBar]),
    statusText,
  ]);

  const dropzone = el('div', { class: 'dropzone' }, [
    el('p', { text: 'Drag & drop images or a .zip archive here, or click to browse.' }),
  ]);
  const grid = el('div', { class: 'image-grid' });

  addBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async (e) => {
    const files = [...e.target.files].filter(isImportableFile);
    if (files.length) await doUpload(files);
    e.target.value = ''; // allow re-selecting the same file
  });

  dropzone.addEventListener('click', () => fileInput.click());
  ['dragenter', 'dragover'].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add('active'); }));
  ['dragleave', 'drop'].forEach((ev) =>
    dropzone.addEventListener(ev, () => dropzone.classList.remove('active')));
  dropzone.addEventListener('drop', async (e) => {
    e.preventDefault();
    const files = [...(e.dataTransfer.files || [])].filter(isImportableFile);
    if (files.length) await doUpload(files);
  });

  translateBtn.addEventListener('click', startTranslation);
  editBtn.addEventListener('click', openWorkInfoModal);

  // --- data loading ---------------------------------------------------------
  async function loadWork() {
    try {
      const work = await api.getWork(workId);
      if (disposed) return;
      titleEl.textContent = work.name;
      document.title = `${work.name} — llmanga-translator`;
    } catch (e) {
      if (disposed) return;
      if (/^404/.test(e.message)) { container.replaceChildren(el('p', { text: 'Work not found.' }), backLink); return; }
      toast(e.message, 'error');
    }
  }

  async function refreshImages() {
    try { images = await api.listImages(workId); } catch (e) { toast(e.message, 'error'); return; }
    if (disposed) return;
    renderGrid();
  }

  async function doUpload(files) {
    if (translating) return; // no adding images mid-translation
    try {
      const created = await api.uploadImages(workId, files);
      if (disposed) return;
      toast(`Uploaded ${created.length} image(s)`, 'success');
      refreshImages();
    } catch (err) { toast(err.message, 'error'); }
  }

  // --- grid + reorder -------------------------------------------------------
  let sortable = null;
  let sortableReady = null; // shared Promise so concurrent renders load Sortable once

  // Lock/unlock the editing affordances (add images + drag-to-reorder) while a
  // translation job is running. Single source of truth for the busy state, so
  // every code path that starts/ends a job keeps these in sync.
  function setTranslating(active) {
    if (translating === active) return;
    translating = active;
    translateBtn.disabled = active;
    addBtn.disabled = active;
    dropzone.classList.toggle('disabled', active);
    grid.classList.toggle('no-reorder', active);
    if (sortable) { try { sortable.option('disabled', active); } catch (_) {} }
  }

  // Pull SortableJS from the esm.sh CDN on demand (full build, includes AutoScroll).
  // Dynamic import keeps a CDN failure non-fatal: if it can't be loaded we skip
  // reordering instead of breaking the page.
  function loadSortable() {
    if (!sortableReady) {
      sortableReady = import('https://esm.sh/sortablejs')
        .then((m) => m.default || m.Sortable)
        .catch(() => null);
    }
    return sortableReady;
  }

  function renderGrid() {
    grid.replaceChildren();
    if (!images.length) {
      grid.appendChild(el('p', { class: 'muted', text: 'No images yet. Add some above.' }));
      return;
    }
    images.forEach((img, idx) => {
      const hasContext = (img.context || '').trim().length > 0;
      const done = doneIds.has(img.id);

      const thumbWrap = el('div', { class: 'thumb-wrap' }, [
        el('img', { src: api.imageUrl(img.id), alt: img.original_name, loading: 'lazy' }),
        done ? el('span', { class: 'badge done', text: '✓' }) : null,
        // Drag handle: the only part of the card that initiates a reorder, so
        // tapping the thumbnail still opens the viewer and buttons stay tappable.
        el('button', { class: 'btn small drag-handle', title: 'Drag to reorder', text: '⋮⋮' }),
      ]);

      // Tap/click the thumbnail to open the translation viewer. A plain click fires
      // here; an actual drag (reorder) does not, so this never fights the DnD.
      thumbWrap.title = 'View translation';
      thumbWrap.addEventListener('click', () => { location.hash = `#/${workId}/image/${img.id}`; });

      const ctxBtn = el('button', { class: 'btn small', text: hasContext ? 'Edit context' : 'Add context' });
      ctxBtn.addEventListener('click', () => openContextModal(img));
      const delBtn = el('button', { class: 'btn danger small', text: 'Delete' });
      delBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`Remove image "${img.original_name}"?`)) return;
        try { await api.deleteImage(img.id); toast('Removed', 'success'); refreshImages(); }
        catch (err) { toast(err.message, 'error'); }
      });

      const card = el('div', { class: 'image-card', 'data-id': String(img.id) }, [
        thumbWrap,
        el('span', { class: 'index-badge', text: String(idx + 1) }),
        el('p', { class: 'caption', title: img.original_name, text: img.original_name }),
        hasContext ? el('div', { class: 'ctx-hint', text: '● context' }) : null,
        el('div', { class: 'row' }, [ctxBtn, delBtn]),
      ]);

      grid.appendChild(card);
    });
    initSortable();
  }

  // Drag-to-reorder via SortableJS (loaded from esm.sh; full build, so dragging
  // near the top/bottom edge auto-scrolls the page to reveal more pages).
  // Re-created on every grid render because the DOM is rebuilt each time.
  async function initSortable() {
    if (sortable) { try { sortable.destroy(); } catch (_) {} sortable = null; }
    const S = await loadSortable();
    // The view may have been torn down or re-rendered while we were waiting.
    if (!S || !images.length || disposed) return;
    sortable = new S(grid, {
      handle: '.drag-handle',
      disabled: translating,
      animation: 150,
      ghostClass: 'image-card--ghost',
      onEnd(evt) {
        // Dropped back in the same slot → order unchanged; skip request + toast.
        if (evt.oldIndex === evt.newIndex) return;
        const ids = [...grid.querySelectorAll('.image-card')].map((c) => Number(c.dataset.id));
        persistOrder(ids);
      },
    });
  }

  async function persistOrder(orderedIds) {
    try { await api.reorderImages(workId, orderedIds); toast('Reordered', 'success'); refreshImages(); }
    catch (err) { toast(err.message, 'error'); }
  }

  // --- context modal --------------------------------------------------------
  function openContextModal(image) {
    const area = el('textarea', { rows: '6' });
    area.value = image.context || '';

    showModal({
      title: `Context — ${image.original_name}`,
      subtitle: 'Extra context fed to the LLM for this page (not picked up automatically).',
      body: [area],
      onSave: async () => {
        await api.updateImage(image.id, { context: area.value });
        toast('Context saved', 'success');
        refreshImages();
      },
    });
    area.focus();
  }

  // --- work info modal (title + description) --------------------------------
  async function openWorkInfoModal() {
    let work;
    try { work = await api.getWork(workId); }
    catch (e) { toast(e.message, 'error'); return; }
    if (disposed) return;

    const nameInput = el('input', { type: 'text' });
    nameInput.value = work.name || '';
    const descArea = el('textarea', { rows: '4' });
    descArea.value = work.description || '';

    showModal({
      title: 'Edit work',
      body: [
        el('label', { text: 'Title' }), nameInput,
        el('label', { text: 'Description (optional)' }), descArea,
      ],
      onSave: async () => {
        const name = nameInput.value.trim();
        if (!name) throw new Error('Title is required');
        // Send the trimmed string (possibly "") so clearing actually clears it;
        // a null would mean "keep existing" on the backend.
        await api.updateWork(workId, { name, description: descArea.value.trim() });
        toast('Saved', 'success');
        loadWork(); // refresh title + document.title
      },
    });
    nameInput.focus();
  }

  // --- translation + live updates ------------------------------------------
  function setProgress(pct, text) {
    progressBar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
    statusText.textContent = text || '';
  }

  function connectWs() {
    if (ws || disposed) return;
    let url;
    try {
      url = api.wsUrl(workId);
      console.log('[works-ws] connecting', { url, workId });
      ws = new WebSocket(url);
    } catch (e) {
      // A synchronous throw means the URL itself is malformed. Log it so we can see
      // exactly why, and rely on REST polling for progress instead.
      console.error('[works-ws] could not open live connection', { url, workId, error: e && e.message });
      if (!wsWarned) { toast('Live updates unavailable — using polling.', 'error'); wsWarned = true; }
      scheduleReconnect();
      return;
    }
    const log = (...args) => console.log('[works-ws]', ...args);
    ws.onopen = () => {
      reconnectAttempts = 0;
      log('OPEN', { url, readyState: ws.readyState });
      // Live connection is back -> the REST polling fallback is no longer needed.
      if (wsGraceTimer) { clearTimeout(wsGraceTimer); wsGraceTimer = null; }
      stopPolling();
    };
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); }
      catch (e) { console.warn('[works-ws] unparseable message', ev.data, e); return; }
      log('MSG', m.type, m.data || {});
      handleEvent(m);
    };
    ws.onerror = (ev) => console.error('[works-ws] ERROR event', { url, readyState: ws.readyState, error: ev && ev.message });
    ws.onclose = (ev) => {
      log('CLOSE', { code: ev.code, reason: ev.reason || '', wasClean: ev.wasClean });
      ws = null;
      if (disposed) return;
      // Unexpected drop -> reconnect with backoff; keep progress moving via REST.
      scheduleReconnect();
      maybeStartFallbackPolling();
    };
  }

  function isWsLive() {
    return !!ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING);
  }

  // Auto-reconnect with exponential backoff (1s, 2s, 4s … capped at 15s).
  function scheduleReconnect() {
    if (disposed || reconnectTimer) return;
    const delay = Math.min(15000, 1000 * 2 ** reconnectAttempts);
    reconnectAttempts += 1;
    console.log('[works-ws] scheduling reconnect in', delay, 'ms');
    reconnectTimer = setTimeout(() => { reconnectTimer = null; connectWs(); }, delay);
  }

  // --- REST polling fallback (only while the WebSocket is down) ---------------
  function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

  async function startPolling(jobId) {
    console.log('[works-ws] polling job', jobId, 'ws state:', ws ? `readyState=${ws.readyState}` : 'not connected');
    stopPolling();
    const tick = async () => {
      let job;
      try { job = await api.getJob(jobId); } catch { return; }
      if (disposed) return;
      applyJobStatus(job);
    };
    await tick();
    pollTimer = setInterval(tick, 1500);
  }

  // Arm the fallback for a known in-flight job: poll only if the WS hasn't
  // opened within the grace period; an OPEN event cancels it, a CLOSE starts it.
  function armFallbackGrace(jobId) {
    activeJobId = jobId;
    stopPolling();
    if (wsGraceTimer) clearTimeout(wsGraceTimer);
    wsGraceTimer = setTimeout(() => { wsGraceTimer = null; maybeStartFallbackPolling(); }, 2000);
  }

  function maybeStartFallbackPolling() {
    if (jobFinished || !activeJobId || isWsLive()) return;
    startPolling(activeJobId);
  }

  function finishJob() {
    if (jobFinished) return;
    jobFinished = true;
    stopPolling();
    if (wsGraceTimer) { clearTimeout(wsGraceTimer); wsGraceTimer = null; }
    activeJobId = null;
    progressWrap.classList.remove('hidden');
    setProgress(100, 'Complete');
    setTranslating(false);
    toast('Translation complete', 'success');
    refreshImages();
  }

  function applyJobStatus(job) {
    if (!job || jobFinished) return;
    progressWrap.classList.remove('hidden');
    const pct = typeof job.progress === 'number' ? job.progress : 0;
    setProgress(pct, `Translating… ${pct}%`);
    if (job.status === 'completed') finishJob();
    else if (job.status === 'failed') {
      toast(job.error_message || 'Translation failed', 'error');
      setTranslating(false);
      stopPolling();
      if (wsGraceTimer) { clearTimeout(wsGraceTimer); wsGraceTimer = null; }
      activeJobId = null;
    }
  }

  function handleEvent(msg) {
    if (!msg || !msg.type) return;
    const d = msg.data || {};
    switch (msg.type) {
      case 'job_status':
        progressWrap.classList.remove('hidden');
        setProgress(d.progress, `Translating ${d.current_image_name || ''}`.trim());
        break;
      case 'translation_complete':
        doneIds.add(d.image_id);
        renderGrid(); // refresh checkmarks without refetching
        break;
      case 'error':
        toast(`Error on a page: ${d.error}`, 'error');
        break;
      case 'job_complete':
        finishJob();
        break;
    }
  }

  // --- state restoration (page reload / re-attach) -------------------------
  // Without this the view would stay empty until the next live WebSocket
  // event — i.e. until the in-flight page's LLM call finishes.
  async function restoreState() {
    // Checkmarks for pages that already have a result (survives reloads).
    try {
      const ids = await api.getWorkResults(workId);
      if (disposed || !Array.isArray(ids)) return;
      let changed = false;
      for (const id of ids) { if (!doneIds.has(id)) { doneIds.add(id); changed = true; } }
      if (changed && images.length) renderGrid();
    } catch { /* non-fatal: marks simply stay empty */ }

    // Re-attach to an in-flight job after a page reload: progress bar + live
    // updates (WebSocket primary, REST polling only if it's down).
    try {
      const job = await api.getActiveJob(workId);
      if (disposed || !job) return;
      setTranslating(true);
      applyJobStatus(job);
      statusText.textContent = `Translating… ${typeof job.progress === 'number' ? job.progress : 0}% (reconnected)`;
      connectWs();
      armFallbackGrace(job.id);
    } catch (e) {
      if (!disposed && !/^404/.test(String(e.message))) toast(e.message, 'error');
    }
  }

  async function startTranslation() {
    if (!images.length) { toast('Add images first', 'error'); return; }
    setTranslating(true);
    progressWrap.classList.remove('hidden');
    setProgress(0, 'Starting…');
    try {
      const job = await api.startTranslation(workId, {});
      if (disposed) return;
      jobFinished = false;
      connectWs();
      armFallbackGrace(job.id); // WS is primary; REST polling only if it's down
      statusText.textContent = `Job #${job.id} started`;
    } catch (err) {
      const code = String(err.message).split(':')[0];
      if (code === '409') {
        // A job is already running elsewhere — attach to it and watch live.
        restoreState();
      } else {
        toast(err.message, 'error');
        setTranslating(false);
        progressWrap.classList.add('hidden');
      }
    }
  }

  // --- mount ----------------------------------------------------------------
  container.replaceChildren(
    el('div', { class: 'view-header' }, [backLink, titleEl]),
    el('div', { class: 'toolbar' }, [editBtn, addBtn, translateBtn]),
    progressWrap,
    dropzone,
    grid,
    fileInput,
  );

  connectWs(); // open live connection immediately (catches an in-flight job)
  loadWork();
  refreshImages();
  restoreState();

  return () => {
    disposed = true;
    stopPolling();
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (wsGraceTimer) clearTimeout(wsGraceTimer);
    if (ws) { try { ws.close(); } catch {} ws = null; }
    closeModal();
    document.title = 'llmanga-translator';
  };
}
