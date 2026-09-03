"""
数据类：使用 dataclass 固化所有后端模块之间流转的字段名。

字段名已按博看线上 API（api.bookan.com.cn）2026-08 实测返回校准：
  · issueInfoList 返回 resourceName / issueName / count / publish / press /
    issn / cn / isbn / text / jpg / webp / tags
  · 作者字段为 owner（type=3 图书实测返回，如 "曾仕强"）；杂志无作者信息
  · catalogInfo 返回 id / name / page / sublevels，page 可为 0 或负数（封面/目录/广告）
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


# ────────────── 资源信息（issueInfoList） ──────────────
@dataclass
class IssueInfo:
    """单本杂志 / 单本书的元数据。"""

    resource_id: str  # 资源 ID
    issue_id: str  # 期号 / 卷 ID
    resource_name: str  # 书名 / 杂志名
    issue_name: str  # 期号名（如 "2026年7期"）
    resource_type: int  # 1=杂志 3=书籍
    count: int  # 总页数
    author: str = ""  # 作者（博看接口通常不返回，留空）
    publisher: str = ""  # 出版社 ← API 字段 press
    pub_date: str = ""  # 出版日期 ← API 字段 publish
    isbn: str = ""  # ISBN
    issn: str = ""  # ISSN
    cn: str = ""  # 国内统一刊号
    description: str = ""  # 简介 ← API 字段 text
    jpage_node: str = "8"  # CDN 节点号 ← API 字段 jpg / webp
    tags: list[str] = field(default_factory=list)
    cover_url: str = ""  # 第 1 页缩略图 URL（接口无封面字段，由我们拼装）
    extra: dict = field(default_factory=dict)  # 其它未消费字段原样保留

    @property
    def display_title(self) -> str:
        """人类可读标题：资源名 + 期号名（书籍的 issue_name 可能为空，容错）。"""
        if self.issue_name and self.issue_name not in self.resource_name:
            return f"{self.resource_name} - {self.issue_name}"
        return self.resource_name


# ────────────── 图片 hash 列表项（getHash） ──────────────
@dataclass
class PageHash:
    """单页的图片 hash + 起始页码。hash 实测为 8 位十六进制串。"""

    page: int  # 第几页（1-based）
    hash: str  # 用于拼图片 URL 的 hash 串
    encrypted_hash: str = ""  # 加密版本 hash（如有）

    def image_url(
        self, resource_id: str, issue_id: str, jpage: str = "8", size: str = "big"
    ) -> str:
        """
        拼图片 URL。
        实测可用后缀：_big.jpg（原图，约 200KB）、_small.jpg（缩略图，约 20KB）。
        _big.webp / _mid.jpg / .jpg 均 404。
        """
        return (
            f"http://img1-qn.bookan.com.cn/jpage{jpage}/"
            f"{resource_id}/{resource_id}-{issue_id}/{self.hash}_{size}.jpg"
        )


# ────────────── 章节 / 目录（catalogInfo） ──────────────
@dataclass
class ChapterStart:
    """
    单章：标题 + 对应 PDF 中的起始页（1-based）+ 可选子章节。

    博看 catalogInfo 是两级结构：顶层是「栏目」，sublevels 是「文章」。
    顶层存在 page<=0 的伪条目（封面 -2、目录 0、广告 0），需由 catalog.normalize 过滤。
    """

    title: str
    start_page: int
    end_page: int = 0
    level: int = 0
    children: list[ChapterStart] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """page<=0 表示封面/目录/广告等无实际页码的条目。"""
        return self.start_page >= 1

    def to_outline_dict(self):
        return {"title": self.title, "page": self.start_page}

    def flatten(self) -> list[ChapterStart]:
        """深度优先展开为扁平列表（保留层级信息）。"""
        out: list[ChapterStart] = []
        if self.is_valid:
            out.append(self)
        for c in self.children:
            out.extend(c.flatten())
        return out


# ────────────── 任务状态 ──────────────
@dataclass
class TaskState:
    """前端展示用的统一任务状态。"""

    task_id: str
    status: str  # pending / running / succeeded / failed / cancelled
    progress: float = 0.0  # 0..1
    message: str = ""
    output_files: list[str] = field(default_factory=list)

    def to_json(self):
        return asdict(self)
