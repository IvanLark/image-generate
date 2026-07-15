---
name: image-generate
description: >
  通过 OpenAI 兼容 /v1/images 接口生成或编辑图片（默认 gpt-image-2），支持多供应商 profile，
  以及本机异步任务（submit/status，避免 bash 长时间阻塞）。
  当用户要求生成图片、画图、文生图、图生图、调用 images API、用 gpt-image-2，
  或提到 create/generate image 时使用。入口为 skills/image-generate 下的 Python CLI。
---

# Image Generate Skill

用本 skill 目录下的 Python CLI 调用 **OpenAI 兼容**图片接口。  
默认模型 `gpt-image-2`，协议为 `POST .../images/generations` 与 `.../images/edits`。

## 依赖初始化（首次必做）

本 skill **需要安装 Python 依赖**后才能生成图片。Agent 在第一次调用前应检查 skill 根目录是否已就绪。

Skill 根目录：本 `SKILL.md` 所在目录（例如仓库内 `skills/image-generate/`，或全局安装后的 skill 路径）。

**有 [uv](https://github.com/astral-sh/uv)（推荐）：**

```bash
cd <skill根目录>
uv sync
# 之后一律优先：
uv run image-gen <子命令> ...
```

**没有 uv，用 python / pip：**

```bash
cd <skill根目录>
python3 -m pip install -e .
# 或：pip install -e .
# 之后：
python3 -m image_generate.cli <子命令> ...
# 也可：
python3 scripts/image_gen.py <子命令> ...
```

依赖：`httpx`、`pyyaml`（见 `pyproject.toml`）。  
Python ≥ 3.11。

**调用约定：**

| 环境 | 推荐命令 |
|------|----------|
| 已安装 `uv` | `uv run image-gen ...` |
| 无 `uv`，已 `pip install -e .` | `python3 -m image_generate.cli ...` 或 `python3 scripts/image_gen.py ...` |

包装脚本 `scripts/image_gen.py`：检测到 `uv` 时会走 `uv run`；无 `uv` 时尝试直接 import 本包（需已按上面装过依赖）。

若出现 `ModuleNotFoundError: httpx` / `image_generate` 等，先回到本节做 `uv sync` 或 `pip install -e .`，再重试出图。

## 同步 vs 异步（必读）

图片生成常要 **1～数分钟**。两种用法：

| 模式 | 命令 | 单次命令耗时 | 适用 |
|------|------|--------------|------|
| **异步（Agent 默认）** | `submit` → 稍后 `status` | 秒级 | AI / 怕 bash 超时 / **2K·4K** |
| **同步** | `generate` / `edit` | 整段阻塞 | 人在终端、1K 且可接受等待 |

异步是 **本机后台进程 + jobs 状态文件**，供应商接口仍是同步长连接。

### 2K / 4K 必须优先异步

- **2K、4K** 往往要 **数分钟**，且 **按张计费**（例如本仓库 `paid_hq`）。
- 若 Agent 用同步 `generate`/`edit`，bash/工具调用可能先超时，任务中断，**钱花了图却没稳稳拿到**。
- **规则：生成或编辑 2K/4K 时，一律 `submit` + 周期性 `status`。** 不要用同步；也不要默认 `wait`（`wait` 仍会长时间占住工具调用）。
- 1K 在人机交互、可接受阻塞时可用同步；Agent 侧仍更推荐异步。

## 何时使用

- 用户要生成/编辑光栅图片（PNG/JPEG/WebP）
- 需要走中转站或官方 OpenAI 的 Images API
- 需要切换不同供应商（多个 profile）

## 何时不用

- 改 SVG/矢量图标体系、用 HTML/CSS 画界面
- 用户明确只要本地图片处理（裁剪、压缩）且与生图模型无关

## 目录与入口

```text
<skill根目录>/          # 本 SKILL.md 所在目录
  config/
  scripts/image_gen.py
  src/image_generate/
  references/
```

初始化依赖后自检：

```bash
cd <skill根目录>
# 有 uv：
uv run image-gen --help
# 无 uv：
python3 -m image_generate.cli --help
```

### 子命令一览

| 命令 | 作用 |
|------|------|
| `submit generate` | 后台文生图，立刻返回 `job_id` |
| `submit edit` | 后台图生图 |
| `status <job_id>` | 查询任务（秒级） |
| `wait <job_id>` | 阻塞等到完成（人用；Agent 慎用） |
| `list` | 列出本机任务 |
| `generate` | 同步文生图 |
| `edit` | 同步图生图 |
| `profiles` | 列出供应商配置 |

下文示例默认写 `uv run image-gen`；无 `uv` 时把整段换成 `python3 -m image_generate.cli` 即可。

## 配置（多供应商）

```bash
cd <skill根目录>
cp config/profiles.example.yaml config/profiles.yaml
```

编辑 `profiles.yaml`：`type`、`base_url`、密钥、`model`。  
密钥：`api_key_env` / `api_key_file` / `api_key`（勿提交 git）。

环境变量：

- `IMAGE_GENERATE_CONFIG`：配置文件路径
- `IMAGE_GENERATE_JOBS_DIR`：异步任务目录（默认 skill 内 `jobs/`）

**当前 type**：`openai_compatible`

## Agent 推荐流程（异步）

1. **依赖已安装**（见上文「依赖初始化」）；`config/profiles.yaml` 与密钥可用。**不要**让用户把完整密钥贴进聊天。
2. 提交任务（数秒内结束）：

```bash
cd <skill根目录>
uv run image-gen submit generate \
  --prompt "你的提示词" \
  --out /path/to/output.png \
  --json
```

（`size` / `quality` / `moderation` 默认均为 `auto`，`n` 默认 `1`。需要时再显式传。）

3. 从 stdout JSON 读取 `job_id`（或默认模式下一行纯 `job_id`）。
4. 隔 **15～30 秒** 查询（或下一轮对话再查）：

```bash
uv run image-gen status <job_id> --json
```

5. 根据 `status`：
   - `running`：继续等，再 `status`
   - `done`：读 `result_paths`，展示图片
   - `error`：读 `error`；必要时看 `jobs/<id>/run.log`
6. **不要**默认使用同步 `generate` 或 `wait`（会再次长阻塞）。
7. 用户要 **2K/4K**，或 profile 为 `paid_hq` 且尺寸含 2048/3840/2160 时：**强制异步**，并在轮询间隔可略拉长（如 20～30 秒）。

未指定 `--out` / `--out-dir` 时，图片默认写到 `jobs/<job_id>/output.png`。

### submit 输出约定

- 默认：stdout **仅一行** `job_id`；说明在 stderr
- `--json`：stdout 为 JSON（`job_id`、`status`、`job_dir`、`output_paths`、`pid`）

### status --json 字段

`job_id`、`status`、`result_paths`、`error`、`elapsed_ms`、`elapsed_so_far_ms`（running 时）等。

## 同步流程（人用）

```bash
cd <skill根目录>
uv run image-gen generate \
  --prompt "你的提示词" \
  --out /path/to/output.png
```

成功时 stdout **每行一个绝对路径**。  
也可用：`submit` 后 `wait <job_id>`。

## 参数说明（默认与可选值）

完整表见 `references/api.md`。命令行也可用 `uv run image-gen generate --help`。

| 参数 | 默认 | 可选值 | 含义 |
|------|------|--------|------|
| `--size` | `auto` | `auto`；`1024x1024` / `1536x1024` / `1024x1536`（1K 档）；`2048x2048` / `2048x1152`（2K）；`3840x2160` / `2160x3840`（4K）等 | 输出尺寸；2K/4K 仅部分 profile；**2K/4K 请异步** |
| `--quality` | `auto` | `auto` / `low` / `medium` / `high` | 质量；high 更细更慢 |
| `--n` | `1` | 1～10 | 生成张数 |
| `--output-format` | `png` | `png` / `jpeg` / `webp` | 输出编码 |
| `--moderation` | `auto` | `auto` / `low` | 审核严格度 |
| `--model` | profile 内（常 `gpt-image-2`） | 模型 ID | 覆盖 profile |
| `--profile` | 配置 `active` | 如 `free_1k` / `paid_1k` / `paid_hq` | 选供应商 |
| `--timeout` | profile（常 600） | 秒 | HTTP 读超时 |
| `--input-fidelity` | 不传 | `low` / `high` | 仅 edit，输入保真 |
| `--transparent` | 不传（关闭） | 颜色：`green` / `magenta` / `#00FF00` / `0,255,0` 等 | **可选**本地抠图：指定背景色，落盘后处理为透明 PNG。不从提示词猜颜色，由调用方明确传入 |

### 可选抠图 `--transparent`

- **默认关闭**。只有传了 `--transparent <颜色>` 才做后处理。
- **不是 API 原生透明**：先按普通图生成，再本地按色键抠背景。
- AI 在提示词里写了纯色背景时，**把同一颜色直接传给 `--transparent`**，无需再解析提示词。
- 开启后强制 `output_format=png`。
- 示例：

```bash
uv run image-gen generate \
  --prompt "一只白猫贴纸，纯绿色 #00FF00 背景，无阴影" \
  --transparent green \
  --out /path/to/cat.png
```

**profile 配置（非 CLI）：**

| 项 | 默认 | 含义 |
|----|------|------|
| `response_format_b64_json` | `false` | 为 true 时请求附带 `response_format=b64_json`。默认关（与 playground 一致）。客户端仍支持解析 b64 或 url |

本仓库供应商简述：`free_1k`/`paid_1k` 仅 1K；`paid_hq` 可 2K/4K。

## 其它示例

```bash
cd <skill根目录>
uv run image-gen profiles
uv run image-gen generate --help
uv run image-gen list
uv run image-gen list --status running

uv run image-gen submit generate --profile free_1k --prompt "一只猫" --json
# 2K/4K：必须异步 + paid_hq（示例）
uv run image-gen submit generate --profile paid_hq --size 3840x2160 --prompt "一只猫" --json
uv run image-gen status 20260711T221530-a3f2b1 --json
# wait 仅建议人在终端用；Agent 继续用 status 轮询
uv run image-gen wait 20260711T221530-a3f2b1 --interval 10 --timeout 600

uv run image-gen submit edit --image ref.png --prompt "只换背景" --out edited.png --json
```

## 安全

- 禁止在聊天、日志、提交内容中粘贴完整 API Key
- `config/profiles.yaml`、`key.txt`、`jobs/` 勿提交密钥或敏感输出
- job 文件 **不包含** api_key

## 提示词

见 `references/prompting.md`。

## 扩展供应商

1. `providers/` 新增 adapter  
2. `registry.py` 注册 type  
3. 更新 `profiles.example.yaml`  

异步 job 层与供应商协议无关，新 adapter 自动可被 `submit` 使用。

## 更多文档

- `references/api.md`
- `references/prompting.md`
