"""
URL 解析器：输入仅支持完整链接
  • 博看官网完整 URL: https://new.bookan.com.cn/...?type=1&id=233832
  • 移动端分享链接:   https://wk6.bookan.com.cn/?id=130#/dt/1/310823891
  • 含 path 的链接:   https://new.bookan.com.cn/read/1/233832
最终输出 (resource_type, issue_id) 二元组。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# 匹配 URL 中 type / id 查询参数
_URL_PARAM_RE = re.compile(r"[?&](type|id)=([^&]*)")


class ParseError(ValueError):
    """链接解析失败时抛出。"""


def parse_input(text: str) -> tuple[int, str]:
    """
    统一解析入口（仅接受完整链接，纯 ID / 前缀缩写已移除）。
    返回 (resource_type, issue_id)；
      • resource_type: 1=杂志 3=书籍
      • issue_id: 字符串（接口接受字符串）
    """
    if text is None:
        raise ParseError("输入为空")

    raw = text.strip()
    if not raw:
        raise ParseError("输入为空")

    # 1) 移动端分享链接（App 复制）：https://wk6.bookan.com.cn/?id=130#/dt/1/310823891
    #    fragment 格式 #/dt/{type}/{issueId}；查询串 ?id=130 是站点 ID 而非书刊 ID，
    #    必须先于查询参数解析，否则 130 会被误当 issueId
    share_match = re.search(r"#/dt/(\d+)/(\d+)", raw)
    if share_match:
        t = int(share_match.group(1))
        if t not in (1, 3):
            raise ParseError(f"无法识别的 type={t}，仅支持 1(杂志) / 3(书籍)")
        return t, share_match.group(2)

    # 2) 含 type / id 查询参数（任意 URL）
    type_value: str | None = None
    id_value: str | None = None
    for key, val in _URL_PARAM_RE.findall(raw):
        if key == "type":
            type_value = val
        elif key == "id":
            id_value = val

    if id_value:
        t = int(type_value) if type_value else 1
        if t not in (1, 3):
            raise ParseError(f"无法识别的 type={type_value}，仅支持 1(杂志) / 3(书籍)")
        return t, id_value

    # 3) 含 path 参数：/read/{type}/{id} 或 /detail/{type}/{id}
    parsed = urlparse(raw)
    if parsed.path and parsed.path != "/":
        m = re.search(r"/(\d+)/(\d+)(?:/|$)", parsed.path)
        if m:
            t, i = int(m.group(1)), m.group(2)
            if t in (1, 3):
                return t, i

    raise ParseError(f"无法识别的链接，请粘贴完整的书刊详情页网址：{raw!r}")


def describe_type(resource_type: int) -> str:
    return (
        "杂志"
        if resource_type == 1
        else ("书籍" if resource_type == 3 else f"未知({resource_type})")
    )
