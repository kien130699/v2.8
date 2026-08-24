const S = {
  status: null,
  templates: [],
  jobs: [],
  pages: [],
  runs: [],
  flow: null,
  logs: [],
  filter: 'all',
  logFilter: 'all',
  editing: null,
  creatingTemplate: null,
  busyJobs: new Set(),
  loadSeq: 0,
  shopee: {
    results: [],
    basket: JSON.parse(localStorage.getItem('v28_shopee_basket') || '[]'),
    mode: localStorage.getItem('v28_shopee_mode') || 'one_product_per_video'
  }
};

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
const short = s => { s = String(s || ''); return s.length > 42 ? s.slice(0, 39) + '…' : s; };
const when = s => s ? new Date(s).toLocaleString('vi-VN') : '—';

async function api(url, opt = {}) {
  const r = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(opt.headers || {}) },
    ...opt
  });
  let d;
  const t = await r.text();
  try { d = JSON.parse(t); } catch { d = t; }
  if (!r.ok) throw new Error(d?.detail || d || `${r.status}`);
  return d;
}

async function apiForm(url, form) {
  const r = await fetch(url, { method: 'POST', body: form });
  let d;
  const t = await r.text();
  try { d = JSON.parse(t); } catch { d = t; }
  if (!r.ok) throw new Error(d?.detail || d || `${r.status}`);
  return d;
}

let toastTimer = null;
function toast(msg, bad = false) {
  const x = $('#toast');
  if (!x) return;
  if (toastTimer) clearTimeout(toastTimer);
  x.classList.remove('hidden');
  x.textContent = msg;
  x.className = 'toast ' + (bad ? 'bad' : 'good');
  toastTimer = setTimeout(() => x.classList.add('hidden'), 3200);
}

function statusLabel(v) {
  const s = String(v || 'unknown');
  const map = {
    unknown: 'không rõ', queued: 'đang chờ', waiting_flow: 'chờ Flow',
    dispatching: 'đang gửi', preparing: 'đang chuẩn bị', running: 'đang chạy',
    rendering: 'đang render', publish_queued: 'chờ publish', publishing: 'đang publish',
    done: 'xong', done_no_pages: 'xong - chưa gắn Page', published: 'đã publish',
    ready: 'sẵn sàng', rendered: 'đã render', success: 'thành công', idle: 'rảnh',
    failed: 'lỗi', error: 'lỗi', offline: 'mất kết nối', cancelled: 'đã hủy', retry_wait: 'chờ retry'
  };
  return map[s] || s;
}

function statusBadge(v) {
  const s = String(v || 'unknown');
  let c = '';
  if (/published|done|ready|rendered|success|idle/i.test(s)) c = 'good';
  else if (/fail|error|offline|cancel/i.test(s)) c = 'bad';
  else if (/queue|wait|dispatch|run|start|prepare|publish|active|retry/i.test(s)) c = 'warn';
  return `<span class="badge ${c}">${esc(statusLabel(s))}</span>`;
}

function flowConnected() {
  const f = S.status?.flow || S.flow?.queue || {};
  return Boolean(f.extensionConnected || f.extension_connected || f.connected || f.workerConnected);
}

// Navigation & View switching
const NAV_ITEMS = [
  { id: 'dashboard', icon: '◫', label: 'Tổng quan' },
  { id: 'jobs', icon: '⚡', label: 'Quản lý Job' },
  { id: 'facebook', icon: 'f', label: 'Facebook' },
  { id: 'shopee', icon: 'S', label: 'Shopee Research' },
  { id: 'runs', icon: '▶', label: 'Lần chạy & output' },
  { id: 'flow', icon: '⚙', label: 'Cài đặt Flow' },
  { id: 'logs', icon: '≡', label: 'Nhật ký' }
];

function buildNav() {
  const sidenav = $('#sidenav');
  if (sidenav) {
    sidenav.innerHTML = NAV_ITEMS.map(n =>
      `<button class="nav-btn${n.id === 'dashboard' ? ' active' : ''}" data-view="${n.id}">${n.icon} <span>${n.label}</span></button>`
    ).join('');
    $$('.nav-btn').forEach(b => b.onclick = () => setView(b.dataset.view));
  }
}

function setView(v) {
  $$('.nav-btn').forEach(x => x.classList.toggle('active', x.dataset.view === v));
  $$('.view').forEach(x => x.classList.toggle('active', x.id === 'view-' + v));
  if (v === 'logs') loadLogs();
  if (v === 'runs') loadRuns();
  if (v === 'flow') loadFlow();
  if (v === 'shopee') renderShopee();
}

// Topbar Status Updating
function updateStatusBar() {
  const on = flowConnected();
  const fd = $('#flowDot');
  if (fd) {
    fd.classList.toggle('on', on);
    fd.classList.toggle('warn', !on);
  }
  const ft = $('#flowText');
  if (ft) ft.textContent = on ? 'Đã kết nối' : 'Mất kết nối';

  const pages = S.pages || [];
  const fb = $('#fbTokenText'), fbd = $('#fbTokenDot');
  if (pages.length > 0) {
    if (fb) fb.textContent = pages.length + ' Page';
    if (fbd) { fbd.classList.add('on'); fbd.classList.remove('warn', 'off'); }
  } else {
    if (fb) fb.textContent = '0 Page';
    if (fbd) { fbd.classList.add('warn'); fbd.classList.remove('on', 'off'); }
  }

  const dd = $('#diskDot'), dt = $('#diskText');
  if (dd) { dd.classList.add('on'); dd.classList.remove('warn'); }
  if (dt) dt.textContent = 'OK';
}

async function checkShopeeSession() {
  try {
    const r = await api('/api/shopee/session-health');
    const ok = Boolean(r && r.ok && r.loggedIn);
    const sd = $('#shopeeDot'), st = $('#shopeeText');
    if (sd) {
      sd.classList.toggle('on', ok);
      sd.classList.toggle('warn', !ok);
    }
    if (st) st.textContent = ok ? 'Sẵn sàng' : 'Chưa login';
  } catch {
    const sd = $('#shopeeDot'), st = $('#shopeeText');
    if (sd) { sd.classList.remove('on'); sd.classList.add('warn'); }
    if (st) st.textContent = 'Mất kết nối';
  }
}

async function loadActivityFeed() {
  try {
    const logs = await api('/api/logs?limit=20&kind=server');
    const el = $('#activityFeed');
    if (!el) return;
    el.innerHTML = logs.slice(0, 15).map(x =>
      `<div class="activity-item"><span class="ts">${esc(x.created_at || '')}</span><span class="msg">${x.instance_id ? `<b class="job-id">${esc(x.instance_id)}</b> ` : ''}${esc(x.message || '')}</span></div>`
    ).join('') || '<div class="muted">Chưa có hoạt động.</div>';
  } catch {}
}

async function loadAll() {
  const seq = ++S.loadSeq;
  try {
    const [status, templates, jobs, pages, runs] = await Promise.all([
      api('/api/status'),
      api('/api/job-templates'),
      api('/api/jobs'),
      api('/api/facebook/pages'),
      api('/api/runs?limit=80')
    ]);
    if (seq !== S.loadSeq) return;
    Object.assign(S, { status, templates, jobs, pages, runs });
    renderAll();
    checkShopeeSession();
  } catch (e) {
    if (seq === S.loadSeq) toast('Load lỗi: ' + e.message, true);
  }
}

function renderAll() {
  updateStatusBar();
  renderStats();
  renderTemplates();
  renderJobs();
  renderPages();
  renderRuns(S.runs, '#dashRuns', 8);
  renderDashFlow();
  renderShopee();
  loadActivityFeed();
}

function renderStats() {
  const active = (S.status?.jobs?.active || []).length;
  const pub = (S.status?.facebook?.publish || []).filter(x => ['queued', 'starting', 'uploading', 'finishing', 'retry_wait'].includes(x.status)).length;
  const cards = [
    ['Bản Job', S.jobs.length, `${S.templates.length} loại Job`],
    ['Page Facebook', S.pages.length, 'import dùng chung'],
    ['Đang xử lý', active, `${pub} publish đang chờ`],
    ['Worker Flow', flowConnected() ? 'ĐANG NỐI' : 'MẤT KẾT NỐI', flowConnected() ? 'Hàng đợi sẵn sàng' : 'Mở Edge/Chrome + extension']
  ];
  const grid = $('#statsGrid') || $('#stats');
  if (grid) {
    grid.innerHTML = cards.map(x =>
      `<div class="stat"><div class="k">${esc(x[0])}</div><div class="v">${esc(x[1])}</div><div class="s">${esc(x[2])}</div></div>`
    ).join('');
  }
}

function renderTemplates() {
  const all = `<button class="${S.filter === 'all' ? 'active' : ''}" data-filter="all">Tất cả</button>`;
  const tf = $('#templateFilters');
  if (!tf) return;
  tf.innerHTML = all + S.templates.map(t =>
    `<button class="${S.filter === t.id ? 'active' : ''}" data-filter="${esc(t.id)}">${esc(t.id)} · ${esc(t.name)} <span class="muted">${t.instance_count}</span></button>`
  ).join('');
  $$('#templateFilters [data-filter]').forEach(b => b.onclick = () => {
    S.filter = b.dataset.filter;
    renderTemplates();
    renderJobs();
  });
}

function pagesHtml(j) {
  return j.pages?.length
    ? j.pages.map(p => `<span class="chip">f · ${esc(p.name || p.page_id)}</span>`).join('')
    : `<span class="muted">Chưa gắn Facebook Page</span>`;
}

function scheduleText(s) {
  if (!s || !Object.keys(s).length) return 'Thủ công';
  if (s.mode === 'interval') return `mỗi ${s.interval_minutes || s.minutes || 60} phút`;
  if (s.mode === 'daily') return (s.daily_slots || s.slots || []).join(', ') || 'Hằng ngày';
  return s.mode || 'Thủ công';
}

function jobCard(j, compact = false) {
  const last = j.last_run;
  if (compact) {
    return `<div class="job-row" data-job="${esc(j.id)}">
      <div><b>${esc(j.id)}</b> <span class="muted">${esc(j.name)}</span></div>
      <div>${statusBadge(last?.status || 'idle')}</div>
    </div>`;
  }
  return `<div class="job-card" data-job="${esc(j.id)}">
    <div class="job-card-head">
      <div>
        <div><span class="job-id">${esc(j.id)}</span><h3>${esc(j.name)}</h3></div>
        <div class="muted">${esc(j.template?.name || '')} · ${esc(j.template?.engine || '')}</div>
      </div>
      <label title="Bật/Tắt"><input class="toggle job-toggle" type="checkbox" ${j.enabled ? 'checked' : ''}></label>
    </div>
    <div class="job-meta">
      ${statusBadge(last?.status || 'idle')}
      <span class="badge">${j.pages?.length || 0} Page</span>
      <span class="badge">${scheduleText(j.schedule)}</span>
    </div>
    <div class="page-chips">${pagesHtml(j)}</div>
    <div class="job-actions">
      <button class="btn primary small run-job" ${S.busyJobs.has(j.id) ? 'disabled' : ''}>${S.busyJobs.has(j.id) ? 'Đang gửi…' : '▶ Run'}</button>
      <button class="btn small edit-job">Sửa</button>
      <button class="btn small clone-job">Clone</button>
      <button class="btn small danger delete-job">Xóa</button>
    </div>
  </div>`;
}

function renderJobs() {
  const js = S.jobs.filter(j => S.filter === 'all' || j.template_id === S.filter);
  const jg = $('#jobsGrid');
  if (jg) jg.innerHTML = js.map(j => jobCard(j)).join('') || '<div class="muted">Chưa có Job.</div>';
  const dj = $('#dashJobs');
  if (dj) dj.innerHTML = S.jobs.slice(0, 6).map(j => jobCard(j, true)).join('');
  bindJobButtons();
}

function bindJobButtons() {
  $$('.job-card').forEach(card => {
    const id = card.dataset.job;
    const runBtn = card.querySelector('.run-job');
    if (runBtn) runBtn.onclick = () => runJob(id);
    const editBtn = card.querySelector('.edit-job');
    if (editBtn) editBtn.onclick = () => openEdit(id);
    const cloneBtn = card.querySelector('.clone-job');
    if (cloneBtn) cloneBtn.onclick = () => cloneJob(id);
    const delBtn = card.querySelector('.delete-job');
    if (delBtn) delBtn.onclick = () => deleteJob(id);
    const toggle = card.querySelector('.job-toggle');
    if (toggle) toggle.onchange = e => patchJob(id, { enabled: e.target.checked });
  });
}

async function testAllJobs() {
  try {
    const r = await api('/api/jobs/run-all', { method: 'POST', body: '{}' });
    toast(`Đã queue ${r.results?.filter(x => x.ok).length || 0}/${r.count || 0} Job`);
    await loadAll();
    setView('runs');
  } catch (e) {
    toast(e.message, true);
  }
}

async function runJob(id) {
  if (S.busyJobs.has(id)) return;
  S.busyJobs.add(id);
  renderJobs();
  try {
    const r = await api(`/api/jobs/${encodeURIComponent(id)}/run`, { method: 'POST', body: JSON.stringify({ trigger: 'manual-ui' }) });
    toast(`${id}: ${r.deduped ? 'đang chạy' : 'đã vào queue'} · ${r.run_id}`);
    await loadAll();
    setView('runs');
  } catch (e) {
    toast(e.message, true);
  } finally {
    S.busyJobs.delete(id);
    renderJobs();
  }
}

async function cloneJob(id) {
  try {
    const r = await api(`/api/jobs/${encodeURIComponent(id)}/clone`, { method: 'POST', body: '{}' });
    toast(`Đã clone ${id} → ${r.job.id}`);
    await loadAll();
  } catch (e) {
    toast(e.message, true);
  }
}

async function deleteJob(id) {
  if (!confirm(`Xóa Job ${id}? Video cũ không bị xóa.`)) return;
  try {
    await api(`/api/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' });
    toast('Đã xóa ' + id);
    await loadAll();
  } catch (e) {
    toast(e.message, true);
  }
}

async function patchJob(id, payload) {
  try {
    await api(`/api/jobs/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) });
    await loadAll();
  } catch (e) {
    toast(e.message, true);
  }
}

function renderPages() {
  const fpc = $('#fbPageCount');
  if (fpc) fpc.textContent = `${S.pages.length} Page`;
  const fbp = $('#fbPages');
  if (fbp) {
    fbp.innerHTML = S.pages.length
      ? S.pages.map(p =>
        `<div class="page-card">
          <div>
            <h3>${esc(p.name || 'Facebook Page')}</h3>
            <div class="muted">ID ${esc(p.id)} · ${p.enabled ? 'ON' : 'OFF'}</div>
            ${p.last_test?.name ? `<div class="muted">✓ ${esc(p.last_test.name)}</div>` : ''}
          </div>
          <div class="actions">
            <button class="btn small fb-test" data-page="${esc(p.id)}">Test</button>
            <button class="btn small danger fb-del" data-page="${esc(p.id)}">Xóa</button>
          </div>
        </div>`
      ).join('')
      : '<div class="muted">Chưa import Page. Nhập token bên trái.</div>';
    $$('.fb-test').forEach(b => b.onclick = () => testPage(b.dataset.page));
    $$('.fb-del').forEach(b => b.onclick = () => delPage(b.dataset.page));
  }
}

async function testAllFbPages() {
  try {
    toast('Đang kiểm tra toàn bộ Page Facebook…');
    const r = await api('/api/facebook/pages/test-all', { method: 'POST' });
    toast(`Đã test xong ${r.tested || 0} Page`);
    await loadAll();
  } catch (e) {
    toast(e.message, true);
  }
}

async function testPage(id) {
  try {
    const r = await api(`/api/facebook/pages/${encodeURIComponent(id)}/test`, { method: 'POST' });
    toast(`Page OK · ${r.name || id}`);
    await loadAll();
  } catch (e) {
    toast(e.message, true);
  }
}

async function delPage(id) {
  if (!confirm('Xóa Page khỏi V2.8?')) return;
  try {
    await api(`/api/facebook/pages/${encodeURIComponent(id)}`, { method: 'DELETE' });
    toast('Đã xóa Page');
    await loadAll();
  } catch (e) {
    toast(e.message, true);
  }
}

function expectedScenes(r) {
  const cps = Object.values(r.flow_checkpoints || {}).find(x => x?.resume_plan);
  const rp = cps?.resume_plan;
  if (rp?.total_scenes) return rp.total_scenes;
  const out = r.output || {}, e = out.engine;
  if (e?.plan?.scenes) return e.plan.scenes.length;
  if (e && typeof e === 'object') {
    let n = 0;
    Object.values(e).forEach(x => { if (x?.scenes?.length) n += x.scenes.length; });
    if (n) return n;
  }
  return 0;
}

function checkpointSummary(r) {
  const groups = Object.entries(r.flow_checkpoints || {});
  if (!groups.length) return '';
  return groups.map(([jid, data]) => {
    if (!data?.ok) return `<div class="muted">Flow ${esc(short(jid))}: ${esc(short(data?.error || 'không có checkpoint'))}</div>`;
    const plan = data.resume_plan || {}, cps = data.checkpoints || [];
    const total = plan.total_scenes || cps.length || 0;
    const done = (plan.complete_scene_ids || cps.filter(x => x.complete).map(x => x.scene_id)).length;
    const missing = (plan.unresolved || []).length;
    const down = (plan.download_missing || []).length;
    const chips = cps.map(x => {
      const cls = x.complete ? 'good' : (x.needs_download ? 'warn' : (x.last_error ? 'bad' : 'warn'));
      const label = x.complete ? 'OK' : (x.needs_download ? 'TẢI' : (x.can_resume_image ? 'RETRY VIDEO' : 'CHỜ'));
      return `<button class="badge ${cls} scene-retry" title="${esc(x.last_error || 'retry scene')}" data-job="${esc(jid)}" data-scene="${esc(x.scene_id)}">S${esc(x.scene_id)} ${label}</button>`;
    }).join(' ');
    return `<div class="scene-box"><div class="muted">Flow ${esc(short(jid))}: scene ${done}/${total} · thiếu ${missing} · tải lại ${down} <button class="btn small resume-job" data-job="${esc(jid)}">Resume</button></div><div>${chips}</div></div>`;
  }).join('');
}

function pipelineSummary(r) {
  const steps = r.orchestrator_steps || [];
  if (!steps.length) return '';
  const icons = { done: '✓', running: '⏳', queued: '⏱', skipped: '-', failed: '!' };
  return `<div class="scene-box"><div class="muted">Pipeline</div>${steps.map(s =>
    `<span class="badge ${s.status === 'done' ? 'good' : s.status === 'failed' ? 'bad' : s.status === 'running' ? 'warn' : ''}" title="${esc(s.detail || '')}">${icons[s.status] || ''} ${esc(s.step_key)}:${esc(s.status)}</span>`
  ).join(' ')}</div>`;
}

function outputHealth(r) {
  const vids = r.output?.video_paths || [], exp = expectedScenes(r), ok = vids.length > 0 && !String(r.status || '').includes('fail');
  const txt = exp ? `video cuối ${vids.length} / cảnh ${exp}` : `video cuối ${vids.length}`;
  return `<div class="muted">${ok ? 'OK' : 'CẢNH BÁO'} · ${esc(txt)}</div>${pipelineSummary(r)}${checkpointSummary(r)}`;
}

function renderRuns(rows, target = '#runsTable', limit = 999) {
  rows = (rows || []).slice(0, limit);
  const html = `<table class="data-table">
    <thead>
      <tr>
        <th>Lần chạy</th><th>Job</th><th>Trạng thái</th><th>Output + Scene</th><th>Facebook</th><th>Bắt đầu</th><th>Lỗi</th><th></th>
      </tr>
    </thead>
    <tbody>
      ${rows.map(r => {
        const vids = r.output?.video_paths || [];
        const pubs = r.publish_jobs || [];
        const canCancel = ['queued', 'waiting_flow', 'dispatching'].includes(String(r.status || ''));
        return `<tr>
          <td><b>${esc(short(r.id))}</b></td>
          <td><span class="job-id">${esc(r.instance_id)}</span></td>
          <td>${statusBadge(r.status)}</td>
          <td>${outputHealth(r)}${vids.length ? vids.map((_, i) => `<a target="_blank" href="/api/runs/${encodeURIComponent(r.id)}/video?index=${i}">▶ Video ${i + 1}</a>`).join('<br>') : '<span class="muted">chưa có video cuối</span>'}</td>
          <td>${pubs.length ? pubs.map(p => `${statusBadge(p.status)} <span class="muted">${esc(p.page_name || p.page_id)}</span>`).join('<br>') : '—'}</td>
          <td>${esc(when(r.created_at))}</td>
          <td class="muted">${esc(short(r.error))}</td>
          <td>${canCancel ? `<button class="btn small danger cancel-run" data-run="${esc(r.id)}">Hủy</button>` : ''}</td>
        </tr>`;
      }).join('')}
    </tbody>
  </table>`;
  const el = $(target);
  if (el) el.innerHTML = rows.length ? html : '<div class="muted">Chưa có run nào.</div>';
  $$(`${target} .cancel-run`).forEach(b => b.onclick = () => cancelRun(b.dataset.run));
  $$(`${target} .resume-job`).forEach(b => b.onclick = () => resumeFlowJob(b.dataset.job));
  $$(`${target} .scene-retry`).forEach(b => b.onclick = () => retryFlowScene(b.dataset.job, b.dataset.scene));
}

async function cancelRun(id) {
  try {
    await api(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
    toast('Đã hủy ' + short(id));
    await loadAll();
    await loadRuns();
  } catch (e) {
    toast(e.message, true);
  }
}

async function resumeFlowJob(jobId) {
  try {
    await api(`/api/flow/jobs/${encodeURIComponent(jobId)}/resume`, { method: 'POST' });
    toast('Đã resume Flow ' + short(jobId));
    await loadRuns();
  } catch (e) {
    toast(e.message, true);
  }
}

async function retryFlowScene(jobId, sceneId) {
  try {
    await api(`/api/flow/jobs/${encodeURIComponent(jobId)}/scenes/${encodeURIComponent(sceneId)}/retry`, { method: 'POST' });
    toast(`Đã retry scene ${sceneId}`);
    await loadRuns();
  } catch (e) {
    toast(e.message, true);
  }
}

async function loadRuns() {
  try {
    S.runs = await api('/api/runs?limit=200&checkpoints=true');
    renderRuns(S.runs);
  } catch (e) {
    toast(e.message, true);
  }
}

function renderDashFlow() {
  const f = S.status?.flow || {};
  const df = $('#dashFlow');
  if (df) {
    df.innerHTML = `<div class="flow-card">
      <div class="flow-line"><span>Extension</span><b>${flowConnected() ? 'ĐÃ NỐI' : 'MẤT KẾT NỐI'}</b></div>
      <div class="flow-line"><span>Hàng đợi</span><b>${esc(f.pending?.length ?? f.queue_length ?? f.queued ?? 0)}</b></div>
      <div class="flow-line"><span>Đang chạy</span><b>${esc(f.active?.source || f.active?.job_id || '—')}</b></div>
      <div class="flow-line"><span>Nguồn</span><span class="muted">Job 2 + Job 3 · model/luồng GLOBAL</span></div>
    </div>`;
  }
}

async function loadFlow() {
  try {
    S.flow = await api('/api/flow');
    renderFlow();
  } catch (e) {
    toast(e.message, true);
  }
}

function renderFlow() {
  const q = S.flow?.queue || {}, set = S.flow?.settings || {}, models = S.flow?.models || {};
  const fs = $('#flowStatus');
  if (fs) {
    fs.innerHTML = `<div class="flow-card">
      <div class="flow-line"><span>Extension</span><b class="${q.extensionConnected ? 'good' : 'bad'}">${q.extensionConnected ? 'ĐÃ NỐI' : 'MẤT KẾT NỐI'}</b></div>
      <div class="flow-line"><span>Phiên bản</span><b>${esc(q.extension?.version || '?')}</b></div>
      <div class="flow-line"><span>Cỡ hàng đợi</span><b>${q.pending?.length ?? 0}</b></div>
      <div class="flow-line"><span>Đang chạy</span><b>${esc(q.active?.jobId || q.active?.source || '—')}</b></div>
      <div class="flow-line"><span>Cầu Job 2</span><b>${q.sources?.beauty?.connected ? 'ON' : 'OFF'}</b></div>
      <div class="flow-line"><span>Cầu Job 3</span><b>${q.sources?.parenting?.connected ? 'ON' : 'OFF'}</b></div>
    </div>`;
  }
  const groups = [
    { title: '1. Model Flow', help: 'Dùng để tạo ảnh/video trên Flow.', items: [
      ['imageModel', 'Model tạo ảnh', 'select', models.image || ['Nano Banana 2', 'Nano Banana 2 Lite', 'Nano Banana Pro']],
      ['videoModel', 'Model tạo video', 'select', models.video || ['Veo 3.1 - Fast', 'Veo 3.1 - Lite', 'Veo 3.1 - Quality']]
    ]},
    { title: '2. Luồng & Output Flow', help: 'Thiết lập số luồng tạo song song.', items: [
      ['imageConcurrency', 'Luồng tạo ảnh', 'number', null],
      ['videoConcurrency', 'Luồng tạo video', 'number', null],
      ['aspectRatio', 'Tỷ lệ khung hình', 'select', ['9:16', '16:9', '1:1']],
      ['videoDuration', 'Độ dài clip Flow', 'select', ['4s', '6s', '8s']]
    ]},
    { title: '3. Model Viết Kịch Bản (9Router)', help: 'Model AI tạo kịch bản cho các Job.', items: [
      ['scriptAiModel', 'Model chính', 'select', ['ag/gemini-3.1-pro-high', 'cx/gpt-5.5', 'gemini-2.5-flash']],
      ['scriptFallbackModel', 'Model dự phòng', 'select', ['cx/gpt-5.5', 'ag/gemini-3.1-pro-high', 'gemini-2.5-flash']]
    ]},
    { title: '4. Timeout & Queue Policy', help: 'Thời gian chờ Flow tối đa.', items: [
      ['imageTimeoutSec', 'Timeout ảnh (giây)', 'number', null],
      ['videoTimeoutSec', 'Timeout video (giây)', 'number', null]
    ]},
    { title: '5. Tự động Dọn Rác Flow', help: 'Tránh dính prompt/ảnh cũ.', items: [
      ['autoDownloadVideo', 'Tự tải video', 'checkbox', null],
      ['clearComposerBeforeRun', 'Xóa khung nhập trước khi chạy', 'checkbox', null]
    ]}
  ];
  const bounds = { imageConcurrency: [1, 10], videoConcurrency: [1, 10], imageTimeoutSec: [30, 3600], videoTimeoutSec: [60, 7200] };
  const input = ([k, l, t, opts]) => {
    const [mn, mx] = bounds[k] || [null, null];
    const body = t === 'select'
      ? `<select data-fs="${k}">${opts.map(o => `<option value="${esc(o)}" ${String(set[k]) === String(o) ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select>`
      : t === 'checkbox'
      ? `<label><input data-fs="${k}" type="checkbox" ${set[k] ? 'checked' : ''}> Bật</label>`
      : `<input data-fs="${k}" type="number" ${mn !== null ? `min="${mn}"` : ''} ${mx !== null ? `max="${mx}"` : ''} value="${esc(set[k] ?? '')}">`;
    return `<div class="field"><label><span>${l}</span>${body}</label></div>`;
  };
  const setEl = $('#flowSettings');
  if (setEl) {
    setEl.innerHTML = groups.map(g =>
      `<section class="panel settings-section"><div class="panel-head"><h2>${g.title}</h2><p class="muted">${g.help}</p></div><div class="row">${g.items.map(input).join('')}</div></section>`
    ).join('');
  }
  const queue = q.pending || [];
  const qEl = $('#flowQueue');
  if (qEl) {
    qEl.innerHTML = `<table class="data-table"><thead><tr><th>#</th><th>Nguồn</th><th>Job ID</th><th>Trạng thái</th></tr></thead><tbody>${queue.map((x, i) =>
      `<tr><td>${i + 1}</td><td>${esc(x.source || '')}</td><td>${esc(x.jobId || x.job_id || '')}</td><td>${esc(statusLabel(x.status || 'WAITING'))}</td></tr>`
    ).join('')}</tbody></table>`;
  }
}

async function loadLogs() {
  try {
    const filter = S.logFilter || 'all';
    const qs = filter === 'all' ? '/api/logs?limit=500' : `/api/logs?limit=500&kind=${filter}`;
    S.logs = await api(qs);
    renderLogs();
  } catch (e) {
    toast('Lỗi tải nhật ký: ' + e.message, true);
  }
}

function renderLogs() {
  const logs = S.logs || [];
  const lb = $('#logsBox');
  if (!lb) return;
  lb.innerHTML = logs.map(x => {
    const isExt = x.kind === 'extension';
    const tagCls = isExt ? 'ext-tag' : 'server-tag';
    const tagText = isExt ? 'EXTENSION' : (x.kind || 'SERVER').toUpperCase();
    const lvl = String(x.level || 'INFO').toUpperCase();
    const lvlCls = lvl === 'SUCCESS' ? 'good' : lvl === 'ERROR' || lvl === 'FAIL' ? 'bad' : lvl === 'WARNING' || lvl === 'WARN' ? 'warn' : '';
    return `<div class="log-line ${isExt ? 'log-ext' : ''}">
      <span class="log-ts">${esc(x.created_at || '')}</span>
      <span class="badge ${lvlCls}">${esc(lvl)}</span>
      <span class="badge ${tagCls}">${esc(tagText)}</span>
      <span class="log-msg">${x.instance_id ? `<b class="job-id">${esc(x.instance_id)}</b> ` : ''}${esc(x.message || '')}</span>
    </div>`;
  }).join('') || '<div class="muted">Chưa có nhật ký.</div>';
}

async function copyLogsToClipboard(kind, label) {
  try {
    const qs = kind === 'all' ? '/api/logs?limit=1000' : `/api/logs?limit=1000&kind=${kind}`;
    const data = await api(qs);
    if (!data || !data.length) {
      toast(`Không có ${label} để copy`, true);
      return;
    }
    const text = data.map(x => `[${x.created_at || ''}] [${(x.level || 'INFO').toUpperCase()}] [${(x.kind || 'system').toUpperCase()}] ${x.instance_id ? x.instance_id + ' · ' : ''}${x.message || ''}`).join('\n');
    await navigator.clipboard.writeText(text);
    toast(`Đã copy ${data.length} dòng ${label}`);
  } catch (e) {
    toast('Lỗi copy: ' + e.message, true);
  }
}

function openEdit(id) {
  const j = S.jobs.find(x => x.id === id);
  if (!j) return;
  S.editing = j;
  S.creatingTemplate = null;
  openJobModal(j.template_id, j);
}

function openCreate(templateId) {
  S.editing = null;
  S.creatingTemplate = templateId;
  openJobModal(templateId, null);
}

// Shopee
function shopeeSave() {
  localStorage.setItem('v28_shopee_basket', JSON.stringify(S.shopee.basket || []));
  localStorage.setItem('v28_shopee_mode', S.shopee.mode || 'one_product_per_video');
}
function productUrl(x) { return x.affiliate_url || x.url || x.origin_url || x.product_url || ''; }
function productTitle(x) { return x.title || x.name || x.product_title || 'Sản phẩm Shopee'; }
function productImg(x) { return x.image || x.image_url || x.imageUrl || x.thumbnail || ''; }
function normalizeProduct(x) {
  const url = x.url || x.origin_url || x.product_url || '';
  return {
    id: x.id || x.product_id || `${x.shopId || ''}.${x.itemId || ''}` || url,
    title: productTitle(x), url, origin_url: url,
    affiliate_url: x.affiliate_url || '',
    price: x.price || x.price_text || '',
    image: productImg(x),
    shopId: x.shopId || x.shop_id || '',
    itemId: x.itemId || x.item_id || ''
  };
}

function renderProductRow(x, i, mode) {
  const img = productImg(x);
  const aff = x.affiliate_url ? '<span class="badge good">AFF</span>' : '<span class="badge warn">GỐC</span>';
  return `<div class="product-card" data-product-card="${i}">
    <label><input type="checkbox" data-prod-index="${i}" ${mode === 'basket' ? '' : 'checked'}></label>
    ${img ? `<img src="${esc(img)}" loading="lazy">` : ''}
    <div>
      <b>${esc(short(productTitle(x)))}</b>
      <div class="muted">${esc(x.price || '')} ${aff}</div>
      <a target="_blank" href="${esc(productUrl(x))}">${esc(short(productUrl(x)))}</a>
      ${x.affiliate_url ? `<div class="muted">affiliate_url: ${esc(short(x.affiliate_url))}</div>` : ''}
    </div>
    ${mode === 'basket' ? `<button class="btn small danger basket-del" data-basket-del="${i}">Xóa</button>` : ''}
  </div>`;
}

function renderShopee() {
  const v = $('#view-shopee');
  if (!v) return;
  const bm = $('#basketMode'); if (bm) bm.value = S.shopee.mode || 'one_product_per_video';
  const bc = $('#basketCount'); if (bc) bc.textContent = `${S.shopee.basket.length} sản phẩm`;
  const sr = $('#shopeeResults'); if (sr) sr.innerHTML = S.shopee.results.length ? S.shopee.results.map((x, i) => renderProductRow(x, i, 'result')).join('') : '<div class="muted">Chưa tìm sản phẩm.</div>';
  const sb = $('#shopeeBasket'); if (sb) sb.innerHTML = S.shopee.basket.length ? S.shopee.basket.map((x, i) => renderProductRow(x, i, 'basket')).join('') : '<div class="muted">Basket trống.</div>';
  $$('.basket-del').forEach(b => b.onclick = () => {
    S.shopee.basket.splice(Number(b.dataset.basketDel), 1);
    shopeeSave();
    toast('Đã xóa sản phẩm khỏi Basket');
    renderShopee();
  });
}

async function shopeeSearch() {
  const keyword = $('#shopeeKeyword')?.value?.trim();
  if (!keyword) return toast('Thiếu keyword Shopee', true);
  try {
    const count = Number($('#shopeeCount')?.value || 10);
    const r = await api('/api/shopee/research', { method: 'POST', body: JSON.stringify({ keyword, count }) });
    S.shopee.results = (r.items || r.results || []).map(normalizeProduct);
    toast(`Tìm được ${S.shopee.results.length} SP`);
    renderShopee();
  } catch (e) {
    toast(e.message, true);
  }
}

function shopeeAddSelected() {
  const picked = $$('#shopeeResults [data-prod-index]:checked').map(x => S.shopee.results[Number(x.dataset.prodIndex)]).filter(Boolean).map(normalizeProduct);
  const seen = new Set(S.shopee.basket.map(x => x.url || x.origin_url || x.id));
  for (const x of picked) {
    const key = x.url || x.origin_url || x.id;
    if (!seen.has(key)) {
      S.shopee.basket.push(x);
      seen.add(key);
    }
  }
  shopeeSave();
  toast(`Basket có ${S.shopee.basket.length} SP`);
  renderShopee();
}

async function shopeeConvertAffiliate() {
  const take = S.shopee.basket.filter(x => !x.affiliate_url).slice(0, 5);
  if (!take.length) return toast('Không có link gốc cần đổi', true);
  toast(`Đang đổi affiliate ${take.length} link…`);
  try {
    const sid = ($('#affiliateSubId')?.value || '').trim().replace(/[^a-zA-Z0-9]/g, '').slice(0, 50);
    const r = await api('/api/shopee/affiliate/convert', { method: 'POST', body: JSON.stringify({ links: take.map(x => x.origin_url || x.url), sub_ids: sid ? [sid] : [] }) });
    for (const row of (r.items || [])) {
      const item = S.shopee.basket.find(x => (x.origin_url || x.url) === row.origin_url);
      if (item && row.affiliate_url) item.affiliate_url = row.affiliate_url;
    }
    shopeeSave();
    toast(`Đã đổi ${S.shopee.basket.filter(x => x.affiliate_url).length}/${S.shopee.basket.length} link`);
    renderShopee();
  } catch (e) {
    toast(e.message, true);
  }
}

// Job Modal
function fieldInput(k, spec, val) {
  const id = 'cfg_' + k, label = spec.label || k, t = spec.type || 'text';
  if (t === 'checkbox') return `<label class="field"><span>${esc(label)}</span><input id="${id}" data-cfg="${esc(k)}" type="checkbox" ${val ? 'checked' : ''}></label>`;
  if (t === 'select') return `<label class="field"><span>${esc(label)}</span><select id="${id}" data-cfg="${esc(k)}">${(spec.options || []).map(o => `<option value="${esc(o)}" ${String(val) === String(o) ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select></label>`;
  if (t === 'textarea') return `<label class="field span2"><span>${esc(label)}</span><textarea id="${id}" data-cfg="${esc(k)}" rows="4">${esc(val ?? '')}</textarea></label>`;
  return `<label class="field"><span>${esc(label)}</span><input id="${id}" data-cfg="${esc(k)}" type="${t === 'number' ? 'number' : 'text'}" value="${esc(val ?? '')}"></label>`;
}

function openJobModal(templateId, j) {
  const t = S.templates.find(x => x.id === templateId);
  if (!t) return;
  $('#modalTitle').textContent = j ? `Sửa ${j.id}` : `Tạo ${t.name}`;
  $('#modalSub').textContent = t.description || '';
  const cfg = j?.config || t.defaults || {}, selected = new Set((j?.pages || []).map(x => x.page_id)), sched = j?.schedule || {};
  $('#modalBody').innerHTML = `<div class="edit-grid">
    <label class="field span2"><span>Tên Job</span><input id="jobName" value="${esc(j?.name || `${t.name} mới`)}"></label>
    <div class="span2 section-title">Nội dung / Prompt / Cấu hình</div>
    ${Object.entries(t.schema || {}).map(([k, s]) => fieldInput(k, s, cfg[k])).join('')}
    <div class="span2 section-title">Page Facebook</div>
    <div class="span2 page-checks">
      ${S.pages.length ? S.pages.map(p => `<label class="page-check"><input type="checkbox" data-pagecheck="${esc(p.id)}" ${selected.has(p.id) ? 'checked' : ''}><span><b>${esc(p.name)}</b><br><small class="muted">${esc(p.id)}</small></span></label>`).join('') : '<div class="muted">Chưa import Page Facebook.</div>'}
    </div>
    <div class="span2 section-title">Lịch chạy tự động</div>
    <label class="field"><span>Chế độ</span><select id="scheduleMode"><option value="manual" ${!sched.mode || sched.mode === 'manual' ? 'selected' : ''}>Thủ công</option><option value="daily" ${sched.mode === 'daily' ? 'selected' : ''}>Khung giờ hằng ngày</option><option value="interval" ${sched.mode === 'interval' ? 'selected' : ''}>Lặp theo phút</option></select></label>
    <label class="field"><span>Khung giờ (10:00,19:00)</span><input id="scheduleSlots" value="${esc((sched.daily_slots || sched.slots || []).join(','))}"></label>
    <label class="field"><span>Interval phút</span><input id="scheduleMinutes" type="number" min="5" value="${esc(sched.interval_minutes || sched.minutes || 60)}"></label>
  </div>`;
  $('#modal').classList.remove('hidden');
}

function closeModal() {
  $('#modal').classList.add('hidden');
  S.editing = null;
  S.creatingTemplate = null;
}

// Global Event Listeners
document.addEventListener('DOMContentLoaded', () => {
  buildNav();
  loadAll();
});

// Click handlers
document.addEventListener('click', e => {
  const target = e.target;
  if (!target) return;

  if (target.id === 'modalClose' || target.id === 'modalCancel') closeModal();

  if (target.id === 'modalSave') {
    (async () => {
      const tId = S.editing?.template_id || S.creatingTemplate;
      if (!tId) return;
      const cfg = {};
      $$('[data-cfg]').forEach(x => {
        cfg[x.dataset.cfg] = x.type === 'checkbox' ? x.checked : x.type === 'number' ? Number(x.value) : x.value;
      });
      const page_ids = $$('[data-pagecheck]:checked').map(x => x.dataset.pagecheck);
      const mode = $('#scheduleMode')?.value || 'manual';
      let schedule = { enabled: false, mode: 'manual' };
      if (mode === 'daily') schedule = { enabled: true, mode: 'daily', daily_slots: ($('#scheduleSlots')?.value || '').split(',').map(x => x.trim()).filter(Boolean) };
      else if (mode === 'interval') schedule = { enabled: true, mode: 'interval', interval_minutes: Number($('#scheduleMinutes')?.value || 60) };

      try {
        if (S.editing) {
          await api(`/api/jobs/${encodeURIComponent(S.editing.id)}`, { method: 'PATCH', body: JSON.stringify({ name: $('#jobName')?.value?.trim(), config: cfg, page_ids, schedule }) });
          toast(`Đã lưu ${S.editing.id}`);
        } else {
          const r = await api('/api/jobs', { method: 'POST', body: JSON.stringify({ template_id: tId, name: $('#jobName')?.value?.trim(), config: cfg, page_ids }) });
          if (Object.keys(schedule).length) await api(`/api/jobs/${encodeURIComponent(r.job.id)}`, { method: 'PATCH', body: JSON.stringify({ schedule }) });
          toast(`Đã tạo ${r.job.id}`);
        }
        closeModal();
        await loadAll();
      } catch (err) {
        toast(err.message, true);
      }
    })();
  }

  if (target.id === 'newJobBtn' || target.id === 'dashNewJob' || target.id === 'jobsNewBtn') {
    if (!S.templates.length) return;
    const names = S.templates.map(x => `${x.id}: ${x.name}`).join('\n');
    const id = prompt(`Chọn loại Job:\n${names}`, S.templates[0].id);
    if (id && S.templates.some(x => x.id === id.trim())) openCreate(id.trim());
  }

  if (target.id === 'refreshBtn' || target.id === 'dashRefresh') loadAll();
  if (target.id === 'testAllBtn') testAllJobs();
  if (target.id === 'runsRefresh') loadRuns();
  if (target.id === 'logsRefresh') loadLogs();
  // \u0110\u00e3 click
  if (target.id === 'fbTestAllBtn') testAllFbPages();
  if (target.id === 'shopeeCheckSession') { toast('Đang kiểm tra Shopee session…'); checkShopeeSession(); }
  if (target.id === 'shopeeSearchBtn') { toast('Đã click Tìm sản phẩm'); shopeeSearch(); }
  if (target.id === 'shopeeAddSelectedBtn') { toast('Đã click thêm sản phẩm đã chọn'); shopeeAddSelected(); }
  if (target.id === 'affiliateConvertBtn') { toast('Đã click đổi affiliate link'); shopeeConvertAffiliate(); }
  if (target.id === 'shopeeClearBasketBtn') { S.shopee.basket = []; shopeeSave(); toast('Đã click xóa toàn bộ giỏ'); renderShopee(); }
  if (target.id === 'basketCopyBtn') { navigator.clipboard?.writeText(JSON.stringify(S.shopee.basket, null, 2)); toast('Đã click Copy JSON'); }

  if (target.id === 'pruneMediaBtn') {
    if (!confirm('Dọn dẹp các video tạm và scene cũ hơn 7 ngày?')) return;
    api('/api/system/prune-media?days=7', { method: 'POST' }).then(r => {
      toast(`Đã xóa ${r.deleted_files || 0} file tạm, giải phóng ${r.reclaimed_mb || 0} MB`);
    }).catch(err => toast(err.message, true));
  }

  if (target.id === 'fbImportBtn') {
    const token = $('#fbToken')?.value?.trim();
    if (!token) return toast('Thiếu token Facebook', true);
    api('/api/facebook/import', { method: 'POST', body: JSON.stringify({ token }) }).then(r => {
      if ($('#fbToken')) $('#fbToken').value = '';
      toast(`Import xong · ${r.pages?.length ?? ''} Page`);
      loadAll();
    }).catch(err => toast(err.message, true));
  }

  if (target.id === 'manualSaveBtn') {
    api('/api/facebook/pages', {
      method: 'POST',
      body: JSON.stringify({
        page_id: $('#manualPageId')?.value?.trim(),
        name: $('#manualPageName')?.value?.trim() || 'Facebook Page',
        access_token: $('#manualPageToken')?.value?.trim()
      })
    }).then(() => {
      if ($('#manualPageId')) $('#manualPageId').value = '';
      if ($('#manualPageName')) $('#manualPageName').value = '';
      if ($('#manualPageToken')) $('#manualPageToken').value = '';
      toast('Đã lưu Page');
      loadAll();
    }).catch(err => toast(err.message, true));
  }

  if (target.id === 'saveFlowBtn') {
    const p = {};
    $$('[data-fs]').forEach(x => p[x.dataset.fs] = x.type === 'checkbox' ? x.checked : x.type === 'number' ? Number(x.value) : x.value);
    api('/api/flow/settings', { method: 'PATCH', body: JSON.stringify(p) }).then(() => {
      toast('Đã lưu cài đặt Flow');
      loadFlow();
    }).catch(err => toast(err.message, true));
  }

  const flowActionBtn = target.closest('[data-flow-action]');
  if (flowActionBtn) {
    api('/api/flow/control', { method: 'POST', body: JSON.stringify({ action: flowActionBtn.dataset.flowAction }) }).then(() => {
      toast('Flow: ' + flowActionBtn.dataset.flowAction);
      loadFlow();
    }).catch(err => toast(err.message, true));
  }

  const logFilterBtn = target.closest('[data-log-filter]');
  if (logFilterBtn) {
    $$('#logFilterSeg button').forEach(x => x.classList.toggle('active', x === logFilterBtn));
    S.logFilter = logFilterBtn.dataset.logFilter;
    loadLogs();
  }

  if (target.id === 'copyServerLogsBtn') copyLogsToClipboard('server', 'log Server');
  if (target.id === 'copyExtLogsBtn') copyLogsToClipboard('extension', 'log Extension');
  if (target.id === 'copyAllLogsBtn') copyLogsToClipboard('all', 'toàn bộ log');
});

// Auto-refresh poll
setInterval(() => {
  if (document.visibilityState === 'visible') {
    loadAll();
    if ($('#view-flow')?.classList.contains('active')) loadFlow();
  }
}, 10000);

// Initial bootstrap
buildNav();
loadAll();
