# BookanTool 安卓打包配置（pywebview Android 后端）
# 构建环境：Linux / WSL2（buildozer 不支持 Windows 原生运行）
#   pip install buildozer pywebview cryptography
#   buildozer android debug          # 输出 bin/*.apk
#
# 原理：pywebview 6.x 在 p4a 环境自动启用 Android 后端
# （guilib 检测 sys.getandroidapilevel，无需 Kivy），
# js_api / evaluate_js 通道与桌面完全一致 —— bridge.py 与前端零改动。
# 服务端走 pywebview 内置 HTTP（http://127.0.0.1:端口），
# 明文放行依赖 manifest 的 usesCleartextTraffic="true"：
#   新版 p4a webview 模板已内置该属性；若 dist 是旧模板创建的，
#   需给 dists/<app>/templates/AndroidManifest.tmpl.xml 的 <application> 补上这一行。
# cryptography 已移除：armv7 交叉编译 LONG_BIT 不匹配，无法构建。

[app]
title = BookanTool
package.name = bookantool
package.domain = com.mumeione
source.dir = .
version = 1.0.1

# 前端静态资源与后端 Python 一并打包
source.include_exts = py,png,jpg,css,js,html

# 排除桌面专属内容（图标生成器 / 截图 / 调试探针 / ruff / 构建产物）
source.exclude_dirs = devtools,.tools,build,dist,bin,.git,__pycache__,assets/screenshots
source.exclude_exts = spec,md,ico,bat,log

# 依赖：
#   pywebview 的 pywebview-android.jar 经 android.add_jars 打入 APK
#   bottle/proxy_tools/typing_extensions 为 pywebview 内置 HTTP 服务的依赖
#   android 是 p4a 官方 recipe（android.activity / android.runnable 等），
#   pywebview Android 后端启动时必需 —— 不写会闪退 ModuleNotFoundError: 'android'
#   urllib3/idna/charset_normalizer/certifi 为 requests 的传递依赖，p4a 不做依赖解析，需显式列出
#   pyjnius 是 Android 后端必需
requirements = python3,android,pywebview,pyjnius,bottle,proxy_tools,typing_extensions,requests,urllib3,idna,charset_normalizer,certifi,pillow,img2pdf,pypdf

# 图标与启动画面
icon.filename = %(source.dir)s/assets/icon_preview.png
presplash.filename = %(source.dir)s/assets/presplash.png
android.presplash_color = #FFFFFF

orientation = portrait
fullscreen = 0

# 权限：
#   INTERNET 访问博看接口；WRITE_EXTERNAL_STORAGE 供 Android 9- 写公共下载
#   MANAGE_EXTERNAL_STORAGE 备用（用户可在系统设置手动开启，应用运行时不申请、
#   无任何权限弹窗 —— 下载目录免权限直写 Download/bookantool）
android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,MANAGE_EXTERNAL_STORAGE
android.api = 34
android.minapi = 24
# 仅 arm64：2025 年起的设备全部原生 arm64，32 位兼容层已无必要；
# 单架构可砍掉约 40% APK 体积（armeabi-v7a 的全部 .so 副本）
android.archs = arm64-v8a
# release 产物用 apk（默认 aab；GitHub 发布直接分发 apk）
android.release_artifact = apk
android.accept_sdk_license = True
# 去掉默认 ActionBar（跟随 pywebview 官方 todos 示例）
android.apptheme = @android:style/Theme.Material.NoActionBar
# pywebview 内置 HTTP 服务走 http://127.0.0.1:端口，必须放行明文流量，
# 否则 WebView 报 ERR_CLEARTEXT_NOT_PERMITTED 白屏（新 dist 模板不带此属性）。
# 注意：buildozer 的该字段是「文件路径」，文件内容才会注入 <application>
android.extra_manifest_application_arguments = %(source.dir)s/android/manifest_extra.txt

# pywebview-android.jar：随 pywebview 6.x 分发，已复制到 android/ 目录锁定版本。
# 升级 pywebview 后可用以下命令重新获取并覆盖：
#   python -c "from webview import util; print(util.android_jar_path())"
android.add_jars = %(source.dir)s/android/pywebview-android.jar

# Kivy 型图形 bootstrap（pywebview Android 后端在其上直接 setContentView）
p4a.bootstrap = sdl2

[buildozer]
log_level = 2
warn_on_root = 1
