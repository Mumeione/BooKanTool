// BookanTool 弹窗：下载目录说明 / 章节书签开关 / 使用说明 / 关于 / 运行日志

const K = { STATE: 'bt_jobState', LOGS: 'bt_logs', SETTINGS: 'bt_settings' };

// ── 版本 ──
document.getElementById('ver').textContent = `v${chrome.runtime.getManifest().version}`;

// ── 折叠面板 ──
document.querySelectorAll('.head[data-toggle]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const sec = btn.closest('.item');
    const wasOpen = sec.classList.contains('open');
    document.querySelectorAll('.item.open').forEach((s) => s.classList.remove('open'));
    if (!wasOpen) sec.classList.add('open');
  });
});

// ── 下载目录 ──
document.getElementById('btn-open-dir').addEventListener('click', () => {
  chrome.downloads.showDefaultFolder();
  window.close();
});

// ── 章节书签开关 ──
const bkCheckbox = document.getElementById('bookmarks');
chrome.storage.local.get(K.SETTINGS).then((o) => {
  const st = o[K.SETTINGS] || {};
  bkCheckbox.checked = st.addBookmarks !== false; // 默认开
});
bkCheckbox.addEventListener('change', async () => {
  const o = await chrome.storage.local.get(K.SETTINGS);
  const st = o[K.SETTINGS] || {};
  st.addBookmarks = bkCheckbox.checked;
  await chrome.storage.local.set({ [K.SETTINGS]: st });
});

// ── 运行日志 ──
const logbox = document.getElementById('logbox');
const esc = (s) =>
  String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

async function renderLogs() {
  const o = await chrome.storage.local.get(K.LOGS);
  const logs = o[K.LOGS] || [];
  if (!logs.length) {
    logbox.innerHTML = '<span class="empty">暂无日志。开始一次下载后，这里会记录执行过程。</span>';
    return;
  }
  const time = (t) => {
    const d = new Date(t);
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  };
  logbox.innerHTML = logs
    .slice(-120)
    .map(
      (l) =>
        `<div class="${l.lv === 'error' ? 'err' : ''}">[${time(l.t)}] ${esc(l.m)}</div>`
    )
    .join('');
  logbox.scrollTop = logbox.scrollHeight;
}
document.getElementById('btn-clear-log').addEventListener('click', async () => {
  await chrome.storage.local.set({ [K.LOGS]: [] });
  renderLogs();
});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes[K.LOGS]) renderLogs();
});
renderLogs();
