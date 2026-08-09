"""自测：检查伪装后常见的自动化指纹值。

运行（会真实打开 Edge 窗口，稍等片刻后输出结果）：
    python -m browser.detect_check
"""

from __future__ import annotations

from edge_browser import EdgeBrowser

CHECKS = """
async () => {
    const out = [];
    // 1. 最关键的：navigator.webdriver 是否已隐藏
    out.push(['navigator.webdriver', String(navigator.webdriver)]);
    // 2. webdriver getter 是否伪装成原生函数（防源码比对）
    const wd = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
    out.push(['webdriver getter source', wd ? wd.get.toString().slice(0, 40) : 'N/A']);
    // 3. window.chrome 是否完整（真实 Edge 应为原生、含 runtime）
    out.push(['window.chrome.runtime', String(!!(window.chrome && window.chrome.runtime))]);
    out.push(['window.chrome keys', Object.keys(window.chrome || {}).join(',')]);
    // 4. 自动化层残留全局对象
    out.push(['__playwright leaked', String('__playwright' in window)]);
    // 5. 常见隐藏检查项
    out.push(['navigator.plugins.length', String(navigator.plugins.length)]);
    out.push(['navigator.languages', JSON.stringify(navigator.languages)]);
    out.push(['window.webdriver', String('webdriver' in window)]);
    return out.map(([k, v]) => `${k} = ${v}`).join('\\n');
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
