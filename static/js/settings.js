// Settings modal — global app settings (stored server-side as key -> value strings).
// Data-driven: each entry in SETTINGS_ITEMS declares one setting with `keys`
// (one or more storage keys), a label/description, and create(scope) returning
// {load(values), read() -> values} where values maps the item's keys. Adding a
// new setting is one more entry; the backend (/api/settings) already accepts
// arbitrary keys.

import { api } from './api.js';
import { el, toast } from './state.js';

const POPULAR_LANGUAGES = [
  'English',
  'Chinese (Simplified)',
  'Korean',
  'Spanish',
  'French',
  'German',
  'Russian',
  'Portuguese (Brazilian)',
  'Italian',
  'Turkish',
  'Vietnamese',
  'Indonesian',
  'Thai',
  'Polish',
  'Ukrainian',
];

const CUSTOM_VALUE = '__custom__'; // sentinel option value for manual language input

function createLanguageControls(scope) {
  const select = el('select');
  for (const lang of POPULAR_LANGUAGES) select.appendChild(el('option', { value: lang, text: lang }));
  select.appendChild(el('option', { value: CUSTOM_VALUE, text: 'Custom…' }));
  const customInput = el('input', { type: 'text', placeholder: 'Type a language name…', hidden: '' });

  function sync() {
    const isCustom = select.value === CUSTOM_VALUE;
    customInput.hidden = !isCustom;
    if (isCustom) customInput.focus();
  }
  select.addEventListener('change', sync);
  scope.replaceChildren(select, customInput);

  return {
    load(values) {
      const v = values.target_language;
      const raw = typeof v === 'string' ? v.trim() : '';
      if (!raw || POPULAR_LANGUAGES.includes(raw)) {
        select.value = raw || POPULAR_LANGUAGES[0]; // empty/unknown -> default language
      } else {
        select.value = CUSTOM_VALUE;
        customInput.value = raw;
      }
      sync();
    },
    read() {
      const value = select.value === CUSTOM_VALUE ? customInput.value.trim() : (select.value || '');
      return { target_language: value };
    },
  };
}

// One entry per setting. `create(scope)` builds the control(s) inside scope and
// returns {load(values), read() -> values} so the modal can hydrate/collect
// generically; optional validate(values) -> error string blocks saving when invalid.
function createSystemPromptControls(scope) {
  const textarea = el('textarea', { rows: '6', placeholder: 'Optional extra instructions…' });
  scope.replaceChildren(textarea);
  return {
    load(values) {
      textarea.value = typeof values.system_prompt === 'string' ? values.system_prompt : '';
    },
    read() {
      // empty is valid: built-in prompt only
      return { system_prompt: textarea.value };
    },
  };
}

function createImageResizeControls(scope) {
  const check = el('input', { type: 'checkbox' });
  check.checked = true; // app default: resize enabled
  const num = el('input', { type: 'number', min: '64', max: '16384', step: '64', value: '1048' });
  const row = el('div', { class: 'row' }, [check, num, el('span', { text: 'px' })]);
  check.addEventListener('change', () => { num.disabled = !check.checked; });
  scope.replaceChildren(row);
  return {
    load(values) {
      const px = parseInt(values.image_resize_px, 10);
      check.checked = values.image_resize_enabled === undefined ? true : values.image_resize_enabled === '1';
      num.value = Number.isFinite(px) ? px : 1048;
      num.disabled = !check.checked;
    },
    read() {
      return {
        image_resize_enabled: check.checked ? '1' : '0',
        image_resize_px: String(num.value || ''),
      };
    },
  };
}

const SETTINGS_ITEMS = [
  {
    keys: ['target_language'],
    label: 'Translation language',
    description: 'Language to translate the manga into.',
    create: createLanguageControls,
    validate(values) {
      return values.target_language && String(values.target_language).trim() ? null : 'Enter a language name';
    },
  },
  {
    keys: ['system_prompt'],
    label: 'System prompt',
    description: 'Optional instructions prepended to the built-in translator prompt. Leave empty for default behavior.',
    create: createSystemPromptControls,
  },
  {
    keys: ['image_resize_enabled', 'image_resize_px'],
    label: 'Image resize',
    description: 'When enabled, pages are resized so their biggest side equals this value before being sent to the LLM.',
    create: createImageResizeControls,
    validate(values) {
      if (values.image_resize_enabled !== '1') return null;
      const px = parseInt(values.image_resize_px, 10);
      return Number.isInteger(px) && px >= 64 && px <= 16384 ? null : 'Enter a size between 64 and 16384 px';
    },
  },
];

let activeClose = null; // only one settings modal at a time

export function openSettings() {
  if (activeClose) activeClose();

  const controls = [];
  const sections = SETTINGS_ITEMS.map((item) => {
    // Controls get their own container so create()'s replaceChildren() can't
    // wipe out the label/description that live in the section wrapper.
    const controlScope = el('div', { class: 'settings-controls' });
    const section = el('div', { class: 'settings-item' }, [
      el('label', { class: 'settings-label', text: item.label }),
      el('p', { class: 'muted settings-desc', text: item.description || '' }),
      controlScope,
    ]);
    controls.push(item.create(controlScope));
    return section;
  });

  const saveBtn = el('button', { class: 'btn primary', text: 'Save' });
  const cancelBtn = el('button', { class: 'btn', text: 'Cancel' });
  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    modal.remove();
    document.removeEventListener('keydown', onKey);
    if (activeClose === close) activeClose = null;
  }
  const modal = el('div', { class: 'modal-overlay' }, [
    el('div', { class: 'modal settings-modal' }, [
      el('h3', { text: 'Settings' }),
      ...sections,
      el('div', { class: 'row' }, [saveBtn, cancelBtn]),
    ]),
  ]);

  function onKey(e) { if (e.key === 'Escape') close(); }
  document.addEventListener('keydown', onKey);
  cancelBtn.addEventListener('click', close);
  saveBtn.addEventListener('click', async () => {
    const patch = {};
    for (let i = 0; i < SETTINGS_ITEMS.length; i++) {
      const values = controls[i].read();
      const err = SETTINGS_ITEMS[i].validate ? SETTINGS_ITEMS[i].validate(values) : null;
      if (err) { toast(err, 'error'); return; }
      Object.assign(patch, values);
    }
    try {
      await api.saveSettings(patch);
      toast('Settings saved', 'success');
      close();
    } catch (e) { toast(e.message, 'error'); }
  });

  document.body.appendChild(modal);
  activeClose = close;

  // Hydrate controls from the server; non-fatal if it fails (defaults stay).
  api.getSettings()
    .then((settings) => {
      if (closed) return;
      const stored = settings || {};
      SETTINGS_ITEMS.forEach((item, i) => {
        const values = {};
        for (const k of item.keys) values[k] = stored[k];
        controls[i].load(values);
      });
    })
    .catch(() => {});

  const firstControl = modal.querySelector('select, input');
  if (firstControl) firstControl.focus();
}
