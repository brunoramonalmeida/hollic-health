import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { eventIcon } from '../components/NotificationBell'
import PageHeader from '../components/PageHeader'
import { formatDateTime } from '../dates'

export default function Notifications() {
  const [rows, setRows] = useState([])
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [error, setError] = useState('')

  async function load() {
    setRows(await api(`/notifications?limit=200${unreadOnly ? '&unread_only=true' : ''}`))
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [unreadOnly])

  async function toggle(item) {
    await api(`/notifications/${item.id}/${item.read_at ? 'unread' : 'read'}`, { method: 'POST', message: false })
    await load()
  }

  async function readAll() {
    await api('/notifications/read-all', { method: 'POST', message: 'All notifications marked as read' })
    await load()
  }

  return (
    <section>
      <PageHeader crumb="Notifications" title="Notifications" hint="Everything the system told you, newest first. Emails are sent for the same events.">
        <label className="check">
          <input type="checkbox" checked={unreadOnly} onChange={(event) => setUnreadOnly(event.target.checked)} />
          Unread only
        </label>
        <button type="button" className="ghost" onClick={readAll}>Mark all as read</button>
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card notification-list">
        {rows.map((item) => (
          <article key={item.id} className={item.read_at ? 'read' : 'unread'}>
            <span className="icon">{eventIcon(item.event)}</span>
            <div className="text">
              <strong>{item.title}</strong>
              <pre>{item.body}</pre>
              <small>{formatDateTime(item.created_at)}{item.read_at ? ' · read' : ''}</small>
            </div>
            <div className="actions">
              {item.link && <Link className="button ghost" to={item.link}>Open</Link>}
              <button type="button" className="link" onClick={() => toggle(item)}>
                {item.read_at ? 'Mark unread' : 'Mark read'}
              </button>
            </div>
          </article>
        ))}
        {rows.length === 0 && <p className="muted" style={{ padding: 16 }}>Nothing here.</p>}
      </div>
    </section>
  )
}
