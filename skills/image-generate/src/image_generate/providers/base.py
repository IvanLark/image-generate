"""供应商适配器协议。新增供应商时实现此协议并注册。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from image_generate.config import Profile
from image_generate.models import EditRequest, GenerateRequest, ImageResult


@runtime_checkable
class ImageProvider(Protocol):
    """所有供应商适配器的统一接口。"""

    def generate(self, req: GenerateRequest) -> ImageResult:
        """文生图。阻塞直到出图或失败。"""
        ...

    def edit(self, req: EditRequest) -> ImageResult:
        """图生图 / 局部重绘。不支持的供应商应抛出明确错误。"""
        ...


def unsupported_edit(provider_type: str) -> None:
    raise NotImplementedError(
        f"供应商类型「{provider_type}」暂不支持 edit。"
        "请使用 generate，或换支持图生图的 profile。"
    )


class BaseProvider:
    """可选基类：保存 profile，子类实现 generate/edit。"""

    def __init__(self, profile: Profile) -> None:
        self.profile = profile

    def generate(self, req: GenerateRequest) -> ImageResult:
        raise NotImplementedError

    def edit(self, req: EditRequest) -> ImageResult:
        unsupported_edit(self.profile.type)
