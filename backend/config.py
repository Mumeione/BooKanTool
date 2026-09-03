"""
全局配置：把博看 API 中所有写死的 instanceId / 路径模板汇总在此处集中维护。
注意：博看至少有两套 instance —— 资源信息走 12696，章节目录走 13790。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import threading
import time

# ────────────── 应用信息 ──────────────
# 版本号双轨：安卓独立从 1.0.0 起版，不与 PC 同步
APP_VERSION = "1.0.1" if "ANDROID_ARGUMENT" in os.environ else "2.1.0"

# ────────────── API 端点常量 ──────────────
API_BASE = "https://api.bookan.com.cn"
EPUB_BASE = "http://epub.bookan.com.cn"

# 不同业务使用不同的 instance_id
INSTANCE_ID_RESOURCE = 12696  # issueInfoList / getHash
INSTANCE_ID_CATALOG = 13790  # catalogInfo  ← 注意这里的不同

# 默认资源类型：1=杂志 3=书籍
RESOURCE_TYPE_MAGAZINE = 1
RESOURCE_TYPE_BOOK = 3

# HTTP 请求参数
HTTP_TIMEOUT = 30  # 单次请求超时 (秒)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

# ────────────── 行为开关 ──────────────
# jpage 探测范围（仅在 issueInfoList 未返回 jpg/webp 字段时启用）
JPAGE_PROBE_RANGE = range(1, 11)  # 探测 jpage1..jpage10
JPAGE_DEFAULT = 8  # 兜底节点号（与官方 Web 端一致）

# 图片尺寸后缀：实测仅 big / small 可用（mid、webp 均 404）
IMAGE_SIZE_FULL = "big"  # 正文页，约 200KB/页
IMAGE_SIZE_THUMB = "small"  # 封面缩略图，约 20KB

# 可选压缩三档：原图 1080x1466 约 200~840KB/页，182 页可达 100MB+。
# 重编码 JPEG + 限制最大宽度（等比缩小），体积约降至 60% / 40% / 25%。
# hint 供前端悬停提示展示。
COMPRESSION_LEVELS = {
    1: {"label": "轻度", "quality": 85, "max_width": 1600, "hint": "体积约 60%"},
    2: {"label": "中度", "quality": 75, "max_width": 1280, "hint": "体积约 40%"},
    3: {"label": "高度", "quality": 60, "max_width": 1000, "hint": "体积约 25%"},
}

# 并发下载图片线程数
IMAGE_DOWNLOAD_THREADS = 4

# 图片下载重试次数
IMAGE_MAX_RETRIES = 3

# 临时目录模板
TEMP_DIR_PREFIX = "bookan_tmp_"

# 输出文件名字符清理（路径分隔符会导致保存失败）
INVALID_PATH_CHARS = '/\\:*?"<>|'


def sanitize_filename(name: str) -> str:
    """替换 /\\:*?\"<>| 等路径不安全字符为下划线。"""
    import re

    return re.sub(f"[{re.escape(INVALID_PATH_CHARS)}]", "_", name).strip(".")


# ────────────── 应用配置持久化（用户使用习惯） ──────────────
# 位置：~/BookanTool/config.json —— 与输出目录、日志放在一起，便于用户查找
# 安卓端：应用私有目录（p4a 注入的环境变量），无需存储权限


def is_android() -> bool:
    """是否运行在 p4a 安卓环境（ANDROID_ARGUMENT 由 python-for-android 注入）。"""
    return "ANDROID_ARGUMENT" in os.environ


def android_base_dir() -> str:
    """安卓应用私有文件目录；环境变量缺失时退回当前目录（本地联调用）。"""
    return os.environ.get("ANDROID_APP_PATH") or os.environ.get("ANDROID_PRIVATE") or os.getcwd()


def android_external_files_dir() -> str | None:
    """应用外部专属目录 /storage/emulated/0/Android/data/<pkg>/files（永可写，
    但 Android 11+ 文件管理器不可浏览）。不碰 pyjnius：从 p4a 注入的
    ANDROID_APP_PATH 推导包名。"""
    import re

    m = re.search(
        r"/data/(?:user/\d+/|data/)([A-Za-z0-9_.]+)", os.environ.get("ANDROID_APP_PATH", "")
    )
    return f"/storage/emulated/0/Android/data/{m.group(1)}/files" if m else None


def place_output_file(src: str, final_path: str, on_log=None) -> str:
    """把私有临时目录里的成品文件落位到最终输出路径，返回实际路径。

    Android 11+ 分区存储约束（FUSE 直写规则）：
      • 新建 .pdf/.epub 等已知媒体类型文件 —— 免权限可写
      • 目标文件已存在且归属旧安装身份（重装/清数据后重下同名书刊）—— EACCES
    三级落位：覆盖直写 → 时间戳另存 → 应用外部专属目录兜底。
    PC 端目标文件被占用（PDF 阅读器开着）时同样走另存，行为一致。
    """
    log = on_log or (lambda m: None)
    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(final_path), exist_ok=True)

    try:
        shutil.copyfile(src, final_path)
        return final_path
    except PermissionError:
        pass

    stem, ext = os.path.splitext(final_path)
    alt = f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}{ext}"
    try:
        shutil.copyfile(src, alt)
        log(f"  原文件被系统占用，已另存为: {os.path.basename(alt)}")
        return alt
    except PermissionError:
        pass

    if is_android():
        ext_dir = android_external_files_dir() or android_base_dir()
        fb_dir = os.path.join(ext_dir, "downloads")
    else:
        # 桌面端两级都失败说明磁盘/权限有更深问题，如实抛出
        raise PermissionError(f"无法写入 {final_path}（文件被占用或磁盘不可写）")
    os.makedirs(fb_dir, exist_ok=True)
    dest = os.path.join(fb_dir, os.path.basename(alt))
    shutil.copyfile(src, dest)
    log(f"  公共下载目录不可写（存储权限受限），已保存到应用专属目录: {dest}")
    return dest


APP_DATA_DIR = (
    os.path.join(android_base_dir(), "BookanTool")
    if is_android()
    else os.path.join(os.path.expanduser("~"), "BookanTool")
)
CONFIG_PATH = os.path.join(APP_DATA_DIR, "config.json")

# 允许写入配置文件的选项键（前端表单使用习惯），其余键一律忽略
_ALLOWED_KEYS = {
    "output_dir",
    "output_format",
    "add_bookmarks",
    "compress_images",
    "compress_level",
}

_config_lock = threading.Lock()


def _default_config() -> dict:
    return {
        "output_dir": "",
        "output_format": "auto",
        "add_bookmarks": True,
        "compress_images": False,
        "compress_level": 1,
        "window": {},  # {x, y, width, height}，由 main.py 在关闭时写入
    }


def load_config() -> dict:
    """读取配置文件；缺失/损坏时返回默认值（不抛异常，保证启动流程）。"""
    cfg = _default_config()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            for k in _ALLOWED_KEYS:
                if k in stored:
                    cfg[k] = stored[k]
            if isinstance(stored.get("window"), dict):
                cfg["window"] = stored["window"]
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    """原子写配置文件（先写临时文件再替换，避免半截 JSON）。"""
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with _config_lock:
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG_PATH)
        except OSError:
            with contextlib.suppress(OSError):
                os.remove(tmp)


def update_config(patch: dict) -> dict:
    """合并写入：只接受白名单键 + window 子对象，返回合并后的完整配置。"""
    cfg = load_config()
    for k in _ALLOWED_KEYS:
        if k in patch:
            cfg[k] = patch[k]
    if isinstance(patch.get("window"), dict):
        cfg["window"] = patch["window"]
    save_config(cfg)
    return cfg
