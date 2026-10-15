/* bark-hub web console */
const $ = (id) => document.getElementById(id);
const esc = (s) => { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; };

let TOKEN = localStorage.getItem('barkhub_token') || '';

async function api(path, opts = {}) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
  if (TOKEN) headers['X-API-Token'] = TOKEN;
  const r = await fetch(path, Object.assign({}, opts, { headers }));
  if (r.status === 401) { showTokenBar(); throw new Error('unauthorized'); }
  return r.json();
}
function showTokenBar() { $('tokenbar').classList.remove('hidden'); }
function saveToken() {
  TOKEN = $('token-input').value.trim();
  localStorage.setItem('barkhub_token', TOKEN);
  $('token-msg').textContent = '已保存';
  boot();
}

/* ---------- tabs ---------- */
document.querySelectorAll('.tab').forEach(btn => btn.onclick = () => {
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tabpane').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  $('tab-' + btn.dataset.tab).classList.add('active');
  if (btn.dataset.tab === 'history') loadHistory();
  if (btn.dataset.tab === 'settings') loadSettings();
});

/* ---------- overview ---------- */
async function loadStatus() {
  try {
    const s = await api('/api/status');
    $('subtitle').textContent = `事项 ${s.items} · 端点 ${s.endpoints} · 今日 ${s.today_count} 条 · bark-hub v${s.version}`;
    const cards = [
      { t: s.quiet ? '🌙 免打扰中' : '🔔 正常响铃', s: `窗口 ${s.window.start} – ${s.window.end} · 现在 ${s.now}`, cls: s.quiet ? 'warn' : 'ok' },
      { t: String(s.today_count), s: '今日推送', cls: '' },
      { t: String(s.total_count), s: `历史总量（保留 ${s.retention_days} 天）`, cls: '' },
      { t: String(s.items), s: `事项 · ${s.endpoints} 个启用端点`, cls: '' },
    ];
    $('stat-cards').innerHTML = cards.map(c =>
      `<div class="card"><div class="big ${c.cls}">${esc(c.t)}</div><div class="dimtxt">${esc(c.s)}</div></div>`).join('');
  } catch (e) { /* token bar shown */ }
}

/* ---------- items ---------- */
let ITEMS = [];
async function loadItems() {
  try { ITEMS = await api('/api/items'); } catch (e) { return; }
  $('send-item').innerHTML = '<option value="">选择事项…</option>' +
    ITEMS.map(i => `<option value="${esc(i.key)}">${esc(i.name)} (${esc(i.key)})</option>`).join('');
  $('hist-filter').innerHTML = '<option value="">全部事项</option>' +
    ITEMS.map(i => `<option value="${esc(i.key)}">${esc(i.name)}</option>`).join('');
  $('items-list').innerHTML = ITEMS.map(i => `
    <div class="card">
      <div class="row" style="justify-content:space-between">
        <div>
          <div class="nm">${esc(i.name)} <code>${esc(i.key)}</code>
            ${i.enabled ? '' : '<span class="pill">停用</span>'}
            ${i.bypass_quiet ? '<span class="pill crit">绕过免打扰</span>' : ''}</div>
          <div class="nt">${esc(i.description || '')}</div>
          <div class="nt">今日 ${i.today_count} 条 · 上次推送 ${esc(i.last_notification_at || '—')}</div>
        </div>
        <div class="row">
          <button class="ghost" onclick="testItem('${esc(i.key)}')">测试</button>
          <button class="ghost" onclick="openItemModal('${esc(i.key)}')">编辑</button>
          <button class="danger" onclick="deleteItem('${esc(i.key)}')">删除</button>
        </div>
      </div>
      <div class="eps">${i.endpoints.map(e => `
        <div class="ep"><span class="${e.enabled ? 'ok-dot' : 'dim-dot'}">●</span>
          ${esc(e.name || e.server_url)} <code>${esc(String(e.device_key).slice(0, 10))}…</code>
          ${Object.keys(e.params || {}).length ? `<span class="pill">${esc(JSON.stringify(e.params))}</span>` : ''}
        </div>`).join('') || '<div class="nt">（无端点）</div>'}
      </div>
    </div>`).join('');
}

async function testItem(key) {
  const r = await api(`/api/items/${key}/test`, { method: 'POST' });
  alert(r.code === 200 ? '✅ 已发送（force 响铃）' : '❌ ' + JSON.stringify(r));
  loadItems(); loadStatus(); loadRecent();
}
async function deleteItem(key) {
  if (!confirm(`删除事项 ${key}？其通知历史将一并删除。`)) return;
  await api(`/api/items/${key}`, { method: 'DELETE' });
  loadItems(); loadStatus();
}

/* ---------- item modal ---------- */
function endpointRow(ep = {}) {
  const params = Object.keys(ep.params || {}).length ? JSON.stringify(ep.params) : '';
  return `<div class="ep-row">
    <input placeholder="名称" class="e-name" value="${esc(ep.name || '')}">
    <input placeholder="https://api.day.app" class="e-url" value="${esc(ep.server_url || '')}">
    <input placeholder="device key" class="e-key" value="${esc(ep.device_key || '')}">
    <input placeholder='参数 JSON {"sound":"minuet"}' class="e-params" value="${esc(params)}">
    <label class="chk"><input type="checkbox" class="e-enabled" ${ep.enabled === false ? '' : 'checked'}>启用</label>
    <button class="danger" onclick="this.parentElement.remove()">✕</button>
  </div>`;
}
function addEndpointRow(ep) { $('f-endpoints').insertAdjacentHTML('beforeend', endpointRow(ep)); }

function openItemModal(key) {
  const it = key ? ITEMS.find(i => i.key === key) : null;
  $('modal-title').textContent = it ? '编辑事项' : '新建事项';
  $('f-key').value = it ? it.key : '';
  $('f-key').disabled = !!it;
  $('f-name').value = it ? it.name : '';
  $('f-desc').value = it ? it.description : '';
  $('f-bypass').checked = it ? it.bypass_quiet : false;
  $('f-enabled').checked = it ? it.enabled : true;
  $('f-endpoints').innerHTML = '';
  (it && it.endpoints.length ? it.endpoints : [{}]).forEach(e => addEndpointRow(e));
  $('modal').classList.remove('hidden');
}
function closeModal() { $('modal').classList.add('hidden'); }

async function saveItem() {
  const eps = [];
  for (const row of document.querySelectorAll('#f-endpoints .ep-row')) {
    let params = {};
    const raw = row.querySelector('.e-params').value.trim();
    if (raw) { try { params = JSON.parse(raw); } catch (e) { alert('端点参数不是合法 JSON：' + raw); return; } }
    eps.push({
      name: row.querySelector('.e-name').value.trim(),
      server_url: row.querySelector('.e-url').value.trim(),
      device_key: row.querySelector('.e-key').value.trim(),
      params, enabled: row.querySelector('.e-enabled').checked,
    });
  }
  const body = {
    key: $('f-key').value.trim(), name: $('f-name').value.trim(), description: $('f-desc').value.trim(),
    bypass_quiet: $('f-bypass').checked, enabled: $('f-enabled').checked,
    endpoints: eps.filter(e => e.server_url && e.device_key),
  };
  if (!body.key || !body.name) { alert('key 和名称必填'); return; }
  try {
    await api('/api/items', { method: 'POST', body: JSON.stringify(body) });
    closeModal(); loadItems(); loadStatus();
  } catch (e) { alert('保存失败'); }
}

/* ---------- history ---------- */
async function loadHistory(offset = 0) {
  const key = $('hist-filter').value || '';
  let d;
  try {
    d = await api(`/api/notifications?limit=50&offset=${offset}${key ? '&item_key=' + encodeURIComponent(key) : ''}`);
  } catch (e) { return; }
  $('hist-list').innerHTML = d.rows.map(n => `
    <details class="notif">
      <summary>
        <span class="dimtxt">${esc(n.created_at)}</span>
        <span class="pill">${esc(n.item_name)}</span>
        <strong>${esc(n.title)}</strong>
        <span class="nt">${esc((n.body || '').slice(0, 60))}</span>
        ${n.quiet ? '<span class="pill">静默</span>' : ''}
        ${n.forced ? '<span class="pill crit">强制</span>' : ''}
        <span class="lv lv-${n.level_effective}">${esc(n.level_effective)}</span>
        ${n.deliveries.some(x => !x.ok) ? '<span class="pill crit">投递失败</span>' : ''}
      </summary>
      <div class="deliv">
        ${n.deliveries.map(x => `
          <div class="ep"><span class="${x.ok ? 'ok-dot' : 'bad-dot'}">${x.ok ? '●' : '✕'}</span>
            ${esc(x.endpoint_name)} <span class="dimtxt">HTTP ${x.status_code} · ${x.latency_ms}ms</span></div>`).join('')
          || '<div class="nt">（无投递记录）</div>'}
        <div class="nt">来源 ${esc(n.source || '—')} · 请求级别 ${esc(n.level_requested)}</div>
      </div>
    </details>`).join('') || '<div class="nt">暂无记录</div>';
}

async function loadRecent() {
  try {
    const d = await api('/api/notifications?limit=8');
    $('recent-list').innerHTML = d.rows.map(n => `
      <div class="sender">
        <div><div class="nm">${esc(n.title)}</div>
        <div class="nt">${esc(n.item_name)} · ${esc(n.created_at)}</div></div>
        <span class="lv lv-${n.level_effective}">${esc(n.level_effective)}</span>
      </div>`).join('') || '<div class="nt">暂无</div>';
  } catch (e) { /* ignore */ }
}

/* ---------- manual send ---------- */
async function manualSend() {
  const key = $('send-item').value;
  if (!key) { $('send-result').textContent = '先选择事项'; return; }
  const body = {
    title: $('send-title').value || '(无标题)', body: $('send-body').value,
    group: 'webui', level: $('send-level').value || null,
    force: $('send-force').checked, source: 'webui',
  };
  const r = await api(`/api/notify/${key}`, { method: 'POST', body: JSON.stringify(body) });
  $('send-result').textContent = r.code === 200
    ? `✅ ${r.message}（delivered=${r.data ? r.data.delivered : '?'}）` : `❌ ${r.message}`;
  loadRecent(); loadStatus();
}

/* ---------- settings ---------- */
async function loadSettings() {
  try {
    const s = await api('/api/settings');
    $('set-qs').value = s.quiet_start; $('set-qe').value = s.quiet_end;
    $('set-ret').value = s.retention_days; $('set-dedup').value = s.dedup_seconds;
  } catch (e) { /* ignore */ }
}
async function saveSettings() {
  const body = {
    quiet_start: $('set-qs').value, quiet_end: $('set-qe').value,
    retention_days: parseInt($('set-ret').value, 10), dedup_seconds: parseInt($('set-dedup').value, 10),
  };
  const r = await api('/api/settings', { method: 'PUT', body: JSON.stringify(body) });
  $('set-result').textContent = r.quiet_start ? '✅ 已保存' : '❌ ' + JSON.stringify(r);
  loadStatus();
}

/* ---------- boot ---------- */
function boot() { loadStatus(); loadItems(); loadRecent(); }
boot();
setInterval(loadStatus, 30000);
