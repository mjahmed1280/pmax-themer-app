// Shared helpers (no inline scripts: CSP forbids them).
window.PM = (() => {
  const csrf = () => document.querySelector('meta[name="csrf-token"]').content;
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  async function api(path, opts = {}) {
    const init = { method: opts.method || 'GET', headers: {}, credentials: 'same-origin' };
    if (init.method !== 'GET') {
      init.headers['X-CSRF-Token'] = csrf();
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body ?? {});
    }
    const res = await fetch(path, init);
    if (res.status === 409) { location.href = '/connect'; throw new Error('Connect Google Ads'); }
    if (res.status === 401) { location.href = '/login'; throw new Error('Signed out'); }
    let data = null;
    try { data = await res.json(); } catch (_) { /* non-JSON */ }
    if (!res.ok) {
      let msg = (data && data.detail) || `Request failed (${res.status})`;
      if (Array.isArray(msg)) msg = msg.map((m) => m.msg).join('; ');
      throw new Error(msg);
    }
    return data;
  }
  return { api, esc, csrf };
})();
