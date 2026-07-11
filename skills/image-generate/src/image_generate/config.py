"""读取多供应商 profile 配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# skill 根目录：skills/image-generate/
SKILL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = SKILL_ROOT / "config" / "profiles.yaml"
EXAMPLE_CONFIG_PATH = SKILL_ROOT / "config" / "profiles.example.yaml"


class ConfigError(Exception):
    """配置错误。"""


@dataclass(slots=True)
class Profile:
    """单个供应商连接配置。"""

    name: str
    type: str
    base_url: str
    model: str
    timeout: float = 600.0
    api_key: str = ""
    api_key_env: str | None = None
    api_key_file: str | None = None
    # 供应商私有配置（未来 custom_http / fal 用）
    options: dict[str, Any] = field(default_factory=dict)
    # 是否在请求中附带 response_format=b64_json（默认关，与 playground 一致；多数中转默认也会返回 b64 或 url）
    response_format_b64_json: bool = False
    # 延迟解析密钥用
    _raw_api_key: str | None = field(default=None, repr=False)
    _config_dir: Path | None = field(default=None, repr=False)
    _key_resolved: bool = field(default=False, repr=False)

    def ensure_api_key(self) -> str:
        """解析并返回密钥；仅在真正调用 API 前需要。"""
        if self._key_resolved and self.api_key:
            return self.api_key
        key = _resolve_api_key(
            raw_key=self._raw_api_key,
            api_key_env=self.api_key_env,
            api_key_file=self.api_key_file,
            config_dir=self._config_dir or Path("."),
            profile_name=self.name,
        )
        self.api_key = key
        self._key_resolved = True
        return key


@dataclass(slots=True)
class AppConfig:
    active: str
    profiles: dict[str, Profile]
    config_path: Path

    def get_profile(self, name: str | None = None, *, require_api_key: bool = True) -> Profile:
        key = name or self.active
        if key not in self.profiles:
            available = ", ".join(sorted(self.profiles)) or "(无)"
            raise ConfigError(f"找不到 profile「{key}」。已配置: {available}")
        profile = self.profiles[key]
        if require_api_key:
            profile.ensure_api_key()
        return profile


def _resolve_api_key(
    *,
    raw_key: str | None,
    api_key_env: str | None,
    api_key_file: str | None,
    config_dir: Path,
    profile_name: str,
) -> str:
    if raw_key and str(raw_key).strip():
        return str(raw_key).strip()

    if api_key_env:
        env_val = os.environ.get(api_key_env, "").strip()
        if env_val:
            return env_val

    if api_key_file:
        path = Path(api_key_file)
        if not path.is_absolute():
            path = (config_dir / path).resolve()
        if not path.is_file():
            raise ConfigError(
                f"profile「{profile_name}」的 api_key_file 不存在: {path}"
            )
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key

    if api_key_env:
        raise ConfigError(
            f"profile「{profile_name}」未找到密钥。"
            f"请设置环境变量 {api_key_env}，或在配置中写 api_key_file / api_key。"
        )
    raise ConfigError(
        f"profile「{profile_name}」未配置密钥。"
        "请设置 api_key_env、api_key_file 或 api_key 之一。"
    )


def _parse_profile(
    name: str,
    data: dict[str, Any],
    config_dir: Path,
) -> Profile:
    if not isinstance(data, dict):
        raise ConfigError(f"profile「{name}」必须是对象")

    ptype = str(data.get("type") or "").strip()
    if not ptype:
        raise ConfigError(f"profile「{name}」缺少 type")

    base_url = str(data.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ConfigError(f"profile「{name}」缺少 base_url")

    model = str(data.get("model") or "gpt-image-2").strip()
    timeout = float(data.get("timeout") or 600)

    api_key_env = data.get("api_key_env")
    api_key_file = data.get("api_key_file")
    raw_key = data.get("api_key")

    options = data.get("options") or {}
    if not isinstance(options, dict):
        raise ConfigError(f"profile「{name}」的 options 必须是对象")

    public_keys = {
        "type",
        "base_url",
        "model",
        "timeout",
        "api_key",
        "api_key_env",
        "api_key_file",
        "options",
        "response_format_b64_json",
    }
    merged_options = dict(options)
    for k, v in data.items():
        if k not in public_keys:
            merged_options.setdefault(k, v)

    response_format_b64_json = bool(data.get("response_format_b64_json", False))

    return Profile(
        name=name,
        type=ptype,
        base_url=base_url,
        model=model,
        timeout=timeout,
        api_key="",
        api_key_env=str(api_key_env) if api_key_env else None,
        api_key_file=str(api_key_file) if api_key_file else None,
        options=merged_options,
        response_format_b64_json=response_format_b64_json,
        _raw_api_key=str(raw_key) if raw_key is not None else None,
        _config_dir=config_dir,
        _key_resolved=False,
    )


def load_config(
    path: Path | str | None = None,
    *,
    require_api_key: bool = True,
) -> AppConfig:
    """加载 profiles 配置。

    密钥默认延迟到 get_profile(..., require_api_key=True) 时再解析，
    避免未使用的 profile 缺密钥导致整份配置加载失败。
    require_api_key 参数保留兼容：为 True 时会预解析 active profile。
    """
    if path is None:
        env_path = os.environ.get("IMAGE_GENERATE_CONFIG", "").strip()
        config_path = Path(env_path) if env_path else DEFAULT_CONFIG_PATH
    else:
        config_path = Path(path)

    if not config_path.is_file():
        hint = (
            f"配置文件不存在: {config_path}\n"
            f"请复制示例配置：\n"
            f"  cp {EXAMPLE_CONFIG_PATH} {DEFAULT_CONFIG_PATH}\n"
            f"然后填入 base_url / 密钥相关字段。"
        )
        raise ConfigError(hint)

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML 解析失败: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层必须是对象")

    profiles_raw = raw.get("profiles")
    if not isinstance(profiles_raw, dict) or not profiles_raw:
        raise ConfigError("配置文件缺少 profiles，或 profiles 为空")

    config_dir = config_path.parent
    profiles: dict[str, Profile] = {}
    for name, pdata in profiles_raw.items():
        profiles[str(name)] = _parse_profile(str(name), pdata, config_dir)

    active = str(raw.get("active") or "").strip()
    if not active:
        active = next(iter(profiles))
    if active not in profiles:
        raise ConfigError(
            f"active「{active}」不在 profiles 中。可选: {', '.join(profiles)}"
        )

    cfg = AppConfig(active=active, profiles=profiles, config_path=config_path)
    if require_api_key:
        # 仅预检 active，不强迫其它 profile 都有密钥
        profiles[active].ensure_api_key()
    return cfg


def mask_secret(value: str, keep: int = 4) -> str:
    """日志/展示用，遮蔽密钥。"""
    if not value:
        return "(空)"
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}***{value[-keep:]}"
