import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

/** 按行解析网址；空行忽略。也兼容「空行分段」。 */
export function parseUrlBlocks(text) {
  const urls = []
  const seen = new Set()
  for (const line of text.split(/\r?\n/)) {
    const url = line.trim()
    if (!url) continue
    if (!/^https?:\/\//i.test(url)) continue
    if (seen.has(url)) continue
    seen.add(url)
    urls.push(url)
  }
  return urls
}

export default function Admin() {
  const [raw, setRaw] = useState('')
  const [summarizing, setSummarizing] = useState(false)
  const [ingesting, setIngesting] = useState(false)
  const [error, setError] = useState('')
  const [summaryReport, setSummaryReport] = useState(null)
  const [ingestReport, setIngestReport] = useState(null)

  const previewUrls = useMemo(() => parseUrlBlocks(raw), [raw])

  async function runSummarize(e) {
    e.preventDefault()
    const urls = parseUrlBlocks(raw)
    if (!urls.length || summarizing || ingesting) return

    setError('')
    setIngestReport(null)
    setSummaryReport(null)
    setSummarizing(true)

    try {
      const res = await fetch('/summarize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({ urls }),
      })
      const data = await res.json()
      if (!res.ok) {
        throw new Error(data.error || `抓取失败 (${res.status})`)
      }
      setSummaryReport({
        pageCount: Number(data.page_count) || 0,
        documentCount: Number(data.document_count) || 0,
        pages: data.pages || [],
        results: data.results || [],
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSummarizing(false)
    }
  }

  async function runIngest() {
    if (!summaryReport || ingesting || summarizing) return

    setError('')
    setIngestReport(null)
    setIngesting(true)

    try {
      const res = await fetch('/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({}),
      })
      const data = await res.json()
      if (!res.ok) {
        throw new Error(data.error || `入库失败 (${res.status})`)
      }
      setIngestReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setIngesting(false)
    }
  }

  return (
    <div className="app admin-app">
      <header className="topbar">
        <div className="brand">资料管理</div>
        <div className="topbar-actions">
          <Link className="nav-link" to="/">
            问答
          </Link>
        </div>
      </header>

      <main className="main admin-main">
        <section className="admin-panel">
          <h1>批量抓取 Wiki</h1>
          <p className="admin-lead">
            每段一个网址，提交后会爬取页面、拆章并生成 Markdown。
          </p>

          <form className="admin-form" onSubmit={runSummarize}>
            <textarea
              className="admin-urls"
              rows={12}
              value={raw}
              onChange={(e) => setRaw(e.target.value)}
              placeholder={
                '输入网址......'
              }
              disabled={summarizing || ingesting}
            />
            <div className="composer-bar">
              <span className="hint">
                已识别 {previewUrls.length} 个网址
                {summarizing ? ' · 抓取与总结可能较久，请稍候' : ''}
              </span>
              <button
                type="submit"
                disabled={summarizing || ingesting || previewUrls.length === 0}
              >
                {summarizing ? '处理中…' : '开始抓取'}
              </button>
            </div>
          </form>

          {error && <p className="admin-error">{error}</p>}

          {summaryReport && (
            <section className="admin-result">
              <h2>抓取完成</h2>
              <div className="admin-metrics">
                <div className="admin-metric">
                  <div className="admin-metric-label">网页数量</div>
                  <div className="admin-metric-value">
                    {summaryReport.pageCount}
                  </div>
                  <div className="admin-metric-note">成功抓取并拆章的页面</div>
                </div>
                <div className="admin-metric">
                  <div className="admin-metric-label">文档数量</div>
                  <div className="admin-metric-value">
                    {summaryReport.documentCount}
                  </div>
                  <div className="admin-metric-note">生成的 Markdown 章节数</div>
                </div>
              </div>
              {summaryReport.pages.length > 0 && (
                <details className="admin-details">
                  <summary>查看页面列表（{summaryReport.pageCount}）</summary>
                  <ul>
                    {summaryReport.pages.map((url) => (
                      <li key={url}>
                        <a href={url} target="_blank" rel="noreferrer">
                          {url}
                        </a>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <button
                type="button"
                className="admin-ingest"
                onClick={runIngest}
                disabled={ingesting || summarizing}
              >
                {ingesting ? '入库中…' : '将这些文档入库'}
              </button>
            </section>
          )}

          {ingestReport && (
            <section className="admin-result">
              <h2>入库完成</h2>
              <ul className="admin-stats">
                <li>
                  写入：<strong>{ingestReport.upserted}</strong>
                </li>
                <li>
                  跳过：<strong>{ingestReport.skipped}</strong>
                </li>
                <li>
                  扫描 md：<strong>{ingestReport.total_files}</strong>
                </li>
                <li>
                  库内共：<strong>{ingestReport.store_count}</strong>
                </li>
              </ul>
            </section>
          )}
        </section>
      </main>
    </div>
  )
}
