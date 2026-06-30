const state = {
  sites: [],
  changes: [],
  siteChanges: [],
  notificationSettings: null,
  notificationDirty: false,
  selectedSiteId: null,
  activeView: 'overview',
  activeViewEl: null,
  activeNavEl: null,
  refreshInFlight: false,
};

const els = {
  navItems: Array.from(document.querySelectorAll('[data-view-target]')),
  views: Array.from(document.querySelectorAll('[data-view]')),
  statsSites: document.getElementById('stat-sites'),
  statsEnabled: document.getElementById('stat-enabled'),
  statsOk: document.getElementById('stat-ok'),
  statsFailed: document.getElementById('stat-failed'),
  statsChanges: document.getElementById('stat-changes'),
  sidebarCount: document.getElementById('sidebar-count'),
  overviewSitesBody: document.getElementById('overviewSitesBody'),
  overviewChangesBody: document.getElementById('overviewChangesBody'),
  sitesBody: document.getElementById('sitesBody'),
  changesBody: document.getElementById('changesBody'),
  detailPane: document.getElementById('detailPane'),
  detailSubtitle: document.getElementById('detailSubtitle'),
  refreshBtn: document.getElementById('refreshBtn'),
  addSiteBtn: document.getElementById('addSiteBtn'),
  searchInput: document.getElementById('searchInput'),
  statusFilter: document.getElementById('statusFilter'),
  dialog: document.getElementById('siteDialog'),
  form: document.getElementById('siteForm'),
  dialogTitle: document.getElementById('dialogTitle'),
  siteId: document.getElementById('siteId'),
  siteName: document.getElementById('siteName'),
  sitePlatform: document.getElementById('sitePlatform'),
  siteBaseUrl: document.getElementById('siteBaseUrl'),
  siteInterval: document.getElementById('siteInterval'),
  siteAuthMode: document.getElementById('siteAuthMode'),
  siteLoginEnabled: document.getElementById('siteLoginEnabled'),
  siteLoginUsername: document.getElementById('siteLoginUsername'),
  siteLoginPassword: document.getElementById('siteLoginPassword'),
  siteAccessToken: document.getElementById('siteAccessToken'),
  siteSub2apiAccessToken: document.getElementById('siteSub2apiAccessToken'),
  siteRefreshToken: document.getElementById('siteRefreshToken'),
  siteTokenExpiresAt: document.getElementById('siteTokenExpiresAt'),
  siteAccessUserId: document.getElementById('siteAccessUserId'),
  loginFields: document.getElementById('loginFields'),
  newapiAuthSection: document.getElementById('newapiAuthSection'),
  sub2apiAuthSection: document.getElementById('sub2apiAuthSection'),
  sub2apiPasswordFields: document.getElementById('sub2apiPasswordFields'),
  sub2apiTokenFields: document.getElementById('sub2apiTokenFields'),
  siteEnabled: document.getElementById('siteEnabled'),
  dialogMsg: document.getElementById('dialogMsg'),
  testConnBtn: document.getElementById('testConnBtn'),
  testLoginBtn: document.getElementById('testLoginBtn'),
  closeDialogBtn: document.getElementById('closeDialogBtn'),
  notifyForm: document.getElementById('notifyForm'),
  emailEnabled: document.getElementById('emailEnabled'),
  smtpHost: document.getElementById('smtpHost'),
  smtpPort: document.getElementById('smtpPort'),
  smtpUsername: document.getElementById('smtpUsername'),
  smtpPassword: document.getElementById('smtpPassword'),
  smtpUseSsl: document.getElementById('smtpUseSsl'),
  smtpFrom: document.getElementById('smtpFrom'),
  smtpTo: document.getElementById('smtpTo'),
  notifyStatus: document.getElementById('notifyStatus'),
  testEmailBtn: document.getElementById('testEmailBtn'),
};

function fmtTime(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString('zh-CN', { hour12: false });
}

function escapeHtml(str) {
  return String(str ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function badgeClass(status) {
  if (status === 'ok') return 'ok';
  if (status === 'failed') return 'bad';
  if (status === 'warning') return 'warn';
  return 'neutral';
}

function changeClass(change) {
  if (change.change_type === 'group_removed') return 'bad';
  if (change.change_type === 'ratio_changed' && Number(change.change_percent) > 0) return 'warn';
  if (change.change_type === 'ratio_changed') return 'ok';
  return 'neutral';
}

function changeTypeLabel(type) {
  const labels = {
    ratio_changed: '倍率变化',
    group_added: '新增分组',
    group_removed: '删除分组',
    desc_changed: '描述变化',
    status_changed: '状态变化',
    is_exclusive_changed: '专属变化',
    subscription_type_changed: '订阅变化',
    rpm_limit_changed: 'RPM 变化',
    platform_changed: '平台变化',
  };
  return labels[type] || type || '-';
}

function ratioLabel(item) {
  if (!item) return '-';
  const ratio = item.ratio;
  if (item.ratio_type === 'text') return `${ratio}`;
  const n = Number(ratio);
  if (Number.isFinite(n)) return `${n.toFixed(2)}x`;
  return `${ratio}`;
}

function setLoginFieldsVisible(visible) {
  els.loginFields.classList.toggle('hidden', !visible);
}

function sub2apiAuthMode() {
  return els.siteAuthMode.value || 'password';
}

function updateSub2apiAuthFields() {
  const tokenMode = sub2apiAuthMode() === 'token';
  els.sub2apiPasswordFields.classList.toggle('hidden', tokenMode);
  els.sub2apiTokenFields.classList.toggle('hidden', !tokenMode);
}

function platformLabel(siteOrValue) {
  const value = typeof siteOrValue === 'string' ? siteOrValue : (siteOrValue?.platform || 'newapi');
  return value === 'sub2api' ? 'sub2api' : 'NewAPI';
}

function updatePlatformFields() {
  const platform = els.sitePlatform.value || 'newapi';
  const isSub2api = platform === 'sub2api';
  els.newapiAuthSection.classList.toggle('hidden', isSub2api);
  els.sub2apiAuthSection.classList.toggle('hidden', !isSub2api);
  updateSub2apiAuthFields();
  els.testLoginBtn.textContent = isSub2api ? (sub2apiAuthMode() === 'token' ? '测试登录态' : '测试登录') : '测试认证';
  if (isSub2api) {
    els.siteLoginEnabled.checked = true;
    setLoginFieldsVisible(false);
  } else {
    setLoginFieldsVisible(els.siteLoginEnabled.checked);
  }
}

function setActiveView(view) {
  if (state.activeView === view && state.activeViewEl) {
    renderActiveView();
    return;
  }
  state.activeView = view;
  const nextView = els.views.find((item) => item.dataset.view === view);
  const nextNav = els.navItems.find((item) => item.dataset.viewTarget === view);
  if (state.activeViewEl && state.activeViewEl !== nextView) state.activeViewEl.classList.remove('active');
  if (state.activeNavEl && state.activeNavEl !== nextNav) state.activeNavEl.classList.remove('active');
  if (nextView) nextView.classList.add('active');
  if (nextNav) nextNav.classList.add('active');
  state.activeViewEl = nextView || null;
  state.activeNavEl = nextNav || null;
  requestAnimationFrame(() => {
    renderActiveView();
    pruneInactiveViews();
  });
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = { raw: text }; }
  if (!res.ok) {
    throw new Error(body?.message || body?.error || `HTTP ${res.status}`);
  }
  return body;
}

function renderStats() {
  const sites = state.sites;
  els.statsSites.textContent = sites.length;
  els.statsEnabled.textContent = sites.filter((s) => s.enabled).length;
  els.statsOk.textContent = sites.filter((s) => s.status === 'ok').length;
  els.statsFailed.textContent = sites.filter((s) => ['warning', 'failed'].includes(s.status)).length;
  els.statsChanges.textContent = state.changes.length;
  els.sidebarCount.textContent = sites.length;
}

function renderActiveView() {
  if (state.activeView === 'overview') {
    renderStats();
    els.overviewSitesBody.innerHTML = siteRows(state.sites.slice(0, 6));
    els.overviewChangesBody.innerHTML = changeRows(state.changes.slice(0, 8));
    return;
  }
  if (state.activeView === 'sites') {
    els.sitesBody.innerHTML = siteRows(filteredSites());
    return;
  }
  if (state.activeView === 'changes') {
    els.changesBody.innerHTML = changeRows(state.changes);
    return;
  }
  if (state.activeView === 'notifications') {
    renderNotificationSettings();
    return;
  }
  if (state.activeView === 'detail') {
    renderDetail(state.sites.find((s) => s.id === state.selectedSiteId));
  }
}

function pruneInactiveViews() {
  if (state.activeView !== 'overview') {
    els.overviewSitesBody.innerHTML = '';
    els.overviewChangesBody.innerHTML = '';
  }
  if (state.activeView !== 'sites') {
    els.sitesBody.innerHTML = '';
  }
  if (state.activeView !== 'changes') {
    els.changesBody.innerHTML = '';
  }
}

function siteRows(sites) {
  return sites.map((site) => {
    const selected = site.id === state.selectedSiteId ? ' selected' : '';
    const authCount = Number(site.current_login_groups_count || 0);
    const publicCount = Number(site.current_groups_count || 0);
    const hiddenCount = Math.max(0, authCount - publicCount);
    return `
      <tr class="${selected}">
        <td>
          <button class="link-cell" data-act="view" data-id="${site.id}">
            <strong>${escapeHtml(site.name)}</strong>
            <span>${escapeHtml(platformLabel(site))} · ${escapeHtml(site.base_url)}</span>
          </button>
        </td>
        <td><span class="badge ${badgeClass(site.status)}">${escapeHtml(site.status)}</span></td>
        <td>
          ${hiddenCount ? `<span class="tag strong">${hiddenCount} 个隐藏</span>` : '<span class="muted">无</span>'}
          ${site.platform === 'sub2api' ? '<span class="tag login">用户登录</span>' : site.login_enabled ? '<span class="tag login">认证增强</span>' : ''}
        </td>
        <td>${site.current_groups_count || 0}</td>
        <td>${fmtTime(site.last_check_at)}</td>
        <td>
          <div class="action-row">
            <button class="btn btn-secondary" data-act="check" data-id="${site.id}">检测</button>
            <button class="btn btn-secondary" data-act="edit" data-id="${site.id}">编辑</button>
            <button class="btn btn-secondary" data-act="delete" data-id="${site.id}">删除</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function filteredSites() {
  const keyword = els.searchInput.value.trim().toLowerCase();
  const filter = els.statusFilter.value;
  return state.sites.filter((site) => {
    if (keyword && !(`${site.name} ${site.base_url}`.toLowerCase().includes(keyword))) return false;
    if (filter && site.status !== filter) return false;
    return true;
  });
}

function renderSites() {
  if (state.activeView === 'sites') {
    els.sitesBody.innerHTML = siteRows(filteredSites());
  }
  if (state.activeView === 'overview') {
    els.overviewSitesBody.innerHTML = siteRows(state.sites.slice(0, 6));
  }
}

function changeRows(changes) {
  return changes.map((change) => `
    <tr>
      <td>${fmtTime(change.created_at)}</td>
      <td>${escapeHtml((state.sites.find((s) => s.id === change.site_id) || {}).name || `#${change.site_id}`)}</td>
      <td><span class="badge ${changeClass(change)}">${escapeHtml(changeTypeLabel(change.change_type))}</span></td>
      <td>${escapeHtml(change.group_name || '-')}</td>
      <td>${escapeHtml(change.message || '-')}</td>
    </tr>
  `).join('');
}

function renderGlobalChanges() {
  if (state.activeView === 'changes') {
    els.changesBody.innerHTML = changeRows(state.changes);
  }
  if (state.activeView === 'overview') {
    els.overviewChangesBody.innerHTML = changeRows(state.changes.slice(0, 8));
  }
}

function renderNotificationSettings(options = {}) {
  if (state.notificationDirty && !options.force) return;
  const settings = state.notificationSettings || {};
  els.emailEnabled.checked = !!settings.email_enabled;
  els.smtpHost.value = settings.smtp_host || '';
  els.smtpPort.value = settings.smtp_port || 465;
  els.smtpUsername.value = settings.smtp_username || '';
  els.smtpPassword.value = '';
  els.smtpUseSsl.checked = settings.smtp_use_ssl !== false;
  els.smtpFrom.value = settings.smtp_from || '';
  els.smtpTo.value = settings.smtp_to || '';
  const parts = [];
  parts.push(settings.email_enabled ? '邮箱推送已启用' : '邮箱推送未启用');
  if (settings.has_smtp_password) parts.push('密码已保存');
  if (settings.email_last_sent_at) parts.push(`上次发送：${fmtTime(settings.email_last_sent_at)}`);
  if (settings.email_last_error) parts.push(`错误：${settings.email_last_error}`);
  els.notifyStatus.textContent = parts.join(' · ');
  els.notifyStatus.classList.toggle('error', !!settings.email_last_error);
}

function renderDetail(site) {
  if (!site) {
    els.detailSubtitle.textContent = '选择一个站点查看当前倍率、隐藏分组和历史变化。';
    els.detailPane.className = 'detail-empty';
    els.detailPane.innerHTML = '还没有选中站点';
    return;
  }

  els.detailSubtitle.textContent = site.base_url;
  const publicGroupsObj = site.current_groups || {};
  const loginGroupsObj = site.current_login_groups || {};
  const activeGroupsObj = site.login_enabled && Object.keys(loginGroupsObj).length ? loginGroupsObj : publicGroupsObj;
  const groups = Object.entries(activeGroupsObj);
  const hiddenGroups = Object.entries(loginGroupsObj).filter(([name]) => !(name in publicGroupsObj));
  const groupRows = groups.length
    ? groups.map(([name, item]) => `
        <div class="group-row">
          <div>
            <strong>${escapeHtml(name)}</strong>
            <span>${escapeHtml([item.platform, item.status, item.is_exclusive ? '专属' : '', item.desc || '-'].filter(Boolean).join(' · '))}</span>
          </div>
          <div class="group-ratio">${escapeHtml(ratioLabel(item))}</div>
        </div>
      `).join('')
    : '<div class="empty-inline">暂无倍率数据</div>';

  const historyRows = state.siteChanges.length
    ? state.siteChanges.slice(0, 10).map((change) => `
        <div class="history-row">
          <div>
            <span class="badge ${changeClass(change)}">${escapeHtml(changeTypeLabel(change.change_type))}</span>
            <strong>${escapeHtml(change.group_name || '-')}</strong>
            <p>${escapeHtml(change.message || '-')}</p>
          </div>
          <time>${fmtTime(change.created_at)}</time>
        </div>
      `).join('')
    : '<div class="empty-inline">暂无历史变化</div>';

  els.detailPane.className = 'detail-box';
  els.detailPane.innerHTML = `
    <div class="detail-title-row">
      <div>
        <div class="detail-title">${escapeHtml(site.name)}</div>
        <div class="muted">${escapeHtml(platformLabel(site))} · ${escapeHtml(site.base_url)}</div>
      </div>
      <span class="badge ${badgeClass(site.status)}">${escapeHtml(site.status)}</span>
    </div>
    ${site.last_error ? `<div class="error-box">${escapeHtml(site.last_error)}</div>` : ''}
    <div class="detail-meta">
      <div class="meta-card"><div class="k">监控间隔</div><div class="v">${site.interval_minutes} 分钟</div></div>
      <div class="meta-card"><div class="k">公开分组</div><div class="v">${site.current_groups_count || 0}</div></div>
      <div class="meta-card"><div class="k">认证分组</div><div class="v">${site.current_login_groups_count || 0}</div></div>
      <div class="meta-card"><div class="k">上次检测</div><div class="v">${fmtTime(site.last_check_at)}</div></div>
      <div class="meta-card"><div class="k">下次检测</div><div class="v">${fmtTime(site.next_check_at)}</div></div>
      <div class="meta-card"><div class="k">连续失败</div><div class="v">${site.consecutive_failures || 0}</div></div>
      <div class="meta-card"><div class="k">启用状态</div><div class="v">${site.enabled ? '启用中' : '已停用'}</div></div>
      <div class="meta-card wide"><div class="k">监控模式</div><div class="v">${site.platform === 'sub2api' ? `sub2api ${site.auth_mode === 'token' ? `导入登录态（refresh ${site.has_refresh_token ? '已配置' : '未配置'}）` : `账号登录（${escapeHtml(site.login_username || '-')}）`}` : site.login_enabled ? `认证增强监控（系统访问令牌 / 用户ID ${escapeHtml(site.access_user_id || '-')}）` : '公开分组监控'}</div></div>
    </div>
    <section class="mode-note ${site.login_enabled ? 'enabled' : ''}">
      ${site.platform === 'sub2api'
        ? (site.auth_mode === 'token' ? '当前站点使用导入登录态检测该账号实际可见的分组倍率；适合开启 Turnstile 的上游。' : '当前站点使用 sub2api 普通用户账号登录，检测该账号实际可见的分组倍率和用户专属倍率。')
        : site.login_enabled
        ? '当前站点已开启认证增强监控，检测时会优先使用系统访问令牌采集该账号可见的隐藏用户分组或专属分组。'
        : '当前站点只监控公开 /api/user/groups。若该站存在特殊分组，可在编辑站点中开启认证增强监控。'}
    </section>
    ${site.login_last_error ? `<div class="error-box">${escapeHtml(site.login_last_error)}</div>` : ''}
    ${site.login_enabled && hiddenGroups.length ? `
      <section class="detail-section">
        <div class="section-title">认证后新增分组</div>
        <div class="info-list">
          ${hiddenGroups.map(([name, item]) => `
            <div class="info-row">
              <div><strong>${escapeHtml(name)}</strong><span>${escapeHtml(item.desc || '-')}</span></div>
              <b>${escapeHtml(ratioLabel(item))}</b>
            </div>
          `).join('')}
        </div>
      </section>
    ` : ''}
    <section class="detail-section">
      <div class="section-title">${site.platform === 'sub2api' ? '用户可见分组倍率' : site.login_enabled && Object.keys(loginGroupsObj).length ? '认证分组倍率' : '当前公开分组倍率'}</div>
      <div class="group-list">${groupRows}</div>
    </section>
    <section class="detail-section">
      <div class="section-title">该站历史变化</div>
      <div class="history-list">${historyRows}</div>
    </section>
  `;
}

function openDialog(site = null) {
  els.dialogMsg.textContent = '';
  if (site) {
    els.dialogTitle.textContent = '编辑站点';
    els.siteId.value = site.id;
    els.siteName.value = site.name;
    els.sitePlatform.value = site.platform || 'newapi';
    els.siteBaseUrl.value = site.base_url;
    els.siteInterval.value = site.interval_minutes;
    els.siteAuthMode.value = site.auth_mode || 'password';
    els.siteLoginEnabled.checked = !!site.login_enabled;
    els.siteLoginUsername.value = site.login_username || '';
    els.siteLoginPassword.value = '';
    els.siteAccessToken.value = '';
    els.siteSub2apiAccessToken.value = '';
    els.siteRefreshToken.value = '';
    els.siteTokenExpiresAt.value = site.token_expires_at || '';
    els.siteAccessUserId.value = site.access_user_id || '';
    els.siteEnabled.checked = !!site.enabled;
  } else {
    els.dialogTitle.textContent = '添加站点';
    els.siteId.value = '';
    els.siteName.value = '';
    els.sitePlatform.value = 'newapi';
    els.siteBaseUrl.value = '';
    els.siteInterval.value = 3;
    els.siteAuthMode.value = 'password';
    els.siteLoginEnabled.checked = false;
    els.siteLoginUsername.value = '';
    els.siteLoginPassword.value = '';
    els.siteAccessToken.value = '';
    els.siteSub2apiAccessToken.value = '';
    els.siteRefreshToken.value = '';
    els.siteTokenExpiresAt.value = '';
    els.siteAccessUserId.value = '';
    els.siteEnabled.checked = true;
  }
  updatePlatformFields();
  els.dialog.showModal();
}

async function loadSelectedSiteChanges() {
  if (!state.selectedSiteId) {
    state.siteChanges = [];
    return;
  }
  const resp = await api(`/api/sites/${state.selectedSiteId}/changes?limit=50`);
  state.siteChanges = resp.data || [];
}

function renderAll() {
  renderActiveView();
}

async function refreshAll(options = {}) {
  if (state.refreshInFlight) return null;
  state.refreshInFlight = true;
  try {
  const [overview, sitesResp, changesResp, notifyResp] = await Promise.all([
    api('/api/overview'),
    api('/api/sites'),
    api('/api/changes?limit=50'),
    api('/api/notifications/settings'),
  ]);
  state.sites = sitesResp.data || [];
  state.changes = changesResp.data || [];
  state.notificationSettings = notifyResp.data || {};
  if (!state.selectedSiteId && state.sites.length) {
    state.selectedSiteId = state.sites[0].id;
  }
  const shouldLoadDetail = options.loadDetail ?? state.activeView === 'detail';
  if (shouldLoadDetail) {
    await loadSelectedSiteChanges();
  }
  renderAll();
  return overview;
  } finally {
    state.refreshInFlight = false;
  }
}

function notificationPayload() {
  return {
    email_enabled: els.emailEnabled.checked,
    smtp_host: els.smtpHost.value.trim(),
    smtp_port: Math.max(1, Number(els.smtpPort.value || 465)),
    smtp_username: els.smtpUsername.value.trim(),
    smtp_password: els.smtpPassword.value,
    smtp_use_ssl: els.smtpUseSsl.checked,
    smtp_from: els.smtpFrom.value.trim(),
    smtp_to: els.smtpTo.value.trim(),
  };
}

async function checkSite(id) {
  await api(`/api/sites/${id}/check`, { method: 'POST', body: '{}' });
  await refreshAll();
}

async function deleteSite(id) {
  if (!confirm('确认删除这个站点？')) return;
  await api(`/api/sites/${id}`, { method: 'DELETE' });
  state.selectedSiteId = null;
  await refreshAll();
}

els.addSiteBtn.addEventListener('click', () => openDialog());
els.closeDialogBtn.addEventListener('click', () => els.dialog.close());
els.siteLoginEnabled.addEventListener('change', () => setLoginFieldsVisible(els.siteLoginEnabled.checked));
els.sitePlatform.addEventListener('change', updatePlatformFields);
els.siteAuthMode.addEventListener('change', updatePlatformFields);
els.refreshBtn.addEventListener('click', () => refreshAll().catch((err) => alert(err.message)));
els.searchInput.addEventListener('input', renderSites);
els.statusFilter.addEventListener('change', renderSites);
els.notifyForm.addEventListener('input', () => { state.notificationDirty = true; });
els.notifyForm.addEventListener('focusin', () => { state.notificationDirty = true; });
els.navItems.forEach((item) => {
  item.addEventListener('click', () => setActiveView(item.dataset.viewTarget));
});

async function handleSiteTableClick(e) {
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const id = Number(btn.dataset.id);
  const act = btn.dataset.act;
  const site = state.sites.find((s) => s.id === id);
  state.selectedSiteId = id;
  renderSites();
  if (act === 'view') {
    setActiveView('detail');
    renderDetail(site);
    loadSelectedSiteChanges()
      .then(() => renderDetail(state.sites.find((s) => s.id === state.selectedSiteId)))
      .catch((err) => console.error(err));
    return;
  }
  await loadSelectedSiteChanges();
  renderDetail(site);
  if (act === 'check') return checkSite(id).catch((err) => alert(err.message));
  if (act === 'edit') return openDialog(site);
  if (act === 'delete') return deleteSite(id).catch((err) => alert(err.message));
}

els.sitesBody.addEventListener('click', handleSiteTableClick);
els.overviewSitesBody.addEventListener('click', handleSiteTableClick);

els.form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const platform = els.sitePlatform.value;
  const authMode = platform === 'sub2api' ? sub2apiAuthMode() : 'password';
  const payload = {
    name: els.siteName.value.trim(),
    platform,
    base_url: els.siteBaseUrl.value.trim(),
    interval_minutes: Math.max(1, Number(els.siteInterval.value || 3)),
    login_enabled: platform === 'sub2api' ? true : els.siteLoginEnabled.checked,
    auth_mode: authMode,
    login_username: authMode === 'password' ? els.siteLoginUsername.value.trim() : '',
    login_password: authMode === 'password' ? els.siteLoginPassword.value : '',
    access_token: platform === 'sub2api' ? els.siteSub2apiAccessToken.value.trim() : els.siteAccessToken.value.trim(),
    refresh_token: authMode === 'token' ? els.siteRefreshToken.value.trim() : '',
    token_expires_at: authMode === 'token' ? els.siteTokenExpiresAt.value.trim() : '',
    access_user_id: els.siteAccessUserId.value.trim(),
    enabled: els.siteEnabled.checked,
  };
  const id = els.siteId.value;
  try {
    if (id) {
      await api(`/api/sites/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
    } else {
      await api('/api/sites', { method: 'POST', body: JSON.stringify(payload) });
    }
    els.dialog.close();
    await refreshAll();
  } catch (err) {
    els.dialogMsg.textContent = err.message;
  }
});

els.testConnBtn.addEventListener('click', async () => {
  const baseUrl = els.siteBaseUrl.value.trim().replace(/\/+$/, '');
  const platform = els.sitePlatform.value;
  if (!baseUrl) {
    els.dialogMsg.textContent = '请先填写 Base URL';
    return;
  }
  const authMode = platform === 'sub2api' ? sub2apiAuthMode() : 'password';
  if (platform === 'sub2api' && authMode === 'password' && (!els.siteLoginUsername.value.trim() || !els.siteLoginPassword.value)) {
    els.dialogMsg.textContent = '请填写 sub2api 用户邮箱和密码';
    return;
  }
  if (platform === 'sub2api' && authMode === 'token' && !els.siteSub2apiAccessToken.value.trim()) {
    els.dialogMsg.textContent = '请填写 sub2api auth_token';
    return;
  }
  els.dialogMsg.textContent = '检测中...';
  try {
    const res = await api('/api/check-connection', {
      method: 'POST',
      body: JSON.stringify({
        platform,
        base_url: baseUrl,
        auth_mode: authMode,
        login_username: els.siteLoginUsername.value.trim(),
        login_password: els.siteLoginPassword.value,
        access_token: els.siteSub2apiAccessToken.value.trim(),
        refresh_token: els.siteRefreshToken.value.trim(),
      }),
    });
    els.dialogMsg.textContent = res.success
      ? `连接成功：${res.groups_count} 个分组`
      : `失败：${res.message}`;
  } catch (err) {
    els.dialogMsg.textContent = `失败：${err.message}`;
  }
});

els.testLoginBtn.addEventListener('click', async () => {
  const baseUrl = els.siteBaseUrl.value.trim().replace(/\/+$/, '');
  const platform = els.sitePlatform.value;
  if (platform === 'sub2api') {
    const authMode = sub2apiAuthMode();
    const username = els.siteLoginUsername.value.trim();
    const password = els.siteLoginPassword.value;
    const accessToken = els.siteSub2apiAccessToken.value.trim();
    const refreshToken = els.siteRefreshToken.value.trim();
    if (authMode === 'password' && (!baseUrl || !username || !password)) {
      els.dialogMsg.textContent = '请填写 Base URL、用户邮箱和密码';
      return;
    }
    if (authMode === 'token' && (!baseUrl || !accessToken)) {
      els.dialogMsg.textContent = '请填写 Base URL 和 auth_token';
      return;
    }
    els.dialogMsg.textContent = authMode === 'token' ? 'sub2api 登录态测试中...' : 'sub2api 登录测试中...';
    try {
      const res = await api('/api/check-connection', {
        method: 'POST',
        body: JSON.stringify({
          platform,
          base_url: baseUrl,
          auth_mode: authMode,
          login_username: username,
          login_password: password,
          access_token: accessToken,
          refresh_token: refreshToken,
        }),
      });
      els.dialogMsg.textContent = res.success
        ? `${authMode === 'token' ? '登录态可用' : '登录成功'}：当前用户可见 ${res.groups_count} 个分组`
        : `${authMode === 'token' ? '登录态失败' : '登录失败'}：${res.message}`;
    } catch (err) {
      els.dialogMsg.textContent = `${authMode === 'token' ? '登录态失败' : '登录失败'}：${err.message}`;
    }
    return;
  }
  const accessToken = els.siteAccessToken.value.trim();
  const accessUserId = els.siteAccessUserId.value.trim();
  if (!baseUrl || !accessToken || !accessUserId) {
    els.dialogMsg.textContent = '请填写 Base URL、系统访问令牌和 NewAPI 用户 ID';
    return;
  }
  els.dialogMsg.textContent = '访问令牌测试中...';
  try {
    const res = await api('/api/check-login', {
      method: 'POST',
      body: JSON.stringify({
        base_url: baseUrl,
        access_token: accessToken,
        access_user_id: accessUserId,
      }),
    });
    els.dialogMsg.textContent = res.success
      ? `验证成功：认证后可见 ${res.groups_count} 个分组`
      : `验证失败：${res.message}`;
  } catch (err) {
    els.dialogMsg.textContent = `验证失败：${err.message}`;
  }
});

els.notifyForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  els.notifyStatus.textContent = '保存中...';
  els.notifyStatus.classList.remove('error');
  try {
    const res = await api('/api/notifications/settings', {
      method: 'PUT',
      body: JSON.stringify(notificationPayload()),
    });
    state.notificationSettings = res.data || {};
    state.notificationDirty = false;
    renderNotificationSettings({ force: true });
  } catch (err) {
    els.notifyStatus.textContent = `保存失败：${err.message}`;
    els.notifyStatus.classList.add('error');
  }
});

els.testEmailBtn.addEventListener('click', async () => {
  els.notifyStatus.textContent = '测试邮件发送中...';
  els.notifyStatus.classList.remove('error');
  try {
    const res = await api('/api/notifications/test-email', {
      method: 'POST',
      body: JSON.stringify(notificationPayload()),
    });
    els.notifyStatus.textContent = res.message || '测试完成';
    state.notificationDirty = false;
    await refreshAll();
  } catch (err) {
    els.notifyStatus.textContent = `测试失败：${err.message}`;
    els.notifyStatus.classList.add('error');
  }
});

refreshAll().catch((err) => {
  console.error(err);
  els.detailPane.textContent = err.message;
});

setInterval(() => {
  refreshAll().catch(() => {});
}, 15000);
