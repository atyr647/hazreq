// Capability probes for IndexedDB / OPFS / localStorage.
// Each probe actually writes/reads/deletes a tiny record before
// claiming healthy, because Safari has shipped half-implementations
// for both IDB and OPFS in the past — feature-detect alone is not
// enough.
//
// Results are cached for the lifetime of the page; they don't change
// between requests, and re-probing burns IO.

const IDB_DB = 'hazreq-probe';
const OPFS_DIR = 'hazreq-probe';

let cached = null;

async function probeIdb() {
  if (typeof indexedDB === 'undefined') return false;
  try {
    const open = indexedDB.open(IDB_DB, 1);
    open.onupgradeneeded = () => open.result.createObjectStore('probe');
    const db = await new Promise((res, rej) => {
      open.onsuccess = () => res(open.result);
      open.onerror = () => rej(open.error);
    });
    const tx = db.transaction('probe', 'readwrite');
    const store = tx.objectStore('probe');
    await new Promise((res, rej) => {
      const r = store.put({ ts: Date.now() }, 'probe');
      r.onsuccess = res;
      r.onerror = () => rej(r.error);
    });
    await new Promise((res, rej) => {
      const r = store.delete('probe');
      r.onsuccess = res;
      r.onerror = () => rej(r.error);
    });
    db.close();
    indexedDB.deleteDatabase(IDB_DB);
    return true;
  } catch (e) {
    return false;
  }
}

async function probeOpfs() {
  if (typeof navigator === 'undefined') return false;
  if (!navigator.storage || typeof navigator.storage.getDirectory !== 'function') return false;
  try {
    const root = await navigator.storage.getDirectory();
    const dir = await root.getDirectoryHandle(OPFS_DIR, { create: true });
    const file = await dir.getFileHandle('probe.txt', { create: true });
    if (typeof file.createWritable !== 'function') return false;
    const w = await file.createWritable();
    await w.write('ok');
    await w.close();
    const f = await file.getFile();
    const txt = await f.text();
    if (txt !== 'ok') return false;
    await dir.removeEntry('probe.txt');
    await root.removeEntry(OPFS_DIR, { recursive: true });
    return true;
  } catch (e) {
    return false;
  }
}

function probeLocalStorage() {
  try {
    const k = '__hazreq_probe__';
    localStorage.setItem(k, '1');
    const ok = localStorage.getItem(k) === '1';
    localStorage.removeItem(k);
    return ok;
  } catch (e) {
    return false;
  }
}

async function probePersistentStorage() {
  if (typeof navigator === 'undefined' || !navigator.storage || typeof navigator.storage.persist !== 'function') {
    return null;  // capability unknown
  }
  try {
    if (typeof navigator.storage.persisted === 'function') {
      const already = await navigator.storage.persisted();
      if (already) return true;
    }
  } catch (e) {
    // fall through
  }
  return null;  // not granted yet — caller can request
}

export async function probe() {
  if (cached) return cached;
  const [idb, opfs, ls, persistent] = await Promise.all([
    probeIdb(),
    probeOpfs(),
    Promise.resolve(probeLocalStorage()),
    probePersistentStorage(),
  ]);
  cached = { idb, opfs, localStorage: ls, persistent };
  return cached;
}

// Best-effort persistent-storage prompt. Per ShoreCalc: ask once,
// remember the answer in localStorage, never bug the user again.
export async function requestPersistenceOnce() {
  if (!('localStorage' in globalThis)) return;
  if (localStorage.getItem('__hazreq_persistence_asked__')) return;
  localStorage.setItem('__hazreq_persistence_asked__', '1');
  try {
    if (navigator.storage && typeof navigator.storage.persist === 'function') {
      await navigator.storage.persist();
    }
  } catch (e) {
    // ignored
  }
}
