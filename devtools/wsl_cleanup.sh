#!/usr/bin/env bash
# 清理 WSL 构建残留：老双架构构建目录、临时目录、Windows 侧整目录拷贝的无用文件、旧 APK
# SDL2_image externals 先迁移到 .externals 常驻目录，保证 wsl_predep.sh 不再依赖可清理的构建目录
# 幂等：重复执行安全
set -u
APP="$HOME/bookantool"
OLD_BUILD="$APP/.buildozer/android/platform/build-arm64-v8a_armeabi-v7a"
EXT_SRC="$OLD_BUILD/build/bootstrap_builds/sdl2/jni/SDL2_image/external"
EXT_DST="$APP/.externals"
WIN_BIN="/mnt/d/Documents/pythoncharm/BookanTool V2.0/bin"

echo "== 1. externals 迁移（老构建目录 → .externals 常驻） =="
if [ -d "$EXT_SRC/libjxl" ] && [ -n "$(ls -A "$EXT_SRC/libjxl" 2>/dev/null)" ]; then
  mkdir -p "$EXT_DST"
  for d in libjxl libavif dav1d; do
    if [ ! -d "$EXT_DST/$d" ] || [ -z "$(ls -A "$EXT_DST/$d" 2>/dev/null)" ]; then
      rm -rf "$EXT_DST/$d"
      cp -a "$EXT_SRC/$d" "$EXT_DST/$d" && echo "  迁移: $d"
    else
      echo "  已存在: $d"
    fi
  done
else
  echo "  老目录无 externals（此前已迁移或本就缺失）"
fi
echo "  校验:"
for d in libjxl libavif dav1d; do
  printf '    %s: %s 项\n' "$d" "$(ls -A "$EXT_DST/$d" 2>/dev/null | wc -l)"
done

echo "== 2. 删除老双架构构建目录（约 3.9G） =="
if [ -d "$OLD_BUILD" ]; then
  ok=1
  for d in libjxl libavif dav1d; do
    [ -d "$EXT_DST/$d" ] && [ -n "$(ls -A "$EXT_DST/$d" 2>/dev/null)" ] || ok=0
  done
  if [ "$ok" = 1 ]; then
    rm -rf "$OLD_BUILD" && echo "  已删除: $OLD_BUILD"
  else
    echo "  externals 迁移不完整，保留老目录以便重试"
  fi
else
  echo "  不存在，跳过"
fi

echo "== 3. 删除根目录无用文件（初次整目录拷贝的副本 / 临时目录） =="
for f in tmp downloads dist build.log BookanTool.spec pyproject.toml README.md CHANGELOG.md LICENSE requirements.txt devtools; do
  if [ -e "$APP/$f" ]; then
    rm -rf "$APP/$f"
    echo "  清除: $f"
  fi
done
find "$APP" -maxdepth 3 -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null

echo "== 4. 删除旧版本 APK =="
ls "$APP/bin/"bookantool-2.1.0-*.apk 2>/dev/null && {
  rm -f "$APP/bin/"bookantool-2.1.0-*.apk
  echo "  旧 APK 已删"
}

echo "== 5. 回传最新 APK 到 Windows bin/ =="
mkdir -p "$WIN_BIN"
latest=$(ls -t "$APP/bin/"*.apk 2>/dev/null | head -1)
if [ -n "$latest" ]; then
  cp -f "$latest" "$WIN_BIN/" && echo "  已回传: $(basename "$latest")"
else
  echo "  无 APK 可回传"
fi

echo "== 6. 清理后占用 =="
du -sh "$APP/.buildozer" "$APP/bin" "$EXT_DST" "$APP" 2>/dev/null
