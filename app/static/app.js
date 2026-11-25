// PMax Themer wizard (vanilla JS, server does all validation).
(() => {
  const { api, esc } = PM;
  const $ = (s) => document.querySelector(s);
  const STEPS = ['Account', 'Target PMax', 'Source keywords', 'Strategy', 'Review', 'Run'];
  const iso = (d) => d.toISOString().slice(0, 10);
  const today = new Date();
  const ago = new Date(Date.now() - 90 * 864e5);

  const S = {
    step: 0, me: null, loading: '', error: '',
    accounts: null, account: null,
    pmax: null, target: null,
    structure: null, campSel: new Set(), agSel: new Set(),
    q: { from: iso(ago), to: iso(today), min: 1 },
    kwRows: null, kwSel: new Set(), manualSeq: 0,
    strat: { bucket: 25, prefix: 'PMax Themer', copyAud: true },
    filters: { acct: '', pmax: '', kw: '' },
    review: null, run: null, confirm: '',
  };

  // ------------------------------------------------------------- helpers
  const norm = (t) => t.replace(/\s+/g, ' ').trim();
  const selectedKeywords = () => (S.kwRows || []).filter((r) => S.kwSel.has(r.id)).map((r) => r.keyword);
  const uniqueCount = () => new Set(selectedKeywords().map((k) => norm(k).toLowerCase()).filter(Boolean)).size;
  const groupsEst = () => Math.ceil(uniqueCount() / Math.max(1, Math.min(25, +S.strat.bucket || 25)));
  const adGroupIds = () => {
    const ids = new Set(S.agSel);
    for (const c of S.structure || []) if (S.campSel.has(c.campaign_id)) c.ad_groups.forEach((a) => ids.add(a.ad_group_id));
    return [...ids];
  };
  const payload = () => ({
    customer_id: S.account.id, login_customer_id: S.account.login_customer_id,
    campaign_id: S.target.campaign_id, asset_group_id: S.target.asset_group_id,
    keywords: selectedKeywords(), bucket_size: Math.max(1, Math.min(25, +S.strat.bucket || 25)),
    name_prefix: S.strat.prefix || 'PMax Themer', copy_audiences: !!S.strat.copyAud,
  });
  const invalidate = () => { S.review = null; S.run = null; S.confirm = ''; };

  function canEnter(n) {
    if (n <= 0) return true;
    if (n >= 1 && !S.account) return false;
    if (n >= 2 && !S.target) return false;
    if (n >= 3 && !(S.kwSel.size > 0)) return false;
    if (n >= 5 && !(S.review && S.review.results.some((r) => r.status === 'validated'))) return false;
    return true;
  }
  const isDone = (n) => (n === 0 && S.account) || (n === 1 && S.target) || (n === 2 && S.kwSel.size > 0) ||
    (n === 3 && S.kwSel.size > 0 && S.step > 3) || (n === 4 && S.review) || (n === 5 && S.run);

  async function guarded(label, fn) {
    S.loading = label; S.error = ''; render();
    try { await fn(); } catch (e) { S.error = e.message; } finally { S.loading = ''; render(); }
  }

  async function go(n) {
    if (!canEnter(n)) return;
    S.step = n; S.error = '';
    render();
    if (n === 0 && !S.accounts) await guarded('Loading accounts…', async () => { S.accounts = (await api('/api/accounts')).accounts; });
    if (n === 1 && !S.pmax) await guarded('Loading PMax asset groups…', async () => {
      S.pmax = (await api(`/api/accounts/${S.account.id}/pmax-asset-groups?login=${S.account.login_customer_id || ''}`)).rows; });
    if (n === 2 && !S.structure) await guarded('Loading search campaigns…', async () => {
      S.structure = (await api(`/api/accounts/${S.account.id}/search-structure?login=${S.account.login_customer_id || ''}`)).campaigns; });
  }

  // -------------------------------------------------------------- render
  function renderStepper() {
    $('#stepper').innerHTML = STEPS.map((t, i) =>
      `<button class="step ${i === S.step ? 'active' : ''} ${isDone(i) && i !== S.step ? 'done' : ''}" data-act="go" data-n="${i}" ${canEnter(i) ? '' : 'disabled'}>
        <span class="n">${isDone(i) && i !== S.step ? '✓' : i + 1}</span>${esc(t)}</button>`).join('');
  }

  function render() {
    renderStepper();
    const body = [step0, step1, step2, step3, step4, step5][S.step]();
    $('#stage').innerHTML =
      (S.error ? `<div class="alert alert-error" role="alert">${esc(S.error)}</div>` : '') +
      (S.loading ? `<div class="card"><span class="spinner"></span>${esc(S.loading)}</div>` : body);
    applyFilters();
    renderSummary();
  }

  function renderSummary() {
    const rows = [
      ['Account', S.account ? `${esc(S.account.name)}<br><span class="muted small">${esc(S.account.id)}</span>` : '<span class="muted">not set</span>'],
      ['Target', S.target ? `${esc(S.target.asset_group_name)}<br><span class="muted small">${esc(S.target.campaign_name)}</span>` : '<span class="muted">not set</span>'],
      ['Source ad groups', S.structure ? adGroupIds().length : '–'],
      ['Keywords selected', S.kwRows ? S.kwSel.size : '–'],
      ['After clean-up', S.kwRows ? uniqueCount() : '–'],
      ['New asset groups', S.kwRows ? `<b>${groupsEst()}</b> <span class="muted small">(paused)</span>` : '–'],
    ];
    $('#summary').innerHTML = `<h2>Selection summary</h2><dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>
      <p class="muted small">Nothing is changed in Google Ads until the final Run step, and new asset groups are always created paused.</p>
      ${S.me && S.me.credentials.mode === 'user-oauth' ? `<p class="muted small">${esc(S.me.credentials.detail)}.
        <button class="link" data-act="disconnect">Disconnect</button></p>` : ''}`;
  }

  const nav = (back, next, nextLabel = 'Next →', nextOff = false) => `<div class="actions">
    <div>${back == null ? '' : `<button class="btn" data-act="go" data-n="${back}">← Back</button>`}</div>
    <div class="right">${next == null ? '' : `<button class="btn primary" data-act="go" data-n="${next}" ${nextOff ? 'disabled' : ''}>${nextLabel}</button>`}</div></div>`;

  // ---- step 0: account
  function step0() {
    if (!S.accounts) return '';
    const mccs = S.accounts.filter((a) => a.is_manager);
    const leaf = (a) => `<tr class="pick ${S.account && S.account.id === a.id ? 'sel' : ''}" data-act="pick-account" data-id="${esc(a.id)}" data-text="${esc((a.name + ' ' + a.id).toLowerCase())}">
        <td><input type="radio" tabindex="-1" ${S.account && S.account.id === a.id ? 'checked' : ''}></td><td>${esc(a.name)}</td><td class="muted">${esc(a.id)}</td></tr>`;
    let rows = '';
    for (const m of mccs) {
      const kids = S.accounts.filter((a) => !a.is_manager && a.parent === m.id);
      if (!kids.length) continue;
      rows += `<tr><td colspan="3" class="muted small"><b>${esc(m.name)}</b> · manager ${esc(m.id)}</td></tr>` + kids.map(leaf).join('');
    }
    const direct = S.accounts.filter((a) => !a.is_manager && !a.parent);
    if (direct.length) rows += `<tr><td colspan="3" class="muted small"><b>Direct access</b></td></tr>` + direct.map(leaf).join('');
    return `<div class="card"><h2>1 · Choose the Google Ads account</h2>
      <p class="muted">Only accounts your own Google Ads login can access are listed.</p>
      <div class="toolbar"><input type="search" placeholder="Filter accounts…" data-filter="acct" value="${esc(S.filters.acct)}"></div>
      ${rows ? `<div class="table-wrap"><table><tbody>${rows}</tbody></table></div>` : `<div class="empty">${S.me && S.me.credentials.mode === 'user-oauth' ? 'This Google account has no Google Ads access. Ask an account admin to add you, then reconnect.' : 'No accessible accounts found.'}</div>`}
      ${nav(null, 1, 'Next →', !S.account)}</div>`;
  }

  // ---- step 1: target
  function step1() {
    if (!S.pmax) return '';
    const rows = S.pmax.map((r) => `<tr class="pick ${S.target && S.target.asset_group_id === r.asset_group_id ? 'sel' : ''}" data-act="pick-ag" data-id="${esc(r.asset_group_id)}"
        data-text="${esc((r.campaign_name + ' ' + r.asset_group_name + ' ' + r.asset_group_id).toLowerCase())}">
      <td><input type="radio" tabindex="-1" ${S.target && S.target.asset_group_id === r.asset_group_id ? 'checked' : ''}></td>
      <td>${esc(r.campaign_name)}<div class="muted small">${esc(r.campaign_id)}</div></td>
      <td>${esc(r.asset_group_name)}<div class="muted small">${esc(r.asset_group_id)}</div></td>
      <td><span class="pill ${r.status === 'ENABLED' ? 'ok' : 'warn'}">${esc(r.status)}</span></td></tr>`).join('');
    return `<div class="card"><h2>2 · Choose the PMax asset group to clone</h2>
      <p class="muted">Its assets, URL and reusable audiences are copied into each new paused asset group; the new groups are created in this asset group's campaign.</p>
      <div class="toolbar"><input type="search" placeholder="Filter campaigns / asset groups…" data-filter="pmax" value="${esc(S.filters.pmax)}"></div>
      ${rows ? `<div class="table-wrap"><table><thead><tr><th></th><th>Campaign</th><th>Asset group</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table></div>`
             : '<div class="empty">No enabled PMax campaigns with asset groups in this account.</div>'}
      ${nav(0, 2, 'Next →', !S.target)}</div>`;
  }

  // ---- step 2: source
  function step2() {
    if (!S.structure) return '';
    const tree = S.structure.map((c) => `<div class="camp"><label class="inline"><input type="checkbox" data-act="toggle-camp" data-id="${esc(c.campaign_id)}" ${S.campSel.has(c.campaign_id) ? 'checked' : ''}> ${esc(c.campaign_name)} <span class="muted small">${esc(c.campaign_id)}</span></label></div>
      <div class="ags">${c.ad_groups.map((a) => `<label class="inline"><input type="checkbox" data-act="toggle-ag" data-id="${esc(a.ad_group_id)}" data-camp="${esc(c.campaign_id)}"
        ${S.campSel.has(c.campaign_id) || S.agSel.has(a.ad_group_id) ? 'checked' : ''}> ${esc(a.ad_group_name)}</label>`).join('')}</div>`).join('');
    const kwBlock = S.kwRows ? `
      <h3>Keywords <span class="muted small" id="kw-count">${S.kwSel.size} of ${S.kwRows.length} selected</span></h3>
      <div class="toolbar">
        <input type="search" placeholder="Filter keywords…" data-filter="kw" value="${esc(S.filters.kw)}">
        <button class="btn sm" data-act="kw-all">Select visible</button><button class="btn sm" data-act="kw-none">Clear all</button>
      </div>
      ${S.kwRows.length ? `<div class="table-wrap"><table><thead><tr><th></th><th>Keyword</th><th>Campaign / ad group</th><th class="num">Clicks</th><th class="num">Conv.</th><th class="num">Cost</th></tr></thead><tbody>
        ${S.kwRows.map((r) => `<tr class="${S.kwSel.has(r.id) ? 'sel' : ''}" data-rid="${r.id}" data-text="${esc((r.keyword + ' ' + r.campaign_name + ' ' + r.ad_group_name).toLowerCase())}">
          <td><input type="checkbox" data-act="toggle-kw" data-id="${r.id}" ${S.kwSel.has(r.id) ? 'checked' : ''}></td>
          <td>${esc(r.keyword)}</td><td class="muted small src">${esc(r.campaign_name)} › ${esc(r.ad_group_name)}</td>
          <td class="num">${r.clicks ?? ''}</td><td class="num">${r.conversions ?? ''}</td><td class="num">${r.cost ?? ''}</td></tr>`).join('')}</tbody></table></div>`
        : '<div class="empty">No keywords matched. Try a wider date range or a lower minimum clicks.</div>'}
      <h3>Add extra themes manually</h3>
      <textarea id="manual" placeholder="One theme per line"></textarea>
      <button class="btn sm" data-act="add-manual">Add themes</button>` : '';
    return `<div class="card"><h2>3 · Choose the source Search keywords</h2>
      <p class="muted">Pick campaigns (or individual ad groups). Keywords become PMax search themes.</p>
      ${tree || '<div class="empty">No enabled Standard Search campaigns found.</div>'}
      <div class="fields">
        <label>From<input type="date" data-bind="q.from" value="${esc(S.q.from)}"></label>
        <label>To<input type="date" data-bind="q.to" value="${esc(S.q.to)}"></label>
        <label>Min. clicks<input type="number" min="0" data-bind="q.min" value="${esc(S.q.min)}"></label>
      </div>
      <button class="btn primary" data-act="fetch-kw" ${adGroupIds().length ? '' : 'disabled'}>Fetch keywords</button>
      ${kwBlock}${nav(1, 3, 'Next →', S.kwSel.size === 0)}</div>`;
  }

  // ---- step 3: strategy
  function step3() {
    return `<div class="card"><h2>4 · Strategy</h2>
      <label class="inline"><input type="radio" checked disabled> <span><b>Equal buckets</b> · split all selected keywords into new asset groups of up to N search themes each</span></label>
      <p class="muted small">Preserving your Search ad-group structure is not available yet.</p>
      <div class="fields">
        <label>Themes per asset group (max 25)<input type="number" min="1" max="25" data-bind="strat.bucket" value="${esc(S.strat.bucket)}"></label>
        <label>Asset group name prefix<input maxlength="100" data-bind="strat.prefix" value="${esc(S.strat.prefix)}"></label>
      </div>
      <label class="inline"><input type="checkbox" data-bind="strat.copyAud" ${S.strat.copyAud ? 'checked' : ''}> Reuse the source asset group's customer-level audience signals</label>
      <div class="alert alert-info">New asset groups are always created <b>paused</b>. Names look like “${esc(S.strat.prefix || 'PMax Themer')} 1/${groupsEst() || 'N'}”.</div>
      ${nav(2, 4, 'Review →')}</div>`;
  }

  // ---- step 4: review
  function step4() {
    const r = S.review;
    let out = '';
    if (!r) {
      out = `<p class="muted">Builds the plan and asks Google Ads to <b>validate</b> every search theme against policy. This is a dry-run: nothing is created.</p>
        <button class="btn primary" data-act="validate">Run policy check</button>`;
    } else {
      const st = r.plan.stats;
      const banned = r.results.flatMap((x) => x.banned);
      const failed = r.results.filter((x) => x.status === 'failed' || x.status === 'skipped');
      out = `<div class="kv"><div><b>${st.input}</b><span>keywords selected</span></div><div><b>${st.duplicates_removed}</b><span>duplicates removed</span></div>
        <div><b>${st.unique}</b><span>unique themes</span></div><div><b>${st.asset_groups}</b><span>asset groups to create</span></div></div>
        <h3>Clone source</h3>
        <p><b>${esc(r.source.name)}</b> · ${r.source.asset_total} assets · ${r.source.audiences} reusable audience(s)<br>
        <span class="muted small">URL: ${esc(r.source.final_url || 'none')}</span></p>
        <div class="chips">${Object.entries(r.source.asset_counts).map(([k, v]) => `<span class="chip">${esc(k)} × ${v}</span>`).join('')}</div>
        ${r.plan.skipped.length ? `<div class="alert alert-warn">${r.plan.skipped.length} theme(s) skipped: ${r.plan.skipped.map((s) => esc(s.theme.slice(0, 40)) + '… (' + esc(s.reason) + ')').join('; ')}</div>` : ''}
        ${banned.length ? `<div class="alert alert-warn"><b>${banned.length} theme(s) rejected by policy</b> and will be left out:
          <div class="chips">${banned.map((b) => `<span class="chip bad" title="${esc(b.policy)}">${esc(b.theme)}</span>`).join('')}</div>
          <button class="btn sm" data-act="drop-banned">Remove them from my selection</button></div>` : '<div class="alert alert-ok">No policy problems found.</div>'}
        ${failed.length ? `<div class="alert alert-error">${failed.length} asset group(s) cannot be created: ${failed.map((f) => esc(f.name) + ' – ' + esc(f.error)).join('; ')}</div>` : ''}
        <div class="table-wrap"><table><thead><tr><th>#</th><th>New asset group</th><th class="num">Themes</th><th>Check</th></tr></thead><tbody>
        ${r.results.map((x) => `<tr><td>${x.index + 1}</td><td>${esc(x.name)}</td><td class="num">${x.themes_final || x.themes.length}</td>
          <td><span class="pill ${x.status === 'validated' ? 'ok' : 'bad'}">${esc(x.status)}</span></td></tr>`).join('')}</tbody></table></div>
        <p><button class="btn sm" data-act="validate">Re-run check</button></p>`;
    }
    return `<div class="card"><h2>5 · Review (dry-run)</h2>${out}${nav(3, 5, 'Continue →', !(r && r.results.some((x) => x.status === 'validated')))}</div>`;
  }

  // ---- step 5: run
  function step5() {
    const r = S.review, n = r.plan.chunks.length;
    let out = '';
    if (S.run) {
      out = `${S.run.results.every((x) => x.status === 'created') ? '<div class="alert alert-ok">All asset groups were created (paused).</div>' : '<div class="alert alert-warn">Some asset groups were not created. See below.</div>'}
        <div class="table-wrap"><table><thead><tr><th>Asset group</th><th class="num">Themes</th><th>Status</th><th>Resource</th></tr></thead><tbody>
        ${S.run.results.map((x) => `<tr><td>${esc(x.name)}</td><td class="num">${x.themes_final}</td><td><span class="pill ${x.status === 'created' ? 'ok' : 'bad'}">${esc(x.status)}</span>${x.error ? `<div class="small">${esc(x.error)}</div>` : ''}</td>
          <td class="small">${esc(x.resource_name || '')}</td></tr>`).join('')}</tbody></table></div>
        <p class="muted small">Check them in Google Ads and enable when ready. <a href="${esc(S.run.ads_link)}" target="_blank" rel="noopener noreferrer">Open campaign in Google Ads</a></p>
        <p><button class="btn" data-act="download">Download results (CSV)</button></p>`;
    } else if (!S.me.allow_mutate) {
      out = `<div class="alert alert-info"><b>Writes are disabled on this deployment.</b> The dry-run above is as far as this environment goes. Ask the app admin to enable writes (ALLOW_MUTATE) when you are ready to create the asset groups.</div>
        <p>It would create <b>${n}</b> paused asset group(s) in <b>${esc(S.target.campaign_name)}</b>.</p>`;
    } else {
      out = `<div class="alert alert-warn">This will create <b>${n}</b> paused asset group(s) in <b>${esc(S.target.campaign_name)}</b> (${esc(S.account.name)}).</div>
        <label>Type <b>${n}</b> to confirm<input data-bind="confirm" value="${esc(S.confirm)}" inputmode="numeric" autocomplete="off"></label>
        <button class="btn danger" data-act="run" id="run-btn" ${+S.confirm === n ? '' : 'disabled'}>Create ${n} asset group(s)</button>`;
    }
    return `<div class="card"><h2>6 · Run</h2>${out}${nav(4, null)}</div>`;
  }

  // ------------------------------------------------------------- filters
  function applyFilters() {
    for (const inp of document.querySelectorAll('[data-filter]')) {
      const q = inp.value.trim().toLowerCase();
      const table = inp.closest('.card').querySelector('table');
      if (!table) continue;
      table.querySelectorAll('tr[data-text]').forEach((tr) => tr.classList.toggle('hidden', !!q && !tr.dataset.text.includes(q)));
    }
  }

  // -------------------------------------------------------------- events
  function setPath(path, val) {
    const parts = path.split('.'); let o = S;
    while (parts.length > 1) o = o[parts.shift()];
    o[parts[0]] = val;
  }

  document.addEventListener('input', (ev) => {
    const t = ev.target;
    if (t.dataset.filter) { S.filters[t.dataset.filter] = t.value; applyFilters(); return; }
    if (t.dataset.bind) {
      setPath(t.dataset.bind, t.type === 'checkbox' ? t.checked : (t.type === 'number' ? t.value : t.value));
      if (t.dataset.bind !== 'q.from' && t.dataset.bind !== 'q.to' && t.dataset.bind !== 'q.min') invalidate();
      if (t.dataset.bind === 'confirm') { const b = $('#run-btn'); if (b) b.disabled = +S.confirm !== S.review.plan.chunks.length; }
      renderSummary();
    }
  });
  document.addEventListener('change', (ev) => {
    const t = ev.target;
    if (t.type === 'checkbox' && t.dataset.act === 'toggle-camp') {
      const c = S.structure.find((x) => x.campaign_id === t.dataset.id);
      if (t.checked) S.campSel.add(c.campaign_id); else S.campSel.delete(c.campaign_id);
      c.ad_groups.forEach((a) => S.agSel.delete(a.ad_group_id));
      render();
    } else if (t.type === 'checkbox' && t.dataset.act === 'toggle-ag') {
      const camp = S.structure.find((x) => x.campaign_id === t.dataset.camp);
      if (S.campSel.has(camp.campaign_id)) {          // un-checking one ad group of a fully selected campaign
        S.campSel.delete(camp.campaign_id);
        camp.ad_groups.forEach((a) => S.agSel.add(a.ad_group_id));
      }
      if (t.checked) S.agSel.add(t.dataset.id); else S.agSel.delete(t.dataset.id);
      render();
    } else if (t.type === 'checkbox' && t.dataset.act === 'toggle-kw') {
      const id = +t.dataset.id;
      if (t.checked) S.kwSel.add(id); else S.kwSel.delete(id);
      invalidate();
      t.closest('tr').classList.toggle('sel', t.checked);
      const kc = $('#kw-count'); if (kc) kc.textContent = `${S.kwSel.size} of ${S.kwRows.length} selected`;
      renderSummary(); renderStepper();
    }
  });

  document.addEventListener('click', async (ev) => {
    const el = ev.target.closest('[data-act]');
    if (!el || el.tagName === 'INPUT') return;
    const act = el.dataset.act;
    if (act === 'go') return go(+el.dataset.n);
    if (act === 'pick-account') {
      const a = S.accounts.find((x) => x.id === el.dataset.id);
      if (!S.account || S.account.id !== a.id) {
        Object.assign(S, { account: a, pmax: null, target: null, structure: null, kwRows: null,
          campSel: new Set(), agSel: new Set(), kwSel: new Set() });
        invalidate();
      }
      return render();
    }
    if (act === 'disconnect') {
      if (!confirm('Disconnect your Google Ads access? You will need to connect again to continue.')) return;
      await api('/api/ads/disconnect', { method: 'POST' }); location.href = '/connect'; return;
    }
    if (act === 'pick-ag') {
      S.target = S.pmax.find((x) => x.asset_group_id === el.dataset.id); invalidate(); return render();
    }
    if (act === 'fetch-kw') return guarded('Fetching keywords…', async () => {
      const res = await api('/api/keywords', { method: 'POST', body: {
        customer_id: S.account.id, login_customer_id: S.account.login_customer_id,
        ad_group_ids: adGroupIds(), date_from: S.q.from, date_to: S.q.to, min_clicks: Math.max(0, +S.q.min || 0) } });
      S.kwRows = res.rows.map((r, i) => ({ ...r, id: i })); S.manualSeq = S.kwRows.length;
      S.kwSel = new Set(S.kwRows.map((r) => r.id)); invalidate();
    });
    if (act === 'kw-all') {
      document.querySelectorAll('tr[data-rid]:not(.hidden)').forEach((tr) => S.kwSel.add(+tr.dataset.rid));
      invalidate(); return render();
    }
    if (act === 'kw-none') { S.kwSel = new Set(); invalidate(); return render(); }
    if (act === 'add-manual') {
      const lines = $('#manual').value.split('\n').map(norm).filter(Boolean);
      for (const l of lines) { const id = S.manualSeq++; S.kwRows.push({ id, keyword: l, campaign_name: 'manual', ad_group_name: 'entry', clicks: null, conversions: null, cost: null }); S.kwSel.add(id); }
      invalidate(); return render();
    }
    if (act === 'validate') return guarded('Checking every theme against Google Ads policy…', async () => {
      S.review = await api('/api/validate', { method: 'POST', body: payload() });
    });
    if (act === 'drop-banned') {
      const bad = new Set(S.review.results.flatMap((x) => x.banned.map((b) => norm(b.theme).toLowerCase())));
      (S.kwRows || []).forEach((r) => { if (bad.has(norm(r.keyword).toLowerCase())) S.kwSel.delete(r.id); });
      invalidate(); return go(4);
    }
    if (act === 'run') return guarded('Creating asset groups…', async () => {
      S.run = await api('/api/run', { method: 'POST', body: { ...payload(), confirm_groups: S.review.plan.chunks.length } });
    });
    if (act === 'download') {
      const rows = [['name', 'status', 'themes', 'resource_name', 'error']].concat(
        S.run.results.map((x) => [x.name, x.status, x.themes_final, x.resource_name || '', x.error || '']));
      const csv = rows.map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n');
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); a.download = 'pmax-themer-results.csv'; a.click();
    }
  });

  // ---------------------------------------------------------------- boot
  (async () => {
    render();
    try { S.me = await api('/api/me'); } catch (e) { S.error = e.message; return render(); }
    go(0);
  })();
})();
