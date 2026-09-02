"""
URL 解析器：兼容多种输入形式
  • 博看官网完整 URL: https://new.bookan.com.cn/...?type=1&id=233832
  • 纯 ID:             233832
  • 自定义格式前缀:    id:233832 / mag:233832 / book:233832
最终输出 (resource_type, issue_id) 二元组。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# 匹配 URL 中 type / id 查询参数
_URL_PARAM_RE = re.compile(r"[?&](type|id)=([^&]*)")


class ParseError(ValueError):
    """URL/ID 解析失败时抛出。"""


def parse_input(text: str) -> tuple[int, str]:
    """
    统一解析入口。
    返回 (resource_type, issue_id)；
      • resource_type: 1=杂志 3=书籍
      • issue_id: 字符串（接口接受字符串）
    """
    if text is None:
        raise ParseError("输入为空")

    raw = text.strip()
    if not raw:
        raise ParseError("输入为空")

    # 1) 自定义前缀：mag: / book: / id:
    pref_match = re.match(r"^(mag|magazine|book|id)\s*[:=]\s*([\w\-]+)$", raw, re.I)
    if pref_match:
        kind = pref_match.group(1).lower()
        value = pref_match.group(2)
        kind_map = {"mag": 1, "magazine": 1, "book": 3, "id": 1}
        return kind_map[kind], value

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

    # 4) 退化：纯数字当杂志 ID 处理
    if re.fullmatch(r"\d{3,}", raw):
        return 1, raw

    raise ParseError(f"无法解析输入：{raw!r}")


def describe_type(resource_type: int) -> str:
    return (
        "杂志"
        if resource_type == 1
        else ("书籍" if resource_type == 3 else f"未知({resource_type})")
    )
