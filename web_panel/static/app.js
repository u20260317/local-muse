/* Local-Muse 面板交互：轮询刷新、批量提交、单任务终止、配置/技能/API 源/会话管理 */
(function () {
  'use strict';

  const page = document.body.dataset.page || '';
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value === undefined || value === null ? '' : value)
    .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '--';

  async function api(url, options) {
    const response = await fetch(url, options);
    return response.json();
  }
  function post(url, data) {
    return api(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data || {})
    });
  }
  let toastTimer = null;
  function toast(message, isError) {
    const box = $('toast');
    if (!box) return;
    box.textContent = message;
    box.className = 'toast' + (isError ? ' err' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.add('hidden'), 3600);
  }
  function note(id, message, isError) {
    const el = $(id);
    if (el) { el.textContent = message; el.style.color = isError ? '#fda4af' : ''; }
    toast(message, isError);
  }

  /* ---------------- 全局状态 ---------------- */
  async function refreshStatus() {
    let data;
    try { data = await api('/api/status'); } catch (e) { return; }
    if (!data || !data.ok) return;
    const hw = data.hardware || {};
    const set = (id, value) => { const el = $(id); if (el) el.textContent = value; };
    set('top-active', `并发 ${data.active_count} / ${data.max_concurrency}`);
    set('bar-cpu', hw.cpu_percent === undefined ? '--' : hw.cpu_percent);
    set('bar-mem', hw.memory_percent === undefined ? '--' : hw.memory_percent);
    set('bar-active', data.active_count);
    set('bar-queue', data.queue_size);
    set('metric-active', data.active_count);
    set('metric-queued', data.queue_size);
    set('metric-today', data.today);
    set('metric-success', (data.stats || {}).succeeded || 0);
    set('active-pill', data.active_count);
    set('queue-pill', `${data.queue_size} 条等待`);
    const degraded = $('top-degraded');
    if (degraded) degraded.classList.toggle('hidden', !data.degraded);
    const bar = $('bar-degraded');
    if (bar) {
      bar.innerHTML = data.degraded
        ? '<i class="dot" style="background:#F59E0B"></i>硬件降级：暂停派发新任务'
        : '<i class="dot dot-ok"></i>调度正常';
    }
    const sources = Object.values(data.providers || {});
    set('metric-inflight', sources.reduce((n, p) => n + (p.inflight || 0), 0));
    set('metric-cooldown', sources.filter((p) => p.cooldown_s > 0).length);
  }

  /* ---------------- 任务看板 ---------------- */
  function statusBadge(status) {
    return `<span class="badge badge-${esc(status)}">${esc(status)}</span>`;
  }
  async function refreshTasks() {
    let data;
    try { data = await api('/api/tasks'); } catch (e) { return; }
    if (!data || !data.ok) return;

    const cards = $('active-cards');
    if (cards) {
      cards.innerHTML = data.active.length ? data.active.map((t) => `
        <article class="task-card">
          <div class="task-head"><span class="task-id">${esc(t.task_id)}</span>${statusBadge(t.status)}</div>
          <div class="task-stage">${esc(t.stage)} · ${esc(t.skill)}</div>
          <div class="progress"><div class="progress-bar" style="width:${Number(t.progress) || 0}%"></div></div>
          <div class="task-meta">
            <span>进度 ${Number(t.progress) || 0}%</span><span>耗时 ${esc(t.elapsed_s)}s</span>
            <span>重试 ${esc(t.retry)}</span><span>pid ${esc(t.pid || '-')}</span>
            ${t.simulated ? '<span>模拟产物</span>' : ''}
          </div>
          <div class="task-actions"><button class="btn btn-danger-ghost btn-xs" data-cancel="${esc(t.task_id)}">单独终止</button></div>
        </article>`).join('') : '<div class="empty">当前没有运行中的任务，提交一条指令即可开始。</div>';
    }

    const queue = $('queue-body');
    if (queue) {
      queue.innerHTML = data.queued.length ? data.queued.map((t) => `
        <tr><td>${esc(t.task_id)}</td><td>${esc(t.priority)}</td><td>${esc(t.source)}</td>
        <td>${esc(t.skill)}</td><td>${esc(t.dry_run ? '自检' : '真实')}</td></tr>`).join('')
        : '<tr><td colspan="5" class="muted">队列为空</td></tr>';
    }

    const history = $('history-body');
    if (history) {
      history.innerHTML = data.history.length ? data.history.slice(0, 40).map((t) => {
        const canCancel = ['running', 'retrying', 'queued'].includes(t.status);
        return `<tr><td>${esc(t.task_id)}</td><td>${statusBadge(t.status)}</td>
          <td>${esc(t.stage)}</td><td>${esc(t.retry)}</td><td>${t.simulated ? '是' : '否'}</td>
          <td>${canCancel ? `<button class="btn btn-danger-ghost btn-xs" data-cancel="${esc(t.task_id)}">终止</button>` : '<span class="muted">—</span>'}</td></tr>`;
      }).join('') : '<tr><td colspan="6" class="muted">暂无记录</td></tr>';
    }
  }

  /* ---------------- 产物预览 ---------------- */
  async function refreshOutputs() {
    const box = $('outputs-grid');
    if (!box) return;
    let data;
    try { data = await api('/api/outputs'); } catch (e) { return; }
    const items = (data && data.items) || [];
    if (!items.length) { box.innerHTML = '<div class="empty">暂无产物</div>'; return; }
    box.innerHTML = items.map((it) => {
      const meta = `<div class="output-meta"><b title="${esc(it.name)}">${esc(it.name)}</b>${esc(it.task_id)} · ${(it.size / 1024).toFixed(1)} KB</div>`;
      if (it.kind === 'image') {
        return `<div class="output-card"><img src="${esc(it.url)}" alt="${esc(it.name)}" loading="lazy" onerror="this.style.display='none';this.parentElement.classList.add('img-error')">${meta}</div>`;
      }
      return `<div class="output-card"><div class="file-tile">${esc(it.kind.toUpperCase())}</div>${meta.replace('</div>', '<br><a href="' + esc(it.url) + '" target="_blank" rel="noopener">打开</a></div>')}</div>`;
    }).join('');
  }

  /* ---------------- 实时日志 ---------------- */
  let lastLogTs = 0;
  let logCounts = { INFO: 0, WARNING: 0, ERROR: 0, DEBUG: 0 };
  async function refreshLogs() {
    const stream = $('log-stream');
    if (!stream) return;
    if ($('log-pause') && $('log-pause').checked) return;
    const params = new URLSearchParams({ since: String(lastLogTs), limit: '300' });
    const taskId = ($('log-task') && $('log-task').value.trim()) || '';
    const level = ($('log-level') && $('log-level').value) || 'ALL';
    if (taskId) params.set('task_id', taskId);
    if (level !== 'ALL') params.set('level', level);
    let data;
    try { data = await api('/api/logs?' + params.toString()); } catch (e) { return; }
    if (!data || !data.ok) return;
    lastLogTs = data.now;
    if (!data.logs.length) return;
    if (stream.querySelector('.empty')) stream.innerHTML = '';
    const fragment = document.createDocumentFragment();
    data.logs.forEach((entry) => {
      const line = document.createElement('div');
      line.className = 'log-line lv-' + esc(entry.level);
      const time = document.createElement('span'); time.className = 'log-time'; time.textContent = entry.time;
      const task = document.createElement('span'); task.className = 'log-task'; task.textContent = 'task=' + entry.task_id;
      const stage = document.createElement('span'); stage.className = 'log-stage'; stage.textContent = entry.stage;
      const msg = document.createElement('span'); msg.className = 'log-msg'; msg.textContent = entry.message;
      line.append(time, task, stage, msg);
      fragment.appendChild(line);
      if (logCounts[entry.level] !== undefined) logCounts[entry.level] += 1;
    });
    stream.appendChild(fragment);
    while (stream.childElementCount > 500) stream.removeChild(stream.firstChild);
    if ($('log-autoscroll') && $('log-autoscroll').checked) stream.scrollTop = stream.scrollHeight;
    const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    set('metric-info', logCounts.INFO); set('metric-warn', logCounts.WARNING); set('metric-error', logCounts.ERROR);
  }

  /* ---------------- API 源 / 会话 ---------------- */
  async function refreshSources() {
    const body = $('sources-body');
    if (!body) return;
    let data;
    try { data = await api('/api/sources'); } catch (e) { return; }
    if (!data || !data.ok) return;
    const list = Object.values(data.sources || {});
    body.innerHTML = list.length ? list.map((p) => `
      <tr data-source="${esc(p.name)}">
        <td><b>${esc(p.name)}</b></td><td>${esc(p.kind)}</td>
        <td>${p.available ? '<span class="badge badge-succeeded">可用</span>'
          : `<span class="badge badge-paused">${p.has_key ? '已禁用' : '缺 Key'}</span>`}</td>
        <td><input type="number" min="1" max="20" value="${esc(p.weight)}" data-field="weight"></td>
        <td><input type="number" min="1" max="8" value="${esc(p.max_concurrency)}" data-field="max_concurrency"></td>
        <td>${esc(p.inflight)} / ${esc(p.max_concurrency)}</td>
        <td><input type="number" min="1" max="120" value="${esc(p.rpm)}" data-field="rpm"></td>
        <td>${p.cooldown_s > 0 ? esc(p.cooldown_s) + 's' : '—'}</td>
        <td>
          <button class="btn btn-ghost btn-xs" data-toggle="${esc(p.name)}" data-enabled="${p.enabled ? 'false' : 'true'}">${p.enabled ? '禁用' : '启用'}</button>
          <button class="btn btn-primary btn-xs" data-save="${esc(p.name)}">保存</button>
        </td>
      </tr>`).join('') : '<tr><td colspan="9" class="muted">未配置 API 源</td></tr>';

    const usage = $('usage-body');
    if (usage) {
      const rows = (data.usage && data.usage.length ? data.usage : data.history) || [];
      usage.innerHTML = rows.length ? rows.slice(0, 30).map((u) => `
        <tr><td>${esc(fmtTime(u.created_at))}</td><td>${esc(u.provider)}</td><td>${esc(u.task_id)}</td>
        <td>${statusBadge(u.status === 'ok' ? 'succeeded' : (u.status === '429' ? 'timeout' : 'failed'))}</td>
        <td>${esc(u.note || '')}</td></tr>`).join('') : '<tr><td colspan="5" class="muted">暂无记录</td></tr>';
    }

    let sessions;
    try { sessions = await api('/api/sessions'); } catch (e) { return; }
    const sBody = $('sessions-body');
    if (sBody && sessions && sessions.ok) {
      sBody.innerHTML = sessions.browserr.length ? sessions.browserr.map((s) => `
        <tr><td>${esc(s.name)}</td><td>${esc(s.task_id)}</td><td>${esc(s.size_mb)} MB</td></tr>`).join('')
        : '<tr><td colspan="3" class="muted">暂无会话</td></tr>';
    }
  }

  /* ---------------- Vault 自定义连接器 ---------------- */
  let connectorSchema = {};

  function renderConnectorFields() {
    const box = $('v-fields');
    const kind = $('v-kind') && $('v-kind').value;
    if (!box || !kind || !connectorSchema[kind]) { if (box) box.innerHTML = ''; return; }
    box.innerHTML = connectorSchema[kind].fields.map((f) => {
      const control = f.type === 'textarea'
        ? `<textarea data-v-field="${esc(f.key)}" rows="2" placeholder="${esc(f.label)}"></textarea>`
        : `<input data-v-field="${esc(f.key)}" type="${f.type === 'password' ? 'password' : 'text'}"
             value="${esc(f.default || '')}" autocomplete="new-password"
             placeholder="${esc(f.label)}${f.required ? '（必填）' : ''}">`;
      return `<label>${esc(f.label)}${control}</label>`;
    }).join('');
  }

  async function refreshVault() {
    const body = $('vault-body');
    if (!body) return;
    let data;
    try { data = await api('/api/vault'); } catch (e) { return; }
    if (!data || !data.ok) return;
    body.innerHTML = data.cards.length ? data.cards.map((c) => `
      <tr><td>${esc(c.name)}</td><td>${esc(c.kind_label)}</td><td>${esc(c.masked)}</td>
      <td>${esc(c.note || '')}</td><td>${c.encrypted ? '已加密' : '—'}</td>
      <td><button class="btn btn-danger-ghost btn-xs" data-vault-del="${esc(c.name)}">删除</button></td></tr>`).join('')
      : '<tr><td colspan="6" class="muted">暂无连接器</td></tr>';
  }

  async function loadConnectorSchema() {
    try {
      const r = await api('/api/vault/connectors');
      if (!r.ok) return;
      connectorSchema = r.connectors || {};
      const select = $('v-kind');
      if (select) {
        select.innerHTML = Object.keys(connectorSchema)
          .map((key) => `<option value="${esc(key)}">${esc(connectorSchema[key].label)}</option>`).join('');
        select.addEventListener('change', renderConnectorFields);
        renderConnectorFields();
      }
    } catch (e) { /* 保持空表单 */ }
  }

  /* ---------------- 对话工作台（SQLite 会话记忆） ---------------- */
  let currentConv = '';
  let chatTimer = null;

  async function loadConversations() {
    const box = $('conv-list');
    if (!box) return;
    try {
      const r = await api('/api/conversations');
      if (!r.ok) return;
      box.innerHTML = r.conversations.length ? r.conversations.map((c) => `
        <div class="conv-item ${c.id === currentConv ? 'active' : ''}" data-conv="${esc(c.id)}">
          <span class="conv-name">${esc(c.title)}</span>
          <span class="conv-meta">${c.message_count || 0} 条消息 · ${fmtTime(c.updated_at)}</span>
          <div class="conv-tools">
            <button class="btn btn-ghost btn-xs" data-conv-rename="${esc(c.id)}">重命名</button>
            <button class="btn btn-danger-ghost btn-xs" data-conv-del="${esc(c.id)}">删除</button>
          </div>
        </div>`).join('') : '<div class="muted" style="font-size:12px;padding:8px">暂无会话，点上方新建</div>';
    } catch (e) { /* 保持原样 */ }
  }

  function renderMessages(messages) {
    const box = $('chat-messages');
    if (!box) return;
    if (!messages.length) {
      box.innerHTML = '<div class="chat-empty">新会话已创建。<br>在下方输入指令，Agent 会按优先级并发执行。</div>';
      return;
    }
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    box.innerHTML = messages.map((m) => `
      <div class="msg ${m.role === 'user' ? 'user' : 'assistant'}">
        ${esc(m.content)}<span class="msg-time">${fmtTime(m.created_at)}${m.task_id ? ' · 任务 ' + esc(m.task_id.slice(0, 8)) : ''}</span>
      </div>`).join('');
    if (atBottom) box.scrollTop = box.scrollHeight;
  }

  function renderConversationTasks(tasks) {
    const counter = $('conv-task-count');
    if (counter) counter.textContent = `本会话 ${tasks.length} 个任务`;
    const box = $('chat-tasks');
    if (!box) return;
    box.innerHTML = tasks.length ? tasks.slice(0, 12).map((t) => `
      <div class="task-card">
        <div class="t-row"><b>${esc(String(t.task_id || '').slice(0, 12))}</b><span>${esc(t.status || '')}</span></div>
        <div class="t-row" style="margin-top:5px;opacity:.7"><span>${esc(t.stage || 'queued')}</span><span>${Number(t.progress || 0)}%</span></div>
        <div class="bar"><i style="width:${Math.max(0, Math.min(100, Number(t.progress || 0)))}%"></i></div>
      </div>`).join('') : '<div class="muted" style="font-size:12px">暂无任务</div>';
  }

  async function refreshConversation() {
    if (!currentConv) return;
    try {
      const r = await api('/api/conversations/' + encodeURIComponent(currentConv));
      if (!r.ok) return;
      const title = $('conv-title');
      if (title) title.textContent = r.conversation.title || '对话工作台';
      renderMessages(r.messages || []);
      renderConversationTasks(r.tasks || []);
      const terminal = ['succeeded', 'success', 'failed', 'cancelled'];
      const running = (r.tasks || []).some((t) => !terminal.includes(String(t.status || '').toLowerCase()));
      const steps = $('chat-steps');
      if (steps) {
        try {
          const logs = await api('/api/logs?limit=12');
          steps.innerHTML = (logs.logs || []).slice(-12).reverse()
            .map((l) => `<div class="step-line">${esc(String(l.message || l.msg || ''))}</div>`).join('')
            || '<div class="muted" style="font-size:12px">暂无日志</div>';
        } catch (e) { /* 日志暂不可用 */ }
      }
      if (chatTimer) clearTimeout(chatTimer);
      if (running) chatTimer = setTimeout(refreshConversation, 2000);
    } catch (e) { /* 保持当前视图 */ }
  }

  async function selectConversation(id) {
    currentConv = id;
    await loadConversations();
    await refreshConversation();
  }

  async function initChat() {
    /* 左右栏可收起；状态存 localStorage，刷新后保持 */
    const shell = $('chat-shell');
    const paneKey = 'local_muse_chat_panes';
    let panes = { left: true, right: true };
    try {
      const saved = JSON.parse(localStorage.getItem(paneKey) || 'null');
      if (saved && typeof saved.left === 'boolean' && typeof saved.right === 'boolean') panes = saved;
    } catch (e) { /* 隐私模式下使用默认值 */ }

    const applyPanes = () => {
      if (!shell) return;
      shell.classList.toggle('left-collapsed', !panes.left);
      shell.classList.toggle('right-collapsed', !panes.right);
      [['btn-toggle-left', panes.left], ['btn-toggle-right', panes.right]].forEach(([id, shown]) => {
        const el = $(id);
        if (!el) return;
        el.classList.toggle('off', !shown);
        el.setAttribute('aria-pressed', String(shown));
        el.title = shown ? '点击收起' : '点击展开';
      });
      try { localStorage.setItem(paneKey, JSON.stringify(panes)); } catch (e) { /* 忽略 */ }
    };
    const bindPane = (id, side) => {
      const el = $(id);
      if (el) el.addEventListener('click', () => { panes[side] = !panes[side]; applyPanes(); });
    };
    bindPane('btn-toggle-left', 'left');
    bindPane('btn-collapse-left', 'left');
    bindPane('btn-toggle-right', 'right');
    bindPane('btn-collapse-right', 'right');
    applyPanes();

    try {
      const r = await api('/api/skills');
      const select = $('chat-skill');
      if (select && r.ok) select.innerHTML = r.skills.length
        ? r.skills.map((s) => `<option value="${esc(s.name)}">${esc(s.title)}</option>`).join('')
        : '<option value="">无可用技能</option>';
    } catch (e) { /* 保持占位 */ }

    const create = async (title) => {
      const r = await post('/api/conversations', { title: title || '新会话' });
      if (r.ok) await selectConversation(r.id);
    };

    const newBtn = $('btn-new-conv');
    if (newBtn) newBtn.addEventListener('click', () => create('新会话'));

    const list = $('conv-list');
    if (list) list.addEventListener('click', async (event) => {
      const del = event.target.closest('[data-conv-del]');
      const rename = event.target.closest('[data-conv-rename]');
      const item = event.target.closest('[data-conv]');
      if (del) {
        if (!confirm('删除会话会同时删除其中的消息记录，确定？')) return;
        const id = del.dataset.convDel;
        await post('/api/conversations/' + encodeURIComponent(id), { action: 'delete' });
        if (currentConv === id) {
          currentConv = '';
          const box = $('chat-messages');
          if (box) box.innerHTML = '<div class="chat-empty">会话已删除，点击左侧「＋ 新会话」重新开始。</div>';
          renderConversationTasks([]);
        }
        await loadConversations();
        return;
      }
      if (rename) {
        const id = rename.dataset.convRename;
        const title = prompt('会话名称', '新会话');
        if (title) await post('/api/conversations/' + encodeURIComponent(id), { title });
        await loadConversations();
        if (currentConv === id) await refreshConversation();
        return;
      }
      if (item) await selectConversation(item.dataset.conv);
    });

    const form = $('chat-form');
    if (form) form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const text = $('chat-input').value.trim();
      if (!text) { note('chat-note', '请输入指令', true); return; }
      if (!currentConv) await create(text.slice(0, 24));
      if (!currentConv) { note('chat-note', '会话创建失败', true); return; }
      try {
        const r = await post('/api/tasks/submit', {
          instruction: text, conversation_id: currentConv,
          count: Number($('chat-count').value) || 1,
          priority: $('chat-priority').value,
          skill: $('chat-skill').value,
          dry_run: $('chat-dry').checked
        });
        if (r.ok) {
          $('chat-input').value = '';
          note('chat-note', `已提交 ${r.count} 条任务`);
          await refreshConversation();
        } else note('chat-note', r.error || '提交失败', true);
      } catch (e) { note('chat-note', '提交失败：' + e.message, true); }
    });

    await loadConversations();
    const first = document.querySelector('[data-conv]');
    if (first) await selectConversation(first.dataset.conv);
  }

  /* ---------------- 各页面初始化 ---------------- */
  async function initDashboard() {
    try {
      const r = await api('/api/skills');
      const select = $('f-skill');
      if (select && r.ok) {
        select.innerHTML = r.skills.length
          ? r.skills.map((s) => `<option value="${esc(s.name)}">${esc(s.title)}</option>`).join('')
          : '<option value="">无可用技能</option>';
      }
    } catch (e) { /* 保持占位 */ }

    const submit = async (payload) => {
      try {
        const r = await post('/api/tasks/submit', payload);
        if (r.ok) {
          note('submit-note', `已提交 ${r.count} 条任务：${r.task_ids.slice(0, 3).join(', ')}${r.count > 3 ? '…' : ''}`);
          refreshTasks();
        } else note('submit-note', r.error || '提交失败', true);
      } catch (e) { note('submit-note', '提交失败：' + e.message, true); }
    };
    const readForm = () => ({
      count: Number($('f-count').value) || 1,
      priority: $('f-priority').value,
      enqueue_mode: $('f-mode').value,
      skill: $('f-skill').value,
      instruction: $('f-instruction').value.trim() || '面板手动触发任务',
      dry_run: $('f-dryrun').checked
    });
    const btn = $('btn-submit');
    if (btn) btn.addEventListener('click', () => submit(readForm()));
    const quick = $('btn-quick-test');
    if (quick) quick.addEventListener('click', () => submit(Object.assign(readForm(), {
      count: 1, priority: 'high_test', enqueue_mode: 'insert', dry_run: true,
      instruction: '一键高优先级测试任务'
    })));
    refreshTasks();
    refreshOutputs();
    setInterval(refreshTasks, 2000);
    setInterval(refreshOutputs, 10000);
  }

  function initConfig() {
    const form = $('config-form');
    if (form) form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const f = new FormData(form);
      const num = (name, fallback) => Number(f.get(name)) || fallback;
      const payload = {
        scheduler: {
          daily_task_limit: num('daily_task_limit', 10), max_concurrency: num('max_concurrency', 2),
          default_api_max_concurrency: num('default_api_max_concurrency', 1),
          interval_seconds: num('interval_seconds', 300), retry_times: num('retry_times', 2),
          task_timeout_seconds: num('task_timeout_seconds', 600),
          manual_enqueue_mode: f.get('manual_enqueue_mode') || 'queue',
          subprocess_memory_limit_mb: num('subprocess_memory_limit_mb', 512)
        },
        browser: {
          enabled: form.elements.browser_enabled.checked, headless: form.elements.headless.checked,
          timeout_seconds: num('timeout_seconds', 60),
          navigation_timeout_ms: num('navigation_timeout_ms', 30000),
          per_session_profile: form.elements.per_session_profile.checked
        },
        hardware: {
          cpu_threshold: num('cpu_threshold', 85), memory_threshold: num('memory_threshold', 85),
          sample_interval_seconds: num('sample_interval_seconds', 2),
          recovery_margin: num('recovery_margin', 10)
        },
        server: { host: '127.0.0.1', port: num('port', 8765), use_reloader: false },
        providers: Array.from(document.querySelectorAll('[data-key-for]')).map((input) => ({
          name: input.dataset.keyFor, api_key: input.value
        }))
      };
      try {
        const r = await post('/api/config', payload);
        if (r.ok) {
          note('config-note', '配置已保存并热更新（端口变更需重启生效）');
          document.querySelectorAll('[data-key-for]').forEach((i) => { i.value = ''; });
        } else note('config-note', r.error || '保存失败', true);
      } catch (e) { note('config-note', '保存失败：' + e.message, true); }
    });

    const add = $('btn-vault-add');
    if (add) add.addEventListener('click', async () => {
      try {
        const fields = {};
        document.querySelectorAll('[data-v-field]').forEach((el) => {
          fields[el.dataset.vField] = el.value;
        });
        const r = await post('/api/vault', {
          name: $('v-name').value.trim(), kind: $('v-kind').value,
          fields, note: $('v-note').value.trim()
        });
        if (r.ok) {
          $('v-name').value = ''; $('v-note').value = '';
          renderConnectorFields();
          toast('连接器已加密写入 Vault（内容不可回显）');
          refreshVault();
        } else toast(r.error || '新增失败', true);
      } catch (e) { toast('新增失败：' + e.message, true); }
    });
    loadConnectorSchema();
    refreshVault();
    setInterval(refreshVault, 15000);
  }

  function initSkills() {
    const editor = $('skill-content');
    const nameInput = $('skill-name');
    const preview = $('skill-preview');
    const sync = () => {
      if (!editor) return;
      const text = editor.value;
      if (preview) preview.textContent = text || '（空）';
      const counter = $('skill-counter');
      if (counter) counter.textContent = text.length + ' 字';
    };
    if (editor) editor.addEventListener('input', sync);

    const load = async (name) => {
      try {
        const r = await api('/api/skills/' + encodeURIComponent(name));
        if (!r.ok) { note('skill-note', r.error || '加载失败', true); return; }
        nameInput.value = r.skill.name;
        editor.value = r.skill.raw || r.skill.body || '';
        sync();
        note('skill-note', '已加载 ' + r.skill.name);
      } catch (e) { note('skill-note', '加载失败：' + e.message, true); }
    };
    document.querySelectorAll('[data-skill]').forEach((button) =>
      button.addEventListener('click', () => load(button.dataset.skill)));

    const newBtn = $('btn-skill-new');
    if (newBtn) newBtn.addEventListener('click', () => {
      nameInput.value = 'my_skill_' + Date.now().toString(36).slice(-5);
      editor.value = '---\ntitle: 新技能\ndescription: 在这里描述用途\nenabled: true\n---\n\n## 系统提示词\n\n请在此编写提示词。\n';
      sync();
      note('skill-note', '已创建新技能草稿，保存后写入 docs/skills/');
    });

    const save = $('btn-skill-save');
    if (save) save.addEventListener('click', async () => {
      const name = nameInput.value.trim();
      if (!name) { note('skill-note', '请填写技能文件名', true); return; }
      try {
        const r = await post('/api/skills/' + encodeURIComponent(name), { content: editor.value });
        if (r.ok) { note('skill-note', '已保存 ' + name); setTimeout(() => location.reload(), 700); }
        else note('skill-note', r.error || '保存失败', true);
      } catch (e) { note('skill-note', '保存失败：' + e.message, true); }
    });

    const del = $('btn-skill-delete');
    if (del) del.addEventListener('click', async () => {
      const name = nameInput.value.trim();
      if (!name || !confirm('确定删除技能 ' + name + ' ？')) return;
      try {
        const r = await api('/api/skills/' + encodeURIComponent(name), { method: 'DELETE' });
        if (r.ok) { note('skill-note', '已删除'); setTimeout(() => location.reload(), 700); }
        else note('skill-note', r.error || '删除失败', true);
      } catch (e) { note('skill-note', '删除失败：' + e.message, true); }
    });
  }

  function initSources() {
    refreshSources();
    setInterval(refreshSources, 3000);
    const reset = $('btn-reset-browser');
    if (reset) reset.addEventListener('click', async () => {
      if (!confirm('确定清空 browser_profile/ 下全部独立 Browserr 会话？\n此操作不会影响本机日常浏览器。')) return;
      try {
        const r = await post('/api/sessions/reset', {});
        if (r.ok) { note('sessions-note', `已清理 ${r.removed} 个会话目录`); refreshSources(); }
        else note('sessions-note', r.error || '重置失败', true);
      } catch (e) { note('sessions-note', '重置失败：' + e.message, true); }
    });
  }

  function initLogs() {
    refreshLogs();
    refreshOutputs();
    setInterval(refreshLogs, 1500);
    setInterval(refreshOutputs, 15000);
    const clear = $('btn-log-clear');
    if (clear) clear.addEventListener('click', () => {
      const stream = $('log-stream');
      if (stream) stream.innerHTML = '<div class="empty">视图已清空，等待新日志…</div>';
      logCounts = { INFO: 0, WARNING: 0, ERROR: 0, DEBUG: 0 };
    });
    ['log-task', 'log-level'].forEach((id) => {
      const el = $(id);
      if (el) el.addEventListener('change', () => {
        const stream = $('log-stream');
        if (stream) stream.innerHTML = '';
        lastLogTs = 0;
        refreshLogs();
      });
    });
  }

  /* ---------------- 全局事件委托 ---------------- */
  document.addEventListener('click', async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;

    if (target.dataset.cancel) {
      const taskId = target.dataset.cancel;
      if (!confirm('确定单独终止任务 ' + taskId + ' ？其它并行任务不受影响。')) return;
      try {
        const r = await post('/api/tasks/' + encodeURIComponent(taskId) + '/cancel', {});
        toast(r.ok ? '已终止任务 ' + taskId : (r.error || '终止失败'), !r.ok);
        refreshTasks();
      } catch (e) { toast('终止失败：' + e.message, true); }
      return;
    }
    if (target.dataset.toggle) {
      try {
        const r = await post('/api/sources/' + encodeURIComponent(target.dataset.toggle),
          { enabled: target.dataset.enabled === 'true' });
        toast(r.ok ? '服务商状态已更新' : (r.error || '更新失败'), !r.ok);
        refreshSources();
      } catch (e) { toast('更新失败：' + e.message, true); }
      return;
    }
    if (target.dataset.save) {
      const row = target.closest('tr[data-source]');
      if (!row) return;
      const value = (field) => Number(row.querySelector(`[data-field="${field}"]`).value);
      try {
        const r = await post('/api/sources/' + encodeURIComponent(target.dataset.save), {
          weight: value('weight'), max_concurrency: value('max_concurrency'), rpm: value('rpm')
        });
        toast(r.ok ? '并发配额与权重已保存' : (r.error || '保存失败'), !r.ok);
        refreshSources();
      } catch (e) { toast('保存失败：' + e.message, true); }
      return;
    }
    if (target.dataset.vaultDel) {
      if (!confirm('确定删除凭证卡片 ' + target.dataset.vaultDel + ' ？')) return;
      try {
        const r = await api('/api/vault/' + encodeURIComponent(target.dataset.vaultDel), { method: 'DELETE' });
        toast(r.ok ? '凭证卡片已删除' : '删除失败', !r.ok);
        refreshVault();
      } catch (e) { toast('删除失败：' + e.message, true); }
    }
  });

  /* ---------------- 启动 ---------------- */
  refreshStatus();
  setInterval(refreshStatus, 2000);
  if (page === 'chat') initChat();
  if (page === 'dashboard') initDashboard();
  if (page === 'config') initConfig();
  if (page === 'skills') initSkills();
  if (page === 'sources') initSources();
  if (page === 'logs') initLogs();
})();
