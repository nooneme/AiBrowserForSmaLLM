"""Ollama 本地大模型接口客户端。

    面向"本地小模型 + 全视觉"的网页自动化场景：
- 走 Ollama 原生接口 /api/chat（遵循官方文档）
- 支持纯文本对话 (chat)
- 支持多模态视觉输入 (vision)，images 放在每条 message 内部
- 统一返回响应与耗时统计，便于调试小模型

官方文档：https://docs.ollama.com/llms.txt  Vision 章节
关键点：images 数组必须位于 message 对象内部，而非请求体顶层。
"""

from __future__ import annotations

import base64
import sys
import time
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image

import requests

from tools.draw_bbox import draw_bboxes, extract_bboxes


class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3-vl:8b",
        timeout: float = 600.0,
        think: bool | str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.think = think
        self.last_response: dict[str, Any] | None = None

    def _with_think(self, options: dict, think: bool | str | None) -> dict:
        """将 think 参数合并进 options；None 表示不改动、用模型默认。"""
        if think is None:
            think = self.think
        if think is not None:
            options["think"] = think
        return options

    # ---- 底层请求 ----

    def _post(self, path: str, payload: dict) -> Any:
        url = f"{self.base_url}{path}"
        resp = requests.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        resp = requests.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def list_models(self) -> list[str]:
        """列出本地已安装的模型名。"""
        return [m["name"] for m in self._get("/api/tags")["models"]]

    # ---- 文本对话 ----

    def chat(self, messages: list[dict], **options: Any) -> dict:
        """发送对话消息，返回 ollama 完整响应。

        messages: 每项 {"role", "content", 可选 "images"}
        """
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False}
        payload.update(options)
        resp = self._post("/api/chat", payload)
        self.last_response = resp
        return resp

    # ---- 视觉（多模态）----

    def chat_with_images(
        self,
        prompt: str,
        images: list[str | bytes],
        system: str | None = None,
        **options: Any,
    ) -> dict:
        """发送带图片的对话，返回原始响应。

        prompt: 用户文本
        images: 图片列表，每项为 base64 字符串或原始 bytes
        按官方文档，images 放在 message 内部。
        """
        encoded: list[str] = []
        for img in images:
            if isinstance(img, bytes):
                img = base64.b64encode(img).decode("ascii")
            encoded.append(img)

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append(
            {"role": "user", "content": prompt, "images": encoded}
        )
        return self.chat(messages, **options)

    def _reply(self, resp: dict) -> str:
        return resp["message"]["content"]

    # ---- 便捷方法 ----

    def ask(self, prompt: str, system: str | None = None, think: bool | str | None = None) -> str:
        """最简单的文本对话入口，返回助手回复文本。"""
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self._reply(self.chat(messages, **self._with_think({}, think)))

    def ask_with_screenshot(
        self,
        prompt: str,
        image_base64: str,
        think: bool | str | None = None,
        **options: Any,
    ) -> str:
        """传入截图，返回助手对图的分析结果（全视觉核心入口）。

        可选传入 format 等选项，如 format=BBOX_SCHEMA 强制结构化输出。
        think: True/False/str 显式控制思考模式，None 用模型默认。
               Ollama 合法值：high / medium / low / max / true / false
               （传 off 会报错 "invalid think value"）。
               注意：实测 qwen3-vl:8b 的 think=false 不能完全关闭思考。
        """
        return self._reply(
            self.chat_with_images(prompt, [image_base64], **self._with_think(options, think))
        )


_TEST_IMAGE_PATH = r"C:\Users\z\Desktop\project\AiBrowserForSmaLLM\testpic.PNG"


def _make_test_image() -> bytes:
    with open(_TEST_IMAGE_PATH, "rb") as f:
        return f.read()


# 注：实测 qwen3-vl:8b 对 Ollama 的 JSON Schema 结构化输出(format)支持很差，
# 同样图+prompt 从 ~8s 慢到 ~104s(13x)，且定位结果变差，故这里不使用 format。
# 保留 schema 定义仅供参考/换更强模型时使用。
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

_PROMPT = "在页面上找出所有用户头像，以JSON格式输出其bbox坐标。"


def _main() -> None:
    client = OllamaClient(model="qwen3-vl:30b")

    print(f"[*] 测试调用 Ollama: {client.base_url}  模型: {client.model}")
    print(f"[*] 模型列表: {client.list_models()}")
    print("-" * 60)

    t0 = time.perf_counter()
    try:
        # 注：实测 qwen3-vl:8b 的 think=false 不能完全关闭思考（thinking 字段仍在），
        # 只是降低思考 token 量；think 参数已显式接入 ask/ask_with_screenshot。
        reply = client.ask_with_screenshot(
            _PROMPT,
            base64.b64encode(_make_test_image()).decode("ascii"),
            
        )
        dt = time.perf_counter() - t0
        print(f"[OK] 视觉回复 ({dt:.1f}s):\n{reply}")

        # 打印思考过程（若存在）
        msg = client.last_response["message"]
        thinking = msg.get("thinking")
        if thinking:
            print("-" * 60)
            print("[THINKING]:\n" + thinking)

        # 打印每次调用的上下文/耗时统计
        resp = client.last_response
        stats = {k: resp[k] for k in
                 ("prompt_eval_count", "eval_count", "total_duration",
                  "prompt_eval_duration", "eval_duration", "load_duration")
                 if k in resp}
        print("-" * 60)
        print("[STATS] 输入上下文 tokens: {prompt_eval_count}, 输出 tokens: {eval_count}".format(**stats))
        for k in ("total_duration", "prompt_eval_duration", "eval_duration", "load_duration"):
            if k in stats:
                print(f"[STATS] {k}: {stats[k] / 1e9:.2f}s")

        with Image.open(_TEST_IMAGE_PATH) as im0:
            img_w, img_h = im0.size

        boxes = extract_bboxes(reply, img_w, img_h)
        print(f"[OK] 解析到 {len(boxes)} 个 bbox: {boxes}")

        out_path = r"C:\Users\z\Desktop\project\AiBrowserForSmaLLM\llm_bbox.png"
        with Image.open(_TEST_IMAGE_PATH) as im:
            im = im.convert("RGB")
            result = draw_bboxes(im, boxes)
        result.save(out_path)
        print(f"[OK] 已绘制到 {out_path}")
    except Exception as e:
        print(f"[FAIL] 视觉调用失败: {e}")


if __name__ == "__main__":
    _main()
