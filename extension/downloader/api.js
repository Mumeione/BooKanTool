// 博看 API 客户端：backend/api.py 的 JS 移植。
// 扩展页面持有 host_permissions，fetch 直连不受 CORS / 混合内容限制
// （图片 img1-qn 与 EPUB epub.bookan.com.cn 均为 http 明文，manifest 已声明 *://*.bookan.com.cn/*）。

const API_BASE = 'https://api.bookan.com.cn';
const EPUB_BASE = 'http://epub.bookan.com.cn';
const INSTANCE_ID_RESOURCE = 12696; // issueInfoList / getHash
const INSTANCE_ID_CATALOG = 13790; // catalogInfo（与上面不同！）
const JPAGE_DEFAULT = 8;
const HTTP_TIMEOUT = 30000;
const USER_AGENT =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

export class ApiError extends Error {
  constructor(code, msg, url = '') {
    super(`[bookan api] code=${code} ${msg} (${url})`);
    this.code = code;
    this.url = url;
  }
}

export class CancelledError extends Error {
  constructor() {
    super('任务已取消');
    this.cancelled = true;
  }
}

function toInt(v, dflt = 0) {
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : dflt;
}

async function fetchWithTimeout(url, opts = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(new Error('请求超时')), HTTP_TIMEOUT);
  // 外部 signal（用户取消）与超时 signal 级联
  const outer = opts.signal;
  if (outer) {
    if (outer.aborted) ctrl.abort(outer.reason);
    else outer.addEventListener('abort', () => ctrl.abort(outer.reason), { once: true });
  }
  try {
    return await fetch(url, { ...opts, signal: ctrl.signal });
  } finally {
    clearTimeout(t);
  }
}

async function getJson(url, params, signal) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) qs.set(k, String(v));
  const full = `${url}?${qs}`;
  let resp;
  try {
    resp = await fetchWithTimeout(full, { signal });
  } catch (e) {
    if (signal && signal.aborted) throw new CancelledError();
    throw new ApiError('network', String(e && e.message), full);
  }
  if (!resp.ok) throw new ApiError(resp.status, 'HTTP 非 200', resp.url);
  let data;
  try {
    data = await resp.json();
  } catch (e) {
    throw new ApiError('json', '无法解析 JSON', resp.url);
  }
  if (toInt(data.code, 0) !== 0) throw new ApiError(data.code, data.msg || '未知错误', resp.url);
  return data;
}

// ────────────── issueInfoList 单条解析（对齐 backend/api.py _parse_issue） ──────────────
export function parseIssue(raw, defaultResourceType) {
  return {
    resource_id: String(raw.resourceId || ''),
    issue_id: String(raw.issueId || ''),
    resource_name: String(raw.resourceName || '未知'),
    issue_name: String(raw.issueName || ''),
    resource_type: toInt(raw.resourceType ?? raw.type, defaultResourceType),
    count: toInt(raw.count, 0),
    author: String(raw.owner || raw.author || ''),
    publisher: String(raw.press || ''),
    pub_date: String(raw.publish || ''),
    isbn: String(raw.isbn || ''),
    issn: String(raw.issn || ''),
    cn: String(raw.cn || ''),
    description: String(raw.text || raw.explainRecommend || raw.explain || ''),
    jpage_node: String(raw.jpg || raw.webp || JPAGE_DEFAULT),
  };
}

export function displayTitle(issue) {
  if (issue.issue_name && !issue.resource_name.includes(issue.issue_name)) {
    return `${issue.resource_name} - ${issue.issue_name}`;
  }
  return issue.resource_name;
}

// ────────────── 客户端 ──────────────
export class BookanAPI {
  async getIssueInfo(issueId, resourceType, signal) {
    const data = await getJson(
      `${API_BASE}/resource/issueInfoList`,
      { instanceId: INSTANCE_ID_RESOURCE, resourceType, issueIds: issueId, isDetail: 1 },
      signal
    );
    const items = data.data || [];
    if (!items.length) throw new ApiError('empty', 'issueInfoList 返回为空');
    return parseIssue(items[0], resourceType);
  }

  async getIssueInfoList(issueIds, resourceType, signal) {
    const data = await getJson(
      `${API_BASE}/resource/issueInfoList`,
      {
        instanceId: INSTANCE_ID_RESOURCE,
        resourceType,
        issueIds: issueIds.map((i) => String(i).trim()).join(','),
        isDetail: 1,
      },
      signal
    );
    return (data.data || []).filter((x) => x && typeof x === 'object').map((x) => parseIssue(x, resourceType));
  }

  // 「下载全年」：以 base 为中心前后按 issueID 连续推算同刊同年各期
  // （对齐 backend/api.py collect_year_issues 的连续段语义）
  async collectYearIssues(base, signal) {
    const baseId = parseInt(base.issue_id, 10);
    if (!Number.isFinite(baseId)) return [base];

    const ym = /(\d{4})年/.exec(base.issue_name || '');
    const year = ym ? +ym[1] : null;

    const matches = (it) => {
      if (it.resource_name !== base.resource_name) return false;
      if (year) {
        const m = /(\d{4})年/.exec(it.issue_name || '');
        if (m && +m[1] !== year) return false;
      }
      return true;
    };

    const found = new Map([[baseId, base]]);
    const BATCH = 30;
    const MAX_ISSUES = 600;

    for (const direction of [-1, 1]) {
      let edge = baseId;
      while (found.size < MAX_ISSUES) {
        const ids = [];
        for (let k = 1; k <= BATCH; k++) ids.push(edge + direction * k);
        const got = await this._probeIssues(ids, base.resource_type, matches, signal);
        // 只取与已知边界连续相邻的匹配段，中间断开即视为越界
        const run = [];
        for (let k = 1; k <= BATCH; k++) {
          const it = got.get(edge + direction * k);
          if (!it) break;
          run.push(it);
        }
        if (!run.length) break;
        for (const it of run) found.set(parseInt(it.issue_id, 10), it);
        edge += direction * run.length;
        if (run.length < BATCH) break; // 本批内已到边界
      }
    }

    return [...found.keys()].sort((a, b) => a - b).map((k) => found.get(k));
  }

  async _probeIssues(ids, resourceType, matches, signal) {
    // 优先批量请求；失败（混入不存在的 ID 等）时退回逐个探测，
    // 遇到第一个不匹配/不存在的 ID 即停，保证"连续段"语义
    try {
      const items = await this.getIssueInfoList(ids, resourceType, signal);
      if (items.length) {
        const out = new Map();
        for (const it of items) {
          const key = parseInt(it.issue_id, 10);
          if (Number.isFinite(key) && matches(it)) out.set(key, it);
        }
        return out;
      }
    } catch (e) {
      if (e instanceof CancelledError) throw e;
    }
    const out = new Map();
    for (const id of ids) {
      let it;
      try {
        it = await this.getIssueInfo(String(id), resourceType, signal);
      } catch (e) {
        if (e instanceof CancelledError) throw e;
        break;
      }
      if (!matches(it)) break;
      out.set(parseInt(it.issue_id, 10), it);
    }
    return out;
  }

  // getHash：拉图片 hash 列表（start/end 均含端点，page 为物理页号）
  async getHashes(resourceId, issueId, pageCount, resourceType, signal, start = 1) {
    const data = await getJson(
      `${API_BASE}/resource/getHash`,
      { resourceType, resourceId, issueId, start, end: pageCount },
      signal
    );
    const out = [];
    for (const item of data.data || []) {
      const page = toInt(item.page ?? item.pageNum, 0);
      const hash = String(item.hash || item.encryptHash || '');
      if (page && hash) out.push({ page, hash });
    }
    if (!out.length) throw new ApiError('empty', 'getHash 返回为空');
    return out;
  }

  // EPUB 版本 hash：getHash 以 start=0 请求时 page=0 条目的 hash 即 EPUB 版本号
  async getEpubVersionHash(resourceId, issueId, resourceType, signal) {
    const data = await getJson(
      `${API_BASE}/resource/getHash`,
      { resourceType, resourceId, issueId, start: 0, end: 0 },
      signal
    );
    for (const item of data.data || []) {
      if (toInt(item.page, 0) === 0 && item.hash) return String(item.hash);
    }
    throw new ApiError('empty', 'getHash 未返回 page=0 的 EPUB 版本 hash');
  }

  buildEpubUrl(resourceId, issueId, versionHash) {
    return `${EPUB_BASE}/epub2/${resourceId}/${resourceId}-${issueId}/${issueId}_${versionHash}.epub`;
  }
}

// ────────────── catalogInfo 解析（对齐 _parse_catalog_node，递归 sublevels） ──────────────
export function parseCatalogNodes(data, level = 0) {
  const out = [];
  for (const item of data || []) {
    if (!item || typeof item !== 'object') continue;
    const node = parseCatalogNode(item, level);
    if (node) out.push(node);
  }
  return out;
}

function parseCatalogNode(item, level) {
  const title = String(item.name || item.title || '').trim();
  const start = toInt(item.page, 0);
  const end = toInt(item.endPage, 0);
  const children = parseCatalogNodes(item.sublevels || [], level + 1);
  if (!title && !children.length) return null;
  return { title: title || `第 ${start} 页`, start_page: start, end_page: end, level, children };
}

export async function getCatalog(issueId, resourceType, signal) {
  // 部分 issue 没有目录数据是可接受情况；上层捕获后跳过 outline
  try {
    const data = await getJson(
      `${API_BASE}/resource/catalogInfo`,
      { instanceId: INSTANCE_ID_CATALOG, resourceType, categoryId: issueId },
      signal
    );
    return parseCatalogNodes(data.data || [], 0);
  } catch (e) {
    if (e instanceof CancelledError) throw e;
    return [];
  }
}

export function buildImageUrl(resourceId, issueId, pageHash, jpage = JPAGE_DEFAULT, size = 'big') {
  return `http://img1-qn.bookan.com.cn/jpage${jpage}/${resourceId}/${resourceId}-${issueId}/${pageHash}_${size}.jpg`;
}
