import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export function AppBar() {
  const { unreadCount } = useAuth()

  return (
    <header className="app-bar">
      <Link to="/app/notifications" className="app-bar__notify">
        <img src="/assets/icons/icon_notification.svg" alt="Notifications" />
        {unreadCount > 0 && <span className="app-bar__badge">{unreadCount}</span>}
      </Link>
      <p className="app-bar__logo">Hollic Health</p>
      <div className="app-bar__spacer" />
    </header>
  )
}
