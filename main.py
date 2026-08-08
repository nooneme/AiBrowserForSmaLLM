

USER_PROMPT = "打开百度贴吧坦克世界吧，持续打开帖子并回复帖子"

CURRENT_PAGE_INFO = "TODO: 当前页面信息（标题/URL/内容等）"

URL = "https://www.baidu.com"

from pathlib import Path
from datetime import datetime
from io import BytesIO
import json

from PIL import Image

from browser.edge_browser import EdgeBrowser
from llm.llm import LlamaCppClient, ensure_llama_server
from tools.draw_tools import _normalize_coords, draw_coords

# llama.cpp 本地接口（OpenAI 兼容），默认端口 8080。
# 模型在启动时通过菜单选择。

SHOTS_DIR = Path(__file__).resolve().parent / "shots"


def main() -> None:
    global CURRENT_PAGE_INFO
    ensure_llama_server()
    browser = EdgeBrowser()
    browser.start(url=URL)

    client = LlamaCppClient()

    try:
        while True:
            # 第一步：截图并让模型结合用户指令描述页面结构
            shot = browser.screenshot()
            CURRENT_PAGE_INFO = client.describe_page(USER_PROMPT, shot)
            #print(CURRENT_PAGE_INFO)

            # 第二步：让模型先判断下一步方向（纯文本分析）
            direction = client.decide_next_action(USER_PROMPT, CURRENT_PAGE_INFO, shot)
            #print(f"[direction] {direction}")

            # 第三步：让模型基于方向判断产出具体浏览器操作（JSON）
            decision = client.execute_action(USER_PROMPT, CURRENT_PAGE_INFO, direction, shot)
            #print(f"下一步: {decision}")
            action = decision.get("action")

            # 上一步只产出 action/reason，仅当动作为 click/input 时补全具体参数
            if action == "click":
                params = client.resolve_action_params(json.dumps(decision), shot)
                decision.update(params)
                #print(f"[params] {params}")
            elif action == "input":
                params = client.resolve_input_params(json.dumps(decision), shot)
                decision.update(params)
            elif action == "open":
                params = client.resolve_open_params(json.dumps(decision), shot)
                decision.update(params)
            elif action == "scroll":
                params = client.resolve_scroll_params(json.dumps(decision), shot)
                decision.update(params)

            # 第三步：执行动作
            if action == "click":
                box = decision.get("bbox_2d") or [0, 0, 0, 0]
                with Image.open(BytesIO(shot)) as im:
                    im = im.convert("RGB")
                    x1, y1, x2, y2 = _normalize_coords(tuple(float(v) for v in box), *im.size)[:4]
                    x, y = (x1 + x2) / 2, (y1 + y2) / 2
                print(f"[act] click: ({x:.3f}, {y:.3f})")
                browser.click_at(x, y)

                # 把点击位置画到原图上并保存，便于核对坐标是否对准
                SHOTS_DIR.mkdir(parents=True, exist_ok=True)
                marked = draw_coords(im, [(x, y)])
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                out_path = SHOTS_DIR / f"click_{stamp}.png"
                marked.save(out_path)
                print(f"[OK] 已保存带标记点截图: {out_path}")
            elif action == "open":
                url = decision.get("url") or ""
                print(f"[act] open: {url}")
                browser.goto(url)
            elif action == "input":
                box = decision.get("bbox_2d") or [0, 0, 0, 0]
                with Image.open(BytesIO(shot)) as im:
                    x1, y1, x2, y2 = _normalize_coords(tuple(float(v) for v in box), *im.size)[:4]
                    x, y = (x1 + x2) / 2, (y1 + y2) / 2
                text = decision.get("text") or ""
                print(f"[act] input: ({x:.3f}, {y:.3f}) text={text}")
                browser.type_text(text=text, x=x, y=y)
            elif action == "scroll":
                direction = decision.get("scroll_dir") or "down"
                print(f"[act] scroll: {direction}")
                browser.scroll(direction=direction)
            else:
                print(f"[!] 未知动作: {action}")
                continue

            # TODO: 任务完成判断，满足条件后 break 跳出
            continue
    finally:
        browser.close()


if __name__ == "__main__":
    main()
