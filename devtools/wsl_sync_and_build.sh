#!/usr/bin/env bash
# 同步 Windows 仓库改动到 WSL 构建目录并重建 APK
# 用法: wsl bash wsl_sync_and_build.sh [debug|release]   （默认 debug）
set -u
BUILD_TYPE="${1:-debug}"
SRC="/mnt/d/Documents/pythoncharm/BookanTool V2.0"
DST="$HOME/bookantool"

cp "$SRC/main.py" "$DST/main.py"
cp "$SRC/buildozer.spec" "$DST/buildozer.spec"
# backend 整目录同步：逐文件列举易漏（曾漏过 batch.py / pdf_pipeline.py 导致
# 新修复没进 APK），改为全量覆盖，代价可忽略
cp "$SRC/backend/"*.py "$DST/backend/"
cp "$SRC/frontend/index.html" "$DST/frontend/index.html"
cp "$SRC/frontend/app.js" "$DST/frontend/app.js"
cp "$SRC/frontend/style.css" "$DST/frontend/style.css"
mkdir -p "$DST/assets"
cp "$SRC/assets/presplash.png" "$DST/assets/presplash.png"
# manifest 注入参数文件（extra_manifest_application_arguments 指向它）
mkdir -p "$DST/android"
cp "$SRC/android/manifest_extra.txt" "$DST/android/manifest_extra.txt"

cd "$DST"
# 网络：不走代理（Clash 可能未开）。GitHub 源码已预克隆/缓存，
# PyPI 用 TUNA 镜像（~/.config/pip/pip.conf）直连即可
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
source "$HOME/buildozer-env/bin/activate"

if [[ "$BUILD_TYPE" == "release" ]]; then
  # release 签名走 p4a 约定的 P4A_RELEASE_* 环境变量。
  # keystore 与密码文件已 gitignore（防冒签），仅存在于生成它的机器——
  # 换机构建 release 前需手动补齐这两个文件
  PROP="$SRC/android/release_keystore.properties"
  if [[ ! -f "$PROP" || ! -f "$SRC/android/bookantool-release.keystore" ]]; then
    echo "缺少 release 密钥文件（android/bookantool-release.keystore / release_keystore.properties）" >&2
    exit 1
  fi
  cp "$SRC/android/bookantool-release.keystore" "$DST/android/"
  export P4A_RELEASE_KEYSTORE="$DST/android/bookantool-release.keystore"
  export P4A_RELEASE_KEYALIAS="$(grep -oP '^alias=\K.*' "$PROP")"
  export P4A_RELEASE_KEYSTORE_PASSWD="$(grep -oP '^store\.pass=\K.*' "$PROP")"
  export P4A_RELEASE_KEYALIAS_PASSWD="$(grep -oP '^key\.pass=\K.*' "$PROP")"
fi

buildozer android "$BUILD_TYPE" 2>&1 | tee build.log | tail -3
echo "BUILD_EXIT=${PIPESTATUS[0]}"

# 构建产物自动回传 Windows bin/（否则 APK 一直留在 WSL，Windows 侧找不到安装包）
bash "$SRC/devtools/wsl_copy_apk.sh" 2>&1 | tail -1
