"""启动 llama.cpp 服务的独立脚本。

双击运行（配合同目录的 start_server.bat），或在终端执行：
    python llm/start_server.py
通过上下键交互选择模型与思考开关，启动后保持前台运行，便于查看日志。
启动成功后会把这个模型自动注册进 opencode 的全局配置
（~/.config/opencode/opencode.jsonc），方便直接在 opencode 里选用。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

OPENCODE_CONFIG = Path.home() / ".config" / "opencode" / "opencode.jsonc"
LLAMA_BASE_URL = "http://127.0.0.1:8080/v1"
LLAMA_PROVIDER = "llama"
LLAMA_CONTEXT_DEFAULT = 4000

# ---- llama.cpp 服务自动启动 ----

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


def _enable_vt() -> None:
    """启用 Windows 控制台的 ANSI 转义序列支持（VT 处理）。

    双击 bat 启动的 cmd 窗口默认不开启 VT，导致方向键菜单里的光标移动、
    反色等 ANSI 转义失效。这里通过 WinAPI SetConsoleMode 强制开启。
    """
    if sys.platform != "win32":
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    if handle == -1 or handle == 0:
        return
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return
    kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


def _choose_item(options: list[tuple[str, str]]) -> int:
    """上下键选择、回车确认。返回选中项的索引。"""
    import msvcrt

    _enable_vt()

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
        "-ngl", "-1",
        "-t", "14",
        "-c", "150000",
        "--port", "8080",
        "--reasoning", "on" if think else "off",
        "--flash-attn", "on",
        "--cache-type-k", "q4_0",
        "--cache-type-v", "q4_0",
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
    # 记录上下文窗口与思考开关，供 opencode 配置自动同步（取自启动参数）
    try:
        model["context_window"] = int(args[args.index("-c") + 1])
    except (ValueError, IndexError):
        model["context_window"] = None
    model["reasoning"] = bool(think)
    return model


# ---- opencode.jsonc 读写（保留注释）----

def _strip_jsonc_comments(text: str) -> str:
    """去掉 // 和 /* */ 注释，但保留字符串字面量内的内容。"""
    result = []
    i = 0
    n = len(text)
    in_str = False
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_str:
            result.append(c)
            if c == "\\":
                if i + 1 < n:
                    result.append(nxt)
                    i += 2
                    continue
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            result.append(c)
            i += 1
            continue
        if c == "/" and nxt == "/":
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if c == "/" and nxt == "*":
            i += 2
            while i < n and not (text[i] == "*" and text[i + 1 : i + 2] == "/"):
                i += 1
            i += 2
            continue
        result.append(c)
        i += 1
    return "".join(result)


def _read_config() -> dict:
    if not OPENCODE_CONFIG.exists():
        return {"$schema": "https://opencode.ai/config.json"}
    text = OPENCODE_CONFIG.read_text(encoding="utf-8")
    return json.loads(_strip_jsonc_comments(text))


def _write_config(cfg: dict) -> None:
    OPENCODE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    OPENCODE_CONFIG.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def register_model(model: dict) -> None:
    """把已加载的模型注册进 opencode 的 llama provider。"""
    model_id = Path(model["model_path"]).stem
    multimodal = bool(model.get("mmproj_path"))
    context = model.get("context_window") or LLAMA_CONTEXT_DEFAULT

    cfg = _read_config()
    cfg.setdefault("provider", {})
    llama = cfg["provider"].setdefault(
        LLAMA_PROVIDER,
        {
            "name": "llama.cpp (local)",
            "npm": "@ai-sdk/openai-compatible",
            "options": {
                "baseURL": LLAMA_BASE_URL,
                "timeout": False,
                "headerTimeout": False,
                "chunkTimeout": 600000,
            },
            "models": {},
        },
    )
    # 每次注册前先清空旧模型，避免残留
    llama["models"] = {}
    llama["models"][model_id] = {
        "name": model_id,
        "reasoning": bool(model.get("reasoning")),
        "tool_call": True,
        "attachment": multimodal,
        "limit": {
            "context": context,
            "output": context // 4,
        },
        "modalities": {
            "input": ["text", "image"] if multimodal else ["text"],
            "output": ["text"],
        },
    }
    # 确保该 provider 不被禁用
    disabled = cfg.setdefault("disabled_providers", [])
    if LLAMA_PROVIDER in disabled:
        disabled.remove(LLAMA_PROVIDER)

    _write_config(cfg)
    print(f"[ok] 已注册模型 {model_id} 到 opencode 配置 ({OPENCODE_CONFIG})")


def main() -> None:
    print("=== 启动 llama.cpp 服务 ===")
    print("若 8080 端口已有服务在运行，将直接退出。")
    print()
    model = ensure_llama_server()
    print()
    if model is not None:
        print(f"[OK] 已加载模型: {model['model_path'].name}")
        register_model(model)
    else:
        print("[*] 检测到已有服务在运行，本次不重复注册模型。")
    print("服务已就绪： http://127.0.0.1:8080")
    print("（按 Ctrl+C 可退出本脚本，但独立启动的服务进程仍会继续运行。）")
    input("按回车键退出...")


if __name__ == "__main__":
    main()
