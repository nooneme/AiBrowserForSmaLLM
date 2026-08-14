

from pathlib import Path
from datetime import datetime
from io import BytesIO
import json

from PIL import Image

from browser.edge_browser import EdgeBrowser
from llm.llm import LlamaCppClient
from tools.draw_tools import _normalize_coords, draw_coords
from tools.history_tools import append_history, new_history

# 用户指令从本地文本文件读取，程序运行时可中途修改
PROMPT_FILE = Path(__file__).resolve().parent / "user_prompt.txt"
DEFAULT_PROMPT = "打开百度贴吧"

URL = "https://www.baidu.com"


def load_user_prompt() -> str:
    """每次调用时从本地文本文件读取用户指令。"""
    try:
        text = PROMPT_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        text = DEFAULT_PROMPT
    return text or DEFAULT_PROMPT

# llama.cpp 本地接口（OpenAI 兼容），默认端口 8080。
# 模型在启动时通过菜单选择。

SHOTS_DIR = Path(__file__).resolve().parent / "shots"


def main() -> None:
    browser = EdgeBrowser()
    browser.start(url=URL)
    # 固定等待 5 秒，确保页面充分加载
    browser.page.wait_for_timeout(5000)

    client = LlamaCppClient()

    operation_history = new_history()
    while True:
        try:
            # 第一步：截图并让模型结合用户指令与操作历史判断下一步操作（纯文本分析）
            shot = browser.screenshot()
            user_prompt = load_user_prompt()  # 每次循环从文件读取，运行时可改
            direction = client.decide_next_step(user_prompt, shot, operation_history)
            #print(f"[direction] {direction}")

            # 第二步：让模型基于方向判断产出具体浏览器操作（JSON）
            decision = client.execute_action(direction, shot)
            #print(f"下一步: {decision}")
            action = decision.get("action")

            # 上一步只产出 action/reason，仅当动作为 click/input 时补全具体参数
            if action == "click":
                params = client.resolve_action_params(json.dumps(decision), direction, shot)
                decision.update(params)
                #print(f"[params] {params}")
            elif action == "input":
                params = client.resolve_input_params(json.dumps(decision), direction, shot)
                decision.update(params)
            elif action == "open":
                params = client.resolve_open_params(json.dumps(decision), direction, shot)
                decision.update(params)
            elif action == "scroll":
                params = client.resolve_scroll_params(json.dumps(decision), direction, shot)
                decision.update(params)

            # 第三步：执行动作
            if action == "click":
                box = decision.get("bbox_2d") or [0, 0, 0, 0]
                with Image.open(BytesIO(shot)) as im:
                    im = im.convert("RGB")
                    x1, y1, x2, y2 = _normalize_coords(tuple(float(v) for v in box), *im.size)[:4]
                    x, y = (x1 + x2) / 2, (y1 + y2) / 2
                print(f"[act] click: ({x:.3f}, {y:.3f})")
                browser.click_at(x, y, wait_ms=3000)

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
            elif action == "refresh":
                print("[act] refresh")
                browser.refresh()
            else:
                print(f"[!] 未知动作: {action}")
                continue

            action = decision.get("action")
            reason = decision.get("reason")
            text = f"动作={action}"
            if reason:
                text += f"；原因={reason}"
            operation_history = append_history(operation_history, text)
        except Exception as e:
            print(f"[!] 出错，重试: {e}")
            continue


if __name__ == "__main__":
    main()
