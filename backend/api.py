"""
博看 API 客户端：薄薄一层 requests 包装，每个方法对应一个 API。
抛出 BookanAPIError(code, msg) 而不是 print，便于上层做日志聚合与状态展示。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Callable

import requests

from .config import (
    API_BASE,
    EPUB_BASE,
    HTTP_TIMEOUT,
    IMAGE_SIZE_FULL,
    IMAGE_SIZE_THUMB,
    INSTANCE_ID_CATALOG,
    INSTANCE_ID_RESOURCE,
    JPAGE_DEFAULT,
    USER_AGENT,
)
from .models import ChapterStart, IssueInfo, PageHash

# issueName 中的年份提取（如 "2026年7期" → 2026）
_YEAR_RE = re.compile(r"(\d{4})年")


class BookanAPIError(Exception):
    """API 返回 code != 0 或 HTTP 非 200 时抛出。"""

    def __init__(self, code: int | str, msg: str, *, url: str = ""):
        self.code = code
        self.url = url
        super().__init__(f"[bookan api] code={code} {msg} ({url})")


class BookanAPI:
    """
    无状态客户端；可在线程间共享（requests.Session 内部已使用 urllib3 连接池）。
    """

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    # ──────────── 通用 GET ────────────
    def _get_json(self, url: str, params: dict) -> dict:
        try:
            r = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            raise BookanAPIError("network", str(e), url=url) from e

        if r.status_code != 200:
            raise BookanAPIError(r.status_code, "HTTP 非 200", url=r.url)

        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise BookanAPIError("json", f"无法解析 JSON: {e}", url=r.url) from e

        code = data.get("code", 0)
        if code != 0:
            raise BookanAPIError(code, data.get("msg", "未知错误"), url=r.url)
        return data

    # ──────────── issueInfoList：拉杂志/书籍元数据 ────────────
    def get_issue_info(self, issue_id: str, resource_type: int = 1) -> IssueInfo:
        """
        文档步骤 1。
        入参:
            issue_id: 单个 ID
            resource_type: 1 / 3
        """
        url = f"{API_BASE}/resource/issueInfoList"
        params = {
            "instanceId": INSTANCE_ID_RESOURCE,  # 12696
            "resourceType": resource_type,
            "issueIds": issue_id,
            "isDetail": 1,
        }
        data = self._get_json(url, params)
        items = data.get("data") or []
        if not items:
            raise BookanAPIError("empty", "issueInfoList 返回为空", url=url)
        return _parse_issue(items[0], default_resource_type=resource_type)

    # ──────────── issueInfoList：批量拉取多期元数据 ────────────
    def get_issue_info_list(self, issue_ids: list[str], resource_type: int = 1) -> list[IssueInfo]:
        """批量版 issueInfoList：issueIds 参数支持逗号分隔的多个 ID。"""
        url = f"{API_BASE}/resource/issueInfoList"
        params = {
            "instanceId": INSTANCE_ID_RESOURCE,
            "resourceType": resource_type,
            "issueIds": ",".join(str(i).strip() for i in issue_ids),
            "isDetail": 1,
        }
        data = self._get_json(url, params)
        return [
            _parse_issue(item, resource_type)
            for item in (data.get("data") or [])
            if isinstance(item, dict)
        ]

    # ──────────── 「下载全年」：按 issueID 前后推算同刊同年各期 ────────────
    def collect_year_issues(
        self, base: IssueInfo, batch_size: int = 30, max_issues: int = 600
    ) -> list[IssueInfo]:
        """
        以 base 为中心，向前后按 issueID 连续做减法/加法推算，收集同刊同年的所有期。

        停止条件（满足其一即停）：
          • 返回条目的 resourceName 与 base 不一致（推到了别的期刊）
          • issueName 中的年份与 base 不同（跨年；仅当 base 期名含 "xxxx年" 时启用）
          • ID 无返回 / 请求出错（推到了列表边界）
        """
        try:
            base_id = int(base.issue_id)
        except (TypeError, ValueError):
            return [base]

        year_match = _YEAR_RE.search(base.issue_name or "")
        year = int(year_match.group(1)) if year_match else None

        def matches(it: IssueInfo) -> bool:
            if it.resource_name != base.resource_name:
                return False
            if year:
                m = _YEAR_RE.search(it.issue_name or "")
                if m and int(m.group(1)) != year:
                    return False
            return True

        found: dict[int, IssueInfo] = {base_id: base}

        for direction in (-1, 1):
            edge = base_id
            while len(found) < max_issues:
                ids = [edge + direction * k for k in range(1, batch_size + 1)]
                got = self._probe_issues(ids, base.resource_type, matches)
                # 只取与已知边界连续相邻的匹配段，中间断开即视为越界
                run: list[IssueInfo] = []
                for k in range(1, batch_size + 1):
                    it = got.get(edge + direction * k)
                    if it is None:
                        break
                    run.append(it)
                if not run:
                    break
                for it in run:
                    found[int(it.issue_id)] = it
                edge += direction * len(run)
                if len(run) < batch_size:
                    break  # 本批内已到边界

        return [found[k] for k in sorted(found)]

    def _probe_issues(
        self, ids: list[int], resource_type: int, matches: Callable[[IssueInfo], bool]
    ) -> dict[int, IssueInfo]:
        """
        探测一批 ID 是否属于同一刊物。
        优先批量请求；失败（混入不存在的 ID 等）时退回逐个探测，
        遇到第一个不匹配/不存在的 ID 即停，保证"连续段"语义。
        """
        try:
            items = self.get_issue_info_list([str(i) for i in ids], resource_type)
            if items:
                out: dict[int, IssueInfo] = {}
                for it in items:
                    try:
                        key = int(it.issue_id)
                    except (TypeError, ValueError):
                        continue
                    if matches(it):
                        out[key] = it
                return out
        except BookanAPIError:
            pass

        out = {}
        for i in ids:
            try:
                it = self.get_issue_info(str(i), resource_type)
            except BookanAPIError:
                break
            if not matches(it):
                break
            out[int(it.issue_id)] = it
        return out

    # ──────────── getHash：拉图片 hash 列表 ────────────
    def get_hashes(
        self,
        resource_id: str,
        issue_id: str,
        page_count: int,
        resource_type: int = 1,
        start: int = 1,
    ) -> list[PageHash]:
        """
        文档步骤 3。
        入参:
            resource_id, issue_id: 来自 issueInfoList
            page_count: 总页数
            resource_type: 1 / 3
            start: 起始页（默认 1）
        """
        url = f"{API_BASE}/resource/getHash"
        params = {
            "resourceType": resource_type,
            "resourceId": resource_id,
            "issueId": issue_id,
            "start": start,
            "end": page_count,
        }
        data = self._get_json(url, params)
        out: list[PageHash] = []
        for item in data.get("data") or []:
            page = int(item.get("page") or item.get("pageNum") or 0)
            hash_ = item.get("hash") or item.get("encryptHash") or ""
            enc = item.get("encryptHash") or item.get("encryptedHash") or ""
            if page and hash_:
                out.append(PageHash(page=page, hash=hash_, encrypted_hash=enc))
        if not out:
            raise BookanAPIError("empty", "getHash 返回为空", url=url)
        return out

    # ──────────── catalogInfo：拉章节起始页(用于 PDF outline) ────────────
    def get_catalog(self, issue_id: str, resource_type: int = 1) -> list[ChapterStart]:
        """
        拉取本期目录（两级：栏目 → 文章）。

        实测要点（2026-08）：
          · catalog 用 instanceId=13790，与 issueInfoList(12696) 不同
          · categoryId **必须传 issueId**。传 resourceId 会返回该资源的历史文章
            索引（跨期、甚至 2007 年的旧文章），不是本期目录
          · 单条结构: {id, name, page, category, status, html, cover, new_html,
                       speed, sublevels[]}  —— 是 name/page，**不是** title/startPage
          · page 可能为 0 或负数（封面 -2、目录 0、广告 0），此类条目无实际页码
        """
        url = f"{API_BASE}/resource/catalogInfo"
        params = {
            "instanceId": INSTANCE_ID_CATALOG,  # 13790 ← 与上面不同
            "resourceType": resource_type,
            "categoryId": issue_id,
        }
        try:
            data = self._get_json(url, params)
        except BookanAPIError:
            # 部分 issue 没有目录数据是可接受情况；上层捕获后跳过 outline
            return []

        chapters: list[ChapterStart] = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            node = _parse_catalog_node(item, level=0)
            if node is not None:
                chapters.append(node)
        return chapters

    # ──────────── EPUB 资源：官方成品直下 ────────────
    def get_epub_version_hash(self, resource_id: str, issue_id: str, resource_type: int = 1) -> str:
        """
        拉取 EPUB 版本 hash（2026-08-31 Reqable 抓包实测）。

        getHash 以 start=0 请求时返回 page=0 条目，其 hash 即 EPUB 版本号
        （如 "44164560"），用于拼官方 .epub 成品下载地址。
        """
        url = f"{API_BASE}/resource/getHash"
        params = {
            "resourceType": resource_type,
            "resourceId": resource_id,
            "issueId": issue_id,
            "start": 0,
            "end": 0,
        }
        data = self._get_json(url, params)
        for item in data.get("data") or []:
            if int(item.get("page") or 0) == 0 and item.get("hash"):
                return str(item["hash"])
        raise BookanAPIError("empty", "getHash 未返回 page=0 的 EPUB 版本 hash", url=url)

    def build_epub_url(self, resource_id: str, issue_id: str, version_hash: str) -> str:
        """
        官方 EPUB 成品下载地址（2026-08-31 抓包实测）：
        http://epub.bookan.com.cn/epub2/{rid}/{rid}-{iid}/{iid}_{hash}.epub
        """
        return f"{EPUB_BASE}/epub2/{resource_id}/{resource_id}-{issue_id}/{issue_id}_{version_hash}.epub"

    def download_to_file(
        self,
        url: str,
        dest_path: str,
        on_progress: Callable[[int, int], None] | None = None,
        cancel_event=None,
        chunk_size: int = 64 * 1024,
    ) -> str:
        """
        流式下载文件到磁盘（用于 EPUB 成品，约 5~13MB）。
        on_progress(已下载字节, 总字节)；cancel_event 置位时中断并删除半成品。
        """
        try:
            r = self.session.get(url, timeout=HTTP_TIMEOUT, stream=True)
        except requests.RequestException as e:
            raise BookanAPIError("network", str(e), url=url) from e
        if r.status_code != 200:
            raise BookanAPIError(r.status_code, "下载失败", url=url)

        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        try:
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size):
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("用户取消")
                    if chunk:
                        f.write(chunk)
                        done += len(chunk)
                        if on_progress:
                            on_progress(done, total)
        except Exception:
            with contextlib.suppress(OSError):
                os.remove(dest_path)
            raise
        return dest_path

    def download_cover(self, issue: IssueInfo) -> bytes:
        """
        取封面图：接口无 cover 字段，取第 1 页的缩略图（_small.jpg，约 20KB）。
        失败返回 b""（调用方应忽略而不是中断流程）。
        """
        try:
            hashes = self.get_hashes(
                resource_id=issue.resource_id,
                issue_id=issue.issue_id,
                page_count=1,
                resource_type=issue.resource_type,
                start=1,
            )
            if not hashes:
                return b""
            url = build_image_url(
                issue.resource_id,
                issue.issue_id,
                hashes[0].hash,
                issue.jpage_node,
                size=IMAGE_SIZE_THUMB,
            )
            return self.download(url)
        except Exception:
            return b""

    def download(self, url: str) -> bytes:
        """通用二进制下载（封面 / xhtml 章节）。"""
        r = self.session.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            raise BookanAPIError(r.status_code, "下载失败", url=url)
        return r.content

    def download_stream(self, url: str, chunk_size: int = 64 * 1024):
        """流式下载（用于图片），生成器产出 bytes。"""
        with self.session.get(url, timeout=HTTP_TIMEOUT, stream=True) as r:
            if r.status_code != 200:
                raise BookanAPIError(r.status_code, "下载失败", url=url)
            for chunk in r.iter_content(chunk_size):
                if chunk:
                    yield chunk


# ────────────── 私有 helper ──────────────
def _parse_issue(raw: dict, default_resource_type: int) -> IssueInfo:
    """
    把 issueInfoList 的原始 dict 转成 IssueInfo。

    字段名按 2026-08 线上实测校准，关键修正：
      出版社  publisher ← press        （不是 publisher）
      出版日  pub_date  ← publish      （不是 pubDate / publishDate）
      简介    description ← text       （不是 description / intro）
      CDN节点 jpage_node ← jpg or webp （接口直接给出，无需探测）
      作者    author ← 接口不返回该字段，保持空串
      封面    cover_url ← 接口不返回，由第 1 页缩略图拼装（需 hash，见 ImagePipeline）
    """
    consumed = {
        "resourceId",
        "issueId",
        "resourceName",
        "issueName",
        "resourceType",
        "type",
        "count",
        "author",
        "press",
        "publish",
        "isbn",
        "issn",
        "cn",
        "text",
        "explain",
        "explainRecommend",
        "jpg",
        "webp",
        "tags",
        "resourceCode",
        "issueYear",
        "issueNo",
        "categoryName",
    }
    extra = {k: v for k, v in raw.items() if k not in consumed}

    # tags: [{'id','name'}] → ['北大核心', ...]
    tags: list[str] = []
    for t in raw.get("tags") or []:
        if isinstance(t, dict) and t.get("name"):
            tags.append(str(t["name"]))

    # jpage 节点号：接口直接给，形如 '8'
    node = str(raw.get("jpg") or raw.get("webp") or JPAGE_DEFAULT)

    rid = str(raw.get("resourceId") or "")
    iid = str(raw.get("issueId") or "")

    return IssueInfo(
        resource_id=rid,
        issue_id=iid,
        resource_name=str(raw.get("resourceName") or "未知"),
        issue_name=str(raw.get("issueName") or ""),
        resource_type=int(raw.get("resourceType") or raw.get("type") or default_resource_type),
        count=_to_int(raw.get("count"), 0),
        author=str(raw.get("author") or ""),
        publisher=str(raw.get("press") or ""),
        pub_date=str(raw.get("publish") or ""),
        isbn=str(raw.get("isbn") or ""),
        issn=str(raw.get("issn") or ""),
        cn=str(raw.get("cn") or ""),
        description=str(raw.get("text") or raw.get("explainRecommend") or raw.get("explain") or ""),
        jpage_node=node,
        tags=tags,
        cover_url="",  # 需要第 1 页 hash，由 ImagePipeline.download_cover 填充
        extra=extra,
    )


def _parse_catalog_node(item: dict, level: int = 0) -> ChapterStart | None:
    """
    解析 catalogInfo 的单条记录（递归处理 sublevels）。

    返回 None 表示该条目完全无效（无标题且无有效子项）。
    注意：page<=0 的条目（封面/目录/广告）本身会被保留为结构节点，
    但其 start_page 非法，由 catalog.normalize 在生成 outline 时剔除。
    """
    title = str(item.get("name") or item.get("title") or "").strip()
    start = _to_int(item.get("page"), 0)
    end = _to_int(item.get("endPage"), 0)

    children: list[ChapterStart] = []
    for sub in item.get("sublevels") or []:
        if not isinstance(sub, dict):
            continue
        child = _parse_catalog_node(sub, level=level + 1)
        if child is not None:
            children.append(child)

    if not title and not children:
        return None

    return ChapterStart(
        title=title or f"第 {start} 页",
        start_page=start,
        end_page=end,
        level=level,
        children=children,
    )


def _to_int(value, default: int = 0) -> int:
    """API 大量字段是数字字符串（如 count='182'），这里统一安全转换。"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_image_url(
    resource_id: str,
    issue_id: str,
    page_hash: str,
    jpage: str = JPAGE_DEFAULT,
    size: str = IMAGE_SIZE_FULL,
) -> str:
    """
    拼页面图片 URL。
    https 不可用（证书/镜像问题），实测 http://img1-qn.bookan.com.cn 正常。
    """
    return (
        f"http://img1-qn.bookan.com.cn/jpage{jpage}/"
        f"{resource_id}/{resource_id}-{issue_id}/{page_hash}_{size}.jpg"
    )
