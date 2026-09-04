// 图片流水线：backend/image_pipeline.py 的 JS 移植。
// 内存纪律（浏览器无临时目录，用 Blob 托管模拟落盘）：
//   · 下载阶段全程 Blob（浏览器后端托管，不占 JS 堆）
//   · 压缩阶段 worker 池并发（≤4），位图 close() 后立即释放（解码位图 ≈ 6MB/页）
//   · 并发 4（同 PC 版 IMAGE_DOWNLOAD_THREADS），重试 3 次，退避 0.5s*attempt

import { ApiError, CancelledError, buildImageUrl } from './api.js';

export const IMAGE_CONCURRENCY = 4;
export const IMAGE_MAX_RETRIES = 3;
export const JPAGE_PROBE_RANGE = Array.from({ length: 10 }, (_, i) => i + 1); // 1..10
const JPAGE_DEFAULT_NUM = 8;

// 压缩三档（对齐 backend/config.py COMPRESSION_LEVELS）
export const COMPRESSION_LEVELS = {
  1: { label: '轻度', quality: 0.85, max_width: 1600 },
  2: { label: '中度', quality: 0.75, max_width: 1280 },
  3: { label: '高度', quality: 0.6, max_width: 1000 },
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// jpage 节点确定：接口已给出 jpg/webp 字段，仅在缺失或校验失败时才探测
async function resolveJpage(issue, sampleHash, log, signal) {
  const check = async (node) => {
    const url = buildImageUrl(issue.resource_id, issue.issue_id, sampleHash, node, 'big');
    try {
      const resp = await fetch(url, { method: 'HEAD', signal });
      return resp.ok;
    } catch {
      return false;
    }
  };

  const declared = String(issue.jpage_node || '').trim();
  if (declared) {
    if (await check(declared)) {
      log(`使用接口指定的 jpage 节点：jpage${declared}`);
      const n = parseInt(declared, 10);
      return Number.isFinite(n) ? n : JPAGE_DEFAULT_NUM;
    }
    log(`接口指定 jpage${declared} 校验失败，改为轮询探测…`);
  } else {
    log('接口未返回 jpage 节点，开始轮询探测…');
  }
  for (const n of JPAGE_PROBE_RANGE) {
    if (signal.aborted) throw new CancelledError();
    if (await check(String(n))) {
      log(`探测到可用节点：jpage${n}`);
      return n;
    }
  }
  log('未探测到可用节点，回退默认 jpage8');
  return JPAGE_DEFAULT_NUM;
}

// 主入口：返回 [{ page, blob }]（按物理页升序）与 pageNumbers
export async function runImages({ api, issue, log, onProgress, onPhase, signal, compress, compressLevel }) {
  log(`拉取图片 hash 列表，共 ${issue.count} 页…`);
  let hashes = await api.getHashes(issue.resource_id, issue.issue_id, issue.count, issue.resource_type, signal);
  hashes.sort((a, b) => a.page - b.page);
  const total = hashes.length;

  if (issue.count && total < issue.count) {
    log(`  注意：接口声明 ${issue.count} 页，实际返回 ${total} 条 hash，以实际为准`);
  }

  const jpage = await resolveJpage(issue, hashes[0].hash, log, signal);

  log(`开始并发下载（并发=${IMAGE_CONCURRENCY}）…`);
  const results = new Map(); // page -> Blob（Map 插入序 ≠ 页序，最后必须排序）
  let done = 0;
  let failed = 0;
  if (onProgress) onProgress(0, total, 0);

  const queue = [...hashes];
  const worker = async () => {
    while (queue.length) {
      if (signal.aborted) throw new CancelledError();
      const h = queue.shift();
      const url = buildImageUrl(issue.resource_id, issue.issue_id, h.hash, jpage, 'big');
      let blob = null;
      for (let attempt = 1; attempt <= IMAGE_MAX_RETRIES; attempt++) {
        if (signal.aborted) throw new CancelledError();
        try {
          const resp = await fetch(url, { signal });
          if (!resp.ok) throw new ApiError(resp.status, '下载失败', url);
          blob = await resp.blob();
          if (!blob.size) throw new ApiError('empty', '空响应', url);
          break;
        } catch (e) {
          if (signal.aborted) throw new CancelledError();
          blob = null;
          if (attempt >= IMAGE_MAX_RETRIES) break;
          await sleep(500 * attempt);
        }
      }
      done++;
      if (blob) results.set(h.page, blob);
      else failed++;
      if (onProgress) onProgress(done, total, h.page);
    }
  };
  await Promise.all(Array.from({ length: IMAGE_CONCURRENCY }, worker));

  if (signal.aborted) throw new CancelledError();
  log(`下载完成: 成功 ${results.size}/${total}，失败 ${failed}`);
  if (results.size === 0) throw new ApiError('download', '所有页面下载均失败');

  // 页序整理：按物理页升序
  const pages = [...results.entries()].map(([page, blob]) => ({ page, blob }));
  pages.sort((a, b) => a.page - b.page);

  // 可选压缩：worker 池并发（对齐 backend ThreadPoolExecutor(min(4, cpu, 页数))），
  // 各 worker 独立 OffscreenCanvas，位图即用即关；结果写回 pages[i] 故页序不变
  if (compress) {
    const cfg = COMPRESSION_LEVELS[compressLevel] || COMPRESSION_LEVELS[1];
    let before = 0;
    let after = 0;
    let doneC = 0;

    const compressOne = async (p) => {
      const bmp = await createImageBitmap(p.blob);
      const scale = bmp.width > cfg.max_width ? cfg.max_width / bmp.width : 1;
      const cw = Math.max(1, Math.round(bmp.width * scale));
      const ch = Math.max(1, Math.round(bmp.height * scale));
      const cv = new OffscreenCanvas(cw, ch);
      const ctx = cv.getContext('2d');
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(bmp, 0, 0, cw, ch);
      bmp.close(); // 立即释放解码位图
      const nb = await cv.convertToBlob({ type: 'image/jpeg', quality: cfg.quality });
      return nb;
    };

    const COMPRESS_CONCURRENCY = Math.max(1, Math.min(4, navigator.hardwareConcurrency || 4, pages.length));
    let cursor = 0;
    const worker = async () => {
      while (cursor < pages.length) {
        if (signal.aborted) throw new CancelledError();
        const p = pages[cursor++];
        try {
          const nb = await compressOne(p);
          before += p.blob.size;
          after += nb.size;
          p.blob = nb; // 换成压缩后的 Blob，原图失去引用被 GC
        } catch (e) {
          if (signal.aborted) throw new CancelledError();
          log(`  压缩失败，保留原图: page_${p.page} (${e && e.message})`);
        }
        doneC++;
        if (onPhase) onPhase(doneC / pages.length, `压缩图片 ${doneC}/${pages.length}`);
      }
    };
    log(`开始并发压缩（并发=${COMPRESS_CONCURRENCY}）…`);
    await Promise.all(Array.from({ length: COMPRESS_CONCURRENCY }, worker));

    if (before) {
      log(
        `压缩完成: ${(before / 1048576).toFixed(1)}MB → ${(after / 1048576).toFixed(1)}MB（${((after / before) * 100).toFixed(0)}%）`
      );
    }
  }

  return { pages, pageNumbers: pages.map((p) => p.page) };
}
