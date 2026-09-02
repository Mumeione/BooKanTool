"""
章节起始页换算工具：把 catalogInfo 返回的两级目录 → PDF outline / EPUB 导航。

实测要点：
  · catalogInfo 顶层是「栏目」，sublevels 是「文章」，两级都要进 outline
  · 顶层存在 page<=0 的伪条目（封面 -2、目录 0、广告 0），必须剔除
    否则会让 outline 全部堆到第 1 页
  · 同一页码只允许出现一条（取层级最浅、最先出现的那条）
"""

from __future__ import annotations

from .models import ChapterStart


def normalize(chapters: list[ChapterStart], total_pages: int) -> list[ChapterStart]:
    """
    1. 深度优先展开为扁平列表
    2. 剔除 page 越界或 <=0 的条目
    3. 按 start_page 升序；同页去重（保留先出现的）
    """
    flat: list[ChapterStart] = []
    for ch in chapters:
        flat.extend(ch.flatten())

    out: list[ChapterStart] = []
    seen: set[int] = set()
    for ch in sorted(flat, key=lambda c: (c.start_page, c.level)):
        if ch.start_page < 1 or ch.start_page > total_pages:
            continue
        if ch.start_page in seen:
            continue
        seen.add(ch.start_page)
        out.append(ch)
    return out


def to_tree(chapters: list[ChapterStart], total_pages: int) -> list[ChapterStart]:
    """
    保留两级层级关系并过滤无效页，用于 EPUB 导航 / 分级 outline。

    规则：
      · 父节点自身 page 合法 → 作为一级条目，其合法子项作为二级
      · 父节点 page 非法但子项合法 → 子项提升为一级
      · 子项与父项同页码是常态（栏目页上就是首篇文章），两者都保留；
        seen 去重只用于跨分支的同页重复
    """
    out: list[ChapterStart] = []
    seen: set[int] = set()

    def _take(node: ChapterStart, same_page_as_parent: bool = False) -> ChapterStart | None:
        if node.start_page < 1 or node.start_page > total_pages:
            return None
        # 与父项同页的直接子项放行（原文目录即如此）；跨分支重复仍去重
        if node.start_page in seen and not same_page_as_parent:
            return None
        seen.add(node.start_page)
        return node

    for parent in sorted(chapters, key=lambda c: (max(c.start_page, 0), c.level)):
        p = _take(parent)
        kids: list[ChapterStart] = []
        for child in parent.children:
            c = _take(
                child, same_page_as_parent=(p is not None and child.start_page == parent.start_page)
            )
            if c is not None:
                kids.append(c)

        if p is not None:
            p = ChapterStart(
                title=p.title,
                start_page=p.start_page,
                end_page=p.end_page,
                level=0,
                children=[],
            )
            # 子项页码不能小于父项（同页可以：栏目页即首篇首页）
            kids = [k for k in kids if k.start_page >= p.start_page]
            p.children = kids
            out.append(p)
        elif kids:
            # 父无效（如"目录" page=0）→ 子项提升为一级
            out.extend(kids)

    out.sort(key=lambda c: c.start_page)
    return out


def count_valid(chapters: list[ChapterStart]) -> int:
    """统计有效（page>=1）条目总数，含子级。"""
    return sum(1 for c in chapters for _ in c.flatten())


def derive_page_offset(chapters: list[ChapterStart], physical_pages: list[int]) -> int:
    """
    从目录结构推导「印刷页码 → 物理图片页」的偏移量。

    实测依据（南方经济 2026年7期，182 物理页）：
      · getHash 返回的 page 就是物理页序（1=封面，2=中文目录，3=英文目录，
        4=正文第一篇，页脚印 ·1·）
      · catalogInfo 的 page 是印刷页码（首篇=1，封底=179）
      · 封底（目录中最大的印刷页码）必然对应最后一个物理页
        → offset = 最大物理页 - 最大印刷页 = 182 - 179 = 3
      · 交叉验证：首篇印刷页 1 + 3 = 物理页 4，与实拍页面一致

    候选策略（按优先级）：
      1. 封底锚点：max(物理页) - max(印刷页)，要求全部条目映射后不越界
      2. 前导伪条目计数（封面/目录/广告 page<=0 的条目数）
      3. 0（印刷页即物理页，部分图书类资源如此）

    入参:
        chapters: get_catalog 返回的原始目录树（含伪条目，未规整）
        physical_pages: 实际下载成功的各页物理页号（来自 getHash）
    """
    if not physical_pages:
        return 0
    phys_max = max(physical_pages)

    valid = [ch for node in chapters for ch in node.flatten()]  # 仅 page>=1
    printed = sorted({ch.start_page for ch in valid})
    if not printed:
        return 0
    p_min, p_max = printed[0], printed[-1]

    def _feasible(off: int) -> bool:
        # 全部条目映射后必须落在物理页范围内
        if p_min + off < 1 or p_max + off > phys_max:
            return False
        # 首篇不能落在封面（物理页 1 恒为封面，与官方 EPUB cover 同图）
        return not (p_min == 1 and p_min + off < 2)

    candidates: list[int] = []
    # 候选 1：封底锚点（期刊的封底 = 最后一个物理页）
    anchor = phys_max - p_max
    if anchor > 0:
        candidates.append(anchor)
    # 候选 2：前导伪条目数（封面/目录/广告…）
    lead = 0
    for node in chapters:
        if node.start_page < 1:
            lead += 1
        else:
            break
    if lead > 0:
        candidates.append(lead)
    # 候选 3：无偏移
    candidates.append(0)

    for off in candidates:
        if _feasible(off):
            return off
    return 0
