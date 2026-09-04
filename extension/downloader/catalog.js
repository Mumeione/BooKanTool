// 章节目录换算：backend/catalog.py 的 JS 移植。
// 把 catalogInfo 的两级目录（栏目 → 文章）规整为 PDF outline / 校准印刷页码。

// flatten：深度优先展开（保留层级信息），仅 page>=1 的节点进入列表
function flattenValid(node, out = []) {
  if (node.start_page >= 1) out.push(node);
  for (const c of node.children) flattenValid(c, out);
  return out;
}

function flattenAll(node, out = []) {
  out.push(node);
  for (const c of node.children) flattenAll(c, out);
  return out;
}

// normalize：展开 → 剔除越界/伪条目 → 升序 → 同页去重（保留层级最浅、先出现者）
export function normalize(chapters, totalPages) {
  const flat = [];
  for (const ch of chapters) flat.push(...flattenValid(ch));
  const out = [];
  const seen = new Set();
  flat.sort((a, b) => a.start_page - b.start_page || a.level - b.level);
  for (const ch of flat) {
    if (ch.start_page < 1 || ch.start_page > totalPages) continue;
    if (seen.has(ch.start_page)) continue;
    seen.add(ch.start_page);
    out.push(ch);
  }
  return out;
}

// to_tree：保留两级层级（父合法→父+子；父非法→子提升），同页子项放行
export function toTree(chapters, totalPages) {
  const out = [];
  const seen = new Set();

  const take = (node, samePageAsParent = false) => {
    if (node.start_page < 1 || node.start_page > totalPages) return null;
    if (seen.has(node.start_page) && !samePageAsParent) return null;
    seen.add(node.start_page);
    return node;
  };

  const sorted = [...chapters].sort(
    (a, b) => Math.max(a.start_page, 0) - Math.max(b.start_page, 0) || a.level - b.level
  );

  for (const parent of sorted) {
    const p = take(parent);
    const kids = [];
    for (const child of parent.children) {
      const c = take(child, p !== null && child.start_page === parent.start_page);
      if (c) kids.push(c);
    }

    if (p) {
      const pClone = { title: p.title, start_page: p.start_page, end_page: p.end_page, level: 0, children: [] };
      // 子项页码不能小于父项（同页可以：栏目页即首篇首页）
      pClone.children = kids.filter((k) => k.start_page >= pClone.start_page);
      out.push(pClone);
    } else if (kids.length) {
      // 父无效（如"目录" page=0）→ 子项提升为一级
      out.push(...kids);
    }
  }

  out.sort((a, b) => a.start_page - b.start_page);
  return out;
}

// derive_page_offset：印刷页码 → 物理页号偏移（对齐 backend/catalog.py）
// 候选策略：1) 封底锚点 max(物理)-max(印刷)  2) 前导伪条目数  3) 0
export function derivePageOffset(chapters, physicalPages) {
  if (!physicalPages.length) return 0;
  const physMax = Math.max(...physicalPages);

  const valid = [];
  for (const node of chapters) valid.push(...flattenValid(node));
  const printed = [...new Set(valid.map((c) => c.start_page))].sort((a, b) => a - b);
  if (!printed.length) return 0;
  const pMin = printed[0];
  const pMax = printed[printed.length - 1];

  const feasible = (off) => {
    // 全部条目映射后必须落在物理页范围内
    if (pMin + off < 1 || pMax + off > physMax) return false;
    // 首篇不能落在封面（物理页 1 恒为封面）
    return !(pMin === 1 && pMin + off < 2);
  };

  const candidates = [];
  const anchor = physMax - pMax;
  if (anchor > 0) candidates.push(anchor);
  let lead = 0;
  for (const node of chapters) {
    if (node.start_page < 1) lead++;
    else break;
  }
  if (lead > 0) candidates.push(lead);
  candidates.push(0);

  for (const off of candidates) if (feasible(off)) return off;
  return 0;
}

// countValid：统计条目总数（含子级；与 Python 版实现一致——全量 flatten，
// 含 page<=0 的伪条目，仅用于日志展示）
export function countValid(chapters) {
  let n = 0;
  for (const ch of chapters) n += flattenAll(ch).length;
  return n;
}

// bisect_left 的等价实现：sorted 中第一个 >= target 的下标；越界返回 -1
export function locatePage(sortedPages, physical) {
  let lo = 0;
  let hi = sortedPages.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (sortedPages[mid] < physical) lo = mid + 1;
    else hi = mid;
  }
  return lo < sortedPages.length ? lo : -1;
}
