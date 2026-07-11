"""把 ImageResult 落到本地文件，并读取实际分辨率。"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from image_generate.models import ImageResult, extension_for_format


class SaveError(Exception):
    """保存失败。"""


def read_image_dimensions(path: Path | str | bytes) -> tuple[int, int] | None:
    """从文件路径或字节中读取宽高。支持 PNG / JPEG / WebP。失败返回 None。"""
    try:
        if isinstance(path, (bytes, bytearray)):
            data = bytes(path[:64 * 1024])
        else:
            p = Path(path)
            with p.open("rb") as f:
                data = f.read(64 * 1024)
    except OSError:
        return None

    if len(data) < 24:
        return None

    # PNG
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        if w > 0 and h > 0:
            return int(w), int(h)

    # JPEG
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            i += 2
            if marker in (0xD8, 0xD9) or marker == 0x01:
                continue
            if 0xD0 <= marker <= 0xD7:
                continue
            if i + 2 > len(data):
                break
            seg_len = struct.unpack(">H", data[i : i + 2])[0]
            if seg_len < 2:
                break
            # SOF0–SOF3, SOF5–SOF7, SOF9–SOF11, SOF13–SOF15
            if marker in (
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            ):
                if i + 7 <= len(data):
                    h, w = struct.unpack(">HH", data[i + 3 : i + 7])
                    if w > 0 and h > 0:
                        return int(w), int(h)
                break
            i += seg_len

    # WebP (RIFF....WEBP)
    if data[:4] == b"RIFF" and len(data) >= 30 and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            # 24-bit little-endian width-1 / height-1
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            if w > 0 and h > 0:
                return w, h
        if chunk == b"VP8 " and len(data) >= 30:
            # lossy: start code + 14-bit dimensions
            raw = data[23:29] if data[20:23] == b"\x9d\x01\x2a" else data[20:26]
            if len(raw) >= 6 and raw[:3] == b"\x9d\x01\x2a":
                w = struct.unpack("<H", raw[3:5])[0] & 0x3FFF
                h = struct.unpack("<H", raw[5:7])[0] & 0x3FFF
                if w > 0 and h > 0:
                    return w, h
        if chunk == b"VP8L" and len(data) >= 25:
            # lossless signature 0x2f
            if data[20] == 0x2F:
                bits = struct.unpack("<I", data[21:25])[0]
                w = (bits & 0x3FFF) + 1
                h = ((bits >> 14) & 0x3FFF) + 1
                if w > 0 and h > 0:
                    return w, h

    return None


def parse_requested_size(size: str | None) -> tuple[int, int] | None:
    """解析请求 size 字符串，如 1024x1024。auto 或无法解析返回 None。"""
    if not size or not isinstance(size, str):
        return None
    text = size.strip().lower()
    if text in ("", "auto"):
        return None
    if "x" not in text:
        return None
    left, right = text.split("x", 1)
    try:
        w, h = int(left), int(right)
    except ValueError:
        return None
    if w <= 0 or h <= 0:
        return None
    return w, h


def report_actual_sizes(
    *,
    requested_size: str | None,
    paths: list[Path],
) -> list[tuple[int, int] | None]:
    """读取并打印实际分辨率提示（stderr）。返回每张图的 (w,h) 或 None。"""
    requested = parse_requested_size(requested_size)
    dims_list: list[tuple[int, int] | None] = []

    for idx, path in enumerate(paths):
        dims = read_image_dimensions(path)
        dims_list.append(dims)
        if dims is None:
            continue
        w, h = dims
        label = f"第 {idx + 1} 张 " if len(paths) > 1 else ""
        actual = f"{w}x{h}"
        req_text = (requested_size or "auto").strip() or "auto"

        if requested is None:
            # auto 或未指定具体 WxH：始终告知实际输出
            print(
                f"[提示] {label}请求 size={req_text}，实际输出 {actual}",
                file=sys.stderr,
            )
        elif (w, h) != requested:
            print(
                f"[提示] {label}请求 size={req_text}，实际输出 {actual}"
                f"（服务端/模型可能按档位调整分辨率，属常见情况）",
                file=sys.stderr,
            )
        else:
            print(
                f"[信息] {label}实际输出 {actual}（与请求一致）",
                file=sys.stderr,
            )

    return dims_list


def build_output_paths(
    *,
    out: str | None,
    out_dir: str | None,
    n: int,
    output_format: str,
    default_name: str = "output",
) -> list[Path]:
    ext = extension_for_format(output_format)
    if out_dir and out:
        raise SaveError("不要同时指定 --out 与 --out-dir，二选一")

    if out_dir:
        directory = Path(out_dir)
        directory.mkdir(parents=True, exist_ok=True)
        if n == 1:
            return [directory / f"{default_name}.{ext}"]
        return [directory / f"{default_name}_{i}.{ext}" for i in range(1, n + 1)]

    if out:
        path = Path(out)
        if path.suffix == "":
            path = path.with_suffix(f".{ext}")
        if n == 1:
            return [path]
        # 多图：stem-1.ext, stem-2.ext
        return [
            path.with_name(f"{path.stem}-{i}{path.suffix or f'.{ext}'}")
            for i in range(1, n + 1)
        ]

    # 默认写到当前工作目录
    if n == 1:
        return [Path(f"{default_name}.{ext}")]
    return [Path(f"{default_name}_{i}.{ext}") for i in range(1, n + 1)]


def save_result(
    result: ImageResult,
    paths: list[Path],
    *,
    force: bool = False,
    requested_size: str | None = None,
) -> list[Path]:
    if len(result.images) > len(paths):
        raise SaveError(
            f"结果有 {len(result.images)} 张图，但只准备了 {len(paths)} 个输出路径"
        )

    written: list[Path] = []
    for img, path in zip(result.images, paths, strict=False):
        if path.exists() and not force:
            raise SaveError(f"输出文件已存在: {path}（加 --force 可覆盖）")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(img.data)
        written.append(path.resolve())

    if requested_size is not None or written:
        report_actual_sizes(requested_size=requested_size, paths=written)

    return written
