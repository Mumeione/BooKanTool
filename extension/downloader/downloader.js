// BookanTool 下载任务页 —— 整个扩展的"后台引擎"。
// 为什么不用 Service Worker：MV3 SW 约 30 秒无事件即被终止，PDF 合成是纯 CPU 阶段
// 没有网络事件，跑到一半会被杀。本页面由 background 以固定标签页打开，生命周期可控。
//
// 职责：
//   · 消费 bt_pendingTasks（内容脚本写入）→ 串行队列执行
//   · PDF / EPUB 两条流水线（api.js / image.js / pdf.js / epub.js）
//   · 任务状态 bt_jobState + 日志 bt_logs 同步到 chrome.storage，
//     悬浮方块（所有 bookan 页面的 content script）与 popup 由此渲染
//   · 「下载全年」在此展开为多个任务

import { ApiError, CancelledError, BookanAPI, getCatalog, displayTitle } from './api.js';
import { derivePageOffset, toTree, countValid } from './catalog.js';
import { runImages } from './image.js';
import { runEpub } from './epub.js';
import { buildPdf, sanitizeFilename } from './pdf.js';

const api = new BookanAPI();

// ────────────── 状态与存储 ──────────────
const state = {
  tasks: [], // {id,type,issueId,format,compress,compressLevel,downloadAll,title,status,progress,message,fileName,downloadId,error,prefetchedIssue}
  active: false,
  overall: 0,
  label: '',
  okCount: 0,
  failCount: 0,
  doneAt: 0,
};
let logs = []; // {t, m, lv}  环形缓冲 200 条
let dirty = false;

const K = {
  PENDING: 'bt_pendingTasks',
  STATE: 'bt_jobState',
  LOGS: 'bt_logs',
  SETTINGS: 'bt_settings',
};

// 进度广播必须事件驱动（直接调 flush），不能只依赖 setInterval：
// 下载页是隐藏标签页，静置 5 分钟后 Chrome 把定时器强节流到 1 次/分钟，
// 依赖 interval 会导致悬浮方块整个下载期间停留在上一批完成态（进度失联）
let lastFlushAt = 0;
function scheduleSave() {
  dirty = true;
  if (Date.now() - lastFlushAt >= 500) flush(); // 500ms 节流的立即广播
}
setInterval(flush, 300); // 兜底（页面可见时 1Hz；隐藏久后被节流也仅作兜底）
async function flush() {
  lastFlushAt = Date.now();
  if (!dirty) return;
  dirty = false;
  await chrome.storage.local.set({ [K.STATE]: JSON.parse(JSON.stringify(state)) });
  await chrome.storage.local.set({ [K.LOGS]: logs.slice(-200) });
}

function log(msg, lv = 'info') {
  logs.push({ t: Date.now(), m: msg, lv });
  if (logs.length > 200) logs = logs.slice(-200);
  scheduleSave();
}

// ────────────── 队列消费 ──────────────
let running = false;
let cancelRequested = false;
let currentController = null;

async function consumePending() {
  const got = await chrome.storage.local.get(K.PENDING);
  const pending = got[K.PENDING];
  if (!pending || !pending.length) return;

  await chrome.storage.local.set({ [K.PENDING]: [] }); // 先清空，防重复消费
  // 新一批开始时清掉上一批的完结记录，避免“完成”后记录残留、新旧任务混展
  if (!running) {
    state.tasks = state.tasks.filter((t) => t.status === 'queued' || t.status === 'running');
  }
  const seen = new Set(
    state.tasks.filter((t) => t.status === 'queued' || t.status === 'running').map((t) => t.key)
  );
  for (const spec of pending) {
    spec.key = `${spec.type}:${spec.issueId}:${spec.format}:${spec.downloadAll ? 1 : 0}`;
    if (seen.has(spec.key)) continue; // 同书同选项已在队列，跳过
    seen.add(spec.key);
    state.tasks.push({
      ...spec,
      title: '获取资源信息…',
      status: 'queued',
      progress: 0,
      message: '排队中',
      fileName: '',
      downloadId: 0,
      error: '',
    });
  }
  log(`收到 ${pending.length} 条下载请求`);
  state.doneAt = 0;
  state.overall = 0;
  await flush(); // 立即广播，悬浮方块马上从完成态切回进行中
  runQueue();
}

async function runQueue() {
  if (running) return;
  running = true;
  cancelRequested = false;
  state.active = true;
  state.doneAt = 0;
  await flush();

  try {
    while (!cancelRequested) {
      const task = state.tasks.find((t) => t.status === 'queued');
      if (!task) break;

      // 「下载全年」在此展开为多个任务
      if (task.downloadAll) {
        currentController = new AbortController(); // 展开阶段尚未进入 runOne，需自行建 controller（且支持取消）
        let children = null;
        try {
          children = await expandYearTask(task);
        } catch (e) {
          if (e instanceof CancelledError) {
            // 用户在收集阶段取消：标记当前任务并退出（cancelRequested 已由 bt:cancel 置位）
            task.status = 'cancelled';
            task.message = '已取消';
            task.error = '用户取消';
            for (const t of state.tasks) if (t.status === 'queued') t.status = 'cancelled';
            log('[任务被取消]');
            break;
          }
          throw e;
        }
        if (children) {
          const idx = state.tasks.indexOf(task);
          state.tasks.splice(idx, 1, ...children);
          log(`全年共 ${children.length} 期，已展开为 ${children.length} 个任务`);
          scheduleSave();
          continue;
        }
        task.downloadAll = false; // 收集失败 → 退化为单本
      }

      await runOne(task);
      if (cancelRequested) {
        for (const t of state.tasks) if (t.status === 'queued') t.status = 'cancelled';
        log('[任务被取消]');
      }
    }
  } finally {
    state.active = false;
    state.okCount = state.tasks.filter((t) => t.status === 'succeeded').length;
    state.failCount = state.tasks.filter((t) => t.status === 'failed' || t.status === 'cancelled').length;
    if (!cancelRequested) log(`[全部完成] 成功 ${state.okCount}，失败/取消 ${state.failCount}`);
    state.doneAt = Date.now();
    state.overall = 1;
    scheduleSave();
    await flush();
    // 整批结束 → 所有 bookan 页面的悬浮方块展示完成态（√/×）
    await chrome.storage.local.set({ bt_widgetDismissed: false });
    running = false;
    cancelRequested = false;
  }
}

async function expandYearTask(task) {
  try {
    task.status = 'running';
    task.message = '收集全年各期…';
    scheduleSave();
    const base =
      task.prefetchedIssue ||
      (await api.getIssueInfo(task.issueId, task.type, currentController.signal));
    const list = await api.collectYearIssues(base, currentController.signal);
    if (list.length <= 1) {
      task.prefetchedIssue = base; // 复用已拉取的信息
      return null;
    }
    return list.map((it, i) => ({
      id: `${task.id}#${i}`,
      key: `${task.type}:${it.issue_id}:${task.format}:0`,
      type: task.type,
      issueId: it.issue_id,
      format: task.format,
      compress: task.compress,
      compressLevel: task.compressLevel,
      downloadAll: false,
      prefetchedIssue: i === 0 ? it : null,
      title: displayTitle(it),
      status: 'queued',
      progress: 0,
      message: '排队中',
      fileName: '',
      downloadId: 0,
      error: '',
    }));
  } catch (e) {
    if (e instanceof CancelledError) throw e;
    log(`全年收集失败（${e.message}），仅下载当前期`);
    return null;
  }
}

// ────────────── 单任务流水线 ──────────────
async function runOne(task) {
  task.status = 'running';
  task.progress = 0;
  task.message = '准备中…';
  scheduleSave();

  currentController = new AbortController();
  const signal = currentController.signal;
  const report = (frac, label) => {
    task.progress = Math.max(0, Math.min(1, frac));
    task.message = label;
    state.label = label;
    recomputeOverall();
    scheduleSave();
  };
  const tlog = (msg, lv) => log(msg, lv);

  try {
    // 1. 资源信息
    report(0.02, '获取资源信息…');
    log(`[${task.issueId}] 步骤 1: 拉取资源信息…`);
    const issue = task.prefetchedIssue || (await api.getIssueInfo(task.issueId, task.type, signal));
    task.prefetchedIssue = null;
    task.title = displayTitle(issue);
    log(
      `  → ${task.title}，共 ${issue.count} 页 / 主办 ${issue.publisher || '未知'} / 出版 ${issue.pub_date || '未知'}`
    );
    if (!issue.count) log('  警告：接口未返回页数，仍尝试拉取');
    scheduleSave();

    // 2. 格式分流（auto = 优先 EPUB，失败回退 PDF）
    let format = task.format;
    const tryFallback = format === 'auto';
    if (tryFallback) format = 'epub';

    try {
      if (format === 'epub') {
        await runEpubTask({ task, issue, report, tlog, signal });
      } else {
        await runPdfTask({ task, issue, report, tlog, signal });
      }
    } catch (e) {
      if (e instanceof CancelledError || signal.aborted) throw e;
      if (tryFallback) {
        log(`  EPUB 获取失败（${e.message}），自动回退 PDF…`);
        await runPdfTask({ task, issue, report, tlog, signal });
      } else {
        throw e;
      }
    }

    task.status = 'succeeded';
    task.progress = 1;
    task.message = '完成';
    report(1, '完成');
  } catch (e) {
    if (e instanceof CancelledError || signal.aborted) {
      task.status = 'cancelled';
      task.message = '已取消';
      task.error = '用户取消';
    } else {
      task.status = 'failed';
      task.message = '失败';
      task.error = e instanceof ApiError ? e.message : String((e && e.message) || e);
      log(`[${task.issueId}] 失败: ${task.error}`, 'error');
    }
  } finally {
    currentController = null;
    scheduleSave();
  }
}

// ── EPUB 任务 ──
async function runEpubTask({ task, issue, report, tlog, signal }) {
  const res = await runEpub({
    api,
    issue,
    log: tlog,
    onPhase: report,
    signal,
  });
  task.downloadId = res.downloadId;
  task.fileName = 'bookantool/ 内的 .epub 文件';
}

// ── PDF 任务 ──
async function runPdfTask({ task, issue, report, tlog, signal }) {
  const settings = (await chrome.storage.local.get(K.SETTINGS))[K.SETTINGS] || {};
  const addBookmarks = settings.addBookmarks !== false; // 默认开

  // 1. 图片下载（2% ~ 87%）
  const { pages, pageNumbers } = await runImages({
    api,
    issue,
    log: tlog,
    onProgress: (done, total, page) => {
      const frac = 0.02 + 0.85 * (total ? done / total : 0.02);
      report(frac, `下载图片 ${done}/${total}（第 ${page} 页）`);
    },
    onPhase: (frac, label) => report(0.87 + 0.03 * frac, label), // 压缩 87% ~ 90%
    signal,
    compress: !!task.compress,
    compressLevel: task.compressLevel || 1,
  });

  // 2. 目录 + 页码偏移（91%）
  let chapters = [];
  let offset = 0;
  if (addBookmarks) {
    report(0.91, '获取目录结构…');
    try {
      const raw = await getCatalog(issue.issue_id, issue.resource_type, signal);
      offset = derivePageOffset(raw, pageNumbers);
      chapters = toTree(raw, Math.max(...pageNumbers, 0));
      if (chapters.length) {
        const nested = chapters.reduce((n, c) => n + (c.children || []).length, 0);
        tlog(`  → 目录 ${chapters.length} 项（含 ${nested} 个子条目），页码偏移 +${offset}`);
      } else {
        tlog('  → 该资源没有可用目录，跳过书签');
      }
    } catch (e) {
      if (e instanceof CancelledError) throw e;
      tlog(`  目录获取失败（跳过书签）：${e.message}`);
    }
  }

  // 3. 合成（92% ~ 97%）
  report(0.92, '开始合成 PDF…');
  const { blob, usedBookmarks } = await buildPdf({
    pages,
    issue,
    chapters,
    pageOffset: offset,
    onPhase: (frac, label) => report(0.92 + 0.05 * frac, label),
  });
  if (usedBookmarks) tlog(`  outline 写入完成（页码偏移 +${offset}）`);

  // 4. 保存（98% ~ 100%）：走 chrome.downloads 落到 下载目录/bookantool/
  report(0.98, '保存 PDF 文件…');
  const fname = pdfFilename(issue);
  const url = URL.createObjectURL(blob);
  try {
    const downloadId = await new Promise((resolve, reject) => {
      chrome.downloads.download({ url, filename: `bookantool/${fname}`, conflictAction: 'uniquify' }, (id) => {
        const err = chrome.runtime.lastError;
        if (err || !id) reject(new Error(err ? err.message : '无法发起下载'));
        else resolve(id);
      });
    });
    await waitDownloadDone(downloadId, signal);
    task.downloadId = downloadId;
    task.fileName = fname;
    tlog(`PDF 已保存：下载目录/${fname}`);
  } finally {
    URL.revokeObjectURL(url);
  }
}

function pdfFilename(issue) {
  let sub;
  if (issue.resource_type === 3) {
    sub = issue.author || issue.publisher || issue.issue_id;
  } else {
    sub = issue.issue_name || issue.issue_id;
  }
  return `${sanitizeFilename(issue.resource_name)}_${sanitizeFilename(sub)}.pdf`;
}

function waitDownloadDone(downloadId, signal) {
  return new Promise((resolve, reject) => {
    const listener = (delta) => {
      if (delta.id !== downloadId || !delta.state) return;
      if (delta.state.current === 'complete') {
        cleanup();
        resolve();
      } else if (delta.state.current === 'interrupted') {
        cleanup();
        reject(new Error(`下载被中断（${(delta.error && delta.error.current) || '未知原因'}）`));
      }
    };
    const onAbort = () => {
      cleanup();
      try {
        chrome.downloads.cancel(downloadId, () => void chrome.runtime.lastError);
      } catch {
        /* ignore */
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

function recomputeOverall() {
  const list = state.tasks;
  if (!list.length) {
    state.overall = 0;
    return;
  }
  let sum = 0;
  for (const t of list) {
    if (t.status === 'succeeded' || t.status === 'failed' || t.status === 'cancelled') sum += 1;
    else if (t.status === 'running') sum += Math.max(0, Math.min(1, t.progress || 0));
  }
  state.overall = sum / list.length;
}

// ────────────── 消息与启动 ──────────────
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === 'bt:cancel') {
    cancelRequested = true;
    if (currentController) currentController.abort(new CancelledError());
  }
  return false;
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes[K.PENDING]) consumePending();
});

consumePending(); // 页面刚打开时消费 background 创建标签页前已入队的任务
log('BookanTool 下载引擎就绪');
