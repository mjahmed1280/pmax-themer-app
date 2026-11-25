(() => {
  const { api, esc } = PM;
  const $ = (s) => document.querySelector(s);
  const err = (m) => { const e = $('#err'); e.textContent = m || ''; e.classList.toggle('hidden', !m); };

  function render(snap) {
    const li = (kind, v, removable) =>
      `<li><span>${esc(v)}</span>${removable ? `<button class="btn sm" data-kind="${kind}" data-value="${esc(v)}">Remove</button>` : ''}</li>`;
    const emails = snap.emails.map((v) => li('email', v, true)).concat(
      snap.seed_emails.map((v) => li('email', v, false).replace('</span>', ' <span class="pill">config</span></span>')));
    const domains = snap.domains.map((v) => li('domain', v, true)).concat(
      snap.seed_domains.map((v) => li('domain', v, false).replace('</span>', ' <span class="pill">config</span></span>')));
    $('#emails').innerHTML = emails.join('') || '<li class="muted">None. Only admins have access.</li>';
    $('#domains').innerHTML = domains.join('') || '<li class="muted">None</li>';
    $('#admins').innerHTML = snap.admins.map((v) => li('admin', v, false)).join('');
  }

  async function load() {
    try {
      render(await api('/api/admin/access'));
      const a = await api('/api/admin/audit');
      $('#audit tbody').innerHTML = a.rows.map((r) => {
        const { ts, user, action, ...rest } = r;
        return `<tr><td>${esc(ts)}</td><td>${esc(user)}</td><td>${esc(action)}</td><td class="small muted">${esc(JSON.stringify(rest))}</td></tr>`;
      }).join('') || '<tr><td colspan="4" class="muted">No activity yet</td></tr>';
    } catch (e) { err(e.message); }
  }

  for (const kind of ['email', 'domain']) {
    $(`#add-${kind}`).addEventListener('submit', async (ev) => {
      ev.preventDefault(); err('');
      const input = ev.target.elements.value;
      try { render(await api('/api/admin/access/add', { method: 'POST', body: { kind, value: input.value } })); input.value = ''; load(); }
      catch (e) { err(e.message); }
    });
  }
  document.addEventListener('click', async (ev) => {
    const b = ev.target.closest('button[data-kind]');
    if (!b) return;
    err('');
    try { render(await api('/api/admin/access/remove', { method: 'POST', body: { kind: b.dataset.kind, value: b.dataset.value } })); load(); }
    catch (e) { err(e.message); }
  });
  load();
})();
