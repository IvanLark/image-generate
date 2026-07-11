# image-generate

面向 Agent / 命令行的 **OpenAI 兼容图片生成** skill，默认模型 `gpt-image-2`，协议为 `/v1/images/generations` 与 `/v1/images/edits`。

核心能力：

- **多供应商 profile**（`base_url` + 密钥 + 模型）
- **同步**文生图 / 图生图
- **本机异步任务**（`submit` + `status`，避免工具调用长时间阻塞）
- 输出路径可指定；落盘后提示**实际分辨率**（可能与请求 size 不同）

主代码与文档在：

```text
skills/image-generate/
```

---

## 环境

- Python ≥ 3.11
- [uv](https://github.com/astral-sh/uv)

```bash
cd skills/image-generate
uv sync
```

---

## 配置供应商

```bash
cd skills/image-generate
cp config/profiles.example.yaml config/profiles.yaml
# 编辑 profiles.yaml，填写 base_url、model、密钥相关字段
```

密钥推荐用环境变量（示例见 `profiles.example.yaml`），或 `api_key_file` / `api_key`。  
**不要**把真实 `profiles.yaml` 和密钥提交进 git（已在 `.gitignore` 中）。

配置文件路径不必固定在 skill 内，可用：

| 方式 | 说明 |
|------|------|
| 默认 | `skills/image-generate/config/profiles.yaml` |
| `--config /path/to.yaml` | 任意路径 |
| 环境变量 `IMAGE_GENERATE_CONFIG` | 全局指定配置文件 |

查看当前 profile：

```bash
cd skills/image-generate
uv run image-gen profiles
```

---

## 快速使用

在 `skills/image-generate` 目录下：

### 异步（推荐 Agent / 2K·4K）

```bash
uv run image-gen submit generate \
  --prompt "一只橘猫" \
  --out ../../output/cat.png \
  --json

# 轮询
uv run image-gen status <job_id> --json
```

- 成功时看 `status=done` 与 `result_paths`
- **2K / 4K** 往往更慢且可能按张计费：务必异步，避免同步工具超时后结果丢失

### 同步（人在终端、1K 可接受等待）

```bash
uv run image-gen generate \
  --prompt "一只橘猫" \
  --out ../../output/cat.png

uv run image-gen edit \
  --image ../../output/cat.png \
  --prompt "只把背景换成海边，主体不变" \
  --out ../../output/cat-edit.png
```

### 常用子命令

| 命令 | 说明 |
|------|------|
| `submit generate` / `submit edit` | 后台提交，秒级返回 `job_id` |
| `status` / `list` / `wait` | 查状态 / 列表 / 阻塞等待（`wait` 给人用） |
| `generate` / `edit` | 同步阻塞 |
| `profiles` | 列出供应商 |
| `generate --help` | 参数默认值与可选值说明 |

输出路径：

- `--out`：指定单个文件
- `--out-dir`：指定目录（多图自动命名）
- 异步且未指定时：默认 `jobs/<job_id>/output.png`

仓库根目录 `output/` 适合放生成结果（已 gitignore）。

---

## 默认参数（摘要）

| 参数 | 默认 |
|------|------|
| `--size` | `auto` |
| `--quality` | `auto` |
| `--moderation` | `auto` |
| `--n` | `1` |
| `--output-format` | `png` |
| `response_format_b64_json`（profile） | `false`（仍支持解析 b64 或 url） |

完整说明见 skill 内 `SKILL.md` 与 `references/api.md`。

---

## 目录结构

```text
.
├── README.md                 # 本文件
├── output/                   # 建议的出图目录（gitignore）
└── skills/image-generate/
    ├── SKILL.md              # Agent skill 说明
    ├── config/
    │   ├── profiles.example.yaml
    │   └── profiles.yaml     # 本地配置（gitignore）
    ├── scripts/image_gen.py  # 包装入口（内部 uv run）
    ├── src/image_generate/   # CLI 与供应商实现
    ├── jobs/                 # 异步任务状态（gitignore）
    └── references/           # API / 提示词参考
```

依赖管理：`skills/image-generate` 内使用 `uv`（`pyproject.toml` + `uv.lock`）。

---

## 安全

- 勿将 API Key 写入聊天记录或提交到仓库
- `config/profiles.yaml`、`key.txt`、`jobs/`、`output/` 已忽略
- job 元数据中不包含密钥

---

## 更多文档

- [skills/image-generate/SKILL.md](skills/image-generate/SKILL.md) — 使用流程与参数
- [skills/image-generate/references/api.md](skills/image-generate/references/api.md) — 接口与尺寸
- [skills/image-generate/references/prompting.md](skills/image-generate/references/prompting.md) — 提示词建议
