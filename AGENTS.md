# Agent 说明

Cursor 会读取本仓库的 **`.cursor/rules/*.mdc`**（不是 `.ai/`）。改架构或流水线时请同步那些规则。

- 始终生效：`.cursor/rules/project.mdc`
- 打开对应文件时生效：`summarize.mdc` / `rag.mdc` / `frontend-api.mdc`

密钥在 `.env.local`。运行：`python scripts/main.py --summarize|--ingest|--ask`，或 `python api/app.py`（前端生产先 `npm run build`）。
