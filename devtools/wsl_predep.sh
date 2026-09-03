#!/usr/bin/env bash
# 预置 SDL2_image external 依赖（libjxl/libavif/dav1d）到构建目录，避免 git clone 断连
# 依赖源：~/bookantool/.externals 常驻目录（wsl_cleanup.sh 负责从老构建目录迁移至此）
set -u
APP="$HOME/bookantool"
SRC="$APP/.externals"
NEW="$APP/.buildozer/android/platform/build-arm64-v8a/build/bootstrap_builds/sdl2/jni/SDL2_image/external"

echo "== 源目录 $SRC =="
for d in libjxl libavif dav1d; do
  printf '  %s: %s 项\n' "$d" "$(ls -A "$SRC/$d" 2>/dev/null | wc -l)"
done

# 仅缺失或空目录才复制，已 populated 的跳过
for d in libjxl libavif dav1d; do
  if [ ! -d "$NEW/$d" ] || [ -z "$(ls -A "$NEW/$d" 2>/dev/null)" ]; then
    if [ -d "$SRC/$d" ] && [ -n "$(ls -A "$SRC/$d" 2>/dev/null)" ]; then
      rm -rf "$NEW/$d"
      cp -a "$SRC/$d" "$NEW/$d"
      echo "COPIED: $d"
    else
      echo "SKIP (源缺失): $d"
    fi
  else
    echo "OK (已 populated): $d"
  fi
done

echo "== final check =="
for d in libjxl libavif dav1d; do
  printf '%s: %s entries\n' "$d" "$(ls "$NEW/$d" 2>/dev/null | wc -l)"
done
ls "$NEW/libjxl/third_party" 2>/dev/null
