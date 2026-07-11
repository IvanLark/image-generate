"""供应商适配器包。

扩展方式：
1. 新建 providers/xxx.py，实现 ImageProvider
2. 在 registry.py 的 _REGISTRY 中注册 type 名
3. 在 config/profiles.example.yaml 增加示例
"""

from image_generate.providers.base import ImageProvider

__all__ = ["ImageProvider"]
