# image-generate

面向 Agent / 命令行的 **OpenAI 兼容图片生成** skill。  
默认模型 `gpt-image-2`，协议：`/v1/images/generations`、`/v1/images/edits`。

## 安装

```bash
npx skills add https://github.com/IvanLark/image-generate
```

装好后 skill 一般在全局 skills 目录（具体路径以你的 Agent 环境为准），源码与 CLI 在仓库的 `skills/image-generate/`。

---

## 能做什么

- **多供应商 profile**（`base_url` + 密钥 + 模型）
- **同步**文生图 / 图生图
- **本机异步任务**（`submit` + `status`，避免工具调用长时间阻塞）
- 可指定输出路径；落盘后提示**实际分辨率**（可能与请求 size 不同）

| 场景 | 推荐方式 |
|------|----------|
| Agent / 自动化 | `submit` → `status` |
| **2K / 4K**（更慢、可能按张计费） | **必须异步**，避免同步超时白花钱 |
| 人在终端、1K 可接受等待 | 可用同步 `generate` / `edit` |

---

## 环境

- Python ≥ 3.11
- [uv](https://github.com/astral-sh/uv)

```bash
cd skills/image-generate   # 或你安装后的 skill 目录
uv sync
```

---

## 配置供应商

```bash
cd skills/image-generate
cp config/profiles.example.yaml config/profiles.yaml
# 编辑 profiles.yaml：base_url、model、密钥
```

密钥推荐用环境变量（见 `profiles.example.yaml`），也可用 `api_key_file` / `api_key`。  
**不要**把真实 `profiles.yaml` 和密钥提交进 git。

配置文件位置：

| 方式 | 说明 |
|------|------|
| 默认 | skill 内 `config/profiles.yaml` |
| `--config /path/to.yaml` | 任意路径 |
| 环境变量 `IMAGE_GENERATE_CONFIG` | 全局指定配置文件 |

```bash
uv run image-gen profiles
```

---

## 快速使用

在 skill 目录下执行。

### 异步（推荐 Agent / 2K·4K）

```bash
uv run image-gen submit generate \
  --prompt "一只橘猫" \
  --out ./output/cat.png \
  --json

uv run image-gen status <job_id> --json
```

成功时：`status=done`，路径在 `result_paths`。

### 同步（人在终端）

```bash
uv run image-gen generate \
  --prompt "一只橘猫" \
  --out ./output/cat.png

uv run image-gen edit \
  --image ./output/cat.png \
  --prompt "只把背景换成海边，主体不变" \
  --out ./output/cat-edit.png
```

### 常用命令

| 命令 | 说明 |
|------|------|
| `submit generate` / `submit edit` | 后台提交，秒级返回 `job_id` |
| `status` / `list` / `wait` | 查状态 / 列表 / 阻塞等待（`wait` 给人用） |
| `generate` / `edit` | 同步阻塞 |
| `profiles` | 列出供应商 |
| `generate --help` | 参数默认值与可选值 |

输出路径：

- `--out`：单个文件  
- `--out-dir`：目录（多图自动命名）  
- 异步且未指定时：默认 `jobs/<job_id>/output.png`

---

## 默认参数

| 参数 | 默认 |
|------|------|
| `--size` | `auto` |
| `--quality` | `auto` |
| `--moderation` | `auto` |
| `--n` | `1` |
| `--output-format` | `png` |
| `response_format_b64_json`（profile） | `false`（仍可解析 b64 或 url） |

详见 [SKILL.md](skills/image-generate/SKILL.md) 与 [references/api.md](skills/image-generate/references/api.md)。

---

## 目录结构

```text
.
├── README.md
└── skills/image-generate/
    ├── SKILL.md
    ├── config/
    │   ├── profiles.example.yaml
    │   └── profiles.yaml          # 本地配置（gitignore）
    ├── scripts/image_gen.py
    ├── src/image_generate/
    ├── jobs/                      # 异步任务（gitignore）
    └── references/
```

依赖：`skills/image-generate` 内用 `uv`（`pyproject.toml` + `uv.lock`）。

---

## 安全

- 勿将 API Key 写入聊天或提交到仓库  
- `config/profiles.yaml`、`key.txt`、`jobs/`、`output/` 已 ignore  
- job 元数据不含密钥  

---

## 更多文档

- [skills/image-generate/SKILL.md](skills/image-generate/SKILL.md) — 使用流程与参数  
- [skills/image-generate/references/api.md](skills/image-generate/references/api.md) — 接口与尺寸  
- [skills/image-generate/references/prompting.md](skills/image-generate/references/prompting.md) — 提示词建议  
