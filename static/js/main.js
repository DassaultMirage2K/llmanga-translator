// SPA bootstrap: mount the router and dispatch routes to view renderers.
import { Router } from './state.js';
import { renderWorksList, renderWorkDetail } from './works.js';
import { renderViewer } from './viewer.js';
import { openSettings } from './settings.js';

const app = document.getElementById('app');

// Global chrome: the settings button lives in the top bar (every route).
document.getElementById('settings-btn').addEventListener('click', () => openSettings());

const router = new Router();
router.add('/', (params) => renderWorksList(app));
router.add('/:id', (params) => renderWorkDetail(app, params));
router.add('/:id/image/:imageId', (params) => renderViewer(app, params));

// Only start once the DOM is ready.
if (document.readyState === 'loading') {
  window.addEventListener('DOMContentLoaded', () => router.start());
} else {
  router.start();
}
