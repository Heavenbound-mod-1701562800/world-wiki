# world-wiki

原神世界观解释器（后端先行）：抓取 Wiki → 章节精简 → RAG 问答。

## 目录结构

```text
libs/                 # 底层能力
  llm.py              # 火山方舟 Chat / Embedding
  crawler.py          # Crawler + FandomWikiCrawler
  store.py            # Chroma 本地向量库
models/               # 业务编排
  wiki.py             # 拆章 + LLM 总结 + 写 Markdown
  ingest.py           # md → 向量库
  qa.py               # RAG 问答
scripts/main.py       # 唯一 CLI 入口
config.py             # 环境配置
samples/              # 本地样例 HTML
```

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 在 .env 或 .env.local 填入 ARK_API_KEY / 模型 ID
```

唯一入口 `scripts/main.py`，用 flag 切换模式：

```bash
# 1) 总结页面 → data/summaries/*.md
python scripts/main.py --summarize samples/mondstadt_demo.html
python scripts/main.py --summarize --url "https://your-wiki-page-url"
python scripts/main.py --summarize --url "https://your-wiki-page-url" --save-html

# 2) 把 md 写入向量库（content_hash 未变则跳过，hash 存在 Chroma metadata）
python scripts/main.py --ingest
python scripts/main.py --ingest --reset   # 清空后全量重建

# 3) 提问（需已入库）
python scripts/main.py --ask "风神和蒙德的关系是什么？"
python scripts/main.py --ask "晨曦酒庄是什么？" --show-sources
```

## 代码用法

```python
from models import Wiki, Ingest, QA

Wiki().run("samples/mondstadt_demo.html")
print(Ingest().run())  # 按 hash 增量入库
print(QA().ask("风神和蒙德的关系是什么？").answer)
```

## 配置说明

| 变量 | 含义 |
|---|---|
| `ARK_API_KEY` | 火山方舟 API Key |
| `ARK_CHAT_MODEL` | 聊天模型 ID |
| `ARK_EMBEDDING_MODEL` | 向量模型 ID。`doubao-embedding-vision-*` 走 multimodal 接口 |
| `HTTPS_PROXY` / `HTTP_PROXY` | 访问 Fandom 等站点的代理，如 `http://127.0.0.1:7890` |

模型 ID 以方舟控制台开通的为准。
