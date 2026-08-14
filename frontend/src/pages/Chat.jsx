import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'

function MessageBody({ text, pending }) {
  return (
    <div className={`text md${pending ? ' pending' : ''}`}>
      <ReactMarkdown>{text || (pending ? '…' : '')}</ReactMarkdown>
    </div>
  )
}

export default function Chat() {
  const [draft, setDraft] = useState('')
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const bottomRef = useRef(null)
  const textareaRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [history, loading])

  function patchMessage(id, patch) {
    setHistory((prev) =>
      prev.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    )
  }

  async function submit() {
    const question = draft.trim()
    if (!question || loading) return

    const assistantId = crypto.randomUUID()
    setDraft('')
    setError('')
    setLoading(true)
    setHistory((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: 'user', text: question },
      {
        id: assistantId,
        role: 'assistant',
        text: '',
        pending: true,
      },
    ])

    try {
      const res = await fetch('/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({ question, stream: true, show_sources: false }),
      })

      if (!res.ok) {
        let message = `请求失败 (${res.status})`
        try {
          const data = await res.json()
          if (data.error) message = data.error
        } catch {
          /* ignore */
        }
        throw new Error(message)
      }

      const contentType = res.headers.get('content-type') || ''
      if (!contentType.includes('text/event-stream') || !res.body) {
        const data = await res.json()
        if (data.error) throw new Error(data.error)
        patchMessage(assistantId, {
          text: data.answer || '（空回答）',
          pending: false,
        })
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let full = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const chunks = buffer.split('\n\n')
        buffer = chunks.pop() || ''

        for (const chunk of chunks) {
          const line = chunk
            .split('\n')
            .map((part) => part.trim())
            .find((part) => part.startsWith('data:'))
          if (!line) continue
          const raw = line.slice(5).trim()
          if (!raw) continue

          let event
          try {
            event = JSON.parse(raw)
          } catch {
            continue
          }

          if (event.type === 'delta' && event.text) {
            full += event.text
            patchMessage(assistantId, { text: full, pending: true })
          } else if (event.type === 'done') {
            full = event.answer || full
            patchMessage(assistantId, {
              text: full || '（空回答）',
              pending: false,
            })
          } else if (event.type === 'error') {
            throw new Error(event.error || '流式回答失败')
          }
        }
      }

      if (full) {
        patchMessage(assistantId, { text: full, pending: false })
      } else {
        throw new Error('未收到回答')
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setError(message)
      patchMessage(assistantId, {
        text: `出错了：${message}`,
        pending: false,
        isError: true,
      })
    } finally {
      setLoading(false)
      textareaRef.current?.focus()
    }
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  const empty = history.length === 0 && !loading

  return (
    <div className={`app ${empty ? 'is-empty' : ''}`}>
      <header className="topbar">
        <div className="brand">问点提瓦特的事</div>
        <div className="topbar-actions">
          <Link className="nav-link" to="/admin/">
            管理
          </Link>
          {history.length > 0 && (
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setHistory([])
                setError('')
              }}
            >
              新对话
            </button>
          )}
        </div>
      </header>

      <main className="main">
        {empty ? (
          <div className="hero">
            <h1>随便问问吧！</h1>
            <p>依据已入库的世界观资料作答，不会编造设定。</p>
          </div>
        ) : (
          <div className="thread" aria-live="polite">
            {history.map((item) => (
              <article
                key={item.id}
                className={`bubble ${item.role}${item.isError ? ' error' : ''}${
                  item.pending ? ' pending' : ''
                }`}
              >
                <div className="role">
                  {item.role === 'user' ? '你' : '助手'}
                </div>
                {item.role === 'assistant' ? (
                  <MessageBody text={item.text} pending={item.pending} />
                ) : (
                  <div className="text">{item.text}</div>
                )}
              </article>
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </main>

      <footer className="composer-wrap">
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            rows={3}
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            disabled={loading}
          />
          <div className="composer-bar">
            {error && !loading ? (
              <span className="hint error-hint">{error}</span>
            ) : (
              <span className="hint">流式回答 · Markdown 展示</span>
            )}
            <button type="submit" disabled={loading || !draft.trim()}>
              {loading ? '生成中' : '发送'}
            </button>
          </div>
        </form>
      </footer>
    </div>
  )
}
