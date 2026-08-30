// Thin fetch() wrapper for the REST API + WebSocket URL helper.
const BASE = '/api';

async function request(path, options = {}) {
  const opts = { ...options };
  // JSON bodies get a content-type; FormData must keep its multipart boundary.
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(BASE + path, opts);
  if (!res.ok) {
    let detail;
    try {
      const j = await res.json();
      detail = (j && j.detail) ? j.detail : JSON.stringify(j);
    } catch {
      detail = res.statusText || String(res.status);
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

export const api = {
  // Works
  listWorks: () => request('/works'),
  getWork: (id) => request(`/works/${id}`),
  createWork: ({ name, description }) =>
    request('/works', { method: 'POST', body: { name, description } }),
  updateWork: (id, patch) => request(`/works/${id}`, { method: 'PUT', body: patch }),
  deleteWork: (id) => request(`/works/${id}`, { method: 'DELETE' }),

  // Images
  listImages: (workId) => request(`/works/${workId}/images`),
  uploadImages: (workId, files) => {
    const fd = new FormData();
    for (const f of files) fd.append('files', f, f.name);
    return request(`/works/${workId}/images`, { method: 'POST', body: fd });
  },
  reorderImages: (workId, orderedIds) =>
    request(`/works/${workId}/images/reorder`, { method: 'PUT', body: { ordered_ids: orderedIds } }),
  updateImage: (imageId, patch) => request(`/images/${imageId}`, { method: 'PUT', body: patch }),
  deleteImage: (imageId) => request(`/images/${imageId}`, { method: 'DELETE' }),
  imageUrl: (imageId) => `${BASE}/images/${imageId}/file`,

  // Global app settings (key -> value strings)
  getSettings: () => request('/settings'),
  saveSettings: (patch) => request('/settings', { method: 'PUT', body: patch }),

  // Translation
  async startTranslation(workId, settings = {}) {
    const s = { glossary: null, ...settings };
    // Fields that can be filled in from the settings menu when not given explicitly.
    const fillable = ['target_language', 'system_prompt', 'image_resize_enabled', 'image_resize_px'];
    const missing = fillable.filter((k) => !(k in s));
    if (missing.length) {
      try {
        const stored = await api.getSettings();
        for (const k of missing) {
          if (!stored || typeof stored[k] !== 'string' || stored[k] === '') continue;
          if (k === 'image_resize_enabled') s[k] = stored[k] === '1';
          else if (k === 'image_resize_px') { const n = parseInt(stored[k], 10); s[k] = Number.isFinite(n) ? n : 1048; }
          else s[k] = k === 'target_language' ? stored[k].trim() : stored[k];
        }
      } catch { /* fall through to the defaults below */ }
    }
    const target_language = (typeof s.target_language === 'string' && s.target_language.trim()) || 'English';
    const system_prompt = typeof s.system_prompt === 'string' ? s.system_prompt : '';
    const image_resize_enabled = s.image_resize_enabled === undefined ? true : !!s.image_resize_enabled;
    const px = parseInt(s.image_resize_px, 10);
    const image_resize_px = Number.isFinite(px) && px > 0 ? px : 1048;
    return request(`/works/${workId}/translate`, {
      method: 'POST',
      body: { target_language, system_prompt, image_resize_enabled, image_resize_px, glossary: s.glossary },
    });
  },
  getJob: (jobId) => request(`/jobs/${jobId}`),
  getJobResults: (jobId) => request(`/jobs/${jobId}/results`),
  // State restoration after a page reload.
  getActiveJob: (workId) => request(`/works/${workId}/active-job`),
  getWorkResults: (workId) => request(`/works/${workId}/results`),
  getImageResult: (imageId) => request(`/images/${imageId}/result`),

  // WebSocket URL for a work's live events.
  wsUrl: (workId) => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}/ws/works/${workId}`;
  },
};
