"""本机异步任务：状态文件 + 目录。"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from image_generate.config import SKILL_ROOT

JobStatus = Literal["queued", "running", "done", "error"]
JobMode = Literal["generate", "edit"]

SCHEMA_VERSION = 1
# 仅允许安全字符，防止路径穿越
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class JobError(Exception):
    """任务相关错误。"""


def jobs_root() -> Path:
    env = os.environ.get("IMAGE_GENERATE_JOBS_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (SKILL_ROOT / "jobs").resolve()


def validate_job_id(job_id: str) -> str:
    """校验 job_id，拒绝路径分隔符与穿越。"""
    value = (job_id or "").strip()
    if not value or not _JOB_ID_RE.fullmatch(value):
        raise JobError(
            f"非法任务 ID: {job_id!r}。"
            "仅允许字母、数字、点、下划线、连字符。"
        )
    if ".." in value:
        raise JobError(f"非法任务 ID: {job_id!r}")
    return value


def new_job_id() -> str:
    now = datetime.now().strftime("%Y%m%dT%H%M%S")
    suffix = secrets.token_hex(3)
    return f"{now}-{suffix}"


def job_dir(job_id: str) -> Path:
    safe_id = validate_job_id(job_id)
    path = (jobs_root() / safe_id).resolve()
    root = jobs_root().resolve()
    if path != root and root not in path.parents:
        raise JobError(f"任务路径越界: {job_id}")
    return path


def job_json_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def request_json_path(job_id: str) -> Path:
    return job_dir(job_id) / "request.json"


def run_log_path(job_id: str) -> Path:
    return job_dir(job_id) / "run.log"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# 内部兼容别名
_now_iso = now_iso


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_job(job_id: str) -> dict[str, Any]:
    path = job_json_path(job_id)
    if not path.is_file():
        raise JobError(f"找不到任务: {job_id}（路径: {path}）")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JobError(f"任务状态文件损坏: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise JobError(f"任务状态文件格式错误: {path}")
    return data


def load_request(job_id: str) -> dict[str, Any]:
    path = request_json_path(job_id)
    if not path.is_file():
        raise JobError(f"找不到任务请求文件: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JobError(f"任务请求文件损坏: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise JobError(f"任务请求文件格式错误: {path}")
    return data


def update_job(job_id: str, **fields: Any) -> dict[str, Any]:
    job = load_job(job_id)
    job.update(fields)
    _atomic_write_json(job_json_path(job_id), job)
    return job


def is_pid_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但无权限发信号，视为仍在
        return True
    except OSError:
        return False
    return True


def refresh_job_liveness(job: dict[str, Any]) -> dict[str, Any]:
    """若 status=running 但进程已死且未收尾，标为 error。"""
    if job.get("status") != "running":
        return job
    pid = job.get("pid")
    if is_pid_alive(pid if isinstance(pid, int) else None):
        return job

    job_id = str(job.get("id") or "")
    if not job_id:
        return job

    log_path = run_log_path(job_id)
    tail = ""
    if log_path.is_file():
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            tail = text[-800:].strip()
        except OSError:
            tail = ""

    msg = "后台进程已退出但未写入完成状态，详见 run.log"
    if tail:
        msg = f"{msg}\n--- run.log 末尾 ---\n{tail}"

    return update_job(
        job_id,
        status="error",
        finished_at=_now_iso(),
        error=msg,
    )


def list_jobs(*, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    root = jobs_root()
    if not root.is_dir():
        return []

    jobs: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        path = child / "job.json"
        if not path.is_file():
            continue
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(job, dict):
            continue
        job = refresh_job_liveness(job)
        if status and job.get("status") != status:
            continue
        jobs.append(job)
        if len(jobs) >= limit:
            break
    return jobs


def elapsed_so_far_ms(job: dict[str, Any]) -> int | None:
    started = job.get("started_at")
    if not started or not isinstance(started, str):
        return None
    try:
        # 支持带时区的 iso
        dt = datetime.fromisoformat(started)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc).astimezone()
        now = datetime.now(timezone.utc).astimezone()
        return max(0, int((now - dt).total_seconds() * 1000))
    except ValueError:
        return None


def public_status_view(job: dict[str, Any]) -> dict[str, Any]:
    """给 status --json 用的稳定字段。"""
    view: dict[str, Any] = {
        "job_id": job.get("id"),
        "status": job.get("status"),
        "mode": job.get("mode"),
        "profile": job.get("profile"),
        "model": job.get("model"),
        "pid": job.get("pid"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "result_paths": job.get("result_paths") or [],
        "output_paths": job.get("output_paths") or [],
        "error": job.get("error"),
        "elapsed_ms": job.get("elapsed_ms"),
        "job_dir": str(job_dir(str(job.get("id") or ""))),
    }
    if job.get("status") == "running":
        view["elapsed_so_far_ms"] = elapsed_so_far_ms(job)
    return view
