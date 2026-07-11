"""按 profile.type 分发到对应供应商适配器。"""

from __future__ import annotations

from collections.abc import Callable

from image_generate.config import Profile
from image_generate.providers.base import ImageProvider
from image_generate.providers.openai_compatible import OpenAICompatibleProvider

ProviderFactory = Callable[[Profile], ImageProvider]

# 扩展点：新供应商在此注册 type -> 工厂
_REGISTRY: dict[str, ProviderFactory] = {
    "openai_compatible": OpenAICompatibleProvider,
}


def register_provider(type_name: str, factory: ProviderFactory) -> None:
    """注册新的供应商类型（供插件/测试使用）。"""
    key = type_name.strip()
    if not key:
        raise ValueError("type_name 不能为空")
    _REGISTRY[key] = factory


def registered_types() -> list[str]:
    return sorted(_REGISTRY)


def create_provider(profile: Profile) -> ImageProvider:
    factory = _REGISTRY.get(profile.type)
    if factory is None:
        known = ", ".join(registered_types()) or "(无)"
        raise ValueError(
            f"不支持的供应商 type「{profile.type}」。"
            f"已注册: {known}。"
            "新增供应商请实现 ImageProvider 并 register_provider。"
        )
    return factory(profile)
