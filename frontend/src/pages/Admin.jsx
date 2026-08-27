import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

const STATUS_LABEL = {
  pending: '排队中',
  fetching: '下载中',
  summarizing: '总结中',
  done: '完成',
  partial: '部分完成',
  failed: '失败',
}

const SOURCE_LABEL = {
  [-1]: '非专名',
  1: '官中',
  2: 'Wiki',
  3: '手动',
}

const PAGE_SIZE = 50

/** 按行解析网址；空行忽略。也兼容「空行分段」。 */
function parseUrlBlocks(text) {
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

function formatTime(stamp) {
  if (!stamp) return '—'
  const d = new Date(stamp)
  if (Number.isNaN(d.getTime())) return stamp
  return d.toLocaleString()
}

function DictionaryPanel() {
  const [q, setQ] = useState('')
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [items, setItems] = useState([])
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(null)
  const [saving, setSaving] = useState(false)

  const query = q.trim()

  useEffect(() => {
    if (query.length < 2) {
      setItems([])
      setTotal(0)
      setError('')
      return undefined
    }
    let cancelled = false
    const params = new URLSearchParams({
      q: query,
      offset: String(offset),
      limit: String(PAGE_SIZE),
    })
    fetch(`/dictionary?${params}`)
      .then(async (res) => {
        const data = await res.json()
        if (!res.ok) {
          throw new Error(data.error || `搜索失败 (${res.status})`)
        }
        if (!cancelled) {
          setItems(data.items || [])
          setTotal(Number(data.total) || 0)
          setError('')
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err))
        }
      })
    return () => {
      cancelled = true
    }
  }, [query, offset])

  async function saveEdit(e) {
    e.preventDefault()
    if (!editing || saving) return
    setSaving(true)
    try {
      const res = await fetch('/dictionary', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({
          en: editing.en,
          zh: editing.zh,
          source: Number(editing.source),
        }),
      })
      const data = await res.json()
      if (!res.ok) {
        throw new Error(data.error || `保存失败 (${res.status})`)
      }
      setItems((prev) =>
        prev.map((row) =>
          row.en === editing.en
            ? { ...row, zh: editing.zh, source: Number(editing.source) }
            : row,
        ),
      )
      setEditing(null)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  const page = Math.floor(offset / PAGE_SIZE) + 1
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <>
      <h1>词典</h1>
      <p className="admin-lead">
        输入至少两个字符后搜索英文或中文。点一行可改中文和来源。
      </p>
      <input
        className="admin-search"
        value={q}
        onChange={(e) => {
          setQ(e.target.value)
          setOffset(0)
        }}
        placeholder="搜索（至少 2 个字符）"
      />
      {error && <p className="admin-error">{error}</p>}
      {query.length < 2 ? (
        <p className="hint">再输入一些字才会搜索。</p>
      ) : items.length === 0 ? (
        <p className="hint">没有匹配的词。</p>
      ) : (
        <>
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>英文</th>
                  <th>中文</th>
                  <th>来源</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <tr
                    key={`${row.en}:${row.source}`}
                    className={
                      editing?.en === row.en ? 'is-editing' : undefined
                    }
                    onClick={() =>
                      setEditing({
                        en: row.en,
                        zh: row.zh || '',
                        source: String(row.source),
                      })
                    }
                  >
                    <td>{row.en}</td>
                    <td>{row.zh || '—'}</td>
                    <td>{SOURCE_LABEL[row.source] || row.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="admin-pager">
            <button
              type="button"
              disabled={offset <= 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              上一页
            </button>
            <span className="hint">
              {page} / {pages} · 共 {total}
            </span>
            <button
              type="button"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              下一页
            </button>
          </div>
        </>
      )}
      {editing && (
        <form className="admin-edit" onSubmit={saveEdit}>
          <div className="admin-page-title">{editing.en}</div>
          <label>
            中文
            <input
              value={editing.zh}
              onChange={(e) =>
                setEditing((cur) => ({ ...cur, zh: e.target.value }))
              }
            />
          </label>
          <label>
            来源
            <select
              value={editing.source}
              onChange={(e) =>
                setEditing((cur) => ({ ...cur, source: e.target.value }))
              }
            >
              <option value="1">官中</option>
              <option value="2">Wiki</option>
              <option value="3">手动</option>
              <option value="-1">非专名</option>
            </select>
          </label>
          <div className="composer-bar">
            <button type="button" onClick={() => setEditing(null)}>
              取消
            </button>
            <button type="submit" disabled={saving}>
              {saving ? '保存中…' : '保存'}
            </button>
          </div>
        </form>
      )}
    </>
  )
}

export default function Admin() {
  const [tab, setTab] = useState('grab')
  const [raw, setRaw] = useState('')
  const [summarizing, setSummarizing] = useState(false)
  const [ingesting, setIngesting] = useState(false)
  const [error, setError] = useState('')
  const [summaryReport, setSummaryReport] = useState(null)
  const [ingestReport, setIngestReport] = useState(null)
  const [pages, setPages] = useState([])
  const [pagesError, setPagesError] = useState('')

  const previewUrls = useMemo(() => parseUrlBlocks(raw), [raw])

  async function loadPages() {
    const res = await fetch('/pages')
    const data = await res.json()
    if (!res.ok) {
      throw new Error(data.error || `读取页面库失败 (${res.status})`)
    }
    setPages(data.pages || [])
    setPagesError('')
  }

  useEffect(() => {
    if (tab !== 'catalog') return undefined
    let cancelled = false
    loadPages().catch((err) => {
      if (!cancelled) {
        setPagesError(err instanceof Error ? err.message : String(err))
      }
    })
    return () => {
      cancelled = true
    }
  }, [tab])

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
          <div className="admin-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'grab'}
              className={`admin-tab${tab === 'grab' ? ' is-active' : ''}`}
              onClick={() => setTab('grab')}
            >
              抓取
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'catalog'}
              className={`admin-tab${tab === 'catalog' ? ' is-active' : ''}`}
              onClick={() => setTab('catalog')}
            >
              页面库
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'dict'}
              className={`admin-tab${tab === 'dict' ? ' is-active' : ''}`}
              onClick={() => setTab('dict')}
            >
              词典
            </button>
          </div>

          {tab === 'grab' ? (
            <>
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
                  placeholder={'输入网址......'}
                  disabled={summarizing || ingesting}
                />
                <div className="composer-bar">
                  <span className="hint">
                    已识别 {previewUrls.length} 个网址
                    {summarizing ? ' · 抓取可能较久，请稍候' : ''}
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
            </>
          ) : tab === 'catalog' ? (
            <>
              <h1>页面库</h1>
              <p className="admin-lead">
                已提交抓取的网址及其总结状态。要看最新进度请刷新页面。
              </p>
              {pagesError && <p className="admin-error">{pagesError}</p>}
              {pages.length === 0 ? (
                <p className="hint">还没有记录。到「抓取」里提交网址。</p>
              ) : (
                <div className="admin-table-wrap">
                  <table className="admin-table">
                    <thead>
                      <tr>
                        <th>页面</th>
                        <th>状态</th>
                        <th>章节</th>
                        <th>更新</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pages.map((page) => (
                        <tr key={page.url}>
                          <td>
                            <div className="admin-page-title">
                              {page.title || '（无标题）'}
                            </div>
                            {/^https?:\/\//i.test(page.url) ? (
                              <a
                                href={page.url}
                                target="_blank"
                                rel="noreferrer"
                              >
                                {page.url}
                              </a>
                            ) : (
                              <span className="admin-page-url">{page.url}</span>
                            )}
                            {page.error ? (
                              <div className="admin-page-error">{page.error}</div>
                            ) : null}
                          </td>
                          <td>
                            <span
                              className={`admin-status admin-status-${page.status}`}
                            >
                              {STATUS_LABEL[page.status] || page.status}
                            </span>
                          </td>
                          <td>
                            {page.chapter_ok}/{page.chapter_total}
                          </td>
                          <td>{formatTime(page.updated_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          ) : (
            <DictionaryPanel />
          )}
        </section>
      </main>
    </div>
  )
}
