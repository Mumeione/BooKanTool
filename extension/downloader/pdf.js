// 图片版 PDF 生成器（纯 JS，无第三方库）。
// 对应 backend/pdf_pipeline.py 的 img2pdf + pypdf 两步：
//   · JPEG 直嵌（/Filter /DCTDecode，不重压缩，画质零损失）
//   · 两级 outline（栏目 → 文章），/Title 用 UTF-16BE + BOM 六角串（中文安全）
//   · 页码定位对齐 _add_outline：印刷页码 + offset → 物理页号 → 在升序物理页列表中
//     bisect 精确定位，个别页失败时落到最近的后续页，书签永不指错内容
//   · 元数据写入 Info 字典（Title/Author/Subject/Keywords）

// ────────────── JPEG 尺寸探测（SOF 段扫描，免解码） ──────────────
export function jpegSize(u8) {
  let i = 2; // 跳过 FFD8
  while (i + 9 < u8.length) {
    if (u8[i] !== 0xff) {
      i++;
      continue;
    }
    const m = u8[i + 1];
    if (m === 0x01 || (m >= 0xd0 && m <= 0xd7) || m === 0xd8) {
      i += 2;
      continue;
    }
    if (m === 0xd9 || m === 0xda) break; // EOI / SOS
    const len = (u8[i + 2] << 8) | u8[i + 3];
    // SOF0~SOF15（剔除 DHT/JPG/DAC）
    if (m >= 0xc0 && m <= 0xcf && m !== 0xc4 && m !== 0xc8 && m !== 0xcc) {
      const h = (u8[i + 5] << 8) | u8[i + 6];
      const w = (u8[i + 7] << 8) | u8[i + 8];
      return { w, h };
    }
    i += 2 + len;
  }
  return null;
}

// ────────────── 字符串编码 ──────────────
// PDF 文本串：UTF-16BE + BOM 的六角串（中文标题安全）
export function pdfHex(text) {
  const out = ['feff'];
  for (const ch of String(text ?? '')) {
    const cp = ch.codePointAt(0);
    if (cp > 0xffff) {
      // 代理对
      const hi = 0xd800 + ((cp - 0x10000) >> 10);
      const lo = 0xdc00 + ((cp - 0x10000) & 0x3ff);
      out.push(hi.toString(16).padStart(4, '0'), lo.toString(16).padStart(4, '0'));
    } else {
      out.push(cp.toString(16).padStart(4, '0'));
    }
  }
  return `<${out.join('')}>`;
}

// latin1 字节（PDF 对象字典只用 ASCII，安全）
function latin1Bytes(s) {
  const u = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i) & 0xff;
  return u;
}

// ────────────── 写入器 ──────────────
class PdfWriter {
  constructor() {
    this.count = 0;
    this.objs = []; // objs[n-1] = parts 数组（string | Uint8Array）
    this.needed = new Set(); // 已定义的对象号
  }

  newRef() {
    return ++this.count;
  }

  setObj(n, parts) {
    this.objs[n - 1] = parts;
    this.needed.add(n);
  }

  // 序列化为 parts 数组 + 总长（调用方直接 new Blob(parts)，避免整块拷贝）
  serialize() {
    const parts = [];
    let len = 0;
    const push = (p) => {
      const u = typeof p === 'string' ? latin1Bytes(p) : p;
      parts.push(u);
      len += u.length;
    };

    push('%PDF-1.4\n%\u00b5\u00b5\u00b5\u00b5\n');
    const offsets = [];
    for (let n = 1; n <= this.count; n++) {
      offsets[n] = len;
      push(`${n} 0 obj\n`);
      for (const p of this.objs[n - 1] || ['null']) push(p);
      push('\nendobj\n');
    }

    const xrefPos = len;
    let xref = `xref\n0 ${this.count + 1}\n0000000000 65535 f \n`;
    for (let n = 1; n <= this.count; n++) {
      xref += String(offsets[n]).padStart(10, '0') + ' 00000 n \n';
    }
    push(xref);
    push(`trailer\n<< /Size ${this.count + 1} /Root ${this.rootRef} 0 R /Info ${this.infoRef} 0 R >>\n`);
    push(`startxref\n${xrefPos}\n%%EOF\n`);

    return { parts, len };
  }
}

const yieldFrame = () => new Promise((r) => setTimeout(r, 0));

// ────────────── 主入口 ──────────────
// pages: [{ page:number, blob:Blob }] 升序；消费过程中逐页置 null 释放引用
// chapters: catalog.toTree 规整后的两级目录（可为空）
// 返回 { blob, usedBookmarks }
export async function buildPdf({ pages, issue, chapters = [], pageOffset = 0, onPhase }) {
  if (!pages.length) throw new Error('没有可用图片');

  const w = new PdfWriter();
  const refCatalog = w.newRef();
  const refPages = w.newRef();
  const refOutlines = w.newRef();
  const refInfo = w.newRef();
  w.rootRef = refCatalog;
  w.infoRef = refInfo;

  const pageRefs = pages.map(() => w.newRef());
  const contRefs = pages.map(() => w.newRef());
  const imgRefs = pages.map(() => w.newRef());

  // outline 条目引用先占号（写入时可自由前向引用）
  const outlineTops = chapters.map((ch) => ({
    title: ch.title,
    start: ch.start_page,
    ref: w.newRef(),
    kids: (ch.children || []).map((k) => ({ title: k.title, start: k.start_page, ref: w.newRef() })),
  }));

  // 物理页号 → PDF 页下标
  const sortedPages = pages.map((p) => p.page); // 已升序
  const locate = (physical) => {
    let lo = 0;
    let hi = sortedPages.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (sortedPages[mid] < physical) lo = mid + 1;
      else hi = mid;
    }
    return lo < sortedPages.length ? lo : -1;
  };

  // ── 1) 页对象：JPEG 直嵌 ──
  for (let i = 0; i < pages.length; i++) {
    const buf = new Uint8Array(await pages[i].blob.arrayBuffer());
    pages[i].blob = null; // 喂完即弃，峰值 ≈ 一页开销 + 已积累的 parts

    const dim = jpegSize(buf) || { w: 1080, h: 1466 };
    w.setObj(imgRefs[i], [
      `<< /Type /XObject /Subtype /Image /Width ${dim.w} /Height ${dim.h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${buf.length} >>\nstream\n`,
      buf,
      `\nendstream`,
    ]);

    const cs = `q ${dim.w} 0 0 ${dim.h} 0 0 cm /Im0 Do Q`;
    w.setObj(contRefs[i], [`<< /Length ${cs.length} >>\nstream\n${cs}\nendstream`]);

    w.setObj(pageRefs[i], [
      `<< /Type /Page /Parent ${refPages} 0 R /MediaBox [0 0 ${dim.w} ${dim.h}] ` +
        `/Resources << /XObject << /Im0 ${imgRefs[i]} 0 R >> /ProcSet [/PDF /ImageC] >> ` +
        `/Contents ${contRefs[i]} 0 R >>`,
    ]);

    if (onPhase) onPhase((i + 1) / pages.length, `合成 PDF ${i + 1}/${pages.length}`);
    if (i % 8 === 7) await yieldFrame(); // 让出事件循环，避免页面假死
  }

  // ── 2) outline 两级链 ──
  let itemCount = 0;
  const tops = [];
  for (const it of outlineTops) {
    const idx = locate(it.start + pageOffset);
    if (idx < 0) continue;
    const entry = { ref: it.ref, title: it.title, pageRef: pageRefs[idx], kids: [] };
    itemCount++;
    for (const kt of it.kids) {
      const kidx = locate(kt.start + pageOffset);
      if (kidx < 0 || kidx < idx) continue;
      entry.kids.push({ ref: kt.ref, title: kt.title, pageRef: pageRefs[kidx] });
      itemCount++;
    }
    tops.push(entry);
  }

  const itemDict = (e, parentRef, prevRef, nextRef) => {
    let s = `<< /Title ${pdfHex(e.title)} /Parent ${parentRef} 0 R /Dest [${e.pageRef} 0 R /Fit]`;
    if (prevRef) s += ` /Prev ${prevRef} 0 R`;
    if (nextRef) s += ` /Next ${nextRef} 0 R`;
    const kids = e.kids || [];
    if (kids.length) {
      s += ` /First ${kids[0].ref} 0 R /Last ${kids[kids.length - 1].ref} 0 R /Count ${kids.length}`;
    }
    return s + ' >>';
  };

  if (tops.length) {
    w.setObj(refOutlines, [
      `<< /Type /Outlines /First ${tops[0].ref} 0 R /Last ${tops[tops.length - 1].ref} 0 R /Count ${itemCount} >>`,
    ]);
    tops.forEach((e, i) => {
      w.setObj(e.ref, [
        itemDict(
          e,
          refOutlines,
          i > 0 ? tops[i - 1].ref : 0,
          i < tops.length - 1 ? tops[i + 1].ref : 0
        ),
      ]);
      e.kids.forEach((k, j) => {
        w.setObj(k.ref, [
          itemDict(k, e.ref, j > 0 ? e.kids[j - 1].ref : 0, j < e.kids.length - 1 ? e.kids[j + 1].ref : 0),
        ]);
      });
    });
    w.setObj(refCatalog, [`<< /Type /Catalog /Pages ${refPages} 0 R /Outlines ${refOutlines} 0 R >>`]);
  } else {
    w.setObj(refOutlines, ['<< /Type /Outlines /Count 0 >>']);
    w.setObj(refCatalog, [`<< /Type /Catalog /Pages ${refPages} 0 R >>`]);
  }

  // ── 3) Pages / Info ──
  w.setObj(refPages, [
    `<< /Type /Pages /Kids [${pageRefs.map((r) => `${r} 0 R`).join(' ')}] /Count ${pages.length} >>`,
  ]);

  // 元数据（对齐 pdf_pipeline._set_metadata，省去 XMP：Calibre 场景之外 Info 字典已足够）
  try {
    const author = issue.author || issue.publisher || 'Unknown';
    const bits =
      issue.resource_type === 1 ? [`期刊：${issue.resource_name}`] : [`图书：${issue.resource_name}`];
    const code = issue.resource_type === 1 ? issue.issn : issue.isbn;
    if (issue.author) bits.push(`作者：${issue.author}`);
    if (issue.publisher) bits.push(`出版社：${issue.publisher}`);
    if (issue.pub_date) bits.push(`出版：${issue.pub_date}`);
    if (code) bits.push(`${issue.resource_type === 1 ? 'ISSN' : 'ISBN'}：${code}`);
    if (issue.issue_name) bits.push(`期次：${issue.issue_name}`);
    if (issue.description) bits.push(issue.description.slice(0, 200));
    const keywords = ['bookan', issue.issue_name, code].filter(Boolean).join(', ');
    w.setObj(refInfo, [
      `<< /Title ${pdfHex(displayTitleOf(issue))} /Author ${pdfHex(author)} ` +
        `/Producer (BookanTool) /Creator (BookanTool) ` +
        `/Subject ${pdfHex(bits.join(' · '))} /Keywords ${pdfHex(keywords)} >>`,
    ]);
  } catch {
    w.setObj(refInfo, ['<< /Producer (BookanTool) /Creator (BookanTool) >>']);
  }

  // ── 4) 序列化输出 ──
  const { parts } = w.serialize();
  return { blob: new Blob(parts, { type: 'application/pdf' }), usedBookmarks: tops.length > 0 };
}

function displayTitleOf(issue) {
  if (issue.issue_name && !issue.resource_name.includes(issue.issue_name)) {
    return `${issue.resource_name} - ${issue.issue_name}`;
  }
  return issue.resource_name;
}

// 文件名清理（对齐 config.sanitize_filename）
export function sanitizeFilename(name) {
  return String(name || '').replace(/[/\\:*?"<>|]/g, '_').replace(/^\.+|\.+$/g, '');
}
