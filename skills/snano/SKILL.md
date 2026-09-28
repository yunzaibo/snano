---
name: snano
description: 使用 snano 路由进行文生图、参考图生成和图片数量统计。
---

# snano

按用户要求生成；数量未指定时默认 1 张，不必询问。明确数量由 agent 转换为 `--count N`，
不要把画面中的物体数量当作输出图片数量。需求冲突时使用客户端 ask question 工具，所有问题及选项用中文。
尺寸未指定时用中文询问，选项显示 `1K`、`2K`、`4K`，也接受具体尺寸 `2048×2048`；
比例显示 `1:1` 或 `16:9`。不要把宽高比称为像素尺寸。已明确的参数不重复询问。

先按仓库 README 初始化环境和本机配置。运行本技能目录下的 `scripts/snano-run.py`：

```bash
python3 scripts/snano-run.py --prompt "产品摄影" --count 1 --size 2K --output-root /absolute/output --dry-run
```

`--dry-run` 不调用 API，移除后才实际生成。无参考图为文生图；重复 `--reference` 提供参考图。
`--aspect-ratio 16:9` 指定比例；具体尺寸是否可用取决于供应商，不能仅凭 dry-run 保证。
数量有歧义时由 agent 询问并显式传 `--count`，不要依赖正则猜测复杂提示词。
复制安装时设置 `SNANO_PROJECT_ROOT` 为路由根目录。只查看统计时使用 `view` 子命令。
不得展示或提交 API key、本机 .env、图片、ledger 和原始响应。
