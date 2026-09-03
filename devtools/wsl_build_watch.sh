#!/usr/bin/env bash
# 等待 buildozer 构建结束，成功则自动安装到平板并输出验证信息
set -u
export ANDROID_ADB_SERVER_PORT=5037
ADB="adb"
DEV="HA2BN1JQ"

while pgrep -f "buildozer android debug" >/dev/null 2>&1; do sleep 60; done

echo "=== build finished at $(date +%H:%M:%S) ==="
tail -3 ~/bookantool/build.log
APK=$(ls -t ~/bookantool/bin/*.apk 2>/dev/null | head -1)
if [ -n "$APK" ]; then
  echo "=== APK: $(basename "$APK") ($(du -m "$APK" | cut -f1) MB) ==="
  echo "=== install to tablet ==="
  "$ADB" -s "$DEV" install -r "$APK"
  "$ADB" -s "$DEV" logcat -c 2>/dev/null
  "$ADB" -s "$DEV" shell am start -n com.mumeione.bookantool/org.kivy.android.PythonActivity >/dev/null 2>&1
  sleep 8
  echo "pid=$($ADB -s "$DEV" shell pidof com.mumeione.bookantool 2>/dev/null)"
  "$ADB" -s "$DEV" logcat -d 2>/dev/null | grep -E "FATAL|Traceback|Fatal signal" | tail -5
else
  echo "=== NO APK — build failed ==="
  N=$(grep -n "Traceback (most recent call last)" ~/bookantool/build.log | tail -1 | cut -d: -f1)
  [ -n "$N" ] && sed -n "${N},$((N+35))p" ~/bookantool/build.log | tail -15
fi
