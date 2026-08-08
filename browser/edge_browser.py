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

from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

PROFILE_DIR = r"C:\Users\z\Desktop\project\qiongguicode\edge_manual_profile"


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

    def _launch_args(self) -> list[str]:
        args: list[str] = []
        # 默认最大化窗口打开（配合 no_viewport 跟随真实窗口尺寸）
        args.append("--start-maximized")
        # 避免复用 profile 时提示"正在使用此配置"
        if self.headless:
            args.append("--headless")
        return args

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
        self.page.mouse.click(px, py)
        if wait_ms:
            self.page.wait_for_timeout(wait_ms)

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
            self.page.mouse.click(px, py)
        self.page.keyboard.type(text, delay=delay_ms)
        if enter:
            self.page.keyboard.press("Enter")
        if wait_ms:
            self.page.wait_for_timeout(wait_ms)

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
    ) -> None:
        """滚动当前页面，每次滚动 3/4 个视口高度。

        direction: "up" 或 "down"，指定滚动方向。
        factor: 滚动量（相对视口高度比例），默认 0.75。
        wait_ms: 滚动完成后的等待毫秒数。
        """
        if self.page is None:
            raise RuntimeError("浏览器尚未启动，请先调用 start()")
        if self.page.viewport_size:
            h = self.page.viewport_size["height"]
        else:
            window = self.page.evaluate("() => ({h: innerHeight})")
            h = window["h"]
        if direction == "up":
            py = -int(h * factor)
        else:
            py = int(h * factor)
        self.page.mouse.wheel(0, py)
        if wait_ms:
            self.page.wait_for_timeout(wait_ms)

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
    parser.add_argument("url", nargs="?", default="https://www.bing.com", help="要打开的网址")
    parser.add_argument("--profile", default=PROFILE_DIR, help="Edge 用户配置目录")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    args = parser.parse_args()

    browser = EdgeBrowser(profile_dir=args.profile, headless=args.headless)
    try:
        page = browser.start(url=args.url)
        print(f"[OK] 已打开: {args.url}")
        print(f"[OK] 标题: {page.title()}")
        input("按回车退出...")
    finally:
        browser.close()


if __name__ == "__main__":
    _main()
