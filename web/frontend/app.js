// OpenMontage scene-review dashboard
const API = '/api';
const state = {
  project: null, title: '', scenes: [], cost: null, health: null,
  selected: null, draft: {}, job: {}, viewUrl: {}, directorBusy: null, _audio: null,
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function jget(u) { const r = await fetch(u); if (!r.ok) throw new Error(`${u} -> ${r.status}`); return r.json(); }
async function jpost(u, body) {
  const r = await fetch(u, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  if (!r.ok) throw new Error(`${u} -> ${r.status}`); return r.json();
}
const el = (h) => { const t = document.createElement('template'); t.innerHTML = h.trim(); return t.content.firstChild; };
const esc = (s) => (s == null ? '' : String(s)).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const VERDICTS = ['approve', 'needs_work', 'reject'];
const VLABEL = { approve: 'Approve', needs_work: 'Needs work', reject: 'Reject' };
const statusOf = (sc) => (sc.feedback && sc.feedback.verdict) || 'pending';
const scoreClass = (n) => (n >= 8 ? 'score-good' : n >= 5 ? 'score-mid' : 'score-bad');
const base = (sid) => `${API}/projects/${state.project}/scenes/${sid}`;
const estUsd = (slot) => Math.max(0.10, 0.017 * Math.round(slot || 0)).toFixed(2);

async function boot() {
  let projects = [];
  try { projects = await jget(`${API}/projects`); } catch (e) { return fail(e); }
  const pick = projects.find((p) => p.id === 'therac-25-test') || projects[0];
  if (!pick) { document.getElementById('detail').innerHTML = '<p class="muted">No projects found under projects/.</p>'; return; }
  state.project = pick.id;
  try { state.health = await jget(`${API}/health`); } catch { state.health = null; }
  await reload();
}

async function reload() {
  try {
    const data = await jget(`${API}/projects/${state.project}/scenes`);
    state.scenes = data.scenes; state.title = data.title;
  } catch (e) { return fail(e); }
  try { state.cost = await jget(`${API}/projects/${state.project}/cost`); } catch { state.cost = null; }
  if (!state.selected && state.scenes.length) state.selected = state.scenes[0].id;
  render();
}

function fail(e) {
  document.getElementById('detail').innerHTML =
    `<p class="muted">Backend not reachable (${esc(e.message)}). Start it with <code>uvicorn web.backend.app:app --port 8011</code> from the repo root.</p>`;
}

function render() { renderSummary(); renderDetail(); renderGrid(); }

function renderSummary() {
  const c = { approve: 0, needs_work: 0, reject: 0, pending: 0 };
  state.scenes.forEach((s) => c[statusOf(s)]++);
  const cost = state.cost ? `$${state.cost.total_usd}` : '—';
  let health = '';
  if (state.health) {
    const d = state.health.director, dp = state.health.dispatch;
    const dir = d.google_api_key_loaded && d.sdk_installed;
    health = `<span class="chip ${dir ? 'ok-chip' : ''}">director: ${dir ? 'gemini' : 'offline'}</span>
      <span class="chip ${dp.kie_api_key && !dp.disabled ? 'ok-chip' : ''}">dispatch: ${dp.disabled ? 'off' : (dp.kie_api_key ? 'ready' : 'no key')}</span>`;
  }
  document.getElementById('summary').innerHTML = `
    <span class="chip">${esc(state.title)} · ${state.scenes.length} scenes</span>
    <span class="chip"><span class="dot approve"></span>${c.approve}</span>
    <span class="chip"><span class="dot needs_work"></span>${c.needs_work}</span>
    <span class="chip"><span class="dot reject"></span>${c.reject}</span>
    <span class="chip"><span class="dot pending"></span>${c.pending}</span>
    <span class="chip">spent ${cost}</span>${health}`;
}

function renderGrid() {
  const grid = document.getElementById('grid');
  document.getElementById('gridcount').textContent = `(${state.scenes.length})`;
  grid.innerHTML = '';
  state.scenes.forEach((sc) => {
    const st = statusOf(sc);
    const g = sc.auto_gate && sc.auto_gate.score != null ? `<span class="${scoreClass(sc.auto_gate.score)}">${sc.auto_gate.score}/10</span>` : '';
    const tk = sc.take_count ? `<span class="takeflag">T${sc.active_take || sc.take_count}</span>` : '';
    const card = el(`
      <div class="card ${sc.id === state.selected ? 'sel' : ''}">
        <div class="ctop"><div class="cbadge">${sc.number}</div><span class="dot ${st}"></span></div>
        <div class="clane">${esc(sc.lane || '—')}${sc.flf ? ' · flf' : ''}</div>
        <div class="cfoot"><span>${sc.slot_s}s ${tk}</span><span>${g}</span></div>
      </div>`);
    card.addEventListener('click', () => { state.selected = sc.id; render(); window.scrollTo({ top: 0, behavior: 'smooth' }); });
    grid.appendChild(card);
  });
}

function renderDetail() {
  if (state._audio) { try { state._audio.pause(); } catch {} state._audio = null; }
  const sc = state.scenes.find((s) => s.id === state.selected);
  const host = document.getElementById('detail');
  if (!sc) { host.innerHTML = '<p class="muted">Select a scene.</p>'; return; }
  const fb = sc.feedback || {};
  const g = sc.auto_gate || {};
  const mode = sc.narration_mode + (sc.narration_mode_default ? ' (default)' : '');
  const srcUrl = state.viewUrl[sc.id] || sc.clip_url;
  const player = srcUrl
    ? `<video src="${srcUrl}" controls preload="metadata"></video>`
    : (sc.audio_url
        ? `<div class="noclip"><span class="nobadge">no clip for ${esc(sc.id)} — narration only</span><audio src="${sc.audio_url}" controls></audio></div>`
        : `<span class="nobadge">no assembled clip for ${esc(sc.id)}</span>`);

  host.innerHTML = `
    <div class="player">
      <div class="badge">${sc.number}</div>
      <span class="chip lane corner-tr">${esc(sc.lane || '—')}${sc.flf ? ' · flf' : ''}</span>
      <span class="chip corner-br">${sc.slot_s}s</span>
      ${player}
    </div>
    ${srcUrl && sc.audio_url ? '<div class="narrnote">▶ plays with the narration track (clips are silent until final render)</div>' : ''}
    ${takesBar(sc)}

    <p class="narr">${esc(sc.narration)}</p>

    <div class="row mb">
      <span class="chip">${esc(sc.id)}</span>
      <span class="chip">mode: ${esc(mode)}</span>
      <span class="section-label" style="margin:0">auto-gate</span>
      ${g.verdict ? `<span class="chip">${esc(g.verdict)}</span>` : ''}
      ${g.score != null ? `<span class="chip ${scoreClass(g.score)}">${g.score}/10</span>` : '<span class="chip">not scored</span>'}
    </div>
    ${(g.missing && g.missing.length) ? `<div class="muted" style="font-size:13px;margin:-6px 0 8px">missing: ${esc(g.missing.slice(0, 3).join('; '))}</div>` : ''}

    <div class="verdicts">
      ${VERDICTS.map((v) => `<button class="vbtn ${v} ${fb.verdict === v ? 'active' : ''}" data-v="${v}">${VLABEL[v]}</button>`).join('')}
    </div>

    <div class="section-label">Notes</div>
    ${fb.notes && fb.notes.length ? `<ul class="list">${fb.notes.map((n) => `<li>${esc(n.text)}</li>`).join('')}</ul>` : ''}
    <textarea id="note" rows="2" placeholder="Notes — what's wrong, what to keep…"></textarea>
    <div class="row" style="margin-top:6px;justify-content:flex-end"><button class="act" id="addnote">Add note</button></div>

    <div class="section-label">Suggestion for next take</div>
    ${fb.suggestions && fb.suggestions.length ? `<ul class="list">${fb.suggestions.map((s) => `<li><b>${esc(s.change_type || 'other')}:</b> ${esc(s.text)}</li>`).join('')}</ul>` : ''}
    <div class="inline">
      <select id="ctype">${['prompt', 'motion', 'seed', 'keyframe', 'lane', 'duration', 'other'].map((x) => `<option value="${x}">change: ${x}</option>`).join('')}</select>
      <input id="sugg" placeholder="e.g. drive the needle harder, dim the overheads" />
      <button class="act" id="addsugg">Add</button>
    </div>

    <button class="regen" id="regen" ${state.directorBusy === sc.id ? 'disabled' : ''}>
      ${state.directorBusy === sc.id ? 'Running director pass…' : `↻ Regenerate scene ${sc.number} → run director pass`}
    </button>
    <div class="hint">Your notes go to the director (rules applied), not injected raw. Approve the revised plan to generate a new take.</div>

    <div id="revzone"></div>

    ${sc.shots && sc.shots.length ? `
      <details class="shots">
        <summary>${sc.shots.length} sub-shot${sc.shots.length > 1 ? 's' : ''} (drill-down)</summary>
        ${sc.shots.map((s) => `<pre><b>${esc(s.shot_id)}</b> · ${esc(s.provider || '')}\n${esc(s.prompt || '')}</pre>`).join('')}
      </details>` : ''}
  `;

  host.querySelectorAll('.vbtn[data-v]').forEach((b) =>
    b.addEventListener('click', () => capture(sc.id, 'verdict', { verdict: b.dataset.v })));
  host.querySelector('#addnote').addEventListener('click', () => {
    const t = host.querySelector('#note').value.trim(); if (t) capture(sc.id, 'note', { text: t });
  });
  host.querySelector('#addsugg').addEventListener('click', () => {
    const t = host.querySelector('#sugg').value.trim();
    if (t) capture(sc.id, 'suggestion', { text: t, change_type: host.querySelector('#ctype').value });
  });
  host.querySelector('#regen').addEventListener('click', () => runDirectorPass(sc.id));
  host.querySelectorAll('[data-take]').forEach((b) => b.addEventListener('click', () => {
    state.viewUrl[sc.id] = b.dataset.take === 'baseline' ? sc.clip_url : b.dataset.take; renderDetail();
  }));

  wireAudio(host, sc);
  renderRevisions(host.querySelector('#revzone'), sc);
}

function takesBar(sc) {
  if (!sc.take_count) return '';
  const active = state.viewUrl[sc.id];
  const baseActive = !active ? 'active' : '';
  const chips = [`<button class="take-chip ${baseActive}" data-take="baseline">baseline</button>`];
  sc.takes.forEach((t) => {
    const on = active === t.url ? 'active' : '';
    const sc2 = t.score != null ? ` · ${Math.round(t.score * 100)}%` : '';
    chips.push(`<button class="take-chip ${t.verdict} ${on}" data-take="${t.url}">take ${t.take} (${esc(t.verdict)}${sc2})</button>`);
  });
  return `<div class="takesbar"><span class="muted">takes:</span> ${chips.join(' ')}</div>`;
}

function wireAudio(host, sc) {
  const video = host.querySelector('video');
  if (!video || !sc.audio_url) return;
  const audio = new Audio(sc.audio_url); audio.preload = 'metadata'; state._audio = audio;
  const sync = () => { if (Math.abs(audio.currentTime - video.currentTime) > 0.3) audio.currentTime = video.currentTime; };
  video.addEventListener('play', () => { audio.currentTime = video.currentTime; audio.play().catch(() => {}); });
  video.addEventListener('pause', () => audio.pause());
  video.addEventListener('seeking', () => { audio.currentTime = video.currentTime; });
  video.addEventListener('ended', () => audio.pause());
  video.addEventListener('ratechange', () => { audio.playbackRate = video.playbackRate; });
  video.addEventListener('timeupdate', sync);
}

function renderRevisions(zone, sc) {
  const parts = [];
  const job = state.job[sc.id];
  if (job) parts.push(jobBanner(job));
  const draft = state.draft[sc.id];
  if (draft) parts.push(revisionCard(sc, draft.revision_id, draft.revision, 'drafted'));
  const past = (sc.feedback && sc.feedback.revisions) || [];
  past.filter((r) => !draft || r.id !== draft.revision_id).reverse().forEach((r) => parts.push(revisionCard(sc, r.id, r.revision, r.status)));
  zone.innerHTML = parts.join('');
  zone.querySelectorAll('[data-dispatch]').forEach((b) => b.addEventListener('click', () => approveAndDispatch(sc.id, b.dataset.dispatch, sc.slot_s)));
  zone.querySelectorAll('[data-reject]').forEach((b) => b.addEventListener('click', () => decideRevision(sc.id, b.dataset.reject)));
}

function jobBanner(job) {
  const map = {
    queued: ['⏳', `queued — generating a new take (~$${job.est_usd})`],
    running: ['⏳', `generating a new take (~$${job.est_usd})…`],
    succeeded: ['✓', `take ${job.take ? job.take.take : ''} generated${job.take && job.take.score != null ? ` · ${Math.round(job.take.score * 100)}%` : ''}`],
    failed: ['✗', `generation failed: ${esc(job.error || '')}`],
    blocked: ['⛔', `blocked: ${esc(job.error || '')}`],
    not_yet_auto_dispatched: ['ℹ', esc(job.reason || 'not auto-dispatched')],
    busy: ['⏳', 'a job is already running for this scene'],
    idempotent: ['✓', 'this revision was already generated'],
  };
  const m = map[job.status] || ['', esc(job.status)];
  return `<div class="jobbanner ${job.status}">${m[0]} ${m[1]}</div>`;
}

function revisionCard(sc, rid, rev, status) {
  if (!rev) return '';
  const da = rev.described_action || {};
  const gp = rev.gate_precheck || {};
  const src = (rev._source || '').startsWith('gemini') ? `director · ${esc(rev._source)}` : 'director · offline';
  const errNote = rev._error ? `<div class="muted" style="font-size:12px;margin-top:4px">degraded: ${esc(rev._error)}</div>` : '';
  const statusChip = { drafted: '<span class="rev-status drafted">awaiting approval</span>', approved: '<span class="rev-status approved">approved</span>', rejected: '<span class="rev-status rejected">rejected</span>' }[status] || '';
  const row = (label, val) => val ? `<div class="da-row"><span>${label}</span><div>${esc(Array.isArray(val) ? val.join(', ') : val)}</div></div>` : '';
  const seq = (da.action_sequence && da.action_sequence.length) ? `<div class="da-row"><span>action</span><ol class="da-seq">${da.action_sequence.map((a) => `<li>${esc(a)}</li>`).join('')}</ol></div>` : '';
  const lanePlan = (da.lane_plan && da.lane_plan.length) ? `<div class="da-row"><span>lanes</span><div>${da.lane_plan.map((l) => `${esc(l.beat)} → <b>${esc(l.lane)}</b>`).join('<br>')}</div></div>` : '';
  const actions = status === 'drafted'
    ? `<div class="row" style="margin-top:10px;gap:10px">
         <button class="vbtn approve" data-dispatch="${rid}">Approve &amp; generate (~$${estUsd(sc.slot_s)})</button>
         <button class="vbtn reject" data-reject="${rid}">Reject</button>
       </div>` : '';
  return `
    <div class="revcard ${status}">
      <div class="row" style="justify-content:space-between">
        <div><b>Director's revised plan</b> <span class="chip">${esc(rev.lane || '')}</span> <span class="chip">${esc(da.depiction_mode || '')}</span></div>
        ${statusChip}
      </div>
      <div class="da">
        ${row('subjects', da.subjects)}${row('setting', da.setting)}${seq}${row('props', da.props)}
        ${row('on-screen', da.on_screen_text)}${row('manner', da.manner)}${row('characterization', da.characterization)}${lanePlan}
      </div>
      <div class="section-label">revised prompt</div>
      <pre class="revprompt">${esc(rev.revised_prompt)}</pre>
      <div class="section-label">why</div>
      <div class="muted" style="font-size:13px">${esc(rev.rationale)}</div>
      <div class="row" style="margin-top:8px">
        <span class="chip">pre-gate: ${esc(gp.narration_alignment || '—')}</span>
        <span class="chip">subject named: ${gp.subject_named ? 'yes' : 'no'}</span>
        <span class="chip muted">${src}</span>
      </div>${errNote}${actions}
    </div>`;
}

async function runDirectorPass(sid) {
  const note = document.querySelector('#note') ? document.querySelector('#note').value.trim() : '';
  state.directorBusy = sid; delete state.job[sid]; renderDetail();
  try {
    if (note) await jpost(`${base(sid)}/note`, { text: note });
    await jpost(`${base(sid)}/regenerate`, { notes: note ? [note] : [], target: 'scene' });
    state.draft[sid] = await jpost(`${base(sid)}/director-pass`, {});
  } catch (e) { alert('Director pass failed: ' + e.message); }
  state.directorBusy = null;
  await reload();
}

async function approveAndDispatch(sid, rid, slot) {
  if (!confirm(`Approve this plan and generate a new take?\nThis spends ~$${estUsd(slot)} via grok-kie (Kie credits).`)) return;
  let r;
  try { r = await jpost(`${base(sid)}/revision/${rid}/approve-and-dispatch`, {}); }
  catch (e) { alert('Dispatch failed: ' + e.message); return; }
  delete state.draft[sid];
  state.job[sid] = r;
  await reload();
  if (r.status === 'queued' && r.job_id) pollJob(sid, r.job_id);
}

async function pollJob(sid, jobId) {
  for (let i = 0; i < 180; i++) {
    await sleep(2000);
    let j; try { j = await jget(`${API}/projects/${state.project}/jobs/${jobId}`); } catch { break; }
    state.job[sid] = j;
    if (state.selected === sid) renderRevisions(document.getElementById('revzone'), state.scenes.find((s) => s.id === sid) || {});
    if (['succeeded', 'failed', 'blocked'].includes(j.status)) { await reload(); break; }
  }
}

async function decideRevision(sid, rid) {
  try { await jpost(`${base(sid)}/revision/${rid}/reject`, {}); delete state.draft[sid]; await reload(); }
  catch (e) { alert('Failed: ' + e.message); }
}

async function capture(sid, kind, body) {
  try { await jpost(`${base(sid)}/${kind}`, body); await reload(); }
  catch (e) { alert('Save failed: ' + e.message); }
}

boot();
