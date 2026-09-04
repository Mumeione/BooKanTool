// BookanTool 后台 Service Worker：
//  1. ensureDownloader —— 打开/复用固定隐藏的下载任务页（任务不能跑在 SW 里，SW 会空闲自杀）
//  2. openFolder —— chrome.downloads.show 定位已下载文件（内容脚本无 downloads 权限）
//  3. 角标 —— 下载中显示百分比，结束后清除

const DOWNLOADER_URL = chrome.runtime.getURL('downloader/downloader.html');

async function ensureDownloaderTab() {
  const tabs = await chrome.tabs.query({ url: DOWNLOADER_URL });
  if (tabs.length) return tabs[0];
  return chrome.tabs.create({ url: DOWNLOADER_URL, active: false, pinned: true });
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === 'bt:ensureDownloader') {
    ensureDownloaderTab()
      .then((t) => sendResponse({ ok: true, tabId: t.id }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true; // 异步响应
  }
  if (msg && msg.type === 'bt:openFolder') {
    if (msg.downloadId) chrome.downloads.show(msg.downloadId);
    else chrome.downloads.showDefaultFolder();
    sendResponse({ ok: true });
    return false;
  }
  return false;
});

// 角标随任务状态更新（downloader 页把状态写进 chrome.storage）
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local' || !changes.bt_jobState) return;
  const st = changes.bt_jobState.newValue || {};
  if (st.active) {
    const pct = Math.round((st.overall || 0) * 100);
    chrome.action.setBadgeText({ text: pct > 0 ? String(pct) : '…' });
    chrome.action.setBadgeBackgroundColor({ color: '#4f8cff' });
  } else {
    chrome.action.setBadgeText({ text: '' });
  }
});
