"""OpenAI 兼容 /v1/images 供应商。"""

from __future__ import annotations

import base64
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from image_generate.config import Profile
from image_generate.models import (
    EditRequest,
    GenerateRequest,
    ImageBytes,
    ImageResult,
    mime_for_format,
)
from image_generate.providers.base import BaseProvider


class ProviderError(Exception):
    """调用供应商失败。"""


def _join_url(base_url: str, path: str) -> str:
    """拼接 API 路径。

    支持：
    - https://host/v1 + images/generations
    - https://host + images/generations → 自动补 /v1
    """
    base = base_url.rstrip("/")
    path = path.lstrip("/")
    if base.endswith("/v1") or "/v1/" in base:
        return f"{base}/{path}"
    return f"{base}/v1/{path}"


def _heartbeat(stop: threading.Event, label: str, interval: float = 15.0) -> None:
    start = time.time()
    while not stop.wait(interval):
        elapsed = time.time() - start
        print(f"[进度] {label} 仍在进行… 已等待 {elapsed:.0f}s", file=sys.stderr)


def _extract_error_message(payload: Any, status_code: int) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code")
            if msg:
                return str(msg)
        if isinstance(err, str) and err.strip():
            return err
        for key in ("message", "msg", "detail"):
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return f"HTTP {status_code}"


def _parse_images_payload(payload: Any, mime: str) -> list[ImageBytes]:
    if not isinstance(payload, dict):
        raise ProviderError(f"响应不是 JSON 对象: {type(payload).__name__}")

    data = payload.get("data")
    if data is None:
        raise ProviderError(
            "响应缺少 data 字段。原始键: "
            + (", ".join(payload.keys()) if isinstance(payload, dict) else "?")
        )
    if not isinstance(data, list) or not data:
        raise ProviderError("响应 data 为空，没有图片")

    images: list[ImageBytes] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise ProviderError(f"data[{idx}] 不是对象")

        b64 = item.get("b64_json")
        url = item.get("url")
        if isinstance(b64, str) and b64.strip():
            # 允许 data URL 前缀
            raw_b64 = b64
            if "," in raw_b64 and raw_b64.strip().startswith("data:"):
                raw_b64 = raw_b64.split(",", 1)[1]
            try:
                content = base64.b64decode(raw_b64)
            except Exception as exc:
                raise ProviderError(f"data[{idx}].b64_json 解码失败: {exc}") from exc
            images.append(ImageBytes(data=content, mime=mime, raw_url=None))
            continue

        if isinstance(url, str) and url.strip():
            # 先占位，下载在外面统一做（这里只标记 url）
            images.append(ImageBytes(data=b"", mime=mime, raw_url=url.strip()))
            continue

        raise ProviderError(
            f"data[{idx}] 既没有 b64_json 也没有 url。"
            f"字段: {', '.join(item.keys())}"
        )

    return images


def _download_url_images(
    images: list[ImageBytes],
    client: httpx.Client,
    mime: str,
) -> list[ImageBytes]:
    result: list[ImageBytes] = []
    for img in images:
        if img.data:
            result.append(img)
            continue
        if not img.raw_url:
            raise ProviderError("图片条目缺少数据")
        resp = client.get(img.raw_url)
        if resp.status_code >= 400:
            raise ProviderError(
                f"下载图片失败 HTTP {resp.status_code}: {img.raw_url}"
            )
        content_type = resp.headers.get("content-type", mime).split(";")[0].strip()
        result.append(
            ImageBytes(
                data=resp.content,
                mime=content_type or mime,
                raw_url=img.raw_url,
            )
        )
    return result


def _revised_prompts(payload: Any, n: int) -> list[str | None]:
    if not isinstance(payload, dict):
        return [None] * n
    data = payload.get("data")
    if not isinstance(data, list):
        return [None] * n
    out: list[str | None] = []
    for item in data:
        if isinstance(item, dict):
            rp = item.get("revised_prompt")
            out.append(str(rp) if isinstance(rp, str) and rp.strip() else None)
        else:
            out.append(None)
    while len(out) < n:
        out.append(None)
    return out[:n]


class OpenAICompatibleProvider(BaseProvider):
    """POST {base}/images/generations 与 /images/edits。"""

    def __init__(self, profile: Profile) -> None:
        super().__init__(profile)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=httpx.Timeout(
                connect=30.0,
                read=self.profile.timeout,
                write=30.0,
                pool=30.0,
            ),
            headers={
                "Authorization": f"Bearer {self.profile.api_key}",
            },
        )

    def _with_heartbeat(self, label: str, fn):
        stop = threading.Event()
        t = threading.Thread(
            target=_heartbeat,
            args=(stop, label),
            daemon=True,
        )
        t.start()
        try:
            return fn()
        finally:
            stop.set()
            t.join(timeout=1.0)

    def generate(self, req: GenerateRequest) -> ImageResult:
        model = req.model or self.profile.model
        mime = mime_for_format(req.output_format)
        url = _join_url(self.profile.base_url, "images/generations")

        body: dict[str, Any] = {
            "model": model,
            "prompt": req.prompt,
            "n": req.n,
            "size": req.size,
            "quality": req.quality,
            "output_format": req.output_format,
            "moderation": req.moderation or "auto",
        }
        # 默认不强制 b64；服务端可能返回 b64_json 或 url，客户端两种都能解析
        if self.profile.response_format_b64_json:
            body["response_format"] = "b64_json"
        if req.extra:
            body.update(req.extra)

        print(
            f"[请求] generate  profile={self.profile.name}  model={model}  "
            f"size={req.size}  quality={req.quality}  timeout={self.profile.timeout:.0f}s",
            file=sys.stderr,
        )
        print(f"[请求] POST {url}", file=sys.stderr)
        print(
            "[提示] 图片生成通常需要 1～数分钟，请耐心等待。",
            file=sys.stderr,
        )

        started = time.time()

        def do_request():
            with self._client() as client:
                try:
                    resp = client.post(
                        url,
                        json=body,
                        headers={"Content-Type": "application/json"},
                    )
                except httpx.TimeoutException as exc:
                    raise ProviderError(
                        f"请求超时（timeout={self.profile.timeout:.0f}s）: {exc}"
                    ) from exc
                except httpx.HTTPError as exc:
                    raise ProviderError(f"网络错误: {exc}") from exc
                try:
                    payload = resp.json()
                except Exception:
                    payload = None

                if resp.status_code >= 400:
                    msg = _extract_error_message(payload, resp.status_code)
                    raise ProviderError(f"生成失败: {msg}")

                if payload is None:
                    raise ProviderError("响应不是合法 JSON")

                images = _parse_images_payload(payload, mime)
                images = _download_url_images(images, client, mime)
                revised = _revised_prompts(payload, len(images))
                return images, revised

        images, revised = self._with_heartbeat("文生图", do_request)
        elapsed_ms = int((time.time() - started) * 1000)
        print(f"[完成] 生成成功，耗时 {elapsed_ms / 1000:.1f}s，共 {len(images)} 张", file=sys.stderr)

        return ImageResult(
            images=images,
            elapsed_ms=elapsed_ms,
            provider_type=self.profile.type,
            profile_name=self.profile.name,
            model=model,
            revised_prompts=revised,
        )

    def edit(self, req: EditRequest) -> ImageResult:
        model = req.model or self.profile.model
        mime = mime_for_format(req.output_format)
        url = _join_url(self.profile.base_url, "images/edits")

        image_paths = [Path(p) for p in req.image_paths]
        for p in image_paths:
            if not p.is_file():
                raise ProviderError(f"输入图片不存在: {p}")

        mask_path = Path(req.mask_path) if req.mask_path else None
        if mask_path and not mask_path.is_file():
            raise ProviderError(f"遮罩文件不存在: {mask_path}")

        print(
            f"[请求] edit  profile={self.profile.name}  model={model}  "
            f"images={len(image_paths)}  size={req.size}",
            file=sys.stderr,
        )
        print(f"[请求] POST {url}", file=sys.stderr)

        started = time.time()

        def do_request():
            with self._client() as client:
                # multipart：字段名兼容 OpenAI（多图用 image[] 或重复 image）
                data: dict[str, Any] = {
                    "model": model,
                    "prompt": req.prompt,
                    "n": str(req.n),
                    "size": req.size,
                    "quality": req.quality,
                    "output_format": req.output_format,
                    "moderation": req.moderation or "auto",
                }
                if req.input_fidelity:
                    data["input_fidelity"] = req.input_fidelity
                if self.profile.response_format_b64_json:
                    data["response_format"] = "b64_json"
                if req.extra:
                    for k, v in req.extra.items():
                        data[k] = str(v) if not isinstance(v, (bytes, bytearray)) else v

                files: list[tuple[str, tuple[str, Any, str | None]]] = []
                handles = []
                try:
                    for p in image_paths:
                        fh = p.open("rb")
                        handles.append(fh)
                        # 多图时用 image[]，单图用 image
                        field = "image[]" if len(image_paths) > 1 else "image"
                        files.append((field, (p.name, fh, "application/octet-stream")))
                    if mask_path:
                        mh = mask_path.open("rb")
                        handles.append(mh)
                        files.append(("mask", (mask_path.name, mh, "image/png")))

                    try:
                        resp = client.post(url, data=data, files=files)
                    except httpx.TimeoutException as exc:
                        raise ProviderError(
                            f"请求超时（timeout={self.profile.timeout:.0f}s）: {exc}"
                        ) from exc
                    except httpx.HTTPError as exc:
                        raise ProviderError(f"网络错误: {exc}") from exc
                finally:
                    for h in handles:
                        try:
                            h.close()
                        except Exception:
                            pass

                try:
                    payload = resp.json()
                except Exception:
                    payload = None

                if resp.status_code >= 400:
                    msg = _extract_error_message(payload, resp.status_code)
                    raise ProviderError(f"编辑失败: {msg}")
                if payload is None:
                    raise ProviderError("响应不是合法 JSON")

                images = _parse_images_payload(payload, mime)
                images = _download_url_images(images, client, mime)
                revised = _revised_prompts(payload, len(images))
                return images, revised

        images, revised = self._with_heartbeat("图生图", do_request)
        elapsed_ms = int((time.time() - started) * 1000)
        print(f"[完成] 编辑成功，耗时 {elapsed_ms / 1000:.1f}s，共 {len(images)} 张", file=sys.stderr)

        return ImageResult(
            images=images,
            elapsed_ms=elapsed_ms,
            provider_type=self.profile.type,
            profile_name=self.profile.name,
            model=model,
            revised_prompts=revised,
        )
