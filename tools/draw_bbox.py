"""根据 bbox 在图片上绘制框框的工具脚本。

bbox 约定：归一化坐标 (x1, y1, x2, y2)，取值范围 0~1，或像素坐标。
默认按归一化坐标处理（与视觉模型输出对齐）。

用法示例：
    python tools/draw_bbox.py testpic.PNG -b "0.1,0.2,0.5,0.6"
    python tools/draw_bbox.py testpic.PNG -b "0.1,0.2,0.5,0.6;0.7,0.7,0.9,0.9" -o out.png
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image, ImageDraw

_COLOR = (255, 0, 0)
_WIDTH = 3


def _normalize_box(
    x1: float, y1: float, x2: float, y2: float, img_w: int, img_h: int
) -> tuple[float, float, float, float]:
    """把模型输出的坐标统一成归一化 (0~1)。

    兼容三种口径：
    - 0~1000 千分比归一化（模型 bbox_2d 惯例）除以 1000
    - 像素坐标按图片尺寸归一化
    - 已为 0~1 归一化
    """
    box = (x1, y1, x2, y2)
    if max(box) > 100.0:
        return (x1 / 1000, y1 / 1000, x2 / 1000, y2 / 1000)
    if max(box) > 1.0:
        return (x1 / img_w, y1 / img_h, x2 / img_w, y2 / img_h)
    return box


def extract_bbox(
    reply: str, img_w: int, img_h: int
) -> tuple[float, float, float, float]:
    """从模型回复中解析单个 bbox，返回归一化 (x1, y1, x2, y2)。

    最终按出现顺序提取最后4个数字作为 bbox。
    """
    nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", reply)]
    if len(nums) < 4:
        raise ValueError(f"回复中找不到4个坐标数字: {reply!r}")
    return _normalize_box(*nums[-4:], img_w, img_h)


_BOX_GROUP_RE = re.compile(r"[\[(]\s*([-0-9.,\s]+?)[\])]")


def extract_bboxes(
    reply: str, img_w: int, img_h: int
) -> list[tuple[float, float, float, float]]:
    """从模型回复中解析多个 bbox，返回归一化框列表。

    优先按 [] 或 () 分组，每个分组取最后4个数字作为一个框（兼容
    {"bbox_2d": [..]} 等 JSON 式输出）。若找不到任何分组，回退为
    单个 bbox（用 extract_bbox 逻辑）。
    """
    boxes: list[tuple[float, float, float, float]] = []
    for m in _BOX_GROUP_RE.finditer(reply):
        nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", m.group(1))]
        if len(nums) >= 4:
            boxes.append(_normalize_box(*nums[-4:], img_w, img_h))
    if boxes:
        return boxes
    return [extract_bbox(reply, img_w, img_h)]


def parse_bboxes(text: str) -> list[tuple[float, float, float, float]]:
    boxes: list[tuple[float, float, float, float]] = []
    for group in text.split(";"):
        group = group.strip()
        if not group:
            continue
        parts = [float(v) for v in group.replace(",", " ").split()]
        if len(parts) != 4:
            raise ValueError(f"bbox 需要 4 个数字: {group!r}")
        boxes.append((parts[0], parts[1], parts[2], parts[3]))
    return boxes


def draw_bboxes(
    image: Image.Image,
    boxes: list[tuple[float, float, float, float]],
    color: tuple[int, int, int] = _COLOR,
    width: int = _WIDTH,
) -> Image.Image:
    """在原图上画框。box 坐标为归一化 (0~1)。返回新图片副本。"""
    w, h = image.size
    out = image.copy()
    draw = ImageDraw.Draw(out)
    for x1, y1, x2, y2 in boxes:
        # 排序保护：避免 x1>x2 或 y1>y2 触发 PIL "x1 must be >= x0" 错误
        # （不同模型输出坐标顺序/大小可能不同）
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)
        draw.rectangle(
            [x1 * w, y1 * h, x2 * w, y2 * h],
            outline=color,
            width=width,
        )
    return out


def _main() -> None:
    parser = argparse.ArgumentParser(description="根据 bbox 在原图上画框")
    parser.add_argument("image", type=str, help="输入图片路径")
    parser.add_argument("-b", "--bbox", type=str, required=True,
                        help="bbox 列表，多个用分号分隔，每项 x1,y1,x2,y2（归一化 0~1）")
    parser.add_argument("-o", "--output", type=str, default=None, help="输出图片路径")
    parser.add_argument("--color", type=str, default="255,0,0",
                        help="框颜色 RGB，如 0,255,0")
    parser.add_argument("--width", type=int, default=_WIDTH, help="框线宽度")
    args = parser.parse_args()

    img_path = Path(args.image)
    out_path = Path(args.output) if args.output else img_path.with_name(
        f"{img_path.stem}_bbox{img_path.suffix}"
    )

    boxes = parse_bboxes(args.bbox)
    color = tuple(int(v) for v in args.color.split(","))

    with Image.open(img_path) as im:
        im = im.convert("RGB")
        result = draw_bboxes(im, boxes, color=color, width=args.width)

    result.save(out_path)
    print(f"[OK] 已绘制 {len(boxes)} 个框 -> {out_path}")


if __name__ == "__main__":
    _main()
