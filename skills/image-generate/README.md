# image-generate

OpenAI 兼容图片生成 skill（`gpt-image-2` / `/v1/images`），支持多供应商 profile，以及本机异步任务。

## 快速开始

```bash
cd skills/image-generate
uv sync
cp config/profiles.example.yaml config/profiles.yaml
# 编辑 profiles.yaml，配置 base_url 与密钥
export LAMCOLD_API_KEY='你的密钥'
```

### 异步（推荐给 Agent）

```bash
uv run image-gen submit generate --prompt "一只橘猫" --json
# → 得到 job_id

uv run image-gen status <job_id> --json
# running → 稍后再查；done → result_paths

uv run image-gen list
```

### 同步

```bash
uv run image-gen generate --prompt "一只橘猫" --out ../../output/cat.png
uv run image-gen generate --prompt "一只橘猫" --dry-run
```

## 命令

| 命令 | 说明 |
|------|------|
| `submit generate` / `submit edit` | 后台提交，秒级返回 |
| `status` / `wait` / `list` | 查状态 / 等待 / 列表 |
| `generate` / `edit` | 同步阻塞 |
| `profiles` | 供应商配置 |

包装脚本：`python scripts/image_gen.py ...`（内部 `uv run`）。

## 说明

详见 `SKILL.md` 与 `references/`。
