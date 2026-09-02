# -*- coding: utf-8 -*-
"""第二轮探测：catalog 目录语义 + 文本版 EPUB 路径。"""

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


def line(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def info(iid, rt=1):
    return S.get(
        API + "issueInfoList",
        params={"instanceId": 12696, "isDetail": 1, "issueIds": iid, "resourceType": rt},
        timeout=15,
    ).json()["data"][0]


def cat(cid, inst=13790, rt=1):
    return S.get(
        API + "catalogInfo",
        params={"instanceId": inst, "resourceType": rt, "categoryId": cid},
        timeout=15,
    ).json()


raw = info("310826855")
rid, iid, cnt = raw["resourceId"], raw["issueId"], int(raw["count"])
print(f"rid={rid} iid={iid} count={cnt} start={raw.get('start')}")

line("A) catalogInfo categoryId=issueId (%s)" % iid)
d = cat(iid)["data"]
print(f"  n={len(d)}")
for x in d:
    print(
        f"   id={x.get('id'):<10} page={x.get('page'):<6} name={x.get('name')!r} sub={len(x.get('sublevels') or [])}"
    )

line("B) catalogInfo categoryId=resourceId (%s)" % rid)
d2 = cat(rid)["data"]
print(f"  n={len(d2)}")
for x in d2[:60]:
    print(
        f"   id={x.get('id'):<10} page={x.get('page'):<6} name={x.get('name')!r} sub={len(x.get('sublevels') or [])}"
    )

line("C) 文本版：找 html/txt 非空的样本")
for iid_t, rt in [("310069561", 1), ("310640504", 1), ("310836831", 1), ("310823726", 1)]:
    try:
        r = info(iid_t, rt)
        print(
            f"  {iid_t} {r.get('resourceName')}/{r.get('issueName')}: "
            f"rid={r.get('resourceId')} html={r.get('html')!r} txt={r.get('txt')!r} "
            f"pdf={r.get('pdf')!r} new_html={r.get('new_html')} count={r.get('count')}"
        )
    except Exception as e:
        print(f"  {iid_t} ERR {e}")

line("D) 文本版 html 资源：探测 html5 / txt 接口")
r2 = info("310069561")
rid2, iid2 = r2["resourceId"], r2["issueId"]
print(f"  rid={rid2} iid={iid2} html={r2.get('html')!r} txt={r2.get('txt')!r}")
cands = [
    f"https://api.bookan.com.cn/resource/catalogInfo?instanceId=13790&resourceType=1&categoryId={iid2}",
    f"http://epub.bookan.com.cn/epub2/{rid2}/{rid2}-{iid2}/{iid2}/directories.json",
    f"http://epub.bookan.com.cn/epub2/{rid2}/{rid2}-{iid2}/{iid2}_1/directories.json",
    f"https://txt.bookan.com.cn/txt/{rid2}/{rid2}-{iid2}/",
]
for u in cands:
    try:
        rr = S.get(u, timeout=15)
        txt = rr.text[:200].replace("\n", " ")
        print(f"  {rr.status_code}  {u}\n        {txt!r}")
    except Exception as e:
        print(f"  ERR {e}  {u}")

# catalog 的 html 字段暗示有 html 正文
line("E) catalog 项 html 字段 -> 正文接口")
d3 = cat(iid2)["data"]
for x in d3[:12]:
    print(
        f"   id={x.get('id'):<10} page={x.get('page'):<6} html={x.get('html')} name={x.get('name')!r}"
    )

line("DONE")
