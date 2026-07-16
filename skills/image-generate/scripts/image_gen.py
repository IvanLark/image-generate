#!/usr/bin/env python3
"""Skill 入口包装：优先用 uv run，保证依赖与可编辑安装一致。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    env = os.environ.copy()
    # 允许直接 python scripts/image_gen.py 时也能找到包
    src = str(SKILL_ROOT / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    # 优先 uv run（本 skill 用 uv 管理依赖）
    # Windows 上可执行文件可能是 uv.exe
    uv = _which("uv") or _which("uv.exe")
    if uv:
        cmd = [uv, "run", "--directory", str(SKILL_ROOT), "image-gen", *sys.argv[1:]]
        return subprocess.call(cmd, env=env)

    # 回退：已安装 image-gen 或 PYTHONPATH 可用
    try:
        from image_generate.cli import main as cli_main

        return int(cli_main())
    except ImportError:
        py = "python" if os.name == "nt" else "python3"
        print(
            "错误: 未找到 uv，且无法 import image_generate（依赖未安装）。\n"
            f"请在 skill 根目录初始化依赖：\n"
            f"  有 uv:  cd {SKILL_ROOT} && uv sync\n"
            f"          然后: uv run image-gen ...\n"
            f"  无 uv:  cd {SKILL_ROOT} && {py} -m pip install -e .\n"
            f"          然后: {py} -m image_generate.cli ...\n"
            f"                 或: {py} scripts/image_gen.py ...",
            file=sys.stderr,
        )
        return 1


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


if __name__ == "__main__":
    raise SystemExit(main())
