"""自测：检查伪装后常见的自动化指纹值。

运行（会真实打开 Edge 窗口，稍等片刻后输出结果）：
    python -m browser.detect_check

看输出里的"✓ 正常 / ✗ 异常"。异常项说明当前伪装还没盖住，需要回
browser/edge_browser.py 的 _stealth_init_script() 里补。
"""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

from edge_browser import EdgeBrowser

CHECKS = """
async () => {
    const rows = [];
    const add = (name, val, isBad) => rows.push([name, val, isBad]);
    const q = async (p) => { try { return await p; } catch (e) { return 'ERR:' + e.message; } };

    // 1. 最关键：navigator.webdriver 是否已隐藏（应为 undefined / false）
    add('navigator.webdriver', String(navigator.webdriver), !!navigator.webdriver);
    // webdriver getter 是否伪装成原生函数（防源码比对）
    const wd = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
    add('webdriver getter source', wd && wd.get ? wd.get.toString().slice(0, 45) : 'N/A',
        !(wd && wd.get && wd.get.toString().includes('[native code]')));

    // 2. window.chrome 是否完整（真实 Edge 应为原生、含 runtime）
    add('window.chrome.runtime', String(!!(window.chrome && window.chrome.runtime)), !(window.chrome && window.chrome.runtime));
    add('window.chrome keys', Object.keys(window.chrome || {}).join(',') || '(empty)', !window.chrome || Object.keys(window.chrome).length === 0);

    // 3. 自动化层残留全局对象
    add('__playwright leaked', String('__playwright' in window), '__playwright' in window);
    add('_playwright leaked', String('_playwright' in window), '_playwright' in window);
    add('__pw_live leaked', String('__pwLiveBindings' in window), '__pwLiveBindings' in window);

    // 4. 权限/通知
    add('Notification.permission', String(Notification.permission), false);
    const np = await q(navigator.permissions.query({name: 'notifications'}));
    add('permissions.query(notifications)', np && np.state ? np.state : String(np), false);

    // 5. 语言与插件
    add('navigator.languages', JSON.stringify(navigator.languages), !navigator.languages || navigator.languages.length === 0);
    add('navigator.plugins.length', String(navigator.plugins.length), navigator.plugins.length === 0);

    // 6. 媒体设备
    const devs = await q(navigator.mediaDevices.enumerateDevices());
    add('mediaDevices count', Array.isArray(devs) ? String(devs.length) : String(devs), Array.isArray(devs) && devs.length === 0);

    // 7. WebGL 渲染器（有值即真机，空则被识别）
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl');
    if (gl) {
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        const r = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : '(no ext)';
        add('webgl renderer', String(r).slice(0, 40), r === null || r === undefined);
    } else {
        add('webgl renderer', '(no webgl)', true);
    }

    // 8. 窗口尺寸（真机 inner < outer，因为有边框/工具栏；CDP 常相等）
    add('outer-inner (w)', String(window.outerWidth - window.innerWidth), window.outerWidth === window.innerWidth);
    add('outer-inner (h)', String(window.outerHeight - window.innerHeight), window.outerHeight === window.innerHeight);

    return rows.map(([n, v, bad]) => `${bad ? '✗' : '✓'} ${n} = ${v}`).join('\\n');
}
"""


def main() -> None:
    browser = EdgeBrowser()
    page = browser.start(url="https://www.baidu.com")
    result = page.evaluate(CHECKS)
    print(result)
    print("\n[*] 检查完成，正在关闭浏览器...")
    browser.close()


if __name__ == "__main__":
    main()
