// Shared helpers: tiny DOM builder, hash router, and a small toast helper.
// Hash routing keeps the URL path at "/", so no server catch-all is needed.

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined) continue;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return node;
}

function toSegs(p) {
  return p.replace(/\/+?/g, '/').replace(/^\//, '').replace(/\/$/, '').split('/');
}

export class Router {
  constructor() {
    this.routes = [];
    this._cleanup = null;
  }

  add(pattern, handler) {
    this.routes.push({ pattern, handler });
  }

  navigate(hash) {
    if (location.hash === hash) return;
    location.hash = hash;
  }

  start() {
    window.addEventListener('hashchange', () => this._dispatch());
    this._dispatch();
  }

  _match(path) {
    const segs = toSegs(path);
    for (const { pattern, handler } of this.routes) {
      const pseg = toSegs(pattern);
      if (pseg.length !== segs.length) continue;
      const params = {};
      let ok = true;
      for (let i = 0; i < pseg.length; i++) {
        if (pseg[i].startsWith(':')) {
          params[pseg[i].slice(1)] = decodeURIComponent(segs[i]);
        } else if (pseg[i] !== segs[i]) {
          ok = false;
          break;
        }
      }
      if (ok) return { handler, params };
    }
    return null;
  }

  _dispatch() {
    const path = (location.hash || '#/').replace(/^#/, '') || '/';
    const found = this._match(path);
    if (!found) { location.hash = '#/'; return; }
    if (this._cleanup) { try { this._cleanup(); } catch (e) { console.error(e); } this._cleanup = null; }
    let cleanup = null;
    try {
      const result = found.handler(found.params);
      cleanup = typeof result === 'function' ? result : null;
    } catch (e) {
      console.error('render error', e);
    }
    this._cleanup = cleanup;
  }
}

// App-level toast. Writes into #toast-root if present, else falls back to alert.
export function toast(message, kind = 'info', ms = 3500) {
  const root = document.getElementById('toast-root');
  if (!root) { window.alert(String(message)); return; }
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = String(message);
  root.appendChild(node);
  requestAnimationFrame(() => node.classList.add('show'));
  setTimeout(() => {
    node.classList.remove('show');
    setTimeout(() => node.remove(), 300);
  }, ms);
}
