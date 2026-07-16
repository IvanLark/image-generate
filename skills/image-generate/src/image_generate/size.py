"""尺寸解析与规整（对齐 gpt_image_playground 的预设与约束）。

--size 支持：
- auto
- 像素：1024x1024 / 1024X1024 / 1024×1024
- 档位/比例：2k/16:9 → 展开为预设 2560x1440

不支持 2k:16:9 等双冒号写法。
"""

from __future__ import annotations

import math
import re
from typing import Literal

SizeTier = Literal["1k", "2k", "4k"]

SIZE_PATTERN = re.compile(r"^\s*(\d+)\s*[xX×]\s*(\d+)\s*$")
RATIO_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*$")
TIER_RATIO_PATTERN = re.compile(
    r"^\s*([124])\s*[kK]\s*/\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*$"
)
TIER_ONLY_PATTERN = re.compile(r"^\s*([124])\s*[kK]\s*$")

SIZE_MULTIPLE = 16
MAX_EDGE = 3840
MAX_ASPECT_RATIO = 3
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400

# 常用比例 → 各档位官方/通用预设（与 playground COMMON_SIZE_PRESETS 一致）
COMMON_SIZE_PRESETS: dict[SizeTier, dict[str, str]] = {
    "1k": {
        "1:1": "1024x1024",
        "3:2": "1536x1024",
        "2:3": "1024x1536",
        "16:9": "1280x720",
        "9:16": "720x1280",
        "4:3": "1024x768",
        "3:4": "768x1024",
        "21:9": "1280x544",
    },
    "2k": {
        "1:1": "2048x2048",
        "3:2": "2160x1440",
        "2:3": "1440x2160",
        "16:9": "2560x1440",
        "9:16": "1440x2560",
        "4:3": "2048x1536",
        "3:4": "1536x2048",
        "21:9": "2560x1088",
    },
    "4k": {
        "1:1": "2880x2880",
        "3:2": "3456x2304",
        "2:3": "2304x3456",
        "16:9": "3840x2160",
        "9:16": "2160x3840",
        "4:3": "3200x2400",
        "3:4": "2400x3200",
        "21:9": "3840x1600",
    },
}

TIER_PIXEL_BUDGET: dict[SizeTier, int] = {
    "1k": 1_572_864,  # 1024×1536
    "2k": 4_194_304,  # 2048×2048
    "4k": MAX_PIXELS,
}

PRESET_RATIOS = ("1:1", "3:2", "2:3", "16:9", "9:16", "4:3", "3:4", "21:9")


class SizeError(Exception):
    """尺寸参数非法。"""


def _round_to_multiple(value: float, multiple: int) -> int:
    return max(multiple, int(round(value / multiple) * multiple))


def _floor_to_multiple(value: float, multiple: int) -> int:
    return max(multiple, int((value // multiple) * multiple))


def _ceil_to_multiple(value: float, multiple: int) -> int:
    return max(multiple, int(math.ceil(value / multiple) * multiple))


def normalize_dimensions(width: int, height: int) -> tuple[int, int]:
    """将宽高规整到模型安全范围（16 倍数、边长、比例、像素上下限）。"""
    normalized_w = _round_to_multiple(width, SIZE_MULTIPLE)
    normalized_h = _round_to_multiple(height, SIZE_MULTIPLE)

    def scale_to_fit(scale: float) -> None:
        nonlocal normalized_w, normalized_h
        normalized_w = _floor_to_multiple(normalized_w * scale, SIZE_MULTIPLE)
        normalized_h = _floor_to_multiple(normalized_h * scale, SIZE_MULTIPLE)

    def scale_to_fill(scale: float) -> None:
        nonlocal normalized_w, normalized_h
        normalized_w = _ceil_to_multiple(normalized_w * scale, SIZE_MULTIPLE)
        normalized_h = _ceil_to_multiple(normalized_h * scale, SIZE_MULTIPLE)

    for _ in range(4):
        max_edge = max(normalized_w, normalized_h)
        if max_edge > MAX_EDGE:
            scale_to_fit(MAX_EDGE / max_edge)

        if normalized_w / normalized_h > MAX_ASPECT_RATIO:
            normalized_w = _floor_to_multiple(
                normalized_h * MAX_ASPECT_RATIO, SIZE_MULTIPLE
            )
        elif normalized_h / normalized_w > MAX_ASPECT_RATIO:
            normalized_h = _floor_to_multiple(
                normalized_w * MAX_ASPECT_RATIO, SIZE_MULTIPLE
            )

        pixels = normalized_w * normalized_h
        if pixels > MAX_PIXELS:
            scale_to_fit((MAX_PIXELS / pixels) ** 0.5)
        elif pixels < MIN_PIXELS:
            scale_to_fill((MIN_PIXELS / pixels) ** 0.5)

    return normalized_w, normalized_h


def normalize_image_size(size: str) -> str:
    """对 宽x高 做规整；非像素串原样返回。"""
    match = SIZE_PATTERN.match(size.strip())
    if not match:
        return size.strip()
    w, h = normalize_dimensions(int(match.group(1)), int(match.group(2)))
    return f"{w}x{h}"


def _simplify_ratio(rw: float, rh: float) -> str | None:
    if not (rw > 0 and rh > 0):
        return None
    # 整数比优先
    if float(rw).is_integer() and float(rh).is_integer():
        a, b = int(rw), int(rh)

        def gcd(x: int, y: int) -> int:
            while y:
                x, y = y, x % y
            return x

        g = gcd(a, b)
        key = f"{a // g}:{b // g}"
        if key in COMMON_SIZE_PRESETS["1k"]:
            return key
    # 浮点：匹配已知比例
    target = rw / rh
    for key in PRESET_RATIOS:
        parts = key.split(":")
        tw, th = int(parts[0]), int(parts[1])
        if abs(target - tw / th) / (tw / th) <= 0.01:
            return key
    return None


def calculate_image_size(tier: SizeTier, ratio: str) -> str | None:
    """档位 + 比例 → 预设或按像素预算计算。"""
    ratio = ratio.strip()
    if ratio in COMMON_SIZE_PRESETS[tier]:
        return COMMON_SIZE_PRESETS[tier][ratio]

    m = RATIO_PATTERN.match(ratio)
    if not m:
        return None

    rw, rh = float(m.group(1)), float(m.group(2))
    if rw <= 0 or rh <= 0:
        return None

    key = _simplify_ratio(rw, rh)
    if key and key in COMMON_SIZE_PRESETS[tier]:
        return COMMON_SIZE_PRESETS[tier][key]

    # 非预设比例：在档位像素预算内搜索
    return _search_size_for_ratio(tier, rw / rh)


def _search_size_for_ratio(tier: SizeTier, target_ratio: float) -> str | None:
    if not math.isfinite(target_ratio) or target_ratio <= 0:
        return None

    pixel_budget = TIER_PIXEL_BUDGET[tier]
    best_w = best_h = best_pixels = 0
    max_ratio_error = 0.01

    for w in range(SIZE_MULTIPLE, MAX_EDGE + 1, SIZE_MULTIPLE):
        ideal_h = w / target_ratio
        for h in (
            int(ideal_h // SIZE_MULTIPLE) * SIZE_MULTIPLE,
            int(math.ceil(ideal_h / SIZE_MULTIPLE) * SIZE_MULTIPLE),
        ):
            if h < SIZE_MULTIPLE or h > MAX_EDGE:
                continue
            pixels = w * h
            if pixels > pixel_budget or pixels < MIN_PIXELS:
                continue
            if max(w / h, h / w) > MAX_ASPECT_RATIO:
                continue
            err = abs(w / h - target_ratio) / target_ratio
            if err > max_ratio_error:
                continue
            if pixels > best_pixels:
                best_pixels = pixels
                best_w, best_h = w, h

    if best_pixels == 0:
        return None
    return f"{best_w}x{best_h}"


def resolve_size(size: str | None, *, normalize_pixels: bool = True) -> str:
    """把 --size 用户输入解析为 API 可用值：auto 或 宽x高。

    支持：
    - auto
    - 1024x1024
    - 2k/16:9（档位/比例）
    - 2k（仅档位，默认 1:1）
    """
    if size is None:
        return "auto"
    text = size.strip()
    if not text:
        return "auto"

    lower = text.lower()
    if lower == "auto":
        return "auto"

    # 像素尺寸
    if SIZE_PATTERN.match(text):
        return normalize_image_size(text) if normalize_pixels else text.replace("×", "x").replace("X", "x")

    # 档位/比例：2k/16:9
    m = TIER_RATIO_PATTERN.match(text)
    if m:
        tier = f"{m.group(1)}k"  # type: ignore[assignment]
        assert tier in ("1k", "2k", "4k")
        ratio = f"{m.group(2)}:{m.group(3)}"
        # 归一化比例字符串中的数字（去掉多余小数）
        rw, rh = float(m.group(2)), float(m.group(3))
        if rw.is_integer() and rh.is_integer():
            ratio = f"{int(rw)}:{int(rh)}"
        resolved = calculate_image_size(tier, ratio)  # type: ignore[arg-type]
        if not resolved:
            raise SizeError(
                f"无法解析尺寸「{size}」。"
                f"档位 {tier} 下不支持比例 {ratio}，"
                f"常用比例: {', '.join(PRESET_RATIOS)}"
            )
        return normalize_image_size(resolved) if normalize_pixels else resolved

    # 仅档位：2k → 2k/1:1
    m2 = TIER_ONLY_PATTERN.match(text)
    if m2:
        tier = f"{m2.group(1)}k"
        resolved = COMMON_SIZE_PRESETS[tier]["1:1"]  # type: ignore[index]
        return resolved

    # 明确拒绝双冒号等易混写法
    if re.match(r"^\s*[124]\s*[kK]\s*:\s*\d+", text):
        raise SizeError(
            f"不支持「{size}」这种写法。请用斜杠分隔档位与比例，例如: 2k/16:9"
        )

    # 单独 16:9 不够明确
    if RATIO_PATTERN.match(text):
        raise SizeError(
            f"单独写比例「{size}」不够明确，请带上档位，例如: 1k/{text} 或 2k/{text}"
        )

    raise SizeError(
        f"无法解析 --size「{size}」。"
        "支持: auto | 1024x1024 | 2k/16:9 | 2k（默认 1:1）。"
        f"常用比例: {', '.join(PRESET_RATIOS)}"
    )


def format_size_help() -> str:
    """生成 --help / 文档用的尺寸说明。"""
    lines = [
        "输出尺寸。默认 auto。",
        "写法: auto | 宽x高（如 1024x1024） | 档位/比例（如 2k/16:9） | 仅档位（如 2k=2k/1:1）。",
        "档位: 1k | 2k | 4k。比例: " + ", ".join(PRESET_RATIOS) + "。",
        "预设示例: 1k/1:1→1024x1024, 2k/16:9→2560x1440, 4k/16:9→3840x2160。",
        "像素尺寸会规整为 16 的倍数，并受边长/比例/总像素限制。",
        "【重要】2K/4K 往往要数分钟且可能按张计费：Agent 请用 submit+status 异步。",
    ]
    # argparse help 里用空格拼接，避免粘成一长串难读
    return " ".join(lines)


def preset_table_markdown() -> str:
    """文档用完整预设表。"""
    rows = ["| 档位 | 比例 | size |", "|------|------|------|"]
    for tier in ("1k", "2k", "4k"):
        for ratio in PRESET_RATIOS:
            sz = COMMON_SIZE_PRESETS[tier][ratio]
            rows.append(f"| {tier} | {ratio} | `{sz}` |")
    return "\n".join(rows)
