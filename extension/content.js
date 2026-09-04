// BookanTool 内容脚本（bookan.com.cn 全站）：
//  A. 书刊详情页 → 在试读按钮行注入「下载」按钮 + 上方弹出下载选项气泡
//  B. 悬浮进度方块（方案A：所有 bookan 页面重建同一方块，状态经 chrome.storage 同步，
//     站内跨页无缝；点击展开大方块显示任务详情，点外部收起，可拖动且记忆位置）
//
// 所有 UI 挂在 Shadow DOM 内，样式与站点完全隔离。

(() => {
  'use strict';
  if (window.__bookantoolInjected) return;
  window.__bookantoolInjected = true;

  const K = {
    PENDING: 'bt_pendingTasks',
    STATE: 'bt_jobState',
    SETTINGS: 'bt_settings',
    LAST: 'bt_lastOptions',
    POS: 'bt_widgetPos',
    DISMISSED: 'bt_widgetDismissed',
  };
  const FORMAT_NOTES = {
    auto: '优先 EPUB，无 EPUB 版本自动改用 PDF',
    pdf: '图片合成，与纸质版排版一致',
    epub: '官方 EPUB 成品直下，部分资源无 EPUB 版本',
  };

  const storage = {
    get: async (key, dflt) => {
      const o = await chrome.storage.local.get(key);
      return key in o ? o[key] : dflt;
    },
    set: (key, val) => chrome.storage.local.set({ [key]: val }),
  };

  // ═══════════════════════ A. 详情页下载按钮 + 选项气泡 ═══════════════════════

  function detailParams() {
    if (!/\/page\/detail\.html$/.test(location.pathname)) return null;
    const q = new URLSearchParams(location.search);
    const type = parseInt(q.get('type') || '1', 10);
    const id = q.get('id');
    if (!id || ![1, 3].includes(type)) return null;
    return { type, id };
  }

  function injectDetailButton() {
    const params = detailParams();
    if (!params) return;

    // 页面由 jQuery 异步渲染 #infoWrap（buttonMaker 生成按钮行），等锚点出现
    const tryInject = () => {
      const wrap = document.getElementById('infoWrap');
      if (!wrap) return false;
      // 按钮行 = 含 .btn 链接的 <p>
      const row = [...wrap.querySelectorAll('p')].find((p) => p.querySelector('a.btn'));
      if (!row) return false;
      if (row.querySelector('.bt-dl-btn')) return true;

      const btn = document.createElement('a');
      btn.href = 'javascript:;';
      btn.className = 'btn bt-dl-btn';
      btn.textContent = '下载';
      // 描边次要按钮风格：不与官方实心按钮（绿/橙）混淆
      btn.style.cssText =
        'background:#fff;color:#4f8cff;border:1px solid #9db9f5;' +
        'box-sizing:border-box;line-height:30px;height:32px;cursor:pointer;';
      btn.addEventListener('mouseenter', () => {
        btn.style.background = '#f2f6ff';
      });
      btn.addEventListener('mouseleave', () => {
        btn.style.background = '#fff';
      });
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        openOptionsBubble(params, btn);
      });
      row.appendChild(btn);
      return true;
    };

    if (tryInject()) return;
    const obs = new MutationObserver(() => {
      if (tryInject()) obs.disconnect();
    });
    obs.observe(document.body, { childList: true, subtree: true });
    // 10 秒后放弃观察，避免常驻监听
    setTimeout(() => obs.disconnect(), 10000);
  }

  // ── 选项气泡 ──
  async function openOptionsBubble(params, anchorBtn) {
    closeBubble();
    const last = (await storage.get(K.LAST, {})) || {};
    const opts = {
      format: last.format || 'auto',
      compress: !!last.compress,
      compressLevel: last.compressLevel || 1,
      downloadAll: !!last.downloadAll,
    };

    const host = document.createElement('div');
    host.id = 'bt-bubble-host';
    const shadow = host.attachShadow({ mode: 'closed' });
    shadow.innerHTML = `
      <style>
        :host { all: initial; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: "Microsoft YaHei", system-ui, sans-serif; }
        .wrap {
          position: fixed; z-index: 2147483000; width: 264px;
          background: #fff; border: 1px solid #e3e6ef; border-radius: 12px;
          box-shadow: 0 10px 34px rgba(30, 40, 80, .18); padding: 14px;
        }
        .wrap::before {
          content: ''; position: absolute; top: -6px; left: var(--arrow-x, 50%);
          width: 10px; height: 10px; background: #fff;
          border-left: 1px solid #e3e6ef; border-top: 1px solid #e3e6ef;
          transform: translateX(-50%) rotate(45deg);
        }
        .lbl { font-size: 12px; color: #8a8fa3; margin-bottom: 6px; }
        .slider {
          position: relative; display: flex; background: #eef1f7;
          border-radius: 8px; padding: 3px; margin-bottom: 6px;
        }
        .slider .thumb {
          position: absolute; top: 3px; bottom: 3px; width: calc((100% - 6px) / 3);
          background: #fff; border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,.12);
          transition: left .18s ease;
        }
        .slider button {
          flex: 1; position: relative; z-index: 1; border: 0; background: transparent;
          height: 26px; font-size: 12px; color: #6b7186; cursor: pointer; border-radius: 6px;
        }
        .slider button.on { color: #3b76f6; font-weight: 600; }
        .note { font-size: 11px; color: #9aa0b4; margin-bottom: 10px; min-height: 14px; }
        .chk {
          display: flex; align-items: center; gap: 6px;
          font-size: 12px; color: #444; margin: 8px 0; cursor: pointer;
        }
        .chk input { accent-color: #3b76f6; }
        .levels { margin: 0 0 2px 20px; }
        .levels .slider { margin-bottom: 8px; }
        .ft { display: flex; gap: 8px; margin-top: 12px; }
        .ft button {
          flex: 1; height: 30px; border-radius: 8px; font-size: 13px; cursor: pointer;
          border: 1px solid #d9dde8; background: #fff; color: #555;
        }
        .ft .go { background: #3b76f6; border-color: #3b76f6; color: #fff; font-weight: 600; }
        .ft .go:hover { background: #2f63d8; }
      </style>
      <div class="wrap">
        <div class="lbl">输出格式</div>
        <div class="slider" id="fmt">
          <div class="thumb"></div>
          <button data-v="auto">自动</button>
          <button data-v="pdf">PDF</button>
          <button data-v="epub">EPUB</button>
        </div>
        <div class="note" id="note"></div>
        <label class="chk" id="cmpWrap"><input type="checkbox" id="cmp"><span>压缩图片（仅 PDF）</span></label>
        <div class="levels" id="levels">
          <div class="slider" id="lvl">
            <div class="thumb"></div>
            <button data-v="1">轻度</button>
            <button data-v="2">中度</button>
            <button data-v="3">高度</button>
          </div>
        </div>
        <label class="chk" id="allWrap"><input type="checkbox" id="all"><span>下载全年（同刊同年各期）</span></label>
        <div class="ft">
          <button id="cancel">取消</button>
          <button id="ok" class="go">开始下载</button>
        </div>
      </div>`;

    const wrap = shadow.querySelector('.wrap');
    const $ = (sel) => shadow.querySelector(sel);

    function seg(slider, val, onChange) {
      const btns = [...slider.querySelectorAll('button')];
      const thumb = slider.querySelector('.thumb');
      const apply = () => {
        const i = btns.findIndex((b) => b.dataset.v === String(val));
        btns.forEach((b, j) => b.classList.toggle('on', j === i));
        thumb.style.left = `calc(${(i * 100) / 3}% + ${3 - (i * 6) / 3}px)`;
      };
      btns.forEach((b) =>
        b.addEventListener('click', () => {
          val = b.dataset.v;
          apply();
          onChange(val);
        })
      );
      apply();
    }

    let fmt = opts.format;
    let lvl = String(opts.compressLevel);

    const refreshVisibility = () => {
      const showPdfOpts = fmt !== 'epub';
      $('#cmpWrap').style.display = showPdfOpts ? '' : 'none';
      $('#levels').style.display = showPdfOpts && opts.compress ? '' : 'none';
      $('#allWrap').style.display = params.type === 1 ? '' : 'none';
      $('#note').textContent = FORMAT_NOTES[fmt] || '';
    };

    seg($('#fmt'), fmt, (v) => {
      fmt = v;
      refreshVisibility();
    });
    seg($('#lvl'), lvl, (v) => {
      lvl = v;
    });
    $('#cmp').checked = opts.compress;
    $('#cmp').addEventListener('change', () => {
      opts.compress = $('#cmp').checked;
      refreshVisibility();
    });
    $('#all').checked = opts.downloadAll;
    refreshVisibility();

    // 定位：按钮上方居中（空间不足翻到下方）
    document.body.appendChild(host);
    const rect = anchorBtn.getBoundingClientRect();
    const W = 264;
    let x = Math.min(Math.max(8, rect.left + rect.width / 2 - W / 2), innerWidth - W - 8);
    let y = rect.top - 12;
    wrap.style.left = x + 'px';
    wrap.style.top = '0px';
    const H = wrap.offsetHeight;
    if (y - H < 8) {
      y = rect.bottom + 12;
      wrap.style.setProperty('--arrow-x', '50%');
      wrap.classList.add('below');
      // 下方弹出时箭头换到顶边
      const st = shadow.querySelector('style');
      st.textContent += `
        .wrap.below::before { top: auto; bottom: -6px; transform: translateX(-50%) rotate(225deg); }`;
    }
    wrap.style.top = Math.max(8, y - (y > rect.bottom ? 0 : H)) + 'px';
    wrap.style.setProperty('--arrow-x', `${Math.min(90, Math.max(10, rect.left + rect.width / 2 - x))}px`);

    const close = () => {
      document.removeEventListener('mousedown', onOutside, true);
      document.removeEventListener('keydown', onKey, true);
      host.remove();
    };
    const onOutside = (e) => {
      if (!host.contains(e.target) && e.target !== anchorBtn && !anchorBtn.contains(e.target)) close();
    };
    const onKey = (e) => {
      if (e.key === 'Escape') close();
    };
    setTimeout(() => {
      document.addEventListener('mousedown', onOutside, true);
      document.addEventListener('keydown', onKey, true);
    }, 0);
    closeBubble = close;

    shadow.querySelector('#cancel').addEventListener('click', close);
    shadow.querySelector('#ok').addEventListener('click', async () => {
      const spec = {
        id: `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
        type: params.type,
        issueId: params.id,
        format: fmt,
        compress: fmt !== 'epub' && opts.compress,
        compressLevel: parseInt(lvl, 10) || 1,
        downloadAll: params.type === 1 && $('#all').checked,
      };
      await storage.set(K.LAST, {
        format: fmt,
        compress: opts.compress,
        compressLevel: parseInt(lvl, 10) || 1,
        downloadAll: spec.downloadAll,
      });
      // 入队：写 storage（downloader 页监听变更）+ 让 background 确保任务页存在
      // 注意：不动 bt_widgetDismissed —— 完成态由 downloader 在整批结束时展示，
      // 避免入队瞬间旧任务的完成图标闪现
      const cur = (await storage.get(K.PENDING, [])) || [];
      cur.push(spec);
      await chrome.storage.local.set({ [K.PENDING]: cur });
      try {
        chrome.runtime.sendMessage({ type: 'bt:ensureDownloader' }, () => void chrome.runtime.lastError);
      } catch {
        /* 任务页创建失败时下次 storage 变更仍会触发 */
      }
      close();
      toast('已加入下载队列，可点右侧悬浮窗查看进度');
    });
  }

  let closeBubble = () => {};

  function toast(msg) {
    const host = document.createElement('div');
    const shadow = host.attachShadow({ mode: 'closed' });
    shadow.innerHTML = `
      <style>
        :host { all: initial; }
        .t {
          position: fixed; z-index: 2147483600; left: 50%; bottom: 48px;
          transform: translateX(-50%); background: rgba(28, 32, 48, .92); color: #fff;
          font: 13px/1.4 "Microsoft YaHei", system-ui, sans-serif;
          padding: 10px 18px; border-radius: 10px;
          box-shadow: 0 6px 20px rgba(0,0,0,.25);
          animation: bt-fade 2.6s ease forwards; white-space: nowrap;
        }
        @keyframes bt-fade {
          0% { opacity: 0; transform: translate(-50%, 8px); }
          10% { opacity: 1; transform: translate(-50%, 0); }
          82% { opacity: 1; }
          100% { opacity: 0; }
        }
      </style>
      <div class="t">${msg}</div>`;
    document.documentElement.appendChild(host);
    setTimeout(() => host.remove(), 2700);
  }

  // ═══════════════════════ B. 悬浮进度方块（方案A） ═══════════════════════

  const RING_C = 2 * Math.PI * 18; // r=18 → 113.1

  let jobState = null;
  let dismissed = true;
  let expanded = false;

  async function initWidget() {
    const host = document.createElement('div');
    host.id = 'bt-widget-host';
    const shadow = host.attachShadow({ mode: 'closed' });
    shadow.innerHTML = `
      <style>
        :host { all: initial; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: "Microsoft YaHei", system-ui, sans-serif; }
        #sq {
          position: fixed; z-index: 2147483100; width: 46px; height: 46px;
          border-radius: 12px; background: rgba(255,255,255,.94);
          box-shadow: 0 4px 16px rgba(24, 34, 72, .22);
          display: flex; align-items: center; justify-content: center;
          cursor: grab; user-select: none; touch-action: none;
          transition: box-shadow .15s, opacity .2s;
          opacity: .92;
        }
        #sq:hover { opacity: 1; box-shadow: 0 6px 22px rgba(24, 34, 72, .3); }
        #sq.dragging { cursor: grabbing; opacity: 1; }
        #sq svg { width: 34px; height: 34px; }
        #sq circle.bg { fill: none; stroke: #e8ebf3; stroke-width: 4; }
        #sq circle.fg {
          fill: none; stroke: #3b76f6; stroke-width: 4; stroke-linecap: round;
          stroke-dasharray: ${RING_C}; transform: rotate(-90deg); transform-origin: center;
          transition: stroke-dashoffset .25s ease, stroke .25s;
        }
        #sq text {
          font-size: 11px; font-weight: 700; fill: #33415e;
          text-anchor: middle; dominant-baseline: central;
        }
        #sq .mark { display: none; }
        #sq.done .fg { display: none; }
        #sq.done text { display: none; }
        #sq.done .mark { display: block; }
        #sq.hidden { display: none; }

        #card {
          position: fixed; z-index: 2147483200; width: 300px;
          background: #fff; border: 1px solid #e3e6ef; border-radius: 14px;
          box-shadow: 0 14px 44px rgba(24, 34, 72, .25); overflow: hidden;
        }
        #card.hidden { display: none; }
        .hd {
          display: flex; align-items: center; justify-content: space-between;
          padding: 12px 14px; border-bottom: 1px solid #eef0f6;
          font-size: 13px; font-weight: 700; color: #2c3350;
        }
        .hd .st { font-size: 12px; color: #8a8fa3; font-weight: 400; }
        #list { max-height: 240px; overflow-y: auto; padding: 6px 8px; }
        .row { padding: 7px 8px; border-radius: 8px; }
        .row:hover { background: #f5f7fc; }
        .r1 { display: flex; align-items: center; gap: 7px; }
        .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
        .dot.queued { background: #c3c9d9; }
        .dot.running { background: #3b76f6; animation: blink 1.2s ease infinite; }
        .dot.succeeded { background: #22c55e; }
        .dot.failed { background: #ef4444; }
        .dot.cancelled { background: #f59e0b; }
        @keyframes blink { 50% { opacity: .35; } }
        .ttl {
          flex: 1; font-size: 12px; color: #333c56;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .msg { font-size: 11px; color: #9aa0b4; margin: 3px 0 0 15px;
               white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .msg.err { color: #e05252; }
        .bar { height: 3px; border-radius: 2px; background: #edf0f7; margin: 5px 0 0 15px; overflow: hidden; }
        .bar i { display: block; height: 100%; background: #3b76f6; border-radius: 2px; transition: width .25s; }
        .ft { display: flex; gap: 8px; padding: 10px 12px; border-top: 1px solid #eef0f6; }
        .ft button {
          flex: 1; height: 30px; border-radius: 8px; font-size: 12px; cursor: pointer;
          border: 1px solid #d9dde8; background: #fff; color: #555;
        }
        .ft button.go { background: #3b76f6; border-color: #3b76f6; color: #fff; font-weight: 600; }
        .ft button.go:hover { background: #2f63d8; }
        .tip { font-size: 10px; color: #b6bac9; text-align: center; padding: 0 12px 9px; }
      </style>
      <div id="sq" class="hidden">
        <svg viewBox="0 0 44 44">
          <circle class="bg" cx="22" cy="22" r="18"></circle>
          <circle class="fg" cx="22" cy="22" r="18"></circle>
          <text x="22" y="23"></text>
          <g class="mark">
            <path id="mk-path" fill="none" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/>
            <text id="mk-txt" x="22" y="24" style="display:none"></text>
          </g>
        </svg>
      </div>
      <div id="card" class="hidden">
        <div class="hd"><span>BookanTool 下载</span><span class="st" id="hdSt"></span></div>
        <div id="list"></div>
        <div class="ft">
          <button id="bCancel">取消下载</button>
          <button id="bDone">完成</button>
        </div>
        <div class="tip">下载在浏览器后台进行，请勿关闭固定的任务标签页</div>
      </div>`;
    document.documentElement.appendChild(host);

    const sq = shadow.querySelector('#sq');
    const ring = shadow.querySelector('circle.fg');
    const pctText = shadow.querySelector('#sq > svg > text');
    const mkPath = shadow.querySelector('#mk-path');
    const mkTxt = shadow.querySelector('#mk-txt');
    const card = shadow.querySelector('#card');
    const list = shadow.querySelector('#list');
    const hdSt = shadow.querySelector('#hdSt');
    const bCancel = shadow.querySelector('#bCancel');
    const bDone = shadow.querySelector('#bDone');

    // ── 位置：默认右侧中部，拖动后记忆 ──
    let pos = await storage.get(K.POS, null);
    const place = () => {
      const w = 46;
      let x = pos ? pos.x : innerWidth - w - 14;
      let y = pos ? pos.y : Math.round(innerHeight * 0.45);
      x = Math.min(Math.max(4, x), innerWidth - w - 4);
      y = Math.min(Math.max(4, y), innerHeight - w - 4);
      sq.style.left = x + 'px';
      sq.style.top = y + 'px';
      if (expanded) placeCard();
    };
    const placeCard = () => {
      const r = sq.getBoundingClientRect();
      const cw = 300;
      const ch = card.offsetHeight || 300;
      let x = r.left + r.width / 2 - cw / 2;
      x = Math.min(Math.max(8, x), innerWidth - cw - 8);
      let y = r.top - ch - 10;
      if (y < 8) y = r.bottom + 10;
      y = Math.min(y, innerHeight - ch - 8);
      card.style.left = x + 'px';
      card.style.top = y + 'px';
    };
    window.addEventListener('resize', place);
    place();

    // ── 拖动 / 点击 ──
    let downX = 0, downY = 0, dragging = false, moved = false, startLeft = 0, startTop = 0;
    sq.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      sq.setPointerCapture(e.pointerId);
      downX = e.clientX;
      downY = e.clientY;
      startLeft = sq.offsetLeft;
      startTop = sq.offsetTop;
      dragging = false;
      moved = false;
    });
    sq.addEventListener('pointermove', (e) => {
      if (!sq.hasPointerCapture(e.pointerId)) return;
      const dx = e.clientX - downX;
      const dy = e.clientY - downY;
      if (!dragging && Math.hypot(dx, dy) > 4) {
        dragging = true;
        sq.classList.add('dragging');
      }
      if (dragging) {
        moved = true;
        sq.style.left = Math.min(Math.max(4, startLeft + dx), innerWidth - 50) + 'px';
        sq.style.top = Math.min(Math.max(4, startTop + dy), innerHeight - 50) + 'px';
      }
    });
    sq.addEventListener('pointerup', async (e) => {
      if (!sq.hasPointerCapture(e.pointerId)) return;
      sq.classList.remove('dragging');
      if (dragging) {
        pos = { x: sq.offsetLeft, y: sq.offsetTop };
        await storage.set(K.POS, pos);
        dragging = false;
      } else {
        e.stopPropagation();
        expanded = !expanded;
        render();
      }
    });

    // 展开时点外部收起
    document.addEventListener('mousedown', (e) => {
      if (!expanded) return;
      if (host.contains(e.target) || sq.contains(e.target)) return;
      expanded = false;
      render();
    }, true);

    // ── 按钮 ──
    bCancel.addEventListener('click', () => {
      try {
        chrome.runtime.sendMessage({ type: 'bt:cancel' }, () => void chrome.runtime.lastError);
      } catch { /* downloader 未开则无需取消 */ }
      expanded = false;
      render();
    });
    bDone.addEventListener('click', async () => {
      expanded = false;
      await storage.set(K.DISMISSED, true);
      render();
    });

    // ── 渲染 ──
    const ST_LABEL = { queued: '排队中', running: '', succeeded: '完成', failed: '失败', cancelled: '已取消' };
    function render() {
      const st = jobState;
      const hasRunning = !!(st && st.active);
      const hasDone = !!(st && st.doneAt && !dismissed);
      sq.classList.toggle('hidden', !(hasRunning || hasDone));
      card.classList.toggle('hidden', !expanded || !(hasRunning || hasDone));
      if (!hasRunning && !hasDone) return;

      if (hasRunning) {
        const p = Math.max(0, Math.min(1, st.overall || 0));
        sq.classList.remove('done');
        ring.style.strokeDashoffset = RING_C * (1 - p);
        ring.style.stroke = '#3b76f6';
        pctText.textContent = `${Math.round(p * 100)}%`;
      } else {
        const ok = st.okCount || 0;
        const fail = st.failCount || 0;
        sq.classList.add('done');
        if (ok && !fail) {
          mkPath.setAttribute('d', 'M13 22.5l6 6 12-13');
          mkPath.setAttribute('stroke', '#22c55e');
          mkTxt.style.display = 'none';
        } else if (ok && fail) {
          mkPath.setAttribute('d', 'M13 22.5l6 6 12-13');
          mkPath.setAttribute('stroke', '#f59e0b');
          mkTxt.style.display = 'none';
        } else {
          mkPath.setAttribute('d', '');
          mkTxt.style.display = '';
          mkTxt.textContent = '×';
          mkTxt.style.fill = '#ef4444';
        }
      }

      if (expanded) {
        placeCard();
        hdSt.textContent = hasRunning
          ? `${Math.round((st.overall || 0) * 100)}%`
          : `成功 ${st.okCount || 0} · 失败 ${st.failCount || 0}`;
        list.innerHTML = '';
        for (const t of (st.tasks || []).slice(-30)) {
          const row = document.createElement('div');
          row.className = 'row';
          const r1 = document.createElement('div');
          r1.className = 'r1';
          r1.innerHTML = `<span class="dot ${t.status}"></span>`;
          const ttl = document.createElement('span');
          ttl.className = 'ttl';
          ttl.textContent = t.title || t.issueId;
          ttl.title = ttl.textContent;
          r1.appendChild(ttl);
          row.appendChild(r1);

          const msg = document.createElement('div');
          msg.className = 'msg' + (t.status === 'failed' ? ' err' : '');
          msg.textContent = t.status === 'succeeded' ? `已保存：${t.fileName || ''}` :
            t.status === 'failed' ? t.error || '下载失败' :
            t.status === 'cancelled' ? '已取消' :
            t.message || '等待中';
          row.appendChild(msg);

          if (t.status === 'running') {
            const bar = document.createElement('div');
            bar.className = 'bar';
            bar.innerHTML = `<i style="width:${Math.round((t.progress || 0) * 100)}%"></i>`;
            row.appendChild(bar);
          }
          list.appendChild(row);
        }
        bCancel.style.display = hasRunning ? '' : 'none';
        bDone.style.display = hasRunning ? 'none' : '';
      }
    }

    // ── 状态同步：storage 变化 → 全站所有页面同一方块 ──
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'local') return;
      if (changes[K.STATE]) jobState = changes[K.STATE].newValue || null;
      if (changes[K.DISMISSED]) dismissed = changes[K.DISMISSED].newValue !== false;
      render();
    });
    jobState = await storage.get(K.STATE, null);
    dismissed = (await storage.get(K.DISMISSED, true)) !== false;
    render();
  }

  // ═══════════════════════ 启动 ═══════════════════════
  injectDetailButton();
  initWidget();
})();
