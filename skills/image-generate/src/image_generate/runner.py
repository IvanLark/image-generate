"""同步执行与后台任务启动。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from image_generate.config import Profile, load_config
from image_generate.jobs import (
    SCHEMA_VERSION,
    JobError,
    _atomic_write_json,
    job_dir,
    job_json_path,
    jobs_root,
    load_job,
    load_request,
    new_job_id,
    now_iso,
    request_json_path,
    run_log_path,
    update_job,
)
from image_generate.models import EditRequest, GenerateRequest, ImageResult
from image_generate.registry import create_provider
from image_generate.save import build_output_paths, save_result


def execute_generate(
    profile: Profile,
    *,
    prompt: str,
    model: str | None,
    size: str,
    quality: str,
    n: int,
    output_format: str,
    moderation: str | None,
    output_paths: list[Path],
    force: bool,
) -> tuple[ImageResult, list[Path]]:
    provider = create_provider(profile)
    result = provider.generate(
        GenerateRequest(
            prompt=prompt,
            model=model,
            size=size,
            quality=quality,
            n=n,
            output_format=output_format,
            moderation=moderation,
        )
    )
    use_paths = _adjust_paths(output_paths, len(result.images))
    written = save_result(
        result,
        use_paths,
        force=force,
        requested_size=size,
    )
    return result, written


def execute_edit(
    profile: Profile,
    *,
    prompt: str,
    image_paths: list[str],
    mask_path: str | None,
    model: str | None,
    size: str,
    quality: str,
    n: int,
    output_format: str,
    moderation: str | None,
    input_fidelity: str | None,
    output_paths: list[Path],
    force: bool,
) -> tuple[ImageResult, list[Path]]:
    provider = create_provider(profile)
    result = provider.edit(
        EditRequest(
            prompt=prompt,
            image_paths=image_paths,
            mask_path=mask_path,
            model=model,
            size=size,
            quality=quality,
            n=n,
            output_format=output_format,
            moderation=moderation,
            input_fidelity=input_fidelity,
        )
    )
    use_paths = _adjust_paths(output_paths, len(result.images))
    written = save_result(
        result,
        use_paths,
        force=force,
        requested_size=size,
    )
    return result, written


def _adjust_paths(output_paths: list[Path], count: int) -> list[Path]:
    if count == len(output_paths):
        return output_paths
    if not output_paths:
        raise JobError("没有可用的输出路径")
    first = output_paths[0]
    if count == 1:
        return [first]
    return [
        first.with_name(f"{first.stem}-{i}{first.suffix}")
        for i in range(1, count + 1)
    ]


def spawn_run_job(job_id: str) -> int:
    """启动 `python -m image_generate.cli run-job <id>` 后台进程。"""
    log_path = run_log_path(job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115

    cmd = [sys.executable, "-m", "image_generate.cli", "run-job", job_id]
    env = os.environ.copy()
    # parents[1] = .../src/image_generate → parents[1]=image_generate 包目录的父是 src
    src = str(Path(__file__).resolve().parents[1])
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not prev else src + os.pathsep + prev

    skill_root = Path(__file__).resolve().parents[2]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
        cwd=str(skill_root),
    )
    log_f.close()
    if proc.pid is None:
        raise JobError("无法启动后台进程")
    return int(proc.pid)


def run_job_worker(job_id: str) -> int:
    """后台 worker：真正调 API。"""
    try:
        job = load_job(job_id)
        request = load_request(job_id)
    except JobError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    started = time.time()
    update_job(
        job_id,
        status="running",
        pid=os.getpid(),
        started_at=job.get("started_at") or now_iso(),
    )

    try:
        config_path = request.get("config_path")
        cfg = load_config(config_path if config_path else None, require_api_key=False)
        profile = cfg.get_profile(request.get("profile"), require_api_key=True)
        timeout = request.get("timeout")
        if timeout is not None:
            profile.timeout = float(timeout)

        force = bool(request.get("force"))
        mode = request.get("mode") or job.get("mode")
        raw_paths = request.get("output_paths") or job.get("output_paths") or []
        output_paths = [Path(p) for p in raw_paths]

        if mode == "generate":
            _result, written = execute_generate(
                profile,
                prompt=str(request["prompt"]),
                model=request.get("model"),
                size=str(request.get("size") or "auto"),
                quality=str(request.get("quality") or "auto"),
                n=int(request.get("n") or 1),
                output_format=str(request.get("output_format") or "png"),
                moderation=request.get("moderation") or "auto",
                output_paths=output_paths,
                force=force,
            )
        elif mode == "edit":
            images = request.get("images") or []
            if not images:
                raise JobError("edit 任务缺少 images")
            _result, written = execute_edit(
                profile,
                prompt=str(request["prompt"]),
                image_paths=[str(p) for p in images],
                mask_path=request.get("mask"),
                model=request.get("model"),
                size=str(request.get("size") or "auto"),
                quality=str(request.get("quality") or "auto"),
                n=int(request.get("n") or 1),
                output_format=str(request.get("output_format") or "png"),
                moderation=request.get("moderation") or "auto",
                input_fidelity=request.get("input_fidelity"),
                output_paths=output_paths,
                force=force,
            )
        else:
            raise JobError(f"未知 mode: {mode}")

        elapsed_ms = int((time.time() - started) * 1000)
        update_job(
            job_id,
            status="done",
            finished_at=now_iso(),
            result_paths=[str(p) for p in written],
            elapsed_ms=elapsed_ms,
            error=None,
        )
        for p in written:
            print(p)
        return 0
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = int((time.time() - started) * 1000)
        try:
            update_job(
                job_id,
                status="error",
                finished_at=now_iso(),
                elapsed_ms=elapsed_ms,
                error=str(exc),
            )
        except Exception:
            pass
        print(f"错误: {exc}", file=sys.stderr)
        return 1


def _prepare_job_dir() -> tuple[str, Path]:
    job_id = new_job_id()
    jobs_root().mkdir(parents=True, exist_ok=True)
    directory = job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=False)
    return job_id, directory


def _check_outputs_free(paths: list[Path], force: bool) -> None:
    if force:
        return
    for p in paths:
        if p.exists():
            raise JobError(f"输出文件已存在: {p}（加 --force 可覆盖）")


def submit_generate_job(
    *,
    profile: Profile,
    config_path: Path,
    profile_name: str | None,
    model: str | None,
    timeout: float | None,
    prompt: str,
    size: str,
    quality: str,
    n: int,
    output_format: str,
    moderation: str | None,
    out: str | None,
    out_dir: str | None,
    force: bool,
) -> dict[str, Any]:
    job_id, directory = _prepare_job_dir()

    if out or out_dir:
        paths = build_output_paths(
            out=out,
            out_dir=out_dir,
            n=n,
            output_format=output_format,
            default_name="output",
        )
    else:
        paths = build_output_paths(
            out=None,
            out_dir=str(directory),
            n=n,
            output_format=output_format,
            default_name="output",
        )

    _check_outputs_free(paths, force)
    abs_paths = [str(p.resolve()) for p in paths]
    effective_model = model or profile.model
    params: dict[str, Any] = {
        "size": size,
        "quality": quality,
        "n": n,
        "output_format": output_format,
        "moderation": moderation,
        "model": effective_model,
    }
    request: dict[str, Any] = {
        "mode": "generate",
        "job_id": job_id,
        "config_path": str(config_path.resolve()),
        "profile": profile_name or profile.name,
        "model": model,
        "timeout": timeout,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "n": n,
        "output_format": output_format,
        "moderation": moderation,
        "out": out,
        "out_dir": out_dir,
        "force": force,
        "output_paths": abs_paths,
    }
    job: dict[str, Any] = {
        "id": job_id,
        "schema_version": SCHEMA_VERSION,
        "status": "queued",
        "mode": "generate",
        "created_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "profile": profile.name,
        "provider_type": profile.type,
        "model": effective_model,
        "prompt": prompt,
        "params": params,
        "output_paths": abs_paths,
        "result_paths": [],
        "error": None,
        "elapsed_ms": None,
        "config_path": str(config_path.resolve()),
    }
    _atomic_write_json(job_json_path(job_id), job)
    _atomic_write_json(request_json_path(job_id), request)
    run_log_path(job_id).write_text("", encoding="utf-8")

    pid = spawn_run_job(job_id)
    time.sleep(0.05)
    return update_job(
        job_id,
        status="running",
        pid=pid,
        started_at=now_iso(),
    )


def submit_edit_job(
    *,
    profile: Profile,
    config_path: Path,
    profile_name: str | None,
    model: str | None,
    timeout: float | None,
    prompt: str,
    images: list[str],
    mask: str | None,
    input_fidelity: str | None,
    size: str,
    quality: str,
    n: int,
    output_format: str,
    moderation: str | None,
    out: str | None,
    out_dir: str | None,
    force: bool,
) -> dict[str, Any]:
    for img in images:
        if not Path(img).is_file():
            raise JobError(f"输入图片不存在: {img}")
    if mask and not Path(mask).is_file():
        raise JobError(f"遮罩文件不存在: {mask}")

    job_id, directory = _prepare_job_dir()

    if out or out_dir:
        paths = build_output_paths(
            out=out,
            out_dir=out_dir,
            n=n,
            output_format=output_format,
            default_name="edit",
        )
    else:
        paths = build_output_paths(
            out=None,
            out_dir=str(directory),
            n=n,
            output_format=output_format,
            default_name="edit",
        )

    _check_outputs_free(paths, force)
    abs_paths = [str(p.resolve()) for p in paths]
    abs_images = [str(Path(p).resolve()) for p in images]
    abs_mask = str(Path(mask).resolve()) if mask else None
    effective_model = model or profile.model
    params: dict[str, Any] = {
        "size": size,
        "quality": quality,
        "n": n,
        "output_format": output_format,
        "moderation": moderation,
        "model": effective_model,
        "input_fidelity": input_fidelity,
        "images": abs_images,
        "mask": abs_mask,
    }
    request: dict[str, Any] = {
        "mode": "edit",
        "job_id": job_id,
        "config_path": str(config_path.resolve()),
        "profile": profile_name or profile.name,
        "model": model,
        "timeout": timeout,
        "prompt": prompt,
        "images": abs_images,
        "mask": abs_mask,
        "input_fidelity": input_fidelity,
        "size": size,
        "quality": quality,
        "n": n,
        "output_format": output_format,
        "moderation": moderation,
        "out": out,
        "out_dir": out_dir,
        "force": force,
        "output_paths": abs_paths,
    }
    job: dict[str, Any] = {
        "id": job_id,
        "schema_version": SCHEMA_VERSION,
        "status": "queued",
        "mode": "edit",
        "created_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "profile": profile.name,
        "provider_type": profile.type,
        "model": effective_model,
        "prompt": prompt,
        "params": params,
        "output_paths": abs_paths,
        "result_paths": [],
        "error": None,
        "elapsed_ms": None,
        "config_path": str(config_path.resolve()),
    }
    _atomic_write_json(job_json_path(job_id), job)
    _atomic_write_json(request_json_path(job_id), request)
    run_log_path(job_id).write_text("", encoding="utf-8")

    pid = spawn_run_job(job_id)
    time.sleep(0.05)
    return update_job(
        job_id,
        status="running",
        pid=pid,
        started_at=now_iso(),
    )
