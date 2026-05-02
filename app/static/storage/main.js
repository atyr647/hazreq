// Orchestrator. Loaded as <script type="module"> from base.html when
// the server is in browser-storage mode. Runs capability probes,
// reads any cached snapshot, fetches the server's view, decides
// whether to offer a hydrate prompt, then mirrors (or doesn't, if
// the user is about to restore).

import { probe } from './probe.js';
import { readMirror, refreshFromServer, writeMirror } from './mirror.js';
import { maybeOfferHydrate } from './hydrate.js';
import * as reminder from './reminder.js';

const KEYS = [
  'mips', 'spmigs', 'hazmat_items', 'mrcs', 'mrc_items',
  'requests', 'request_lines', 'audit_log',
];
function isEmpty(s) {
  return !s || KEYS.every((k) => Array.isArray(s[k]) && s[k].length === 0);
}

async function init() {
  const caps = await probe();
  console.info('[hazreq] storage caps:', caps);
  reminder.init();

  // 1. Read mirrored snapshot first — must do this before fetching
  //    server state, otherwise an empty server response would clobber
  //    the cached browser data we want to offer to restore.
  const mirrored = await readMirror();

  // 2. Fetch the server's authoritative state for this session.
  const serverSnap = await fetch('/admin/export.json', { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);

  // 3. If server is empty but the browser has cached data, prompt the
  //    user to restore — and DON'T write the empty server snapshot to
  //    the mirror, otherwise the cached snapshot is gone.
  if (isEmpty(serverSnap) && !isEmpty(mirrored)) {
    await maybeOfferHydrate(serverSnap);
    // Surface the cached snapshot to the reminder UI so the dirty/
    // beforeunload check still has something to compare against.
    window.dispatchEvent(new CustomEvent('hazreq:mirrored', { detail: mirrored }));
  } else if (serverSnap) {
    // Normal case: server has the truth; mirror it.
    await writeMirror(serverSnap);
    window.dispatchEvent(new CustomEvent('hazreq:mirrored', { detail: serverSnap }));
  }

  // 4. After every JS-initiated mutation, the existing app.js fires
  //    hazreq:mutation. Refresh the mirror in response so PATCH-driven
  //    auto-saves stay reflected in IDB.
  window.addEventListener('hazreq:mutation', () => {
    refreshFromServer();
  });

  // 5. Page navigations reload — DOMContentLoaded runs init() again,
  //    so each new server-rendered page gets a fresh refresh. No extra
  //    listener needed for full-page form submits.
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init, { once: true });
} else {
  init();
}
