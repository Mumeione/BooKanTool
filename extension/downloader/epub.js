// EPUB 直下：backend/epub_pipeline.py 的 JS 移植。
// getHash(start=0) 取版本号 → chrome.downloads 整本 .epub 直下。
// 走 chrome.downloads 通道 = 纯流式，一个字节都不进 JS 内存。

import { CancelledError } from './api.js';
import { sanitizeFilename } from './pdf.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function epubFilename(issue) {
  const name = sanitizeFilename(issue.resource_name) || 'book';
  const author = sanitizeFilename(issue.author || issue.publisher) || 'Unknown';
  let base = `${name}-${author}`;
  if (issue.issue_name) base += `-${sanitizeFilename(issue.issue_name)}`;
  return `${base}.epub`;
}

// 等待某个 downloadId 到达终态；onDelta 每次进度事件回调
function waitDownload(downloadId, signal, onDelta) {
  return new Promise((resolve, reject) => {
    const listener = (delta) => {
      if (delta.id !== downloadId) return;
      if (onDelta) {
        try {
          onDelta(delta);
        } catch {
          /* 进度回调异常不中断下载 */
        }
      }
      if (delta.state) {
        const s = delta.state.current;
        if (s === 'complete') {
          cleanup();
          resolve();
        } else if (s === 'interrupted') {
          cleanup();
          reject(new Error(`下载被中断（${(delta.error && delta.error.current) || '未知原因'}）`));
        }
      }
    };
    const onAbort = () => {
      cleanup();
      try {
        chrome.downloads.cancel(downloadId, () => void chrome.runtime.lastError);
      } catch {
        /* 已结束则忽略 */
      }
      reject(new CancelledError());
    };
    const cleanup = () => {
      chrome.downloads.onChanged.removeListener(listener);
      signal.removeEventListener('abort', onAbort);
    };
    chrome.downloads.onChanged.addListener(listener);
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

// 主入口：下载官方 EPUB 成品，返回 { downloadId }
export async function runEpub({ api, issue, log, onPhase, signal }) {
  log('步骤 1/2: 获取 EPUB 版本 hash（getHash start=0）…');
  const versionHash = await api.getEpubVersionHash(issue.resource_id, issue.issue_id, issue.resource_type, signal);
  log(`  → 版本 hash: ${versionHash}`);

  const url = api.buildEpubUrl(issue.resource_id, issue.issue_id, versionHash);
  const fname = `bookantool/${epubFilename(issue)}`;
  log('步骤 2/2: 直接下载官方 EPUB…');
  log(`  → ${fname}`);

  const downloadId = await new Promise((resolve, reject) => {
    chrome.downloads.download({ url, filename: fname, conflictAction: 'uniquify' }, (id) => {
      const err = chrome.runtime.lastError;
      if (err || !id) reject(new Error(err ? err.message : '无法发起下载'));
      else resolve(id);
    });
  });

  await waitDownload(downloadId, signal, (delta) => {
    // 进度：bytesReceived / totalBytes → 0.05..0.95
    if (!delta.bytesReceived && !delta.totalBytes) return;
    chrome.downloads.search({ id: downloadId }, (items) => {
      const it = items && items[0];
      if (!it) return;
      if (it.totalBytes > 0 && onPhase) {
        const frac = 0.05 + 0.9 * Math.min(1, it.bytesReceived / it.totalBytes);
        onPhase(frac, `下载 EPUB ${(it.bytesReceived / 1048576).toFixed(1)}/${(it.totalBytes / 1048576).toFixed(1)} MB`);
      } else if (onPhase) {
        onPhase(0.5, `已下载 ${(it.bytesReceived / 1048576).toFixed(1)} MB`);
      }
    });
  });

  if (onPhase) onPhase(1, '完成');
  return { downloadId };
}
