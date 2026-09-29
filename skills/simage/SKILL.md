---
name: simage
description: 使用 simage 路由进行 GPT Image 2.5 / gpt-image-2.5-flare-vip 文生图、参考图生成和图片数量统计。
---

# simage

按用户要求生成；数量未指定时默认 1 张，不必询问。明确数量由 agent 转换为 `--count N`，
不要把画面中的物体数量当作输出图片数量。需求冲突时使用客户端 ask question 工具，所有问题及选项用中文。
尺寸未指定时用中文询问，选项显示 `1K`、`2K`、`4K`，也接受具体尺寸 `2048×2048`；
比例显示 `1:1` 或 `16:9`。不要把宽高比称为像素尺寸。已明确的参数不重复询问。

先按仓库 README 初始化环境和本机配置。GPT Image 2.5 / gpt-image-2.5-flare-vip 只走 APIyi 聚合地址；运行本技能目录下的 `scripts/simage-run.py`：

```bash
python3 scripts/simage-run.py --prompt "产品摄影" --count 1 --size 2K --output-root /absolute/output --dry-run
```

`--dry-run` 不调用 API，移除后才实际生成。无参考图为文生图；重复 `--reference` 提供参考图。
`--aspect-ratio 16:9` 指定比例；具体尺寸是否可用取决于供应商，不能仅凭 dry-run 保证。
数量有歧义时由 agent 询问并显式传 `--count`，不要依赖正则猜测复杂提示词。
复制安装时设置 `SNANO_PROJECT_ROOT` 为路由根目录。只查看统计时使用 `view` 子命令。
不得展示或提交 API key、本机 .env、图片、ledger 和原始响应。

## Simage 模型判断规则

Simage 的模型选择由 Agent 先把用户意图转换为结构化参数，再由路由执行最终选择。不要根据提示词里出现的模型名称、画面物体数量或自然语言关键词自动猜测模型。

优先级如下：

1. 用户明确指定模型时，传 `--model flare` 或 `--model sunburst`；明确指定优先于其他规则。
2. 没有明确模型，但有参考图且用户要求精细修改、保持主体或局部编辑时，传 `--intent precision`，路由选择 `gpt-image-2.5-sunburst-vip`。
3. 用户强调速度，传 `--intent speed`，路由选择 `gpt-image-2.5-flare-vip`。
4. 其他普通文生图、普通改图或意图不明确时，路由默认选择 `gpt-image-2.5-flare-vip`。

`--model auto` 和 `--intent auto` 是默认值。模型失败时不自动切换到另一型号；`vip`、`all` 等未在本规则中确认的别名不会自动参与选择。若用户要求的模型与显式 provider 限制冲突，路由会报错而不是静默改用别的模型。

示例：

```bash
# 普通生成：Flare
python3 scripts/simage-run.py --prompt "产品摄影" --model auto --intent auto --dry-run --output-root /tmp/simage-run

# 有参考图且精细修改：Sunburst
python3 scripts/simage-run.py --reference /absolute/reference.png --prompt "只调整背景，保持主体细节" --intent precision --dry-run --output-root /tmp/simage-run

# 用户明确指定型号：以明确指定为准
python3 scripts/simage-run.py --prompt "产品摄影" --model sunburst --dry-run --output-root /tmp/simage-run
```

dry-run 输出会包含最终模型、provider 和选择原因；实际生成沿用同一选择结果。文生图使用 `/images/generations` JSON，请求参考图时使用 `/images/edits` multipart。模型后缀是路由配置的一部分，安装 skill 的 Agent 通过本文件和 CLI 参数知道何时选择，不需要自行发现或拼接别名。
