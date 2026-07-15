# API 与参数参考（OpenAI 兼容 /v1/images）

## 端点

| 动作 | 方法 | 路径 |
|------|------|------|
| 文生图 | `POST` | `{base_url}/images/generations` |
| 图生图 | `POST` | `{base_url}/images/edits` |

`base_url` 建议写成带 `/v1` 的形式，例如 `https://api.example.com/v1`。  
若只写到主机名，脚本会自动补 `/v1`。

## CLI / 请求参数

### 共用（generate / edit）

| 参数 | CLI | 默认 | 可选值 | 含义 |
|------|-----|------|--------|------|
| 提示词 | `--prompt` | （必填） | 字符串 | 描述要生成或如何编辑的内容 |
| 模型 | `--model` | profile 的 model | 如 `gpt-image-2` | 覆盖 profile 中的模型 ID |
| 尺寸 | `--size` | `auto` | 见下表 | 输出分辨率；`auto` 由服务端决定 |
| 质量 | `--quality` | `auto` | `auto` / `low` / `medium` / `high` | 细节与耗时/费用权衡 |
| 张数 | `--n` | `1` | 1～10 | 一次生成多少张 |
| 格式 | `--output-format` | `png` | `png` / `jpeg` / `webp` | 输出编码格式 |
| 审核 | `--moderation` | `auto` | `auto` / `low` | 内容审核严格度 |
| 配置 | `--config` | skill 内 profiles.yaml | 路径 | 多供应商配置文件 |
| 供应商 | `--profile` | 配置里的 `active` | profile 名 | 选哪一套 base_url/密钥 |
| 超时 | `--timeout` | profile 内（常 600） | 秒 | HTTP 读超时 |
| 输出文件 | `--out` | 见说明 | 路径 | 单图输出路径 |
| 输出目录 | `--out-dir` | — | 路径 | 多图时写到该目录 |
| 覆盖 | `--force` | 关 | 开关 | 允许覆盖已存在文件 |
| 抠图 | `--transparent` | 不传（关） | 颜色名 / `#RRGGBB` / `R,G,B` | 可选本地抠图；调用方明确指定背景色 |

### 仅 edit

| 参数 | CLI | 默认 | 可选值 | 含义 |
|------|-----|------|--------|------|
| 参考图 | `--image` | （必填，可多次） | 文件路径 | 输入/参考图，可重复传多张 |
| 遮罩 | `--mask` | 无 | PNG 路径 | 透明区域表示可重绘 |
| 输入保真 | `--input-fidelity` | 不传 | `low` / `high` | 更贴近原图细节（视模型） |

### 异步相关

| 命令 | 说明 |
|------|------|
| `submit generate/edit` | 后台跑，stdout 默认一行 `job_id`；`--json` 输出详情 |
| `status <job_id>` | 查状态；`--json` 给 Agent 解析 |
| `wait <job_id>` | 阻塞等到完成（人用） |
| `list` | 列出本机任务 |

未指定 `--out`/`--out-dir` 时，异步默认写到 `jobs/<job_id>/output.<ext>`。

## size 常用值

| 值 | 说明 |
|----|------|
| `auto` | **默认**。服务端按模型/quality 选择 |
| `1024x1024` | 正方形 1K |
| `1536x1024` | 横向约 3:2，1.5K |
| `1024x1536` | 竖向约 2:3，1.5K |
| `2048x2048` | 正方形 2K（部分通道支持） |
| `2048x1152` | 横向 2K（部分通道支持） |
| `3840x2160` | 4K 横（部分通道支持） |
| `2160x3840` | 4K 竖（部分通道支持） |

本仓库 profile 备注：

- `free_1k` / `paid_1k`：建议只用 1K 档  
- `paid_hq`：可用 2K/4K  

**2K / 4K：** 耗时长、常按张计费。Agent 与自动化请用 `submit` + `status`，不要用同步 `generate`/`edit`，避免工具超时后任务结果丢失却已扣费。

## quality

| 值 | 含义 |
|----|------|
| `auto` | **默认**。服务端自选 |
| `low` | 更快、更省，细节少，适合草稿 |
| `medium` | 均衡 |
| `high` | 细节最多，通常更慢更贵 |

## moderation

| 值 | 含义 |
|----|------|
| `auto` | **默认**。常规审核策略 |
| `low` | 更宽松（是否生效看模型/中转） |

## output_format

| 值 | 含义 |
|----|------|
| `png` | **默认**。无损，适合精细/后续处理 |
| `jpeg` | 体积小，适合照片感图 |
| `webp` | 体积与质量折中 |

## response_format（profile 配置，不是 CLI 参数）

| 配置项 | 默认 | 含义 |
|--------|------|------|
| `response_format_b64_json` | **`false`** | 为 `true` 时请求里附带 `response_format=b64_json` |

与 gpt_image_playground 一致：默认不强制 b64。  
客户端会解析响应里的 `data[].b64_json` 或 `data[].url`（url 会再下载）。

需要强制 b64 时，在对应 profile 里设：

```yaml
response_format_b64_json: true
```

## 超时与异步

图片生成常要 **1～数分钟**。profile `timeout` 默认 **600** 秒。  
Agent 建议用 `submit` + `status`，避免单次 bash 挂太久。

## 扩展其它 type

当前实现：`openai_compatible`。  
新增供应商：实现 `ImageProvider`，在 `registry.py` 注册，配置里写对应 `type`。
