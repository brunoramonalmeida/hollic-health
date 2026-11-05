import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export function AppBar() {
  const { unreadCount } = useAuth()

  return (
    <header className="app-bar">
      <Link to="/app/notificacoes" className="app-bar__notify">
        <img src="/assets/icons/icon_notification.svg" alt="Notificações" />
        {unreadCount > 0 && <span className="app-bar__badge">{unreadCount}</span>}
      </Link>
      <img className="app-bar__logo" src="/assets/images/logo.svg" alt="Santé Saúde" />
      <div className="app-bar__spacer" />
    </header>
  )
}
