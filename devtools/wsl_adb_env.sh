#!/usr/bin/env bash
# adb helper: source this file, then use $ADB
export ANDROID_ADB_SERVER_PORT=${ANDROID_ADB_SERVER_PORT:-5039}
ADB="$HOME/.buildozer/android/platform/android-sdk/platform-tools/adb"
DEV="127.0.0.1:16384"
"$ADB" start-server >/dev/null 2>&1
"$ADB" connect "$DEV" >/dev/null 2>&1
