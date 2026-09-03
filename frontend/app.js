// BookanTool 前端逻辑（小窗口三屏式 UI）
//  - 与 Python 桥接层通过 window.pywebview.api 调用方法
//  - 监听 window.__bookan_dispatch(event, data) 接收后端推送
//  - 屏幕流转：主界面 → 进度界面 → 下载完成界面

(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);

    // ───────────── 事件总线 ─────────────
    const app = window.app = {
        listeners: {},
        on(ev, cb) {
            (this.listeners[ev] = this.listeners[ev] || []).push(cb);
        },
        dispatch(ev, data) {
            (this.listeners[ev] || []).forEach(cb => {
                try { cb(data); } catch (e) { console.error(e); }
            });
        }
    };

    // ───────────── 缩放：固定最小缩放 + 适当范围 ─────────────
    // 以 420px 设计宽度为基准：窗口变宽内容等比放大、变窄等比缩小，
    // 并钳制在 [MIN_ZOOM, MAX_ZOOM] 区间，保证任何窗口尺寸下内容清晰可读。
    const DESIGN_WIDTH = 420;
    const MIN_ZOOM = 0.9;
    const MAX_ZOOM = 1.5;
    function applyZoom() {
        const z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, window.innerWidth / DESIGN_WIDTH));
        document.body.style.zoom = z;
    }
    window.addEventListener('resize', applyZoom);
    applyZoom();

    // ───────────── 状态 ─────────────
    let currentTaskId = null;
    let cancelRequested = false;
    let taskRunning = false;
    let currentFormat = 'auto';
    let backendConnected = false;

    // ───────────── 启动握手（带超时看门狗）─────────────
    // 杀后台再重开时，Android 可能不触发完整 onPageFinished，pywebview 的
    // JS 桥（window.pywebview.api）未重新注入，health() 永不返回 → 一直卡在
    // 「正在握手后端」。用一次性 reload 恢复：整页重载会再次触发 onPageFinished
    // → pywebview 重新注入桥，把“死等”变成“自动恢复”。
    const HAND_SHAKE_TIMEOUT = 7000; // 7s 未握手成功视为桥缺失
    const MAX_AUTO_RELOAD = 3;       // 自动重载上限，超出即判定后端异常
    let hsDone = false;
    let hsDeadline = 0;
    // reload 次数存 sessionStorage：跨 reload 累计，避免后端真挂了时无限轮询重载
    let hsReloadCount = Number(sessionStorage.getItem('bookan_hs_reloads') || 0);

    function pingBackend() {
        if (hsDone) return;
        if (Date.now() > hsDeadline) { onHandshakeTimeout(); return; }
        if (!window.pywebview || !window.pywebview.api) {
            setTimeout(pingBackend, 200);
            return;
        }
        window.pywebview.api.health().then(h => {
            if (!hsDone) {
                if (h && h.ok) {
                    hsDone = true;
                    sessionStorage.removeItem('bookan_hs_reloads'); // 握手成功，清除累计重载数
                    backendConnected = true;
                    setStatus('ok', '准备就绪');
                    $('btn-start').disabled = false;
                } else {
                    maybeRetryHandshake();
                }
            }
        }).catch(() => { maybeRetryHandshake(); });
    }

    function maybeRetryHandshake() {
        if (hsDone) return;
        if (Date.now() > hsDeadline) { onHandshakeTimeout(); return; }
        setTimeout(pingBackend, 500);
    }

    function onHandshakeTimeout() {
        if (hsDone) return;
        if (hsReloadCount < MAX_AUTO_RELOAD) {
            hsReloadCount++;
            sessionStorage.setItem('bookan_hs_reloads', String(hsReloadCount));
            log('warn', '后端握手超时，正在重载页面恢复连接…');
            location.reload();
        } else {
            hsDone = true;
            setStatus('err', '后端连接失败，请重启应用');
            log('err', '连续多次握手失败，请重启应用后重试');
        }
    }

    hsDeadline = Date.now() + HAND_SHAKE_TIMEOUT;
    pingBackend();

    // ───────────── 回前台自愈（事件路径，不依赖 setTimeout）─────────────
    // 全局 timers 若被冻结，setTimeout 轮询 / 超时 reload 全部死等；但
    // DOM 事件与微任务仍会派发——回前台（visibilitychange / 后端在
    // on_resume 里派发的 app_resumed）时立即补发一次握手，冻结态也能自愈。
    function recheckHandshake() {
        if (document.hidden || hsDone) return;
        hsDeadline = Date.now() + HAND_SHAKE_TIMEOUT;
        pingBackend();
    }
    document.addEventListener('visibilitychange', recheckHandshake);
    app.on('app_resumed', recheckHandshake);

    // ───────────── UI 工具 ─────────────
    function setStatus(kind, text) {
        const dot = document.querySelector('#header-status .dot');
        dot.classList.remove('idle', 'ok', 'warn', 'err');
        dot.classList.add(kind);
        $('connection').textContent = text;
    }

    function log(level, msg) {
        const box = $('log-box');
        const line = document.createElement('div');
        line.className = 'log-line ' + (level || 'info');
        const ts = new Date().toLocaleTimeString();
        line.textContent = `[${ts}] ${msg}`;
        box.appendChild(line);
        box.scrollTop = box.scrollHeight;
    }

    function showScreen(name) {
        ['main', 'progress', 'done'].forEach(s => {
            $('screen-' + s).classList.toggle('active', s === name);
        });
    }

    // 回到主界面时复位右上角状态，避免"任务完成"一直残留
    function goMain() {
        showScreen('main');
        setStatus(backendConnected ? 'ok' : 'idle',
                      backendConnected ? '准备就绪' : '正在握手后端...');
    }

    function escapeHTML(s) {
        return String(s).replace(/[&<>"']/g, ch => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
    }

    function debounce(fn, ms) {
        let t;
        return function (...a) { clearTimeout(t); t = setTimeout(() => fn.apply(this, a), ms); };
    }

    // ───────────── 输入行管理（右侧仅 ×，底部 + 添加一行，超 3 行滚动） ─────────────
    function buildRowGroup() {
        const group = document.createElement('div');
        group.className = 'row-group';
        group.innerHTML = `
            <div class="input-row">
                <input type="text" spellcheck="false"
                       placeholder="粘贴书刊链接，如 https://new.bookan.com.cn/?type=1&id=233832">
                <button class="icon-btn del" title="删除本行" type="button">×</button>
            </div>
            <div class="row-sub">
                <span class="row-status hidden"></span>
                <button class="year-btn hidden" type="button" title="按 issueID 推算同刊同年的全部期次">⤓ 下载全年</button>
            </div>`;
        const input = group.querySelector('input');
        group.querySelector('.del').addEventListener('click', () => {
            group.remove();
            if (!$('input-rows').children.length) addInputRow();
        });
        input.addEventListener('input', debounce(() => previewRow(group), 400));
        group.querySelector('.year-btn').addEventListener('click', () => expandFullYear(group));
        return group;
    }

    function addInputRow(value) {
        const group = buildRowGroup();
        if (value) group.querySelector('input').value = value;
        $('input-rows').appendChild(group);
        return group;
    }

    // 全年推算产生的行：直接带入解析结果，不再重复请求
    function buildBatchRow(value, chipText) {
        const group = buildRowGroup();
        group.dataset.batch = '1';
        group.querySelector('input').value = value;
        setChip(group, true, chipText);
        return group;
    }

    function setChip(group, ok, text) {
        const status = group.querySelector('.row-status');
        status.classList.remove('hidden', 'ok', 'err');
        status.classList.add(ok ? 'ok' : 'err');
        status.textContent = text;
        status.title = text;
    }

    function previewRow(group) {
        const text = group.querySelector('input').value.trim();
        const status = group.querySelector('.row-status');
        const yearBtn = group.querySelector('.year-btn');
        if (!text) {
            status.classList.add('hidden');
            status.textContent = '';
            yearBtn.classList.add('hidden');
            return;
        }
        if (!window.pywebview || !window.pywebview.api) return;
        window.pywebview.api.resolve_input(text).then(r => {
            if (r.ok) {
                // 精简展示：期刊 = 刊名 · 期数；图书 = 书名 · 出版社（便于区分版本）
                const label = r.resource_type === 1
                    ? `${r.resource_name} · ${r.issue_name || '未知期数'}`
                    : `${r.resource_name} · ${r.publisher || '未知出版社'}`;
                setChip(group, true, label);
                group.dataset.rt = r.resource_type;
                group.dataset.issueId = r.issue_id;
                yearBtn.classList.toggle('hidden', r.resource_type !== 1);
            } else {
                setChip(group, false, '解析失败: ' + r.error);
                yearBtn.classList.add('hidden');
            }
        }).catch(e => {
            setChip(group, false, '解析失败: ' + e);
            yearBtn.classList.add('hidden');
        });
    }

    // ───────────── 下载全年（期刊 type=1 专属） ─────────────
    async function expandFullYear(group) {
        const yearBtn = group.querySelector('.year-btn');
        const issueId = group.dataset.issueId;
        const rt = Number(group.dataset.rt) || 1;
        if (!issueId || yearBtn.disabled) return;
        if (!window.pywebview || !window.pywebview.api) return;

        yearBtn.disabled = true;
        const oldText = yearBtn.textContent;
        yearBtn.textContent = '推算中…';
        try {
            const r = await window.pywebview.api.collect_year_issues(issueId, rt);
            if (!r || !r.ok) {
                log('error', '下载全年推算失败: ' + (r && r.error ? r.error : '未知错误'));
                return;
            }
            // 去重：跳过列表中已有链接（本行自身除外），随后用全年各期替换本行
            const own = group.querySelector('input').value.trim();
            const existing = new Set(
                Array.from($('input-rows').querySelectorAll('input'))
                    .map(i => i.value.trim())
                    .filter(v => v && v !== own)
            );
            const frag = document.createDocumentFragment();
            let added = 0;
            r.issues.forEach(it => {
                if (existing.has(it.url)) return;
                added += 1;
                frag.appendChild(buildBatchRow(it.url, `${r.resource_name} · ${it.issue_name}`));
            });
            group.replaceWith(frag);
            log('success', `《${r.resource_name}》全年推算完成：识别 ${r.count} 期，新增 ${added} 期到列表`);
            if (!added) log('warn', '没有新增期次（可能都已在列表中）');
        } catch (e) {
            log('error', '下载全年推算异常: ' + e);
        } finally {
            yearBtn.disabled = false;
            yearBtn.textContent = oldText;
        }
    }

    $('btn-add-input').addEventListener('click', () => {
        const wrap = $('input-rows');
        const g = addInputRow();
        g.querySelector('input').focus();
        wrap.scrollTop = wrap.scrollHeight;
    });
    addInputRow();

    // ───────────── 导出格式滑块（自动 / PDF / EPUB） ─────────────
    // 按用户要求：格式不持久化，每次打开默认「自动」；PDF 选项另行即时记忆
    const FORMAT_NOTES = {
        auto: '优先 EPUB，无 EPUB 版本下载 PDF',
        pdf: '图片版 PDF，可按三档压缩图片',
        epub: '官方原版 EPUB 直接下载',
    };
    function setFormat(fmt) {
        if (!['auto', 'pdf', 'epub'].includes(fmt)) fmt = 'auto';
        currentFormat = fmt;
        $('format-slider').dataset.active = fmt;
        document.querySelectorAll('#format-slider .seg').forEach(b => {
            b.classList.toggle('active', b.dataset.format === fmt);
        });
        // EPUB 为官方直下：压缩不适用；自动与 PDF 的逻辑与旧版一致
        $('pdf-options').classList.toggle('hidden', fmt === 'epub');
        $('format-note').textContent = FORMAT_NOTES[fmt];
    }
    document.querySelectorAll('#format-slider .seg').forEach(b => {
        b.addEventListener('click', () => setFormat(b.dataset.format));
    });
    $('btn-note-toggle').addEventListener('click', () => {
        $('format-note').classList.toggle('hidden');
    });
    setFormat('auto');

    // PDF 专属选项：切换立即记忆，下次启动恢复
    // 书签默认开启，开关入口在「设置」二级菜单；压缩三档仅在勾选压缩时显示
    function persistPdfOptions() {
        if (!window.pywebview || !window.pywebview.api) return;
        window.pywebview.api.save_config({
            add_bookmarks: $('add-bookmarks').checked,
            compress_images: $('compress-images').checked,
            compress_level: Number($('compress-levels').dataset.active) || 1,
        });
    }
    function setCompressLevel(level) {
        level = Math.max(1, Math.min(3, Number(level) || 1));
        $('compress-levels').dataset.active = level;
        document.querySelectorAll('#compress-levels .seg').forEach(b => {
            b.classList.toggle('active', Number(b.dataset.level) === level);
        });
    }
    function refreshCompressLevelsVisible() {
        $('compress-levels').classList.toggle('hidden', !$('compress-images').checked);
    }
    $('compress-images').addEventListener('change', () => {
        refreshCompressLevelsVisible();
        persistPdfOptions();
    });
    document.querySelectorAll('#compress-levels .seg').forEach(b => {
        b.addEventListener('click', () => {
            setCompressLevel(b.dataset.level);
            persistPdfOptions();
        });
    });
    $('add-bookmarks').addEventListener('change', persistPdfOptions);

    // ───────────── 设置二级菜单（输出目录 / PDF 选项 / 日志 / 关于） ─────────────
    function toggleSettingsPop(show) {
        $('settings-pop').classList.toggle('hidden', !show);
    }
    $('btn-settings').addEventListener('click', (e) => {
        e.stopPropagation();
        toggleSettingsPop($('settings-pop').classList.contains('hidden'));
    });
    document.addEventListener('click', (e) => {
        const pop = $('settings-pop');
        if (pop.classList.contains('hidden')) return;
        if (pop.contains(e.target) || $('btn-settings').contains(e.target)) return;
        toggleSettingsPop(false);
    });

    $('btn-choose-dir').addEventListener('click', async () => {
        try {
            const result = await window.pywebview.api.choose_directory($('output-dir').value.trim());
            if (result && result.path) {
                $('output-dir').value = result.path;
                // 选完立即记住，下次启动自动恢复
                window.pywebview.api.save_config({ output_dir: result.path });
                if (result.hint) toast(result.hint, true);
            } else if (result && result.error) {
                toast(result.error, true);
            }
        } catch (e) {
            $('output-dir').focus();
        }
    });

    // 从其它应用返回：直接重载页面（纯 JS，不发任何 js_api —— 多个 js_api 并发
    // 会触发 pyjnius 竞态崩溃，见 tombstone）。重载后 on_loaded 在主线程执行
    // 排队动作（如拉起文件管理器）。
    // 任务运行中不重载，避免进度界面丢失。
    function isAndroid() {
        return window.__bookan_is_android === true;
    }
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && isAndroid() && !taskRunning) {
            location.reload();
        }
    });

    // ───────────── 日志弹窗 ─────────────
    $('btn-open-log').addEventListener('click', () => {
        toggleSettingsPop(false);
        $('log-modal').classList.remove('hidden');
    });
    $('btn-close-log').addEventListener('click', () => $('log-modal').classList.add('hidden'));
    $('log-modal').addEventListener('click', (e) => {
        if (e.target === $('log-modal')) $('log-modal').classList.add('hidden');
    });
    $('btn-clear-log').addEventListener('click', () => { $('log-box').innerHTML = ''; });
    // 文本复制：优先 Clipboard API，被 WebView 拒绝（NotAllowedError）时
    // 降级 execCommand（隐藏 textarea 选中复制），两端通用
    async function copyText(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (e) {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.setAttribute('readonly', '');
            ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            ta.setSelectionRange(0, text.length);
            let ok = false;
            try { ok = document.execCommand('copy'); } catch (_) { /* 忽略 */ }
            ta.remove();
            return ok;
        }
    }

    $('btn-copy-log').addEventListener('click', async () => {
        const text = Array.from($('log-box').children).map(c => c.textContent).join('\n');
        const ok = await copyText(text);
        log(ok ? 'info' : 'warn', ok ? '日志已复制到剪贴板' : '复制失败，请长按日志区域手动全选复制');
    });

    // ───────────── 开始下载 → 切换到进度界面 ─────────────
    $('btn-start').addEventListener('click', async () => {
        if (taskRunning) return;
        const inputs = Array.from($('input-rows').querySelectorAll('input'))
            .map(i => i.value.trim()).filter(Boolean);
        const options = {
            // 安卓目录固定（后端 AndroidBridge 强制覆盖），桌面走用户配置
            output_dir: isAndroid()
                ? (window.__bookan_default_dir || '')
                : $('output-dir').value.trim(),
            output_format: currentFormat,
            add_bookmarks: $('add-bookmarks').checked,
            compress_images: $('compress-images').checked,
            compress_level: Number($('compress-levels').dataset.active) || 1,
        };
        if (!inputs.length) { log('warn', '请输入至少一条书刊链接'); return; }
        if (!isAndroid() && !options.output_dir) {
            toggleSettingsPop(true);
            setStatus('warn', '请先选择输出目录');
            log('warn', '请先在「设置 → 输出目录」中选择保存位置');
            return;
        }

        taskRunning = true;
        cancelRequested = false;
        setStatus('warn', '任务运行中…');
        setProgress(0, '准备中…', `共 ${inputs.length} 条待下载`);
        showScreen('progress');

        try {
            const r = await window.pywebview.api.start_task({ inputs, options });
            if (!r || !r.ok) {
                taskRunning = false;
                showScreen('main');
                setStatus('err', '启动失败');
                log('error', '启动失败: ' + (r && r.error ? r.error : '未知错误'));
                return;
            }
            currentTaskId = r.task_id;
            log('info', `任务已启动，ID = ${currentTaskId}`);
            // 极端时序：任务尚未确认前用户已点了取消 → 立即补发取消
            if (cancelRequested) {
                window.pywebview.api.cancel_task(currentTaskId);
            }
        } catch (e) {
            taskRunning = false;
            showScreen('main');
            setStatus('err', '启动异常');
            log('error', '启动异常: ' + e);
        }
    });

    // ───────────── 进度界面 + 取消确认 ─────────────
    function setProgress(pct, label, meta) {
        const p = Math.max(0, Math.min(100, Math.round(pct)));
        $('progress-fill').style.width = p + '%';
        $('progress-percent').textContent = p + '%';
        if (label !== undefined && label !== null) $('progress-label').textContent = label;
        $('progress-meta').textContent = meta || '';
    }

    $('btn-cancel').addEventListener('click', () => {
        $('confirm-modal').classList.remove('hidden');
    });
    $('btn-cancel-no').addEventListener('click', () => {
        $('confirm-modal').classList.add('hidden');
    });
    $('btn-cancel-yes').addEventListener('click', async () => {
        $('confirm-modal').classList.add('hidden');
        cancelRequested = true;
        taskRunning = false;
        if (currentTaskId) {
            try { await window.pywebview.api.cancel_task(currentTaskId); } catch (e) { /* 忽略 */ }
        }
        goMain();
        log('warn', '已请求取消，返回主界面');
    });

    // ───────────── 使用说明弹窗（设置 → 使用说明） ─────────────
    $('btn-help').addEventListener('click', () => {
        toggleSettingsPop(false);
        $('help-modal').classList.remove('hidden');
    });
    $('btn-close-help').addEventListener('click', () => $('help-modal').classList.add('hidden'));
    $('help-modal').addEventListener('click', (e) => {
        if (e.target === $('help-modal')) $('help-modal').classList.add('hidden');
    });

    // ───────────── 关于弹窗（设置 → 关于） ─────────────
    $('btn-about').addEventListener('click', () => {
        toggleSettingsPop(false);
        if (window.__bookan_version) {
            $('about-version').textContent = window.__bookan_version;
        }
        $('about-modal').classList.remove('hidden');
    });
    $('btn-close-about').addEventListener('click', () => $('about-modal').classList.add('hidden'));
    $('about-modal').addEventListener('click', (e) => {
        if (e.target === $('about-modal')) $('about-modal').classList.add('hidden');
    });

    // Esc：确认框视为"继续下载"，其余弹窗直接关闭
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        if (!$('confirm-modal').classList.contains('hidden')) {
            $('confirm-modal').classList.add('hidden');
        } else if (!$('log-modal').classList.contains('hidden')) {
            $('log-modal').classList.add('hidden');
        } else if (!$('help-modal').classList.contains('hidden')) {
            $('help-modal').classList.add('hidden');
        } else if (!$('about-modal').classList.contains('hidden')) {
            $('about-modal').classList.add('hidden');
        } else if (!$('settings-pop').classList.contains('hidden')) {
            toggleSettingsPop(false);
        }
    });

    // ───────────── 轻提示 toast（安卓无系统弹窗能力，反馈必须屏上可见） ─────────────
    let toastTimer = null;
    function toast(msg, warn = false) {
        let el = $('app-toast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'app-toast';
            document.body.appendChild(el);
        }
        el.textContent = msg;
        el.classList.toggle('warn', warn);
        el.classList.add('show');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => el.classList.remove('show'), 3000);
    }

    // ───────────── 下载完成界面 ─────────────
    $('btn-back').addEventListener('click', goMain);
    // 打开下载目录：置请求标志 → 重载页面（主线程拉起系统文件管理器并定位
    // 到 Download/bookantool，见 android_compat._start_file_manager）。重载后回主界面
    async function openDir() {
        try {
            await window.pywebview.api.open_in_explorer('');
            setTimeout(() => location.reload(), 600);
        } catch (e) {
            toast('打开失败，请手动打开系统文件管理器的「Download/bookantool」', true);
        }
    }

    function renderDone(data) {
        const list = $('done-list');
        list.innerHTML = '';
        const okCount = data.success || 0;
        const failCount = data.failed || 0;
        const hasErr = failCount > 0;
        $('done-sub').textContent = `成功 ${okCount} 个` + (hasErr ? ` · 失败 ${failCount} 个` : '');
        $('done-title').textContent = hasErr ? '下载完成（部分失败）' : '下载完成';
        $('done-icon').classList.toggle('ok', !hasErr);
        $('done-icon').classList.toggle('err', hasErr);

        (data.output_files || []).forEach(path => {
            const card = document.createElement('div');
            card.className = 'result-card success';
            const name = path.replace(/\\/g, '/').split('/').pop();
            card.innerHTML = `
                <h4><span class="badge ok">成功</span>${escapeHTML(name)}</h4>
                <div class="path">${escapeHTML(path)}</div>
                <div class="card-ops">
                    <button class="btn-secondary small" type="button">打开所在文件夹</button>
                </div>
            `;
            card.querySelector('button').addEventListener('click', () => {
                openDir();
            });
            list.appendChild(card);
        });

        (data.errors || []).forEach(err => {
            const card = document.createElement('div');
            card.className = 'result-card error';
            card.innerHTML = `
                <h4><span class="badge err">失败</span>${escapeHTML(err.input)}</h4>
                <div class="err-line">${escapeHTML(err.error || '已取消')}</div>
            `;
            list.appendChild(card);
        });

        if (!list.children.length) {
            const p = document.createElement('p');
            p.className = 'muted';
            p.textContent = '没有产出文件。';
            list.appendChild(p);
        }
    }

    // ───────────── 接收后端事件 ─────────────
    app.on('ready', () => {
        log('info', '前端已就绪');
        // 恢复上次使用习惯：配置优先，其次后端默认输出目录
        const cfg = window.__bookan_config || {};
        if (isAndroid()) {
            // 安卓下载目录固定为 Download/bookantool 不可自定义：
            // 隐藏整个「下载目录」设置项，仅保留 书签 / 使用说明 / 日志 / 关于
            const sec = $('sec-output-dir');
            if (sec) sec.classList.add('hidden');
            log('info', '下载保存位置：' + (window.__bookan_default_dir || 'Download/bookantool'));
        } else if (cfg.output_dir) {
            $('output-dir').value = cfg.output_dir;
            log('info', '已恢复输出目录：' + cfg.output_dir);
        } else if (window.__bookan_default_dir) {
            $('output-dir').value = window.__bookan_default_dir;
            log('info', '默认输出目录：' + window.__bookan_default_dir);
        }
        // 格式不恢复，每次打开默认「自动」（用户要求）；仅恢复 PDF 选项与输出目录
        if (typeof cfg.add_bookmarks === 'boolean') $('add-bookmarks').checked = cfg.add_bookmarks;
        if (typeof cfg.compress_images === 'boolean') $('compress-images').checked = cfg.compress_images;
        setCompressLevel(cfg.compress_level);
        refreshCompressLevelsVisible();
    });

    app.on('started', data => {
        setProgress(0, '任务开始…', `共 ${data.total} 条`);
    });

    app.on('progress', data => {
        const pct = Math.round(data.ratio * 100);
        const meta = data.total > 1 ? `第 ${data.current}/${data.total} 条` : '';
        setProgress(pct, data.label || '处理中…', meta);
    });

    app.on('log', data => log(data.level || 'info', data.msg));

    app.on('done', data => {
        taskRunning = false;
        if (cancelRequested || data.cancelled) {
            log('warn', '任务已取消');
            return;
        }
        if (data.error) {
            showScreen('main');
            setStatus('err', '任务失败');
            log('error', '任务失败: ' + data.error);
            return;
        }
        setStatus('ok', '任务完成');
        renderDone(data);
        showScreen('done');
    });

})();
