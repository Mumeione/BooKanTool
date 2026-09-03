"""检查 APK 内 arm64 .so 的 ELF LOAD 段对齐（Android 15 16KB 页要求 p_align>=16384）。"""
import struct
import sys
import zipfile

apk = sys.argv[1] if len(sys.argv) > 1 else "bin/bookantool-2.1.0-arm64-v8a_armeabi-v7a-debug.apk"
z = zipfile.ZipFile(apk)
sos = [n for n in z.namelist() if n.endswith(".so") and "arm64" in n]
print(f"{'so':40s} p_align  16KB-ok")
bad = []
for name in sorted(sos):
    data = z.read(name)
    if len(data) < 64 or data[:4] != b"\x7fELF":
        print(f"{name:40s} {'skip(not ELF, %dB)' % len(data):>14s}  -")
        continue
    # ELF64: e_phoff at 0x20, e_phentsize at 0x36, e_phnum at 0x38
    phoff = struct.unpack_from("<Q", data, 0x20)[0]
    phentsize = struct.unpack_from("<H", data, 0x36)[0]
    phnum = struct.unpack_from("<H", data, 0x38)[0]
    if phoff + phnum * phentsize > len(data):
        print(f"{name:40s} {'skip(bad phdr)':>14s}  -")
        continue
    align = 0
    for i in range(phnum):
        off = phoff + i * phentsize
        p_type = struct.unpack_from("<I", data, off)[0]
        if p_type == 1:  # PT_LOAD
            align = max(align, struct.unpack_from("<Q", data, off + 0x30)[0])
    ok = align >= 16384
    print(f"{name:40s} {align:6d}  {'YES' if ok else 'NO'}")
    if not ok:
        bad.append(name)
print()
print(f"BAD: {len(bad)}/{len(sos)}  (4KB-aligned .so will crash-dlopen on 16KB-page devices)")
