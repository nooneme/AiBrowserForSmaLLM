"""llama.cpp 本地大模型接口客户端。

    面向"本地小模型 + 全视觉"的网页自动化场景：
- 走 llama.cpp 的 OpenAI 兼容接口 /v1/chat/completions（streaming SSE）
- 支持纯文本对话 (chat)
- 支持多模态视觉输入 (vision)，用 OpenAI 风格的 content 数组传图片
- 统一返回响应与耗时统计，便于调试小模型

（llama.cpp 服务的启动与模型注册已移至 start_server.py。）

"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import requests


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

    def decide_next_step(
        self, user_prompt: str, image: bytes, operation_history: str
    ) -> str:
        """让模型结合截图与用户指令，直接判断下一步应执行什么操作。

        输入当前截图、用户任务与已执行的操作历史，一步输出具体操作建议
        （纯文本，不输出 JSON）。复用 chat() 的视觉调用。
        """
        prompt = f"""你是网页自动化助手，你负责操纵浏览器执行用户给你的任务。

用户给你的任务：
{user_prompt}

最近的操作历史：
{operation_history}

现在请你：
先查看当前的截图，结合已执行的操作历史，判断当前处于任务中的哪一步，然后回答，为了完成用户任务，下一步应该执行什么操作（例如点击哪里、在哪个输入框输入什么、打开哪个网址、往哪个方向滚动等）
用自然语言回复，不要回复代码，命令，或者json等等
"""
        text = self.chat(prompt, image)
        # 去除所有全空白行（保留普通换行）
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines)

    _ACTION_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

    def execute_action(
        self, decision: str, image: bytes | None = None
    ) -> dict[str, Any]:
        """
        """
        prompt = f"""你是网页自动化助手，你负责操纵浏览器执行用户给你的任务：
{decision}

要完成这个任务，下一步要执行的具体浏览器操作是什么？操作的可选项如下：
1 点击某个坐标
2 打开某个网页
3 在输入框里输入文本
4 滚动屏幕
5 刷新当前页面

只输出一个 JSON 对象，不要输出其他内容，格式如下：
{{"action": "click 或 open 或 input 或 scroll 或 refresh", "reason": "一句话说明为什么这样做"}}

各字段说明：
action: 下一步动作，只能是 "click"（点击坐标）、"open"（打开网页）、"input"（点击输入框并输入文本）、"scroll"（滚动屏幕）、"refresh"（刷新当前页面）
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
            if action not in ("click", "open", "input", "scroll", "refresh", "task_complete"):
                continue  # action 非法，重试
            return data

    _ACTION_PARAMS_RE = re.compile(r"\{.*\}", re.DOTALL)

    def resolve_action_params(self, decision: str, direction: str, image: bytes) -> dict[str, Any]:
        """根据上一步的动作决策与当前操作信息、截图，补全 click 动作的具体参数（JSON）。

        输出 bbox_2d（目标元素包围框）。复用 chat() 视觉调用。解析失败则无限重试。
        """
        prompt = f"""你是网页自动化助手，当前需要执行的操作：
{decision}

请你在页面中找到相关的元素，以json形式输出其bbox坐标
示例：
{{"bbox_2d": [x1, y1, x2, y2], "label": "button text"}}
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

    def resolve_input_params(self, decision: str, direction: str, image: bytes) -> dict[str, Any]:
        """根据上一步的动作决策与当前操作信息、截图，补全 input 动作的具体参数（JSON）。

        只接收上一步的 decision（含 action/reason）、当前操作信息 direction 与截图 image，
        输出 bbox_2d（输入框包围框）与 text（要输入的完整文本）。
        复用 chat() 视觉调用。解析失败则无限重试。
        """
        prompt = f"""你是网页自动化助手。现在需要执行"在输入框输入文本"动作，现在请你根据截图，补全该动作所需的具体参数。

上一步的动作决策（JSON）：
{decision}

当前操作的信息：
{direction}

你现在应该做：
结合截图，找到目标输入框，只输出一个 JSON 对象，包含该动作需要的字段。不要输出其他内容。

需要输出的字段如下：
- "input"：输出 bbox_2d = [x1, y1, x2, y2]，即要点击的输入框bbox； "text" = "要输入的完整文本"。

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

    def resolve_open_params(self, decision: str, direction: str, image: bytes) -> dict[str, Any]:
        """根据上一步的动作决策与当前操作信息、截图，补全 open 动作的具体参数（JSON）。

        只接收上一步的 decision（含 action/reason）、当前操作信息 direction 与截图 image，
        输出 url 字段。复用 chat() 视觉调用。解析失败则无限重试。
        """
        prompt = f"""你是网页自动化助手。现在需要执行"打开网页"动作，现在请你补全具体要打开什么网页。

当前的操作：
{direction}

{decision}

请输出一个 JSON 对象，包含该动作需要的字段。不要输出其他内容。

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

    def resolve_scroll_params(self, decision: str, direction: str, image: bytes) -> dict[str, Any]:
        """根据上一步的动作决策与当前操作信息、截图，补全 scroll 动作的具体参数（JSON）。

        只接收上一步的 decision（含 action/reason）、当前操作信息 direction 与截图 image，
        输出 scroll_dir 字段。复用 chat() 视觉调用。解析失败则无限重试。
        """
        prompt = f"""你是网页自动化助手。现在需要执行"滚动屏幕"动作，现在请你补全要往上面还是下面滚动网页。

当前的操作：
{direction}

{decision}

请输出一个 JSON 对象，包含该动作需要的字段。不要输出其他内容。

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
        reasoning_effort: str = "low",
        enable_thinking: bool = True,
        preserve_thinking: bool = True,
    ) -> str:
        """发送对话，返回助手回复文本。

        prompt: 用户输入
        image: 可选位图 bytes；传入则走多模态视觉，为空则纯文本。
        reasoning_effort: 推理深度，支持 xhigh / medium / low。
        enable_thinking: 是否启用思考。
        preserve_thinking: 是否在回复中保留思考内容。
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
            "reasoning_effort": reasoning_effort,
            "chat_template_kwargs": {
                "enable_thinking": enable_thinking,
                "preserve_thinking": preserve_thinking,
            },
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


def main() -> None:
    """测试入口。

    自动读取 llm 目录下的图片（.png/.jpg/.jpeg/.webp/.bmp，若有）作为视觉输入，
    否则回退为纯文本。
    """
    client = LlamaCppClient()

    image: bytes | None = None
    for name in os.listdir(os.path.dirname(__file__)):
        if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            with open(os.path.join(os.path.dirname(__file__), name), "rb") as f:
                image = f.read()
            print(f"使用图片: {name}")
            break

    if image is None:
        print("未找到图片，使用纯文本模式。")

    prompt = "你好" if image is None else "这里面的选项哪些是可选的？哪些是缺货的？通过字体颜色深浅判断"
    reply = client.chat(prompt, image)
    
    


if __name__ == "__main__":
    main()
