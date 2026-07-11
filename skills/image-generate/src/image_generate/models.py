"""统一请求/结果模型。供应商适配器都面向这套契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GenerateRequest:
    """文生图请求（与供应商无关的公共字段）。"""

    prompt: str
    model: str | None = None
    size: str = "auto"
    quality: str = "auto"
    n: int = 1
    output_format: str = "png"
    moderation: str = "auto"
    # 供应商特有字段，透传用，避免公共结构膨胀
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EditRequest:
    """图生图 / 局部重绘请求。"""

    prompt: str
    image_paths: list[str]
    mask_path: str | None = None
    model: str | None = None
    size: str = "auto"
    quality: str = "auto"
    n: int = 1
    output_format: str = "png"
    moderation: str = "auto"
    input_fidelity: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ImageBytes:
    """单张图片的原始字节。"""

    data: bytes
    mime: str = "image/png"
    raw_url: str | None = None


@dataclass(slots=True)
class ImageResult:
    """统一出图结果。CLI 只关心这个结构。"""

    images: list[ImageBytes]
    elapsed_ms: int
    provider_type: str
    profile_name: str
    model: str
    revised_prompts: list[str | None] = field(default_factory=list)


MIME_BY_FORMAT = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "webp": "image/webp",
}


def mime_for_format(output_format: str) -> str:
    return MIME_BY_FORMAT.get(output_format.lower(), "image/png")


def extension_for_format(output_format: str) -> str:
    fmt = output_format.lower()
    if fmt == "jpg":
        return "jpeg"
    return fmt
