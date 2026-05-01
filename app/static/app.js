// hazreq — minimal vanilla JS for partial updates and auto-save.
// All interactions use data-hr-* attributes. No external deps.

(function () {
  'use strict';

  // --- helpers --------------------------------------------------

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  function debounce(fn, ms) {
    let t;
    return function () {
      const args = arguments, ctx = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(ctx, args); }, ms);
    };
  }

  function setStatus(el, kind, msg) {
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'save-status ' + (kind || '');
  }

  function findStatusFor(el) {
    const root = el.closest('[data-hr-form]') || document;
    return root.querySelector('[data-hr-status]');
  }

  async function send(method, url, body) {
    const opts = { method: method, headers: {} };
    if (body !== undefined) {
      if (body instanceof FormData) {
        opts.body = body;
      } else {
        opts.headers['Content-Type'] = 'application/x-www-form-urlencoded';
        opts.body = new URLSearchParams(body).toString();
      }
    }
    const resp = await fetch(url, opts);
    if (!resp.ok) {
      const text = await resp.text().catch(function () { return ''; });
      throw new Error('HTTP ' + resp.status + ': ' + text.slice(0, 200));
    }
    const ct = resp.headers.get('content-type') || '';
    if (ct.indexOf('text/html') !== -1) return await resp.text();
    return resp;
  }

  function swap(target, html) {
    if (!target) return;
    target.innerHTML = html;
    bind(target);
  }

  function appendSwap(target, html) {
    if (!target) return;
    const tmp = document.createElement('div');
    tmp.innerHTML = html.trim();
    while (tmp.firstChild) {
      const node = tmp.firstChild;
      target.appendChild(node);
      if (node.nodeType === 1) bind(node);
    }
  }

  function getTarget(el) {
    const sel = el.dataset.hrTarget;
    if (!sel) return null;
    if (sel === 'closest-tr') return el.closest('tr');
    if (sel === 'closest-row') return el.closest('[data-hr-row]');
    if (sel.charAt(0) === '#' || sel.charAt(0) === '.') return $(sel);
    return $('#' + sel);
  }

  // --- behaviors ------------------------------------------------

  // Auto-save on blur or change for inputs with data-hr-save="patch:/url"
  function bindAutoSave(el) {
    if (el.__hrBound) return;
    el.__hrBound = true;
    const action = el.dataset.hrSave;
    const idx = action.indexOf(':');
    const method = action.slice(0, idx).toUpperCase();
    const url = action.slice(idx + 1);
    const status = findStatusFor(el);

    async function save() {
      const name = el.name;
      if (!name) return;
      setStatus(status, 'saving', 'Saving…');
      try {
        await send(method, url, [[name, el.value]]);
        setStatus(status, 'saved', 'Saved');
        setTimeout(function () { setStatus(status, '', ''); }, 1500);
      } catch (err) {
        setStatus(status, 'error', "Couldn't save — " + (err.message || 'retry'));
      }
    }

    el.addEventListener('change', save);
    el.addEventListener('blur', save);
  }

  // Form submit replacement for data-hr-form with data-hr-action
  function bindForm(form) {
    if (form.__hrBound) return;
    form.__hrBound = true;
    form.addEventListener('submit', async function (ev) {
      ev.preventDefault();
      const url = form.action;
      const method = (form.method || 'POST').toUpperCase();
      const target = getTarget(form);
      const swapMode = form.dataset.hrSwap || 'innerHTML';
      const fd = new FormData(form);
      try {
        const html = await send(method, url, fd);
        if (typeof html === 'string') {
          if (target) {
            if (swapMode === 'append') appendSwap(target, html);
            else swap(target, html);
          }
          if (form.dataset.hrReset === '1') form.reset();
        } else if (form.dataset.hrReload === '1') {
          window.location.reload();
        }
      } catch (err) {
        const status = findStatusFor(form);
        setStatus(status, 'error', err.message || 'Action failed');
      }
    });
  }

  // Buttons / links with data-hr-action="METHOD:url"
  function bindAction(el) {
    if (el.__hrBound) return;
    el.__hrBound = true;
    el.addEventListener('click', async function (ev) {
      ev.preventDefault();
      const confirmMsg = el.dataset.hrConfirm;
      if (confirmMsg && !window.confirm(confirmMsg)) return;
      const action = el.dataset.hrAction;
      const idx = action.indexOf(':');
      const method = action.slice(0, idx).toUpperCase();
      const url = action.slice(idx + 1);
      const target = getTarget(el);
      const swapMode = el.dataset.hrSwap || 'innerHTML';
      try {
        const html = await send(method, url);
        if (typeof html === 'string') {
          if (swapMode === 'remove' && target) {
            target.parentNode.removeChild(target);
          } else if (target) {
            if (swapMode === 'append') appendSwap(target, html);
            else swap(target, html);
          }
        } else if (el.dataset.hrReload === '1') {
          window.location.reload();
        } else if (swapMode === 'remove' && target) {
          target.parentNode.removeChild(target);
        }
      } catch (err) {
        alert(err.message || 'Action failed');
      }
    });
  }

  // Search-as-you-type: data-hr-search="/url" + data-hr-target
  function bindSearch(el) {
    if (el.__hrBound) return;
    el.__hrBound = true;
    const url = el.dataset.hrSearch;
    const minChars = parseInt(el.dataset.hrMin || '1', 10);
    const param = el.dataset.hrParam || 'q';
    const target = getTarget(el);
    const extra = el.dataset.hrExtra || '';

    const run = debounce(async function () {
      const q = el.value.trim();
      if (q.length < minChars) {
        if (target) target.innerHTML = '';
        return;
      }
      const u = url + (url.indexOf('?') === -1 ? '?' : '&') + param + '=' + encodeURIComponent(q) + (extra ? '&' + extra : '');
      try {
        const html = await send('GET', u);
        if (typeof html === 'string') swap(target, html);
      } catch (err) {
        if (target) target.innerHTML = '<div class="empty">' + (err.message || 'Search failed') + '</div>';
      }
    }, 200);

    el.addEventListener('input', run);
  }

  // Bind everything inside `root`.
  function bind(root) {
    $$('[data-hr-save]', root).forEach(bindAutoSave);
    $$('form[data-hr-form]', root).forEach(bindForm);
    $$('[data-hr-action]', root).forEach(bindAction);
    $$('[data-hr-search]', root).forEach(bindSearch);
  }

  document.addEventListener('DOMContentLoaded', function () { bind(document); });

  // Expose for inline scripts that need to re-bind dynamically.
  window.hazreq = { bind: bind };
})();
