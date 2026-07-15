"""CLI 入口：同步 generate/edit + 异步 submit/status/wait/list。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from image_generate.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    load_config,
    mask_secret,
)
from image_generate.jobs import (
    JobError,
    job_dir,
    list_jobs,
    load_job,
    public_status_view,
    refresh_job_liveness,
    run_log_path,
)
from image_generate.providers.openai_compatible import ProviderError
from image_generate.registry import registered_types
from image_generate.runner import (
    execute_edit,
    execute_generate,
    run_job_worker,
    submit_edit_job,
    submit_generate_job,
)
from image_generate.save import SaveError, build_output_paths
from image_generate.transparent import TransparentError, parse_color

DEFAULT_SIZE = "auto"
DEFAULT_QUALITY = "auto"
DEFAULT_FORMAT = "png"
DEFAULT_MODERATION = "auto"
DEFAULT_N = 1

# --help 用：各参数可选值与含义
HELP_SIZE = (
    "输出尺寸。默认 auto（由服务端按 quality 等决定）。"
    "常用: auto | 1024x1024(方1K) | 1536x1024(横1.5K) | 1024x1536(竖1.5K) | "
    "2048x2048(方2K) | 2048x1152(横2K) | 3840x2160(4K横) | 2160x3840(4K竖)。"
    "是否支持 2K/4K 取决于供应商 profile。"
    "【重要】2K/4K 往往要数分钟且可能按张计费：Agent/自动化务必用 "
    "「submit + status」异步，不要用同步 generate/edit，以免工具调用超时导致白花钱。"
)
HELP_QUALITY = (
    "质量档位。默认 auto。"
    "auto=服务端自选; low=快/草稿; medium=均衡; high=细节最多、更慢更贵。"
)
HELP_N = "生成张数，1～10，默认 1。"
HELP_OUTPUT_FORMAT = (
    "输出格式。默认 png。"
    "png=无损适合透明/精细; jpeg=体积小适合照片; webp=体积与质量折中。"
)
HELP_MODERATION = (
    "内容审核严格度。默认 auto。"
    "auto=默认策略; low=更宽松（部分模型/中转支持）。"
)
HELP_MODEL = "覆盖 profile 中的模型 ID。默认用 profile.model（当前多为 gpt-image-2）。"
HELP_PROFILE = "使用的 profile 名称。默认读配置里的 active。可用 image-gen profiles 查看。"
HELP_CONFIG = (
    f"配置文件路径。默认: {DEFAULT_CONFIG_PATH}，"
    "或环境变量 IMAGE_GENERATE_CONFIG。"
)
HELP_OUT = "输出文件路径（单图）。异步且未指定时默认写到 jobs/<job_id>/output.<ext>。"
HELP_OUT_DIR = "输出目录。多图时自动命名 output_1.png 等。"
HELP_FORCE = "若输出文件已存在则覆盖；默认不覆盖。"
HELP_TIMEOUT = "HTTP 读超时秒数，覆盖 profile.timeout（默认多为 600）。"
HELP_PROMPT = "提示词文本（必填）。"
HELP_IMAGE = "输入/参考图片路径。可重复传多次（多图编辑）。"
HELP_MASK = "可选遮罩 PNG（透明区域表示可重绘）。仅 edit。"
HELP_INPUT_FIDELITY = (
    "输入保真度，仅 edit。默认不传。"
    "low=较低保真; high=更贴近原图细节（可能更贵/更慢，视模型支持）。"
)
HELP_TRANSPARENT = (
    "可选本地抠图：指定要去掉的背景色，落盘后处理为透明 PNG。"
    "不传则不做抠图。"
    "颜色由调用方明确给出（AI 已知提示词里用了什么纯色背景时直接填该色），"
    "不会从提示词自动识别。"
    "支持: green/magenta/white/black 等名称，#00FF00，或 0,255,0。"
    "开启时强制 output_format=png。"
)


def _die(msg: str, code: int = 1) -> None:
    print(f"错误: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _add_common_args(p: argparse.ArgumentParser, *, with_dry_run: bool = True) -> None:
    p.add_argument("--config", default=None, help=HELP_CONFIG)
    p.add_argument("--profile", default=None, help=HELP_PROFILE)
    p.add_argument("--model", default=None, help=HELP_MODEL)
    p.add_argument("--size", default=DEFAULT_SIZE, metavar="SIZE", help=HELP_SIZE)
    p.add_argument(
        "--quality",
        default=DEFAULT_QUALITY,
        choices=["auto", "low", "medium", "high"],
        help=HELP_QUALITY,
    )
    p.add_argument("--n", type=int, default=DEFAULT_N, help=HELP_N)
    p.add_argument(
        "--output-format",
        default=DEFAULT_FORMAT,
        choices=["png", "jpeg", "webp"],
        help=HELP_OUTPUT_FORMAT,
    )
    p.add_argument(
        "--moderation",
        default=DEFAULT_MODERATION,
        choices=["auto", "low"],
        help=HELP_MODERATION,
    )
    p.add_argument("--out", default=None, help=HELP_OUT)
    p.add_argument("--out-dir", default=None, help=HELP_OUT_DIR)
    p.add_argument("--force", action="store_true", help=HELP_FORCE)
    p.add_argument(
        "--transparent",
        default=None,
        metavar="COLOR",
        help=HELP_TRANSPARENT,
    )
    if with_dry_run:
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="只打印将要发送的请求 JSON，不调用 API、不写文件。",
        )
    p.add_argument("--timeout", type=float, default=None, help=HELP_TIMEOUT)


def _add_generate_args(p: argparse.ArgumentParser, *, with_dry_run: bool = True) -> None:
    _add_common_args(p, with_dry_run=with_dry_run)
    p.add_argument("--prompt", required=True, help=HELP_PROMPT)


def _add_edit_args(p: argparse.ArgumentParser, *, with_dry_run: bool = True) -> None:
    _add_common_args(p, with_dry_run=with_dry_run)
    p.add_argument("--prompt", required=True, help=HELP_PROMPT)
    p.add_argument(
        "--image",
        action="append",
        dest="images",
        required=True,
        help=HELP_IMAGE,
    )
    p.add_argument("--mask", default=None, help=HELP_MASK)
    p.add_argument(
        "--input-fidelity",
        default=None,
        choices=["low", "high"],
        help=HELP_INPUT_FIDELITY,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="image-gen",
        description=(
            "OpenAI 兼容图片生成 CLI（同步 + 本机异步任务）。"
            "2K/4K 耗时长且常按张计费：自动化/Agent 请优先 submit+status，避免同步超时白花钱。"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser(
        "generate",
        help="文生图（同步阻塞；2K/4K 请改用 submit generate）",
    )
    _add_generate_args(gen)

    edit = sub.add_parser(
        "edit",
        help="图生图 / 局部重绘（同步阻塞；2K/4K 请改用 submit edit）",
    )
    _add_edit_args(edit)

    submit = sub.add_parser(
        "submit",
        help="提交后台任务（异步，秒级返回；2K/4K 强烈推荐）",
    )
    submit_sub = submit.add_subparsers(dest="submit_command", required=True)
    sg = submit_sub.add_parser(
        "generate",
        help="后台文生图（推荐用于 2K/4K 与 Agent）",
    )
    _add_generate_args(sg, with_dry_run=False)
    sg.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="stdout 输出 JSON（含 job_id）",
    )
    se = submit_sub.add_parser(
        "edit",
        help="后台图生图（推荐用于 2K/4K 与 Agent）",
    )
    _add_edit_args(se, with_dry_run=False)
    se.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="stdout 输出 JSON（含 job_id）",
    )

    status_p = sub.add_parser("status", help="查询任务状态")
    status_p.add_argument("job_id", help="任务 ID")
    status_p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="stdout 输出 JSON",
    )

    wait_p = sub.add_parser("wait", help="等待任务完成（阻塞）")
    wait_p.add_argument("job_id", help="任务 ID")
    wait_p.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="轮询间隔秒数，默认 10",
    )
    wait_p.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="最长等待秒数，默认不限制（建议设为 profile timeout）",
    )
    wait_p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="结束时输出 JSON 状态",
    )

    list_p = sub.add_parser("list", help="列出本机任务")
    list_p.add_argument(
        "--status",
        default=None,
        choices=["queued", "running", "done", "error"],
        help="按状态过滤",
    )
    list_p.add_argument("--limit", type=int, default=20, help="最多条数，默认 20")
    list_p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="stdout 输出 JSON 数组",
    )

    profiles_p = sub.add_parser("profiles", help="列出配置中的 profile")
    profiles_p.add_argument("--config", default=None, help="配置文件路径")

    run_job_p = sub.add_parser(
        "run-job",
        help=argparse.SUPPRESS,  # 内部命令：后台 worker
    )
    run_job_p.add_argument("job_id", help="任务 ID")

    return parser


def _validate_n(n: int) -> None:
    if n < 1 or n > 10:
        _die("--n 应在 1～10 之间")


def _normalize_transparent(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        parse_color(value)  # 尽早校验
    except TransparentError as exc:
        _die(str(exc))
    return str(value).strip()


def _effective_output_format(output_format: str, transparent: str | None) -> str:
    return "png" if transparent else output_format


def _cmd_profiles(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config, require_api_key=False)
    except ConfigError as exc:
        _die(str(exc))

    print(f"配置文件: {cfg.config_path}")
    print(f"当前 active: {cfg.active}")
    print(f"已注册供应商类型: {', '.join(registered_types())}")
    print()
    for name, prof in cfg.profiles.items():
        mark = "*" if name == cfg.active else " "
        try:
            key_display = mask_secret(prof.ensure_api_key())
        except ConfigError:
            key_display = "(未配置)"
        print(
            f"{mark} {name}\n"
            f"    type={prof.type}  model={prof.model}\n"
            f"    base_url={prof.base_url}\n"
            f"    timeout={prof.timeout:.0f}s  "
            f"key={key_display}"
        )
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    _validate_n(args.n)
    transparent = _normalize_transparent(getattr(args, "transparent", None))
    output_format = _effective_output_format(args.output_format, transparent)
    try:
        cfg = load_config(args.config, require_api_key=False)
        profile = cfg.get_profile(args.profile, require_api_key=not args.dry_run)
    except ConfigError as exc:
        _die(str(exc))

    if args.timeout is not None:
        profile.timeout = args.timeout

    model = args.model or profile.model
    try:
        paths = build_output_paths(
            out=args.out,
            out_dir=args.out_dir,
            n=args.n,
            output_format=output_format,
            default_name="output",
        )
    except SaveError as exc:
        _die(str(exc))

    body: dict[str, Any] = {
        "model": model,
        "prompt": args.prompt,
        "n": args.n,
        "size": args.size,
        "quality": args.quality,
        "output_format": output_format,
        "moderation": args.moderation,
    }
    if profile.response_format_b64_json:
        body["response_format"] = "b64_json"

    if args.dry_run:
        _print_json(
            {
                "mode": "generate",
                "profile": profile.name,
                "type": profile.type,
                "endpoint": "images/generations",
                "body": body,
                "transparent": transparent,
                "outputs": [str(p) for p in paths],
            }
        )
        return 0

    try:
        _result, written = execute_generate(
            profile,
            prompt=args.prompt,
            model=args.model,
            size=args.size,
            quality=args.quality,
            n=args.n,
            output_format=output_format,
            moderation=args.moderation,
            output_paths=paths,
            force=args.force,
            transparent=transparent,
        )
    except (
        ConfigError,
        ProviderError,
        SaveError,
        TransparentError,
        ValueError,
        NotImplementedError,
    ) as exc:
        _die(str(exc))
    except Exception as exc:  # noqa: BLE001
        _die(f"未预期错误: {exc}")

    for p in written:
        print(p)
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    _validate_n(args.n)
    transparent = _normalize_transparent(getattr(args, "transparent", None))
    output_format = _effective_output_format(args.output_format, transparent)
    try:
        cfg = load_config(args.config, require_api_key=False)
        profile = cfg.get_profile(args.profile, require_api_key=not args.dry_run)
    except ConfigError as exc:
        _die(str(exc))

    if args.timeout is not None:
        profile.timeout = args.timeout

    model = args.model or profile.model
    try:
        paths = build_output_paths(
            out=args.out,
            out_dir=args.out_dir,
            n=args.n,
            output_format=output_format,
            default_name="edit",
        )
    except SaveError as exc:
        _die(str(exc))

    meta = {
        "model": model,
        "prompt": args.prompt,
        "n": args.n,
        "size": args.size,
        "quality": args.quality,
        "output_format": output_format,
        "moderation": args.moderation,
        "images": args.images,
        "mask": args.mask,
        "input_fidelity": args.input_fidelity,
        "transparent": transparent,
    }

    if args.dry_run:
        _print_json(
            {
                "mode": "edit",
                "profile": profile.name,
                "type": profile.type,
                "endpoint": "images/edits",
                **meta,
                "outputs": [str(p) for p in paths],
            }
        )
        return 0

    try:
        _result, written = execute_edit(
            profile,
            prompt=args.prompt,
            image_paths=list(args.images),
            mask_path=args.mask,
            model=args.model,
            size=args.size,
            quality=args.quality,
            n=args.n,
            output_format=output_format,
            moderation=args.moderation,
            input_fidelity=args.input_fidelity,
            output_paths=paths,
            force=args.force,
            transparent=transparent,
        )
    except (
        ConfigError,
        ProviderError,
        SaveError,
        TransparentError,
        ValueError,
        NotImplementedError,
    ) as exc:
        _die(str(exc))
    except Exception as exc:  # noqa: BLE001
        _die(f"未预期错误: {exc}")

    for p in written:
        print(p)
    return 0


def _cmd_submit_generate(args: argparse.Namespace) -> int:
    _validate_n(args.n)
    transparent = _normalize_transparent(getattr(args, "transparent", None))
    output_format = _effective_output_format(args.output_format, transparent)
    try:
        cfg = load_config(args.config, require_api_key=False)
        profile = cfg.get_profile(args.profile, require_api_key=True)
    except ConfigError as exc:
        _die(str(exc))

    try:
        job = submit_generate_job(
            profile=profile,
            config_path=cfg.config_path,
            profile_name=args.profile or profile.name,
            model=args.model,
            timeout=args.timeout,
            prompt=args.prompt,
            size=args.size,
            quality=args.quality,
            n=args.n,
            output_format=output_format,
            moderation=args.moderation,
            out=args.out,
            out_dir=args.out_dir,
            force=args.force,
            transparent=transparent,
        )
    except (JobError, SaveError, ConfigError) as exc:
        _die(str(exc))
    except Exception as exc:  # noqa: BLE001
        _die(f"提交失败: {exc}")

    job_id = str(job["id"])
    print(f"已提交后台任务: {job_id}", file=sys.stderr)
    print(f"查询: uv run image-gen status {job_id}", file=sys.stderr)
    if args.as_json:
        _print_json(
            {
                "job_id": job_id,
                "status": job.get("status"),
                "job_dir": str(job_dir(job_id)),
                "output_paths": job.get("output_paths") or [],
                "pid": job.get("pid"),
            }
        )
    else:
        print(job_id)
    return 0


def _cmd_submit_edit(args: argparse.Namespace) -> int:
    _validate_n(args.n)
    transparent = _normalize_transparent(getattr(args, "transparent", None))
    output_format = _effective_output_format(args.output_format, transparent)
    try:
        cfg = load_config(args.config, require_api_key=False)
        profile = cfg.get_profile(args.profile, require_api_key=True)
    except ConfigError as exc:
        _die(str(exc))

    try:
        job = submit_edit_job(
            profile=profile,
            config_path=cfg.config_path,
            profile_name=args.profile or profile.name,
            model=args.model,
            timeout=args.timeout,
            prompt=args.prompt,
            images=list(args.images),
            mask=args.mask,
            input_fidelity=args.input_fidelity,
            size=args.size,
            quality=args.quality,
            n=args.n,
            output_format=output_format,
            moderation=args.moderation,
            out=args.out,
            out_dir=args.out_dir,
            force=args.force,
            transparent=transparent,
        )
    except (JobError, SaveError, ConfigError) as exc:
        _die(str(exc))
    except Exception as exc:  # noqa: BLE001
        _die(f"提交失败: {exc}")

    job_id = str(job["id"])
    print(f"已提交后台任务: {job_id}", file=sys.stderr)
    print(f"查询: uv run image-gen status {job_id}", file=sys.stderr)
    if args.as_json:
        _print_json(
            {
                "job_id": job_id,
                "status": job.get("status"),
                "job_dir": str(job_dir(job_id)),
                "output_paths": job.get("output_paths") or [],
                "pid": job.get("pid"),
            }
        )
    else:
        print(job_id)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        job = load_job(args.job_id)
        job = refresh_job_liveness(job)
    except JobError as exc:
        _die(str(exc))

    view = public_status_view(job)
    if args.as_json:
        # 查到任务即 exit 0；失败看 JSON 里的 status/error，方便 Agent 轮询
        _print_json(view)
        return 0

    status = job.get("status")
    print(f"job_id:  {job.get('id')}")
    print(f"status:  {status}")
    print(f"mode:    {job.get('mode')}")
    print(f"profile: {job.get('profile')}  model={job.get('model')}")
    if job.get("pid"):
        print(f"pid:     {job.get('pid')}")
    if view.get("elapsed_so_far_ms") is not None:
        print(f"已等待:  {view['elapsed_so_far_ms'] / 1000:.1f}s")
    if job.get("elapsed_ms") is not None:
        print(f"耗时:    {job['elapsed_ms'] / 1000:.1f}s")
    paths = job.get("result_paths") or []
    if paths:
        print("结果:")
        for p in paths:
            print(f"  {p}")
    if job.get("error"):
        print(f"错误:    {job.get('error')}")
    print(f"目录:    {job_dir(str(job.get('id')))}")
    print(f"日志:    {run_log_path(str(job.get('id')))}")
    return 0


def _cmd_wait(args: argparse.Namespace) -> int:
    deadline = None
    if args.timeout is not None:
        deadline = time.time() + args.timeout

    while True:
        try:
            job = load_job(args.job_id)
            job = refresh_job_liveness(job)
        except JobError as exc:
            _die(str(exc))

        status = job.get("status")
        if status in ("done", "error"):
            if args.as_json:
                _print_json(public_status_view(job))
            elif status == "done":
                for p in job.get("result_paths") or []:
                    print(p)
            else:
                _die(str(job.get("error") or "任务失败"))
            return 0 if status == "done" else 1

        if deadline is not None and time.time() >= deadline:
            _die(f"等待超时（{args.timeout}s），任务仍为 {status}")

        print(
            f"[等待] {args.job_id} status={status} …",
            file=sys.stderr,
        )
        time.sleep(max(0.5, args.interval))


def _cmd_list(args: argparse.Namespace) -> int:
    jobs = list_jobs(status=args.status, limit=max(1, args.limit))
    if args.as_json:
        _print_json([public_status_view(j) for j in jobs])
        return 0

    if not jobs:
        print("(无任务)")
        return 0

    for j in jobs:
        jid = j.get("id")
        st = j.get("status")
        mode = j.get("mode")
        created = j.get("created_at") or ""
        print(f"{jid}  {st:8}  {mode:8}  {created}")
    return 0


def _cmd_run_job(args: argparse.Namespace) -> int:
    return run_job_worker(args.job_id)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "profiles":
        return _cmd_profiles(args)
    if args.command == "generate":
        return _cmd_generate(args)
    if args.command == "edit":
        return _cmd_edit(args)
    if args.command == "submit":
        if args.submit_command == "generate":
            return _cmd_submit_generate(args)
        if args.submit_command == "edit":
            return _cmd_submit_edit(args)
        _die("submit 需要子命令 generate 或 edit")
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "wait":
        return _cmd_wait(args)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "run-job":
        return _cmd_run_job(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
