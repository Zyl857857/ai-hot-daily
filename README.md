# AI 热点日报

每天自动抓取中英文 AI 热点 → LLM 精选 Top 10 + 生成中文摘要和一篇可直接复制的日报文案 → 发布到 GitHub Pages。

## 使用

- 线上网址：`https://<你的用户名>.github.io/ai-hot-daily/`（配置完成后）
- 打开页面 → 复制顶部"今日日报文案"发到微博/小红书/朋友圈，或复制单条新闻的文案
- 每天北京时间 **07:30** 由 GitHub Actions 自动更新（也可能有几分钟延迟）

## 结构

```
scripts/fetch.py        多源抓取 + 去重（RSS / Hacker News / Reddit）
scripts/summarize.py    LLM 精选 + 摘要 + 日报文案（失败自动降级为纯聚合）
scripts/build.py        生成归档清单 data/manifest.json
data/YYYY-MM-DD.json    每日数据
index.html              单页前端（无构建）
```

## 本地运行

```bash
pip install -r requirements.txt
python scripts/fetch.py && python scripts/summarize.py && python scripts/build.py
python -m http.server 8137    # 打开 http://localhost:8137
```

或直接双击 `启动本地预览.bat`（会用最新已生成的数据起本地服务）。

## LLM 通道

优先智谱 GLM，失败自动切 DeepSeek。key 查找顺序：

1. 环境变量 `GLM_API_KEY` / `ZAI_API_KEY` / `DEEPSEEK_API_KEY`（GitHub Actions 上即 secrets）
2. 本机 `~/.dsh/.credentials.yaml`（仅本地运行时）

GitHub 仓库需配置 secrets：`GLM_API_KEY`、`DEEPSEEK_API_KEY`（见仓库 Settings → Secrets and variables → Actions）。

## 自定义信息源

编辑 `config.yaml` 的 `sources` 段：`type: rss` 填任意 RSS 地址；`type: hn` 为 Hacker News（可改关键词和最低分数）。改完推送到 GitHub 即生效。

## 常见问题

- **Actions 没有定时跑**：GitHub Actions 的 cron 可能有几分钟级延迟；到仓库 Actions 页可手动 `workflow_dispatch` 触发。
- **某天某来源缺失**：抓取失败会自动跳过该源，不影响其他源。
- **摘要没生成**：LLM 全部通道失败时会降级为纯聚合（只有标题+链接），检查 secrets 里的 key 是否有效。
