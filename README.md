# Snano

Snano 生图平台，包含两个 AI skill 和本地图片数量统计面板。

- 安装后只提供两个 skill：`snano` 和 `simage`。
- `snano`：只走 APIyi 聚合地址的 `nano-banana-pro`，支持文生图和参考图生成。
- `simage`：只走 APIyi 聚合地址的 GPT Image 2.5，默认 `gpt-image-2.5-flare-vip`。
- 用户明确指定数量时按要求执行，否则默认 1 张。只有数量矛盾或含义不清时才用中文询问。
- 尺寸显示为 `1K`、`2K`、`4K` 或 `2048×2048`；比例显示为 `1:1`、`16:9` 等。

## 安装

把下面这句话粘贴给任意具备终端与文件操作能力的 Agent，即可请它完成安装：

> 请从 https://github.com/yunzaibo/snano 克隆并安装 Snano，按 README 创建 Python 环境和本机配置模板，仅安装名为 snano 和 simage 的两个 skill 到当前客户端的技能目录，保留已有配置，不索取或展示 API key，完成无付费 API 调用的 dry-run 验证，并告诉我生图后如何启动图片数量统计页面。

需要 Python 3.10+；统计面板构建需要 Node.js 和 npm。

```bash
git clone https://github.com/yunzaibo/snano.git
cd snano
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/setup_local.py
```

初始化脚本只在文件缺失时从模板创建 `.env.internal` 和
`configs/sources.internal.yaml`。模板已经填好 APIyi 聚合地址、两个模型和请求路径，
本机只需要在 `.env.internal` 填入 `MIR_APIYI_API_KEY`；不要把真实 key 写回模板。
提供模板不代表附赠 API 额度；真实生图使用你自己的账户。

## 模型和请求方式

公开模板只启用 APIyi 一个上游：

- `snano` 使用 `nano-banana-pro`，文生图走 `POST /v1/images/generations`，参考图走
  `POST /v1/images/edits` 的 multipart 请求。
- `simage` 默认使用 `gpt-image-2.5-flare-vip`；同一个 key 还枚举到
  `gpt-image-2.5-sunburst-vip`，它适合精细编辑。两者同样使用 OpenAI Images 兼容请求，
  由路由统一归一化为图片产物。
- 模型和后缀以 `configs/sources.apiyi.yaml` 中的明确 lane 为准；模板不会自动把 key
  写入日志、仓库或统计数据，也不会根据未知别名猜测模型。

## 使用

```bash
.venv/bin/python skills/snano/scripts/snano-run.py --prompt "产品摄影" --count 1 --size 2K --output-root ./data/output --dry-run
.venv/bin/python skills/simage/scripts/simage-run.py --prompt "产品摄影" --count 1 --size 2K --output-root ./data/output --dry-run
```

`--dry-run` 只验证计划，不调用生图 API；确认配置后移除此参数才实际生成。
通过重复 `--reference /absolute/path/image.png` 提供参考图。
尺寸是否可用取决于供应商契约，dry-run 不保证上游支持所有像素尺寸。

仓库内部的 router 不是 skill，不会安装成第三个名称。使用安装脚本只注册
`snano` 和 `simage` 两个目录。默认安装到 Codex skill 目录：

```bash
bash scripts/install_skills.sh
```

也可以指定其他兼容客户端的 skill 目录：

```bash
SNANO_SKILLS_HOME="$HOME/.claude/skills" bash scripts/install_skills.sh
```

脚本只处理 `snano` 和 `simage`，不会创建或复制 `multi-image-router` skill。
安装使用符号链接，请保留克隆的仓库目录；已有技能备份到技能目录以外，不会多注册备份技能。

## 统计面板

生图之后可以运行统计页面，查看 `snano` / `simage` 的图片数量、请求方式和 provider lane。
包内 `stats/app/public/stats-data.json` 是空数据，不含任何历史记录。

```bash
cd stats/app
npm ci
npm run build

# 使用演示数据查看页面
cd ../..
bash stats/panel.sh mock

# 使用本机 ledger 查看真实统计
bash stats/panel.sh real
```

面板默认只监听本机地址。面板脚本在没有本机记录时可能生成演示数据；
演示数量不代表真实使用。`stats/aggregate.py`、`stats/sync_run.py` 用于本机记录聚合。

## 发布内容

包含源码、测试、配置模板、空统计数据及 skill。
排除真实 `.env`、本地 API 配置、图片、ledger、日志、工作流记录、旧 Git 历史和依赖目录。
发布审计结果见 `RELEASE_AUDIT.json`；它只记录路径、文件哈希和扫描结果，不记录密钥值。
Simage 模型规则已写入 `skills/simage/SKILL.md` 和路由：普通生成默认使用 Flare；有参考图且要求精细修改时使用 Sunburst；用户明确指定模型时优先。Agent 通过 `--model auto|flare|sunburst` 与 `--intent auto|speed|precision` 传递判断，路由会在 dry-run 中显示最终模型和原因，失败时不会静默换型号。
