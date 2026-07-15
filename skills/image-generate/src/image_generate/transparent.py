"""可选：按指定背景色做本地抠图，输出透明 PNG。

不是 API 原生透明通道。调用方（通常是 AI）在提示词里约定纯色背景，
再通过 --transparent <颜色> 明确告诉后处理要抠掉什么色。
"""

from __future__ import annotations

import re
import sys
from collections import deque
from pathlib import Path

# 常见色名 → RGB
NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "green": (0, 255, 0),
    "lime": (0, 255, 0),
    "纯绿": (0, 255, 0),
    "绿色": (0, 255, 0),
    "magenta": (255, 0, 255),
    "fuchsia": (255, 0, 255),
    "品红": (255, 0, 255),
    "洋红": (255, 0, 255),
    "white": (255, 255, 255),
    "白色": (255, 255, 255),
    "black": (0, 0, 0),
    "黑色": (0, 0, 0),
    "red": (255, 0, 0),
    "红色": (255, 0, 0),
    "blue": (0, 0, 255),
    "蓝色": (0, 0, 255),
    "cyan": (0, 255, 255),
    "青色": (0, 255, 255),
    "yellow": (255, 255, 0),
    "黄色": (255, 255, 0),
}


class TransparentError(Exception):
    """抠图参数或处理失败。"""


def parse_color(spec: str) -> tuple[int, int, int]:
    """解析颜色：名称 / #RRGGBB / #RGB / R,G,B。"""
    text = (spec or "").strip()
    if not text:
        raise TransparentError("背景色不能为空")

    lower = text.lower()
    if lower in NAMED_COLORS:
        return NAMED_COLORS[lower]
    if text in NAMED_COLORS:
        return NAMED_COLORS[text]

    # #RGB / #RRGGBB
    hex_match = re.fullmatch(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", text)
    if hex_match:
        h = hex_match.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    # R,G,B 或 R G B
    rgb_match = re.fullmatch(
        r"(\d{1,3})\s*[,，\s]\s*(\d{1,3})\s*[,，\s]\s*(\d{1,3})",
        text,
    )
    if rgb_match:
        r, g, b = (int(rgb_match.group(i)) for i in range(1, 4))
        if not all(0 <= v <= 255 for v in (r, g, b)):
            raise TransparentError(f"RGB 分量须在 0～255：{text}")
        return r, g, b

    raise TransparentError(
        f"无法解析背景色「{spec}」。"
        "支持: green/magenta/white 等名称，#00FF00，或 0,255,0"
    )


def color_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def _color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return (
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    ) ** 0.5


def _confidence(pixel: tuple[int, int, int], key: tuple[int, int, int], max_dist: float = 150.0) -> float:
    d = _color_distance(pixel, key)
    v = (max_dist - d) / max_dist
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def remove_keyed_background_bytes(
    image_bytes: bytes,
    key_color: str | tuple[int, int, int],
    *,
    edge_threshold: float = 0.18,
) -> bytes:
    """对图片字节做色键抠图，返回 PNG 字节。"""
    try:
        from PIL import Image
    except ImportError as exc:
        raise TransparentError(
            "抠图需要 pillow。请在 skill 目录执行: uv add pillow 或 uv sync"
        ) from exc

    key = parse_color(key_color) if isinstance(key_color, str) else key_color

    from io import BytesIO

    img = Image.open(BytesIO(image_bytes)).convert("RGBA")
    width, height = img.size
    pixels = img.load()
    assert pixels is not None

    # 从边缘洪水填充：与背景色足够接近的连通区域视为背景
    mask = bytearray(width * height)  # 1 = 背景
    visited = bytearray(width * height)
    q: deque[int] = deque()

    def idx(x: int, y: int) -> int:
        return y * width + x

    def try_enqueue(x: int, y: int, thr: float) -> None:
        if x < 0 or y < 0 or x >= width or y >= height:
            return
        i = idx(x, y)
        if visited[i]:
            return
        r, g, b, _a = pixels[x, y]
        conf = _confidence((r, g, b), key)
        visited[i] = 1
        if conf < thr:
            return
        mask[i] = 1
        q.append(i)

    for x in range(width):
        try_enqueue(x, 0, edge_threshold)
        try_enqueue(x, height - 1, edge_threshold)
    for y in range(1, height - 1):
        try_enqueue(0, y, edge_threshold)
        try_enqueue(width - 1, y, edge_threshold)

    while q:
        i = q.popleft()
        x, y = i % width, i // width
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            try_enqueue(nx, ny, edge_threshold)

    # 写 alpha：背景全透明；邻近像素半透明 + 去溢出色
    out = Image.new("RGBA", (width, height))
    out_px = out.load()
    assert out_px is not None

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            i = idx(x, y)
            conf = _confidence((r, g, b), key)
            if mask[i]:
                out_px[x, y] = (r, g, b, 0)
                continue

            # 边缘软化：邻域有背景时，按相似度降低 alpha
            near_bg = False
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < width and 0 <= ny < height and mask[idx(nx, ny)]:
                    near_bg = True
                    break
            alpha = a
            if near_bg and conf > 0.12:
                alpha = max(40, int(255 * (1.0 - conf * 0.85)))
            elif conf >= 0.55:
                # 主体内少量色溢
                alpha = max(80, int(255 * (1.0 - conf * 0.5)))

            # 去背景色溢出：把偏 key 的通道往中性拉一点
            if conf > 0.2 and alpha < 250:
                r, g, b = _despill(r, g, b, key, conf)

            out_px[x, y] = (r, g, b, alpha)

    buf = BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def _despill(
    r: int,
    g: int,
    b: int,
    key: tuple[int, int, int],
    conf: float,
) -> tuple[int, int, int]:
    """简单去溢出：削弱相对突出的 key 通道。"""
    kr, kg, kb = key
    # 绿幕
    if kg >= 200 and kr <= 80 and kb <= 80:
        excess = max(0, g - max(r, b))
        g = max(0, g - int(excess * conf * 0.7))
    # 品红
    elif kr >= 200 and kb >= 200 and kg <= 80:
        excess_r = max(0, r - g)
        excess_b = max(0, b - g)
        r = max(0, r - int(excess_r * conf * 0.5))
        b = max(0, b - int(excess_b * conf * 0.5))
    return r, g, b


def apply_transparent_to_file(
    path: Path | str,
    key_color: str,
    *,
    in_place: bool = True,
) -> Path:
    """对已落盘图片抠图。默认覆盖原文件为 PNG。"""
    path = Path(path)
    if not path.is_file():
        raise TransparentError(f"图片不存在: {path}")

    raw = path.read_bytes()
    png_bytes = remove_keyed_background_bytes(raw, key_color)

    out_path = path if in_place else path.with_suffix(".png")
    if in_place and path.suffix.lower() not in (".png",):
        # 透明结果必须是 PNG
        out_path = path.with_suffix(".png")
        path.unlink(missing_ok=True)

    out_path.write_bytes(png_bytes)
    rgb = parse_color(key_color)
    print(
        f"[抠图] 已按背景色 {color_to_hex(rgb)}（{key_color}）处理: {out_path}",
        file=sys.stderr,
    )
    return out_path.resolve()


def apply_transparent_to_paths(
    paths: list[Path],
    key_color: str | None,
) -> list[Path]:
    """批量处理；key_color 为空则原样返回。"""
    if not key_color:
        return paths
    # 先校验颜色，避免生成完才发现参数错
    parse_color(key_color)
    result: list[Path] = []
    for p in paths:
        result.append(apply_transparent_to_file(p, key_color, in_place=True))
    return result
