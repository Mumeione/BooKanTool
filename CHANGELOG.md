# 更新日志 / Changelog

所有显著变更记录在本文件。
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)；版本号唯一来源：`backend/config.py` 的 `APP_VERSION`——PC 端 2.x 与安卓端 1.x 各自独立，按 `ANDROID_ARGUMENT` 环境变量区分。

## [1.0.1] Android - 2026-09-03

### 修复

- **多任务并发下载闪退**：js_api 回传路径（pywebview 安卓后端）每次调用都在独立线程运行时创建 JNI 代理，前端轮询与任务启动叠加时并发创建代理存在竞态，可触发原生崩溃。现将全部 `evaluate_js` 回传引流到单一常驻 worker 线程串行执行（JNI 代理创建完全互斥），9 本书连续下载实测无闪退
- **回前台事件派发收紧**：`on_resume` 生命周期回调本就运行在 UI 线程，改为直接调用 `evaluateJavascript` 派发 `app_resumed`，省去一次冗余 JNI 代理创建

## [1.0.0] Android - 2026-09-03

首个安卓正式版（签名 release 包 `bookantool-1.0.0-arm64-v8a-release.apk`）。与 PC 端 2.1.0 共享同一套源码与前后端桥接，版本号独立管理。

### 新增（安卓专属）

- **原生打包**：buildozer/p4a + pywebview Android 后端，双端共享 `backend/bridge.py` 与前端三屏 UI，业务代码零分叉；安卓差异集中在 `backend/android_compat.py`（路径 / 权限 / 文件管理器）与 `main.py` 生命周期补丁
- **存储适配**：默认下载目录固定为公共 `Download/bookantool`（免权限直写，不可写时回退应用专属目录）；首次启动自动申请存储权限；结果页经系统文件管理器查看产物（Android 11+ 系统限制无法浏览 Android/data，故不提供应用内打开输出目录）
- **移动端 UI**：全面屏 safe-area 适配（`viewport-fit=cover` + `env(safe-area-inset-*)`）；移除页脚版本号；开屏改为白底居中大图 App 图标（`assets/make_presplash.py` 将 `icon_preview.png` 合成到 1600×2560 白底画布，fitCenter 任意分辨率居中）；结果页仅保留「返回主界面」按钮，跳文件管理器返回后回主界面
- **设置页精简**：仅保留书签开关、关于、运行日志与使用说明小弹窗

### 修复

- **强杀重启 / 切后台重开卡「正在握手后端」**：启动早期的一次暂停事件先于生命周期钩子注册到达，JS 定时器被全局冻结后无人解冻，页面轮询与自动重载全部死等。修复：废除全局定时器冻结（on_pause 仅实例级暂停）；每次页面加载完成后自动确保 WebView 处于运行状态；回前台经 DOM 事件路径即时补发握手——事件派发不依赖 JS 定时器，冻结态也能自愈
- **PDF 合成 94% 失败（OOM）**：原流程 `img2pdf.convert()` 在内存生成整份 PDF 字节串（百 MB 级），叠加 pypdf 解析/克隆后移动端必 OOM。改为 `outputstream` 流式写临时文件，pypdf 从磁盘惰性读取补书签/元数据后落盘，内存峰值从「PDF 体积 × 3」降到十 MB 级
- **PDF 合成 90% 失败（errno 13）**：流式合成的 `.raw.tmp` 中间文件落在公共 Download 目录——Android 11+ 分区存储下应用只能在公共目录创建已知媒体类型扩展名的文件（`.pdf` 合法、`.tmp` 被拒 → EACCES）。中间文件改写任务私有临时目录（免权限、随任务自动清理），仅最终 `.pdf` 落公共目录
- **PDF/EPUB 保存失败（errno 13，落位阶段）**：重装/清数据后重下同名书刊，公共目录同名旧文件归属旧安装身份，`open('wb')` 被分区存储拒绝。成品统一先写私有临时目录，再经 `place_output_file` 三级落位（覆盖直写 → 时间戳另存 → 应用专属目录兜底），EPUB 流水线同步接入
- **切换应用卡顿**：切后台后 JS 轮询持续跑，弱 CPU 平板拖累切换动画。事件轮询由 `document.hidden` 守卫跳过后台 tick，1.5s 间隔下后台开销可忽略；on_pause 保留实例级暂停降低渲染开销
- **pyjnius 使用约束**：非主线程调 pyjnius 会真机原生崩溃，Java 回调接口在 p4a python3.14 下不稳定。pyjnius 仅在主线程调用（打开文件管理器等），进度事件一律走 js_api 通道回传
- **lint 修正**：冗余引号注解（UP037）、`try/except/pass` → `contextlib.suppress`（SIM105）

### 变更（性能）

- **下载与压缩解耦**：压缩原在下载线程内执行，PIL 编码抢占 CPU 拖慢下载。现下载线程只管下载；每页完成后压缩任务投递到独立小线程池（worker 上限 2，移动端弱 CPU 下更多并发只会与下载抢时间片）后台消化，下载收束后等压缩收尾再合成
- **压缩等待可视化**：下载完成后进度条文字显示「正在压缩中（X/Y）…」并记录剩余页数日志；下载期间压缩回调不驱动进度条，避免百分比先跳 90% 再回跳的抽搐观感

### 打包要点（注意）

- **架构锁定 arm64-v8a**：APK 34MB → 18MB。改 archs 后必须删除 `.buildozer/android/platform/build-*/dists/bookantool` 重建 dist；新 build 目录需预放置 SDL2_image externals（libavif/dav1d/libjxl）——常驻 `~/bookantool/.externals`，`devtools/wsl_predep.sh` 自动布放
- **明文 HTTP**：pywebview 内置服务器走 `http://127.0.0.1:port`，需 `android:usesCleartextTraffic="true"`。buildozer.spec 通过 `android.extra_manifest_application_arguments` 指向 **文件** `android/manifest_extra.txt` 传入 p4a——勿手工改 p4a 模板，重打包会丢；该文件必须纳入版本控制
- **构建环境**：WSL2（buildozer 不支持原生 Windows）；PyPI / Rust 走 TUNA 镜像；cryptography 与 armv7 LONG_BIT 冲突已从依赖移除（明文 HTTP 无需 ssl）
- **release 签名**：`devtools/wsl_sync_and_build.sh release`（缺省参数为 debug）构建签名包，签名经 p4a 标准环境变量（`P4A_RELEASE_*`）传入；密钥库与密码文件已 gitignore 不入库，换机器构建需手动补齐并**务必在仓库外另行备份，丢失后无法覆盖安装更新**
- **一键脚本**：`devtools/wsl_sync_and_build.sh [debug|release]`（同步源码 + spec + presplash → 构建 → 自动回传 APK 到 Windows `bin/`）；`devtools/wsl_cleanup.sh` 清理 WSL 构建残留（老双架构构建目录、无用副本、旧 APK，幂等可重复执行）；真机装调 `devtools/tab_*.ps1`
- **前端缓存**：样式不更新时 `adb shell pm clear com.mumeione.bookantool` 清 WebView 缓存


## [2.1.0] - 2026-09-02

输入解析、元数据与下载体验的改进版本。

### 新增

- **移动端分享链接支持**：解析 App 分享链接的 fragment 格式 `#/dt/{type}/{issueId}`（如 `https://wk6.bookan.com.cn/?id=130#/dt/1/310823891`），查询参数中的站点 ID 不会被误判为书刊 ID
- **PDF/EPUB 元数据双写（Calibre 兼容）**：PDF 同时写入 Info 字典与 XMP 元数据流——`dc:publisher`→出版社、`dc:date`→出版日期、`dc:identifier`→ISBN、`dc:description`→评论、`dc:subject`→标签；Calibre 导入可正确识别
  - 图书（type=3）：PDF 命名改为「书名+作者」，元数据写入出版社 / 出版日期 / ISBN（自动去除接口变体号后缀并做 10/13 位校验）
  - 期刊（type=1）：写入 ISSN（Calibre identifier 只认 isbn/url/doi，ISSN 放入标签）

### 变更

- **输入规则收紧**：仅支持完整书刊链接（官网 URL / 移动端分享链接 / 含 path 链接），取消纯 ID 与 `mag:` `book:` `id:` 前缀缩写输入；UI 文案同步更新
- **下载进度重新分配**：下载阶段占 2%→90%（原 5%→65%），合成/写盘压缩进最后 10%，进度条与真实下载速度一致
- **README**：三张界面截图改为表格横向排列

### 修复

- **临时缓存自愈**：批量任务开始前清扫历史遗留的 `bookan_tmp_*` 目录（Windows 文件占用 / 进程崩溃导致的残留），单任务临时目录容错清理；合成 PDF 时提前释放百 MB 级字节串，多本连下不再堆积缓存与内存

## [2.0.0] - 2026-09-02（最终发布版）

界面与打包全面打磨版本，并以重构版覆盖发布至 GitHub 仓库。

### 变更

- **界面**：图标重绘（浅蓝紫渐变 + 三本书重叠 + 逆时针进度弧），主题色统一浅蓝/浅紫/白，标题使用导出 logo
- **布局**：开始下载按钮加宽居中，压缩选项条通栏居中，进度条居中、详情文字加粗增大
- **功能调整**：压缩改为三档位滑块；移除拆分双跨页选项；书签开关移入设置菜单；输出格式滑块不持久化，每次启动固定「自动」
- **新增**：设置 → 关于弹窗（版本 / 作者 / 开源协议 GPL-3.0 / 项目地址）
- **文案**：「输出目录」统一改为「下载目录」，页脚改为「仅供技术研究 · 尊重版权 · 禁止商用」，状态栏就绪提示改为「准备就绪」
- **打包**：单文件 exe 瘦身 28.3MB → 17.6MB（排除 pikepdf / lxml / PIL._avif）
- **开源**：补充 GPL-3.0 LICENSE 文件

## [2.0.0]preview - 2026-09-01

首个版本。博看书刊下载与导出工具：把杂志 / 书籍导出为图片版 PDF 或官方文本版 EPUB。

### 新增

- **导出流水线**
  - 图片版 PDF：jpage CDN 节点自动探测，4 线程并发下载 + 3 次重试，img2pdf 合成 + pypdf 写元数据
  - PDF 两级书签：`catalogInfo` 栏目 → 文章两级 outline，印刷页码 → 物理页号映射定位
  - 官方 EPUB 直下：`getHash(start=0)` 取版本号 → `epub2` 成品整本下载（约 5~13MB）
  - 自动模式：EPUB 下载失败（含 424/403 等）自动回退图片版 PDF
  - 可选项：宽跨页拆分、图片压缩（JPEG 重编码，实测 182 页 106MB → 70MB）
- **批量与全年下载**
  - URL 列表批量（换行分隔），单条失败不中断
  - 下载全年（type=1 杂志）：以 issueID ±1 连续推算全年各期，按年份边界自动停止
- **桌面 GUI（PyWebView + 小窗口三屏设计）**
  - 主界面（链接输入 + 导出选项 + 开始）→ 进度界面（大百分比 + 取消）→ 完成界面（结果卡片）
  - 导出格式滑块分段控件（自动 / PDF / EPUB）；取消需二次确认
  - 输出目录与日志入口收纳进设置二级菜单
  - 内容缩放自适应窗口（420×680 默认），窗口位置尺寸记忆
- **配置持久化**：`~/BookanTool/config.json` 原子写入，格式滑块外的选项即时保存
- **图标**：浅蓝紫斜向渐变底 + 合上的书刊（封面印 "bk"）+ 双色下载进度环（`assets/make_icon.py` 生成多尺寸 ico）
- **打包**：PyInstaller 单文件 exe（约 25MB），无控制台，内置 WebView2 缺失检测与 `--selftest` 功能自检

### 兼容性

- Windows 10/11 64 位 + Edge WebView2 Runtime（一般自带）
- 源码运行：`pip install -r requirements.txt && python main.py`

## 备注：1.x（原版 BooKanTool）的 ADB 方案

原仓库 1.x 版本（2025-03 ~ 2025-04）不直连博看接口，而是借助 **ADB** 从安卓模拟器中提取书刊图片：

1. 通过 ADB 连接安卓模拟器（MUMU / 雷电 / 蓝叠 / 夜神 / 逍遥等，默认端口 5555）；
2. 从模拟器路径 `/sdcard/Android/data/cn.com.bookan/files/bookan/magazine` 复制书刊图片到本地；
3. 本地按页序重命名、排序后合成 PDF。

该方案依赖模拟器内已安装并登录的博看 App。2.0 起改为匿名访客身份直接访问博看公开 API，无需模拟器与 ADB。
