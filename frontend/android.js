// 安卓端桥接垫片（由 android_server 注入 <script src="android.js"> 加载）。
// 桌面 pywebview 不会加载本文件；index.html 本身不引用它。
//
// 作用：把 window.pywebview.api.<method>(...) 代理到本地 HTTP 桥
//   POST /bridge/<method>  body={"args":[...]}
// 并通过 SSE（GET /events）接收后端事件，转发给 window.__bookan_dispatch，
// 与桌面端 evaluate_js 注入的事件通道完全一致 —— app.js 因此零改动。

(function () {
    'use strict';

    // 仅在安卓 WebView（服务端注入标记）环境下生效
    if (!window.__bookan_android__) return;

    function call(method, args) {
        return fetch('/bridge/' + encodeURIComponent(method), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ args: args || [] })
        }).then(function (r) { return r.json(); });
    }

    // 代理出任意方法名：pywebview.api.health() / .start_task({...}) 等
    window.pywebview = {
        api: new Proxy({}, {
            get: function (_target, prop) {
                if (typeof prop !== 'string') return undefined;
                return function () {
                    return call(prop, Array.prototype.slice.call(arguments));
                };
            }
        })
    };

    // SSE 事件流 → window.__bookan_dispatch(event, data)（与桌面一致）
    function connectEvents() {
        var es = new EventSource('/events');
        es.onmessage = function (msg) {
            try {
                var ev = JSON.parse(msg.data);
                window.__bookan_dispatch(ev.event, ev.data);
            } catch (e) { /* 忽略坏包 */ }
        };
        es.onerror = function () {
            es.close();
            setTimeout(connectEvents, 2000); // 断线重连（服务重启 / 锁屏回收后）
        };
    }

    // app.js 在 body 末尾同步执行（监听器已注册完），DOMContentLoaded 再派发
    // 'ready' 与连接 SSE，时序与桌面「页面加载完成后注入并推 ready」对齐。
    document.addEventListener('DOMContentLoaded', function () {
        window.__bookan_dispatch('ready', { ts: Date.now() });
        connectEvents();
    });
})();
