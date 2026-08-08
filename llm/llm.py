"""llama.cpp 本地大模型接口客户端。

    面向"本地小模型 + 全视觉"的网页自动化场景：
- 走 llama.cpp 的 OpenAI 兼容接口 /v1/chat/completions（streaming SSE）
- 支持纯文本对话 (chat)
- 支持多模态视觉输入 (vision)，用 OpenAI 风格的 content 数组传图片
- 统一返回响应与耗时统计，便于调试小模型

"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import time
from io import BytesIO
from typing import Any

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

import requests

from tools.draw_tools import draw_coords, parse_coords


class LlamaCppClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        timeout: float = 600.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.last_response: dict[str, Any] | None = None

    # ---- 对话 ----

    def describe_page(self, user_prompt: str, image: bytes) -> str:
        """让模型结合用户指令描述当前页面，给出可落地的具体操作建议。

        复用 chat() 的视觉调用。
        """
        prompt = f"""我的任务：{user_prompt}
        
你的任务：请你简要描述一下当前的页面，并提供有价值的信息，用来帮助我完成任务。你提供的信息需要精确到有助于完成当前任务步骤的具体操作，例如点击哪里，搜集哪些信息，或者在哪里输入哪些信息，是否需要滑动页面，等等。
你只需要帮我完成当前页面的步骤就行。"""
        text = self.chat(prompt, image)
        # 去除所有全空白行（保留普通换行）
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines)

    _ACTION_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

    def decide_next_action(
        self, user_prompt: str, page_info: str, image: bytes | None = None
    ) -> str:
        """根据页面信息与用户指令，判断下一步方向（纯文本，不输出 JSON）。

        让模型先分析：当前处于任务中的哪一步，为了完成任务下一步该做什么方向。
        返回分析文本。具体浏览器操作由 execute_action 生成。
        """
        prompt = f"""你是网页自动化助手，你负责操纵浏览器执行用户给你的任务。下面是当前任务相关信息：

用户给你的任务：
{user_prompt}

当前所在的页面信息：
{page_info}

你现在应该做：
先查看当前的页面信息，然后判断当前处于任务中的哪一步，然后判断为了完成用户给的任务，下一步应该做什么。
"""
        return self.chat(prompt, image)

    def execute_action(
        self, user_prompt: str, page_info: str, decision: str, image: bytes | None = None
    ) -> dict[str, Any]:
        """根据用户指令、页面信息与上一步的方向判断，产出具体浏览器操作。

        结构化输出 JSON，动作有四种：
        - "click": 点击坐标
        - "open": 打开网页
        - "input": 点击输入框并输入文本
        - "scroll": 滚动屏幕

        复用 chat() 纯文本调用。解析失败则无限重试。
        返回形如 {"action": "...", "reason": "...", ...} 的字典。
        """
        prompt = f"""你是网页自动化助手，用户给你的任务：
{decision}

请决定下一步要执行的具体浏览器操作。下一步的可选项如下：
1 点击某个坐标
2 打开某个网页
3 在输入框里输入文本
4 滚动屏幕
只输出一个 JSON 对象，不要输出其他内容，格式如下：
{{"action": "click 或 open 或 input 或 scroll", "reason": "一句话说明为什么这样做"}}
各字段说明：

action: 下一步动作，只能是 "click"（点击坐标）、"open"（打开网页）、"input"（点击输入框并输入文本）、"scroll"（滚动屏幕）
reason: 一句话说明为什么这样做
"""

        while True:
            reply = self.chat(prompt, image)
            m = self._ACTION_JSON_RE.search(reply)
            if not m:
                continue  # 找不到 JSON，重试
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                continue  # JSON 不合法，重试
            action = data.get("action")
            if action not in ("click", "open", "input", "scroll"):
                continue  # action 非法，重试
            return data

    _ACTION_PARAMS_RE = re.compile(r"\{.*\}", re.DOTALL)

    def resolve_action_params(self, decision: str, image: bytes) -> dict[str, Any]:
        """
        """
        prompt = f"""我的任务是：
{decision}

请你在页面中找到相关的元素，以json形式输出bbox坐标
"""
        while True:
            reply = self.chat(prompt, image)
            m = self._ACTION_PARAMS_RE.search(reply)
            if not m:
                continue  # 找不到 JSON，重试
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                continue  # JSON 不合法，重试
            if not isinstance(data, dict):
                continue
            return data

    def resolve_input_params(self, decision: str, image: bytes) -> dict[str, Any]:
        """根据上一步的动作决策与截图，补全 input 动作的具体参数（JSON）。

        只接收上一步的 decision（含 action/reason）与截图 image，
        输出 bbox_2d（输入框包围框）与 text（要输入的完整文本）。
        复用 chat() 视觉调用。解析失败则无限重试。
        """
        prompt = f"""你是网页自动化助手。上一步已经决定要执行"在输入框输入文本"动作，现在请你根据截图，补全该动作所需的具体参数。

上一步的动作决策（JSON）：
{decision}

你现在应该做：
结合截图，找到目标输入框，只输出一个 JSON 对象，包含该动作需要的字段。不要输出其他内容。

需要输出的字段如下：
- "input"：输出 bbox_2d = [x1, y1, x2, y2]，即要点击的输入框包围框，四个值都是 0~1000 的千分比坐标（x1,y1 为左上角，x2,y2 为右下角）；同时输出 text = "要输入的完整文本"。

坐标 bbox_2d 请严格对照截图计算，所有值都是 0~1000 的千分比。
只输出 JSON，不要输出其他内容，示例：
{{"bbox_2d": [x1, y1, x2, y2], "text": "要输入的文本"}}
"""
        while True:
            reply = self.chat(prompt, image)
            m = self._ACTION_PARAMS_RE.search(reply)
            if not m:
                continue  # 找不到 JSON，重试
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                continue  # JSON 不合法，重试
            if not isinstance(data, dict):
                continue
            return data

    def resolve_open_params(self, decision: str, image: bytes) -> dict[str, Any]:
        """根据上一步的动作决策与截图，补全 open 动作的具体参数（JSON）。

        只接收上一步的 decision（含 action/reason）与截图 image，
        输出 url 字段。复用 chat() 视觉调用。解析失败则无限重试。
        """
        prompt = f"""你是网页自动化助手。上一步已经决定要执行"打开网页"动作，现在请你补全该动作的具体参数。

上一步的动作决策（JSON）：
{decision}

你现在应该做：
根据上一步的决策，只输出一个 JSON 对象，包含该动作需要的字段。不要输出其他内容。

需要输出的字段如下：
- "open"：输出 url = "要打开的完整网址"（以 http:// 或 https:// 开头）。

只输出 JSON，不要输出其他内容，示例：
{{"url": "https://www.example.com"}}
"""
        while True:
            reply = self.chat(prompt, image)
            m = self._ACTION_PARAMS_RE.search(reply)
            if not m:
                continue  # 找不到 JSON，重试
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                continue  # JSON 不合法，重试
            if not isinstance(data, dict):
                continue
            return data

    def resolve_scroll_params(self, decision: str, image: bytes) -> dict[str, Any]:
        """根据上一步的动作决策与截图，补全 scroll 动作的具体参数（JSON）。

        只接收上一步的 decision（含 action/reason）与截图 image，
        输出 scroll_dir 字段。复用 chat() 视觉调用。解析失败则无限重试。
        """
        prompt = f"""你是网页自动化助手。上一步已经决定要执行"滚动屏幕"动作，现在请你补全该动作的具体参数。

上一步的动作决策（JSON）：
{decision}

你现在应该做：
根据上一步的决策，只输出一个 JSON 对象，包含该动作需要的字段。不要输出其他内容。

需要输出的字段如下：
- "scroll"：输出 scroll_dir = "up" 或 "down"。

只输出 JSON，不要输出其他内容，示例：
{{"scroll_dir": "down"}}
"""
        while True:
            reply = self.chat(prompt, image)
            m = self._ACTION_PARAMS_RE.search(reply)
            if not m:
                continue  # 找不到 JSON，重试
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                continue  # JSON 不合法，重试
            if not isinstance(data, dict):
                continue
            return data

    _BBOX_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

    def locate_element(
        self,
        user_prompt: str,
        page_info: str,
        reason: str,
        image: bytes,
    ) -> tuple[float, float]:
        """让视觉模型根据指令、页面信息与原因，定位目标元素中心点。

        输出结构化 JSON，含 point_2d = [x, y]（千分比 0~1000 坐标）。
        复用 chat() 的视觉调用。解析失败则无限重试。
        内部换算成归一化 (0~1)，返回 (x, y)。
        """
        prompt = (
            "你是网页自动化助手。用户想在当前页面上采取操作，"
            "下面是相关上下文：\n\n"
            "【用户指令】\n"
            f"{user_prompt}\n\n"
            "【当前页面信息】\n"
            f"{page_info}\n\n"
            "【当前动作原因】\n"
            f"{reason}\n\n"
            "请结合这张截图，定位用户指令要操作的目标元素的中心点，"
            "并输出它的坐标。\n"
            "只输出一个 JSON 对象，不要输出其他内容，格式如下：\n"
            '{"point_2d": [x, y], "description": "目标元素一句话说明"}\n'
        )
        while True:
            reply = self.chat(prompt, image)
            m = self._BBOX_JSON_RE.search(reply)
            if not m:
                continue
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
            pt = data.get("point_2d")
            if not (isinstance(pt, list) and len(pt) == 2):
                continue
            x, y = float(pt[0]), float(pt[1])
            if not (0.0 <= x <= 1000.0 and 0.0 <= y <= 1000.0):
                continue
            return (x / 1000.0, y / 1000.0)

    def chat(
        self,
        prompt: str,
        image: bytes | None = None,
    ) -> str:
        """发送对话，返回助手回复文本。

        prompt: 用户输入
        image: 可选位图 bytes；传入则走多模态视觉，为空则纯文本。
        """
        # 构造 OpenAI 风格消息；有图时 content 为数组
        if image is not None:
            img_b64 = base64.b64encode(image).decode("ascii")
            content: Any = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_b64}"
                    },
                },
            ]
        else:
            content = prompt
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": content}],
            "stream": True,
        }
        url = f"{self.base_url}/v1/chat/completions"
        with requests.post(url, json=payload, timeout=self.timeout, stream=True) as resp:
            resp.raise_for_status()
            # llama.cpp SSE 响应头不带 charset，requests 会误判为 ISO-8859-1，
            # 导致 UTF-8 中文解码乱码，这里强制用 UTF-8。
            resp.encoding = "utf-8"
            in_thinking = False
            content_out = ""
            thinking = ""
            pending = ""
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                # 思考内容里的换行可能把单个 SSE 事件拆成多行，缓冲直到能完整解析
                pending += data_str
                try:
                    chunk = json.loads(pending)
                except json.JSONDecodeError:
                    continue
                pending = ""
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                t = delta.get("reasoning_content")
                c = delta.get("content")
                if t:
                    if not in_thinking:
                        in_thinking = True
                        print("Thinking:\n", end="", flush=True)
                    print(t, end="", flush=True)
                    thinking += t
                elif c:
                    if in_thinking:
                        in_thinking = False
                        print("\n\nAnswer:\n", end="", flush=True)
                    print(c, end="", flush=True)
                    content_out += c
            if in_thinking:
                print("\n\nAnswer:\n", end="", flush=True)
            print()
        self.last_response = {"message": {"content": content_out, "thinking": thinking}}
        return content_out


# ---- llama.cpp 服务自动启动（main.py 与 llm.py 共用）----

LLAMA_CPP_DIR = Path(r"C:\Users\z\llama-cpp")
LLAMA_SERVER_EXE = LLAMA_CPP_DIR / "llama-server.exe"
MODELS_DIR = Path(r"C:\Users\z\.lmstudio\models")
SERVER_URL = "http://127.0.0.1:8080"


def _server_ready(timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{SERVER_URL}/health", timeout=2)
            if r.ok and r.json().get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _scan_models() -> list[dict]:
    """递归扫描 models 目录，找出可用的模型（主 .gguf + 可选 mmproj）。

    过滤规则：
    - 只看 *.gguf（忽略 .part 等未下载完成文件）
    - 排除 mmproj-*.gguf（那是视觉投影，不是主模型）
    - 同一目录下的 mmproj-*.gguf 作为该模型的视觉投影配套
    返回 [{name, model_path, mmproj_path}]，按文件大小降序。
    """
    found: list[dict] = []
    for gguf in MODELS_DIR.rglob("*.gguf"):
        if gguf.name.lower().startswith("mmproj-"):
            continue
        mmproj = next(gguf.parent.glob("mmproj-*.gguf"), None)
        found.append(
            {
                "name": gguf.name,
                "model_path": gguf,
                "mmproj_path": mmproj,
            }
        )
    found.sort(key=lambda m: m["model_path"].stat().st_size, reverse=True)
    return found


def _choose_item(options: list[tuple[str, str]]) -> int:
    """上下键选择、回车确认。返回选中项的索引。"""
    import msvcrt

    n = len(options)
    idx = 0
    first = True
    sys.stdout.write("\x1b[?25l")  # 隐藏光标
    sys.stdout.flush()
    try:
        while True:
            if not first:
                # 光标此刻在最后一行末尾：先回到块首，再逐行清空重绘
                sys.stdout.write(f"\x1b[{n-1}A\x1b[0G" if n > 1 else "\x1b[0G")
            for i, (label, hint) in enumerate(options):
                sys.stdout.write("\x1b[2K")  # 清空当前行
                if i == idx:
                    sys.stdout.write(f"\x1b[7m  > {label:<8} {hint}  \x1b[0m")
                else:
                    sys.stdout.write(f"    {label:<8} {hint}")
                if i < n - 1:
                    sys.stdout.write("\n")
            sys.stdout.flush()
            first = False

            key = msvcrt.getch()
            if key in (b"\x00", b"\xe0"):  # 功能键前缀
                key2 = msvcrt.getch()
                if key2 == b"H":  # 上
                    idx = (idx - 1) % n
                elif key2 == b"P":  # 下
                    idx = (idx + 1) % n
            elif key in (b"\r", b"\n"):  # 回车确认
                break
    finally:
        sys.stdout.write("\x1b[?25h\x1b[0G")  # 恢复光标
        sys.stdout.flush()
    return idx


def _choose_think() -> bool:
    """上下键选择思考开关，回车确认。返回 True=开 / False=关。"""
    options = [
        ("开启思考", "--reasoning on"),
        ("关闭思考", "--reasoning off"),
    ]
    return _choose_item(options) == 0


def _choose_model(models: list[dict]) -> dict:
    """上下键选择要加载的模型，回车确认。返回选中模型的 dict。"""
    if not models:
        print("[!] 未找到任何可用模型")
        sys.exit(1)
    if len(models) == 1:
        return models[0]
    options = [(m["name"], "多模态" if m["mmproj_path"] else "纯文本") for m in models]
    return models[_choose_item(options)]


def ensure_llama_server(think: bool | None = None, model: dict | None = None) -> dict | None:
    """若 8080 端口没有 llama.cpp 服务，则自动启动。

    think: True 开思考 / False 关思考 / None 用上下键交互选择。
    model: 指定模型 dict；None 则扫描并用上下键交互选择。
    服务已在运行时直接返回（返回 None）。
    返回实际选用的模型 dict（服务未运行并成功启动时）。
    """
    try:
        requests.get(f"{SERVER_URL}/health", timeout=2)
        print(f"[*] 检测到 llama.cpp 已在运行: {SERVER_URL}")
        return None
    except Exception:
        pass

    if model is None:
        print("[*] 扫描可用模型...")
        model = _choose_model(_scan_models())

    if think is None:
        think = _choose_think()

    model_path = model["model_path"]
    mmproj_path = model.get("mmproj_path")

    print(f"[*] 加载模型: {model_path.name}  "
          f"({'多模态' if mmproj_path else '纯文本'})")
    print(f"[*] 思考模式: {'开' if think else '关'}，正在启动...")
    args = [
        str(LLAMA_SERVER_EXE),
        "-m", str(model_path),
    ]
    if mmproj_path is not None:
        args += ["--mmproj", str(mmproj_path)]
    args += [
        "-c", "10000",
        "--port", "8080",
        "--reasoning", "on" if think else "off",
    ]
    if not LLAMA_SERVER_EXE.exists():
        print(f"[!] 找不到 {LLAMA_SERVER_EXE}，请手动启动服务")
        sys.exit(1)
    # 独立进程启动，不阻塞主程序
    subprocess.Popen(args, cwd=str(LLAMA_CPP_DIR), stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)

    if not _server_ready():
        print("[!] llama.cpp 服务启动超时")
        sys.exit(1)
    print("[*] llama.cpp 服务已就绪")
    return model


_TEST_IMAGE_PATH = r"C:\Users\z\Desktop\project\AiBrowserForSmaLLM\test2.PNG"


def _make_test_image() -> bytes:
    with open(_TEST_IMAGE_PATH, "rb") as f:
        return f.read()



BBOX_SCHEMA = {
    "type": "object",
    "properties": {
        "bbox_2d": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 4,
            "maxItems": 4,
        }
    },
    "required": ["bbox_2d"],
}

_PROMPT = """我的任务是：
回复楼中楼

请你在页面中找到相关的元素，以json形式输出bbox坐标"""


def _main() -> None:
    ensure_llama_server()
    client = LlamaCppClient()

    print(f"[*] 测试调用 llama.cpp: {client.base_url}")
    print("-" * 60)

    t0 = time.perf_counter()

    reply = client.chat(_PROMPT, _make_test_image())
    dt = time.perf_counter() - t0
    print(f"\n[time] {dt:.2f}s")

    # 在测试原图上画出模型输出的坐标并保存，便于核对对准是否准确
    try:
        with Image.open(BytesIO(_make_test_image())) as im:
            img_w, img_h = im.size
            coords = parse_coords(reply, img_w=img_w, img_h=img_h)
            marked = draw_coords(im.convert("RGB"), coords)
        out_path = Path(_TEST_IMAGE_PATH).with_name(
            f"{Path(_TEST_IMAGE_PATH).stem}_marked{Path(_TEST_IMAGE_PATH).suffix}"
        )
        marked.save(out_path)
        print(f"[OK] 已绘制 {len(coords)} 个坐标 -> {out_path}")
    except Exception as e:
        print(f"[!] 绘制坐标失败: {e}")


if __name__ == "__main__":
    _main()
