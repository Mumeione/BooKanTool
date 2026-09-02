# -*- coding: utf-8 -*-
"""真实 API 探测脚本（开发期一次性使用，不打包进 exe）。"""

import json
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
S = requests.Session()
S.headers.update({"User-Agent": UA})

API = "https://api.bookan.com.cn/resource/"
EPUB = "http://epub.bookan.com.cn"


def line(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def dump_issue(raw):
    for k, v in raw.items():
        t = type(v).__name__
        if isinstance(v, str) and len(v) > 80:
            s = f"{v[:80]}...({len(v)}ch)"
        else:
            s = repr(v)
        print(f"  {k:22s} {t:8s} {s}")


# ── 1. issueInfoList 全字段 ────────────────────────────────
line("1) issueInfoList  全字段 dump")
for iid, rt in [("310826855", 1), ("310850498", 1), ("310069561", 1)]:
    r = S.get(
        API + "issueInfoList",
        params={"instanceId": 12696, "isDetail": 1, "issueIds": iid, "resourceType": rt},
        timeout=15,
    )
    d = r.json()
    items = d.get("data") or []
    print(f"\n--- id={iid} type={rt} code={d.get('code')} n={len(items)}")
    if items:
        dump_issue(items[0])
        raw = items[0]
        # 关键派生值
        rid = raw.get("resourceId")
        jpg_node = raw.get("jpg") or raw.get("webp") or ""
        print(
            f"  >>> derived: rid={rid} jpage_node={jpg_node} txt={raw.get('txt')!r} html={raw.get('html')!r} pdf={raw.get('pdf')!r}"
        )

# ── 2. 图片 URL 真实可达性 ─────────────────────────────────
line("2) 图片 URL 可达性（jpage8 / _big.jpg）")
raw = S.get(
    API + "issueInfoList",
    params={"instanceId": 12696, "isDetail": 1, "issueIds": "310826855", "resourceType": 1},
    timeout=15,
).json()["data"][0]
rid, iid = raw["resourceId"], raw["issueId"]
cnt = int(raw["count"])
hashes = S.get(
    API + "getHash",
    params={"resourceId": rid, "issueId": iid, "start": 1, "end": 3, "resourceType": 1},
    timeout=15,
).json()["data"]
print(f"  hashes(前3): {[(h.get('page'), str(h.get('hash'))[:16]) for h in hashes]}")
h0 = hashes[0]["hash"]
for node in ["8", raw.get("jpg"), raw.get("webp")]:
    if not node:
        continue
    url = f"http://img1-qn.bookan.com.cn/jpage{node}/{rid}/{rid}-{iid}/{h0}_big.jpg"
    try:
        rr = S.head(url, timeout=15, allow_redirects=True)
        print(
            f"  jpage{node}: HTTP {rr.status_code}  len={rr.headers.get('Content-Length')}  ct={rr.headers.get('Content-Type')}"
        )
    except Exception as e:
        print(f"  jpage{node}: ERR {e}")

# 真实下载一张验证是 JPEG
url = f"http://img1-qn.bookan.com.cn/jpage{raw.get('jpg') or 8}/{rid}/{rid}-{iid}/{h0}_big.jpg"
b = S.get(url, timeout=30).content
print(f"  download {len(b)} bytes, magic={b[:4].hex()} (ffd8ff=JPEG)")

# ── 3. catalogInfo 目录 ───────────────────────────────────
line("3) catalogInfo 目录（多 instanceId 尝试）")
for inst in [13790, 12696]:
    for key in ["categoryId", "issueId", "resourceId"]:
        for val_name, val in [("iid", iid), ("rid", rid)]:
            try:
                r = S.get(
                    API + "catalogInfo",
                    params={"instanceId": inst, "resourceType": 1, key: val},
                    timeout=15,
                )
                j = r.json()
                data = j.get("data")
                n = len(data) if isinstance(data, list) else (0 if not data else -1)
                print(f"  inst={inst} {key}={val_name} -> code={j.get('code')} n={n}")
                if isinstance(data, list) and data:
                    print(f"      sample: {data[0]}")
            except Exception as e:
                print(f"  inst={inst} {key}={val_name} -> ERR {e}")

# ── 4. EPUB / 文本版 ──────────────────────────────────────
line("4) 文本版 / EPUB 路径探测")
for base in [f"{EPUB}/epub2/{rid}/{rid}-{iid}"]:
    for suffix in [iid, f"{iid}_1", f"{iid}_0"]:
        u = f"{base}/{suffix}/directories.json"
        try:
            r = S.get(u, timeout=15)
            print(f"  {u}\n     -> {r.status_code} {r.text[:120]!r}")
        except Exception as e:
            print(f"  {u}\n     -> ERR {e}")

# txt 字段含义探测
print(
    f"\n  raw['txt']={raw.get('txt')!r}  raw['html']={raw.get('html')!r}  raw['pdf']={raw.get('pdf')!r}"
)

line("DONE")
