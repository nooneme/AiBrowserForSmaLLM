"""操作历史管理工具。

维护一个用换行分隔的操作历史字符串，每行代表一次已执行的操作。
只保留从现在往前数最近的 n 次操作，避免历史无限增长。
"""

from __future__ import annotations

from typing import Any


def new_history(initial: Any = "任务刚开始") -> str:
    """创建并返回初始化的操作历史字符串。"""
    return str(initial)


def append_history(history: str, operation: Any, n: int = 10) -> str:
    """把一次操作追加进操作历史，只保留最近 n 次，用换行分隔。

    history: 当前操作历史字符串（换行分隔）。
    operation: 本次要追加的操作（会转为字符串）。
    n: 最多保留的历史条数，默认 3。
    """
    entries = [ln for ln in history.splitlines() if ln.strip()]
    entries.append(str(operation))
    entries = entries[-n:]
    return "\n".join(entries)
