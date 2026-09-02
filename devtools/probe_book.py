# -*- coding: utf-8 -*-
"""第三轮：书籍(type=3)字段 + 目录 sublevels 结构 + 封面 URL。"""

import sys, io, json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
S = requests.Session()
S.headers.update({"User-Agent": UA})
API = "https://api.bookan.com.cn/resource/"


def info(iid, rt):
    r = S.get(
        API + "issueInfoList",
        params={"instanceId": 12696, "isDetail": 1, "issueIds": iid, "resourceType": rt},
        timeout=15,
    )
    return r.json()


# 1) 用杂志 ID 试 type=3，看字段是否变化（尤其 author）
print("=" * 70)
print("1) 同一 ID 在 type=1 / type=3 下的字段差异")
print("=" * 70)
for rt in (1, 3):
    j = info("310826855", rt)
    d = j.get("data") or []
    print(f"\n--- type={rt} code={j.get('code')} n={len(d)}")
    if d:
        raw = d[0]
        for k in (
            "resourceName",
            "issueName",
            "author",
            "press",
            "publish",
            "isbn",
            "cn",
            "issn",
            "count",
            "jpg",
            "webp",
            "txt",
            "html",
            "categoryName",
            "resourceType",
            "type",
        ):
            if k in raw:
                v = raw[k]
                s = (
                    f"{str(v)[:60]}...({len(v)}ch)"
                    if isinstance(v, str) and len(v) > 60
                    else repr(v)
                )
                print(f"   {k:18s} {s}")
        print(f"   [author 是否存在] {'author' in raw}")
        print(
            f"   [全部 key 中含 'auth'/'writ'/'edit'] "
            f"{[k for k in raw if 'auth' in k.lower() or 'writ' in k.lower() or 'edit' in k.lower()]}"
        )

# 2) sublevels 结构
print("\n" + "=" * 70)
print("2) catalog sublevels 完整结构")
print("=" * 70)
j = S.get(
    API + "catalogInfo",
    params={"instanceId": 13790, "resourceType": 1, "categoryId": "310826855"},
    timeout=15,
).json()
for x in j["data"]:
    subs = x.get("sublevels") or []
    print(f"\n  [{x.get('page')}] {x.get('name')!r}  (sub={len(subs)})")
    for s in subs:
        print(
            f"        - page={s.get('page')} name={s.get('name')!r} html={s.get('html')} sub={len(s.get('sublevels') or [])}"
        )

# 3) 封面：第 1 页图片可否作为封面
print("\n" + "=" * 70)
print("3) 封面候选")
print("=" * 70)
raw = info("310826855", 1)["data"][0]
rid, iid, node = raw["resourceId"], raw["issueId"], raw.get("jpg") or "8"
h = S.get(
    API + "getHash",
    params={"resourceId": rid, "issueId": iid, "start": 1, "end": 3, "resourceType": 1},
    timeout=15,
).json()["data"]
print(f"  hash 长度: {[len(x['hash']) for x in h]}  样例: {[x['hash'] for x in h]}")
url = f"http://img1-qn.bookan.com.cn/jpage{node}/{rid}/{rid}-{iid}/{h[0]['hash']}_big.jpg"
r = S.get(url, timeout=20)
print(f"  page1: {r.status_code} {len(r.content)}B {r.headers.get('Content-Type')}")

# webp 变体
url2 = f"http://img1-qn.bookan.com.cn/jpage{node}/{rid}/{rid}-{iid}/{h[0]['hash']}_big.webp"
try:
    r2 = S.head(url2, timeout=15)
    print(f"  webp : {r2.status_code} {r2.headers.get('Content-Type')}")
except Exception as e:
    print(f"  webp : ERR {e}")

# 小图（缩略图）用于封面
for suf in ["_small.jpg", ".jpg", "_mid.jpg"]:
    u = f"http://img1-qn.bookan.com.cn/jpage{node}/{rid}/{rid}-{iid}/{h[0]['hash']}{suf}"
    try:
        rr = S.head(u, timeout=10)
        print(f"  {suf:12s} -> {rr.status_code} {rr.headers.get('Content-Length')}")
    except Exception as e:
        print(f"  {suf:12s} -> ERR {e}")

print("\nDONE")
