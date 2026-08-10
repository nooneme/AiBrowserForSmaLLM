"""基于 Playwright 启动本地 Edge 浏览器，并复用已有用户配置目录。

用于"本地小模型 + 全视觉"自动化：复用 edge_manual_profile 登录态，
启动持久化上下文，供后续截图/操作使用。

注意：
- 复用已存在的 profile 目录时，Edge 不能同时在运行，否则会报错
  (该目录已被进程占用)。
- 首次运行需安装浏览器：python -m playwright install msedge
  （channel="msedge" 时 Playwright 使用系统已装的 Edge，通常无需下载）。
"""

from __future__ import annotations

import random
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

PROFILE_DIR = r"C:\Users\z\Desktop\project\qiongguicode\edge_manual_profile2"


class EdgeBrowser:
    def __init__(
        self,
        profile_dir: str = PROFILE_DIR,
        headless: bool = False,
        user_agent: str | None = None,
        detached: bool = True,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.user_agent = user_agent
        # detached=True 时浏览器与脚本解耦：脚本退出后浏览器保持运行不关闭
        self.detached = detached
        self._pw: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._last_mouse: tuple[float, float] | None = None

    def _launch_args(self) -> list[str]:
        args: list[str] = []
        # 默认最大化窗口打开（配合 no_viewport 跟随真实窗口尺寸）
        args.append("--start-maximized")
        if self.headless:
            args.append("--headless")
        else:
            # 有头模式关闭 blink 的自动化控制特征，减小被识别为自动化的概率
            # （配合 start() 里注入的 stealth init script 一起生效）
            args.append("--disable-blink-features=AutomationControlled")
        return args

    def _stealth_init_script(self) -> str:
        """在页面加载前注入的伪装脚本。

        针对"真实 Edge + 有头 + CDP 驱动"补自动化层叠加的痕迹。CDP 驱动
        （Playwright）会在真实浏览器上额外暴露以下区别，这里逐项抹平：
        1. navigator.webdriver 被置 true —— 藏掉并加固 descriptor，防站点读
           getter 源码 / 检查 descriptor 属性。
        2. window.chrome 及 chrome.runtime 等在纯 CDP 上下文里缺失 —— 补齐。
        3. permissions.query / Notification.permission 被限制 —— 放开。
        4. navigator.plugins / mimeTypes 被 CDP 清空 —— 无法伪造数量时至少
           保证查询不抛错（真 Edge 本身有值，此项主要是兜底）。
        5. 自动化层残留的 __playwright / _playwright 等全局对象 —— 清掉。
        6. WebGL / mediaDevices / languages 等可能被识别 —— 补齐常见值。
        """
        return r"""
(function () {
    // 1. 隐藏 navigator.webdriver，并让 getter 伪装成原生，防源码比对
    //    用一个真实 getter 函数返回 undefined，再给它的 toString 打补丁，
    //    使 Function.prototype.toString.call(getter) 显示为原生代码。
    const wdGetter = function () { return undefined; };
    const WDSOURCE = 'function get webdriver() { [native code] }';
    const origToString = Function.prototype.toString;
    wdGetter.toString = function () { return WDSOURCE; };
    Object.defineProperty(Navigator.prototype, 'webdriver', {
        get: wdGetter,
        set: undefined,
        enumerable: true,
        configurable: true,
    });
    // 兜底：拦截对任意函数 toString 的调用，若命中 webdriver getter 则给原生样源码
    Function.prototype.toString = function () {
        if (this === wdGetter) return WDSOURCE;
        return origToString.call(this);
    };
    const wdDesc = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
    void wdDesc;

    // 2. 补齐 window.chrome（真 Edge 有，且带 runtime/csi/loadTimes）
    if (!window.chrome) {
        Object.defineProperty(window, 'chrome', { value: {}, configurable: true });
    }
    const chrome = window.chrome;
    chrome.runtime = chrome.runtime || {};
    chrome.csi = chrome.csi || (() => {});
    chrome.loadTimes = chrome.loadTimes || (() => {});
    chrome.app = chrome.app || {};
    if (!('webstore' in chrome)) chrome.webstore = chrome.webstore || {};

    // 3. 放开 permissions.query / Notification.permission
    try {
        if (window.Notification) {
            Object.defineProperty(Notification, 'permission', {
                get: () => 'default',
                set: undefined,
                configurable: true,
            });
        }
        if (navigator.permissions && navigator.permissions.query) {
            const origQuery = navigator.permissions.query;
            navigator.permissions.query = (params) => (
                params && params.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission, onchange: null })
                    : origQuery(params)
            );
        }
    } catch (_) {}

    // 4. navigator.plugins / mimeTypes：CDP 下可能为空，真机不触发，兜底防抛错
    try {
        if (navigator.plugins && navigator.plugins.length === 0) {
            const p = [{ name: 'PDF Viewer' }];
            Object.defineProperty(navigator.plugins, 'length', { value: p.length });
        }
        if (navigator.languages && navigator.languages.length === 0) {
            Object.defineProperty(navigator, 'languages', { value: ['zh-CN', 'zh', 'en'], configurable: true });
        }
    } catch (_) {}

    // 5. 清掉自动化层残留的全局对象
    const leaked = ['__playwright', '_playwright', '__pw_manual', '__pwViewportScale', '__pwLiveBindings'];
    for (const k of leaked) {
        try {
            if (k in window) {
                Object.defineProperty(window, k, {
                    value: undefined, writable: true, configurable: true,
                });
            }
        } catch (_) {}
    }

    // 6. WebGL 常见检测：补齐渲染器字段（真 Edge 是 ANGLE + 显卡型号）
    try {
        const c = document.createElement('canvas');
        const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
        if (gl && !gl.getExtension('WEBGL_debug_renderer_info')) {
            const fake = { UNMASKED_VENDOR_WEBGL: 0x9245, UNMASKED_RENDERER_WEBGL: 0x9246 };
            const origGetExt = gl.getExtension.bind(gl);
            gl.getExtension = (name) => origGetExt(name) || fake;
        }
    } catch (_) {}

    // 7. mediaDevices.enumerateDevices 补齐（真机往往非空，这里保证不抛）
    try {
        if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
            const orig = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
            navigator.mediaDevices.enumerateDevices = () =>
                orig().then(list =>
                    list.length ? list : [{ deviceId: '', kind: 'audioinput' }]
                );
        }
    } catch (_) {}

    window.__stealth_ready = true;
})();
"""

    def start(self, url: str = "about:blank") -> Page:
        """启动持久化 Edge 上下文，复用已有 profile 目录。"""
        if not self.profile_dir.exists():
            raise FileNotFoundError(f"profile 目录不存在: {self.profile_dir}")

        self._pw = sync_playwright().start()
        try:
            self.context = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                channel="msedge",
                headless=self.headless,
                args=self._launch_args(),
                user_agent=self.user_agent,
                # 默认全屏（最大化窗口），并让 viewport 跟随真实窗口尺寸
                no_viewport=True,
                viewport=None,
            )
        except Exception:
            self._pw.stop()
            self._pw = None
            raise

        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        # 新页面弹出（window.open / target=_blank / 页面自行跳转新标签）时
        # 自动跟随最新页面，保证 screenshot/click_at 操作的是当前显示页
        self.context.on("page", self._on_page)
        # 在页面脚本执行前注入伪装，隐藏自动化痕迹（navigator.webdriver 等）。
        # 用 context 级别，保证后续新打开的标签页也自动带上。
        self.context.add_init_script(self._stealth_init_script())
        self.page.goto(url, wait_until="domcontentloaded")
        return self.page

    def _on_page(self, page: Page) -> None:
        """新页面弹出时自动切换到该页面，作为当前操作页。"""
        try:
            page.bring_to_front()
        except Exception:
            pass
        self.page = page

    def screenshot(
        self,
        path: str | Path | None = None,
        full_page: bool = False,
        type: str = "png",
        quality: int | None = None,
    ) -> Path | bytes:
        """截取当前页面。

        path: 提供则保存到该路径并返回 Path；不传则返回内存位图 bytes。
        full_page=True 时截整页（含滚动区域），否则只截当前视口。
        type: 截图格式 png/jpeg，quality 仅对 jpeg 生效（0~100）。
        """
        if self.page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        kwargs: dict = {"full_page": full_page}
        if path is not None:
            out = Path(path)
            out.parent.mkdir(parents=True, exist_ok=True)
            kwargs["path"] = str(out)
        if type != "png":
            kwargs["type"] = type
            if quality is not None:
                kwargs["quality"] = quality
        data = self.page.screenshot(**kwargs)
        return out if path is not None else data

    def _human_move(
        self,
        x: float,
        y: float,
        *,
        jitter: float = 0.03,
        steps: int | None = None,
    ) -> None:
        """把鼠标从当前位置以带随机抖动、多步贝塞尔式轨迹移到目标像素点。

        直接 mouse.click 是瞬移命中，会被行为指纹识别；这里分多步 move，
        每步加一点随机偏移和随机停顿，更接近真人手臂移动。全程约几十~上百毫秒。
        """
        if self.page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        steps = steps or random.randint(14, 26)
        if self._last_mouse is not None:
            sx, sy = self._last_mouse
        else:
            # 无历史位置时从页面中心开始移动，避免从 (0,0) 飞过去显得机械
            w = self.page.evaluate("() => ({w: innerWidth, h: innerHeight})")
            sx, sy = w["w"] / 2, w["h"] / 2
        # 每步的随机中间点（二次贝塞尔），让轨迹弯曲不沿直线
        cx = sx + (x - sx) * (0.3 + random.random() * 0.4)
        cy = sy + (y - sy) * (0.3 + random.random() * 0.4)
        for i in range(1, steps + 1):
            t = i / steps
            # 二次贝塞尔插值
            inv = (1 - t) ** 2
            px = inv * sx + 2 * (1 - t) * t * cx + t * t * x
            py = inv * sy + 2 * (1 - t) * t * cy + t * t * y
            # 终点收敛抖动，避免在目标点附近发抖
            j = jitter * (1 - t)
            px += random.uniform(-j, j)
            py += random.uniform(-j, j)
            self.page.mouse.move(px, py)
            # 微停顿：越接近终点越快，模拟真人点击前的瞄准停顿
            time.sleep(random.uniform(0.004, 0.018))
        # 末端稍作停顿，再轻点（避免与上一步过于机械衔接）
        self._last_mouse = (x, y)
        time.sleep(random.uniform(0.02, 0.09))

    def _rand_wait(self, lo: int, hi: int) -> None:
        """在 [lo, hi] 毫秒间随机等待，模拟真人反应间隔。"""
        if self.page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        self.page.wait_for_timeout(random.randint(lo, hi))

    def click_at(
        self,
        x: float,
        y: float,
        *,
        normalized: bool = True,
        wait_ms: int = 1500,
    ) -> None:
        """按坐标在页面点击。

        x, y: 若 normalized=True 视为归一化坐标 (0~1)，映射到当前视口；
               否则为像素坐标。
        wait_ms: 点击后等待的毫秒数，便于页面跳转/加载。
        """
        if self.page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        viewport = self.page.viewport_size
        if normalized:
            if not viewport:
                # no_viewport 模式下拿不到固定视口，用浏览器窗口尺寸
                window = self.page.evaluate("() => ({w: innerWidth, h: innerHeight})")
                px, py = x * window["w"], y * window["h"]
            else:
                px, py = x * viewport["width"], y * viewport["height"]
        else:
            px, py = x, y
        # 先做随机停顿，再用多步轨迹移动，最后点击
        self._rand_wait(120, 420)
        self._human_move(px, py)
        self.page.mouse.click(px, py)
        if wait_ms:
            self._rand_wait(max(0, wait_ms - 250), wait_ms + 120)

    def type_text(
        self,
        text: str,
        *,
        x: float | None = None,
        y: float | None = None,
        normalized: bool = True,
        delay_ms: int = 50,
        enter: bool = True,
        wait_ms: int = 1500,
    ) -> None:
        """点击输入框并输入文本，默认最后按回车提交。

        x, y: 指定则先按坐标点击（归一化或像素）再输入；不指定则直接
              在页面当前焦点输入（适合已聚焦的输入框）。
        normalized: x,y 为归一化坐标 (0~1) 时为 True，否则为像素。
        delay_ms: 每个字符间的输入间隔，模拟真人输入。
        enter: 输入完成后是否按回车。
        wait_ms: 输入/回车完成后的等待毫秒数。
        """
        if self.page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        if x is not None and y is not None:
            if normalized:
                viewport = self.page.viewport_size
                if not viewport:
                    window = self.page.evaluate(
                        "() => ({w: innerWidth, h: innerHeight})"
                    )
                    px, py = x * window["w"], y * window["h"]
                else:
                    px, py = x * viewport["width"], y * viewport["height"]
            else:
                px, py = x, y
            self._rand_wait(120, 400)
            self._human_move(px, py)
            self.page.mouse.click(px, py)
        self._rand_wait(150, 400)
        # 逐字输入，每字延迟在 delay_ms 基础上随机抖动，模拟真人敲击
        self.page.keyboard.type(text, delay=max(0, int(delay_ms * random.uniform(0.6, 1.4))))
        if enter:
            self._rand_wait(200, 600)
            self.page.keyboard.press("Enter")
        if wait_ms:
            self._rand_wait(max(0, wait_ms - 250), wait_ms + 120)

    def list_pages(self) -> str:
        """列出当前所有打开的标签页，返回纯文本（每行一条）。"""
        if self.context is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        lines = [f"[{i}] {p.title()} | {p.url}" for i, p in enumerate(self.context.pages)]
        return "\n".join(lines)

    def switch_page(self, index: int, wait_ms: int = 500) -> None:
        """切换到指定索引的标签页并置于前台。

        index: context.pages 中的索引（对应 list_pages() 输出的编号）。
        wait_ms: 切换后的等待毫秒数。
        """
        if self.context is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        pages = self.context.pages
        if not 0 <= index < len(pages):
            raise IndexError(f"标签页索引越界: {index}，当前共 {len(pages)} 个")
        self.page = pages[index]
        self.page.bring_to_front()
        if wait_ms:
            self.page.wait_for_timeout(wait_ms)

    def scroll(
        self,
        *,
        direction: str = "down",
        factor: float = 0.75,
        wait_ms: int = 500,
        x: float = 0.5,
        y: float = 0.5,
        normalized: bool = True,
    ) -> None:
        """滚动当前页面，每次滚动 3/4 个视口高度。

        滚动前默认把鼠标移到屏幕正中间 (0.5, 0.5)，因为 wheel 事件只
        作用于鼠标指针所在区域的滚动容器；要滚左右两侧时用 x 指定即可。

        direction: "up" 或 "down"，指定滚动方向。
        factor: 滚动量（相对视口高度比例），默认 0.75。
        wait_ms: 滚动完成后的等待毫秒数。
        x, y: 滚动前鼠标移到的位置。默认居中 (0.5, 0.5)。
        normalized: x,y 为归一化坐标 (0~1) 时为 True，否则为像素。
        """
        if self.page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        if normalized:
            if self.page.viewport_size:
                vp = self.page.viewport_size
                mx, my = x * vp["width"], y * vp["height"]
            else:
                window = self.page.evaluate("() => ({w: innerWidth, h: innerHeight})")
                mx, my = x * window["w"], y * window["h"]
        else:
            mx, my = x, y
        self._human_move(mx, my)
        if self.page.viewport_size:
            h = self.page.viewport_size["height"]
        else:
            window = self.page.evaluate("() => ({h: innerHeight})")
            h = window["h"]
        if direction == "up":
            py = -int(h * factor)
        else:
            py = int(h * factor)
        # 滚动分 2~3 次完成，更接近真人滚轮节奏
        n = random.randint(2, 3)
        step = max(1, int(py / n))
        for _ in range(n):
            self.page.mouse.wheel(0, step)
            time.sleep(random.uniform(0.02, 0.06))
        if wait_ms:
            self._rand_wait(max(0, wait_ms - 200), wait_ms + 100)

    def go_back(self, wait_ms: int = 1500) -> None:
        """浏览器后退到上一个页面。

        wait_ms: 后退后等待的毫秒数，便于页面加载。
        """
        if self.page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        self.page.go_back(wait_until="domcontentloaded")
        if wait_ms:
            self.page.wait_for_timeout(wait_ms)

    def goto(self, url: str, wait_ms: int = 1500) -> None:
        """在当前已打开的浏览器/标签页中导航到指定网址（不重新启动浏览器）。

        url: 要打开的完整网址。
        wait_ms: 加载完成后的等待毫秒数。
        """
        if self.page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        self.page.goto(url, wait_until="domcontentloaded")
        if wait_ms:
            self.page.wait_for_timeout(wait_ms)


    def close(self) -> None:
        """主动关闭上下文与浏览器，释放 profile 目录占用。

        detached=True 时若脚本直接结束（不调 close），浏览器也会保持运行；
        只有显式调用 close() 才会关闭。
        """
        if self.context is not None:
            self.context.close()
            self.context = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None
        self.page = None


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="用 Playwright 启动本地 Edge，复用已有 profile")
    parser.add_argument("url", nargs="?", default="https://www.baidu.com", help="要打开的网址")
    parser.add_argument("--profile", default=PROFILE_DIR, help="Edge 用户配置目录")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    args = parser.parse_args()

    browser = EdgeBrowser(profile_dir=args.profile, headless=args.headless)
    page = browser.start(url=args.url)
    print(f"[OK] 已打开: {args.url}")
    print(f"[OK] 标题: {page.title()}")
    print("[*] 浏览器已打开，无限等待中...（Ctrl+C 结束）")
    while True:
        import time
        time.sleep(3600)


if __name__ == "__main__":
    _main()
