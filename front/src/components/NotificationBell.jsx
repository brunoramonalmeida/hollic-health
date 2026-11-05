import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { MUTATION_EVENT, api } from '../api'
import { formatDateTime } from '../dates'
import { toast } from '../toast'

const POLL_MS = 30000

const ICONS = {
  'appointment.created': '📅',
  'appointment.rescheduled': '🔁',
  'appointment.cancelled': '✖',
  'appointment.completed': '✔',
  'appointment.checked_in': '🚪',
  'appointment.checked_out': '✍',
  'appointment.started': '🪪',
  'card.locked': '🔒',
  'card.reset': '🪪',
  'payout.advance': '💸',
  'payout.settlement': '🧾',
  'payout.paid': '💸',
  'payout.confirmed': '✍',
  'appointment.reminder_day': '⏰',
  'appointment.reminder_hour': '⏰',
  'subscription.created': '💳',
  'subscription.cancelled': '💳',
  'subscription.upgraded': '⬆',
  'installment.paid': '💰',
  'user.created': '👤',
  'sale.recorded': '🤝',
  'sale.cancelled': '✖',
  'commission.earned': '💰',
  'region.updated': '🗺',
  'seller.moved': '🗺',
  'seller.status': '👤',
  'sale.change_requested': '❓',
  'sale.change_confirmed': '✔',
  'sale.change_rejected': '✖',
  'sale.change_withdrawn': '↩',
  'team.sale_recorded': '🤝',
  'override.earned': '💰',
  'seller.leader_changed': '👥',
  'agreement.conditions': '🏢',
  'agreement.invoice_paid': '🧾',
  'agreement.enrollment': '🏢',
  'agreement.share_changed': '⚖',
  'installment.part_paid': '💰',
  'agreement.employee_updated': '✏',
}

export function eventIcon(event) {
  return ICONS[event] || '🔔'
}

/** Topbar bell: unread badge, latest messages, mark as read. Polls the API
 * and refreshes after every mutation; new arrivals also show a toast. */
export default function NotificationBell() {
  const navigate = useNavigate()
  const [summary, setSummary] = useState({ unread: 0, latest: [] })
  const [open, setOpen] = useState(false)
  const seen = useRef(null) // highest notification id already announced
  const box = useRef(null)

  async function refresh() {
    if (!localStorage.getItem('token')) return // signed out meanwhile: nothing to poll
    try {
      const data = await api('/notifications/summary', { silent: true })
      const newest = data.latest[0]?.id ?? 0
      if (seen.current !== null && newest > seen.current) {
        data.latest
          .filter((item) => item.id > seen.current && !item.read_at)
          .slice(0, 3)
          .forEach((item) => toast.info(item.title))
      }
      seen.current = Math.max(seen.current ?? 0, newest)
      setSummary(data)
    } catch {
      /* keep the last known state */
    }
  }

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, POLL_MS)
    // The delayed refresh after a mutation must not outlive the bell: a poll
    // fired after a sign-out would get a 401 and reload the login page.
    let pending = null
    const onMutation = () => {
      clearTimeout(pending)
      pending = setTimeout(refresh, 300)
    }
    window.addEventListener(MUTATION_EVENT, onMutation)
    return () => {
      clearInterval(timer)
      clearTimeout(pending)
      window.removeEventListener(MUTATION_EVENT, onMutation)
    }
  }, [])

  useEffect(() => {
    if (!open) return undefined
    const onClick = (event) => {
      if (box.current && !box.current.contains(event.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])

  async function openItem(item) {
    setOpen(false)
    if (!item.read_at) await api(`/notifications/${item.id}/read`, { method: 'POST', silent: true }).catch(() => {})
    refresh()
    if (item.link) navigate(item.link)
  }

  async function readAll() {
    await api('/notifications/read-all', { method: 'POST', message: 'All notifications marked as read' })
    refresh()
  }

  return (
    <div className="bell" ref={box}>
      <button
        type="button"
        className={`bell-button ${summary.unread ? 'has-unread' : ''}`}
        aria-label={`Notifications, ${summary.unread} unread`}
        onClick={() => setOpen((value) => !value)}
      >
        🔔
        {summary.unread > 0 && <span className="bell-badge">{summary.unread > 99 ? '99+' : summary.unread}</span>}
      </button>
      {open && (
        <div className="bell-panel">
          <header>
            <strong>Notifications</strong>
            {summary.unread > 0 && (
              <button type="button" className="link" onClick={readAll}>Mark all as read</button>
            )}
          </header>
          <ul>
            {summary.latest.map((item) => (
              <li key={item.id} className={item.read_at ? 'read' : 'unread'}>
                <button type="button" onClick={() => openItem(item)}>
                  <span className="icon">{eventIcon(item.event)}</span>
                  <span className="text">
                    <strong>{item.title}</strong>
                    <small>{formatDateTime(item.created_at)}</small>
                  </span>
                </button>
              </li>
            ))}
            {summary.latest.length === 0 && <li className="empty">No notifications yet.</li>}
          </ul>
          <footer>
            <Link to="/notifications" onClick={() => setOpen(false)}>See all</Link>
          </footer>
        </div>
      )}
    </div>
  )
}
