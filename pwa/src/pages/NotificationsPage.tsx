import { useAuth } from '../context/AuthContext'
import { BackHeader } from '../components/BackHeader'

export function NotificationsPage() {
  const { notifications, markNotificationRead } = useAuth()

  return (
    <div className="page">
      <BackHeader title="Notifications" />
      <div className="list-page">
        {notifications.length === 0 ? (
          <div className="empty-state">No notifications</div>
        ) : (
          notifications.map((n) => (
            <button
              key={n.id}
              type="button"
              className={`notification-item ${n.read ? '' : 'is-unread'}`}
              style={{ width: '100%', textAlign: 'left', border: 'none', cursor: 'pointer' }}
              onClick={() => markNotificationRead(n.id)}
            >
              <h3>{n.title}</h3>
              <p>{n.body}</p>
              <time>
                {new Date(n.date).toLocaleString('en-US', {
                  day: '2-digit',
                  month: '2-digit',
                  year: 'numeric',
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </time>
            </button>
          ))
        )}
      </div>
    </div>
  )
}
