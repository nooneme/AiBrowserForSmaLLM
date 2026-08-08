"""根据 bbox 在图片上绘制框框的工具脚本。

bbox 约定：归一化坐标 (x1, y1, x2, y2)，取值范围 0~1，或像素坐标。
默认按归一化坐标处理（与视觉模型输出对齐）。

用法示例：
    python tools/draw_tools.py testpic.PNG -b "{\"bbox_2d\": [0.1,0.2,0.5,0.6]}"
    python tools/draw_tools.py testpic.PNG -b "{\"point_2d\": [353,147]}" --key point_2d -o out.png
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image, ImageDraw

_COLOR = (255, 0, 0)
_WIDTH = 3


def _normalize_coords(
    coords: tuple[float, ...], img_w: int, img_h: int
) -> tuple[float, ...]:
    """把坐标元组统一成归一化 (0~1)。

    point(2个数) 直接按图像尺寸归一化；bbox(≥4个数) 额外兼容三种口径：
    - 0~1000 千分比归一化（模型 bbox_2d 惯例）除以 1000
    - 像素坐标按图片尺寸归一化
    - 已为 0~1 归一化
    """
    if len(coords) >= 4:
        x1, y1, x2, y2 = coords[-4:]
        box = (x1, y1, x2, y2)
        if max(box) > 100.0:
            return (x1 / 1000, y1 / 1000, x2 / 1000, y2 / 1000)
        if max(box) > 1.0:
            return (x1 / img_w, y1 / img_h, x2 / img_w, y2 / img_h)
        return box
    x, y = coords[-2:]
    # 与 bbox 同口径：0~1000 千分比 /1000，像素按图片尺寸归一化，0~1 直接用
    if max(x, y) > 100.0:
        return (x / 1000, y / 1000)
    if max(x, y) > 1.0:
        return (x / img_w, y / img_h)
    return (x, y)


_COORD_KEYS = ("bbox_2d", "point_2d", "bbox", "point", "box", "coords", "coordinates")


def _looks_like_coords(value: Any) -> bool:
    """判断 JSON 值是否为坐标数组（纯数字列表）。"""
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value)
    )


def _collect_coords(node: Any, keys: tuple[str, ...]) -> list[tuple[float, ...]]:
    """递归遍历 JSON，收集所有匹配坐标键的数组。"""
    found: list[tuple[float, ...]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in keys and _looks_like_coords(v):
                found.append(tuple(float(x) for x in v))
            else:
                found.extend(_collect_coords(v, keys))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_coords(item, keys))
    return found


def parse_coords(
    reply: str,
    key: str | None = None,
    img_w: int | None = None,
    img_h: int | None = None,
) -> list[tuple[float, ...]]:
    """通用坐标解析：从模型回复中取出坐标（bbox 或 point）。

    回复形如 {"action": "click", "point_2d": [353, 147]}、
    {"bbox_2d": [0.1, 0.2, 0.5, 0.6]} 或顶层数组
    [{"bbox_2d": [0, 374, 122, 419], "label": "..."}]。

    key 为空时自动识别坐标键（bbox_2d / point_2d / ...）。先尝试整体按
    JSON 解析（含递归遍历数组/嵌套对象），失败则按正则匹配 `key: [...]`。
    返回坐标元组列表。当提供 img_w/img_h 且坐标为像素值时，归一化到 0~1；
    bbox 额外兼容 0~1000 千分比口径（见 _normalize_coords）。
    """
    keys = (key,) if key else _COORD_KEYS

    coords_list: list[tuple[float, ...]] = []
    try:
        obj = json.loads(reply)
    except (json.JSONDecodeError, TypeError):
        obj = None
    if obj is not None:
        coords_list = _collect_coords(obj, keys)

    if not coords_list:
        pat = re.compile(
            r"['\"]?(?:" + "|".join(re.escape(k) for k in keys) + r")['\"]?\s*[:=]\s*[\[(]([^\]\})]*)[\])]",
            re.IGNORECASE,
        )
        for m in pat.finditer(reply):
            nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", m.group(1))]
            if nums:
                coords_list.append(tuple(nums))

    if not coords_list:
        raise ValueError(f"回复中找不到坐标键 {list(keys)!r}: {reply!r}")

    if img_w is not None and img_h is not None:
        coords_list = [_normalize_coords(c, img_w, img_h) for c in coords_list]
    return coords_list


def draw_coords(
    image: Image.Image,
    coords: list[tuple[float, ...]],
    color: tuple[int, int, int] = _COLOR,
    width: int = _WIDTH,
    radius: int | None = None,
) -> Image.Image:
    """在原图上绘制坐标。坐标为归一化 (0~1)。

    自动区分：2 个数字视为点，≥4 个数字视为 bbox（矩形框）。
    点参考官方风格：半透明红色圆 + 绿色中心小圆点，半径随图片尺寸。
    返回新图片副本。
    """
    w, h = image.size
    out = image.convert("RGBA")
    draw = ImageDraw.Draw(out)
    for coord in coords:
        if len(coord) >= 4:
            x1, y1, x2, y2 = coord[-4:]
            # 排序保护：避免 x1>x2 或 y1>y2 触发 PIL "x1 must be >= x0" 错误
            # （不同模型输出坐标顺序/大小可能不同）
            x1, x2 = min(x1, x2), max(x1, x2)
            y1, y2 = min(y1, y2), max(y1, y2)
            draw.rectangle(
                [x1 * w, y1 * h, x2 * w, y2 * h],
                outline=color,
                width=width,
            )
        else:
            x, y = coord[-2:]
            px, py = x * w, y * h
            # 绿色中心小圆点，标注点击位置
            center_radius = max(1, int(min(w, h) * 0.05 * 0.1))
            draw.ellipse(
                [px - center_radius, py - center_radius,
                 px + center_radius, py + center_radius],
                fill=(0, 255, 0, 255),
            )
    return out.convert("RGB")


def _main() -> None:
    parser = argparse.ArgumentParser(description="根据 bbox 在原图上画框")
    parser.add_argument("image", type=str, help="输入图片路径")
    parser.add_argument("-b", "--bbox", type=str, required=True,
                        help="模型回复文本（含 bbox_2d / point_2d 坐标的 JSON 式输出）")
    parser.add_argument("--key", type=str, default=None,
                        help="要解析的坐标键，如 bbox_2d / point_2d；留空则自动识别")
    parser.add_argument("-o", "--output", type=str, default=None, help="输出图片路径")
    parser.add_argument("--color", type=str, default="255,0,0",
                        help="框颜色 RGB，如 0,255,0")
    parser.add_argument("--width", type=int, default=_WIDTH, help="框线宽度")
    args = parser.parse_args()

    img_path = Path(args.image)
    out_path = Path(args.output) if args.output else img_path.with_name(
        f"{img_path.stem}_bbox{img_path.suffix}"
    )

    color = tuple(int(v) for v in args.color.split(","))

    with Image.open(img_path) as im:
        im = im.convert("RGB")
        img_w, img_h = im.size
        coords = parse_coords(args.bbox, args.key, img_w, img_h)
        result = draw_coords(im, coords, color=color, width=args.width)

    result.save(out_path)
    print(f"[OK] 已绘制 {len(coords)} 个坐标 -> {out_path}")


if __name__ == "__main__":
    _main()
