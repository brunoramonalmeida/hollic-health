import { useNavigate } from 'react-router-dom'
import { Button } from '../components/Button'
import { useAuth } from '../context/AuthContext'
import { APP_VERSION, WHATSAPP } from '../data/mock'

const items = [
  {
    label: 'My details',
    icon: '/assets/icons/user_profile_icon.svg',
    to: '/app/profile/details',
  },
  {
    label: 'Address',
    icon: '/assets/icons/address_profile_icon.svg',
    to: '/app/profile/address',
  },
  {
    label: 'Contact support',
    icon: '/assets/icons/whatsapp_profile_icon.svg',
    action: 'whatsapp' as const,
  },
  {
    label: 'Change password',
    icon: '/assets/icons/password_profile_icon.svg',
    to: '/app/profile/password',
  },
]

export function ProfilePage() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="page profile">
      <h1 className="profile__name">{user?.name}</h1>
      <div className="divider" />
      <div className="profile__menu">
        {items.map((item) => (
          <button
            key={item.label}
            type="button"
            className="profile__item"
            onClick={() => {
              if ('action' in item && item.action === 'whatsapp') {
                window.open(`https://wa.me/${WHATSAPP}`, '_blank')
                return
              }
              if (item.to) navigate(item.to)
            }}
          >
            <img src={item.icon} alt="" />
            {item.label}
          </button>
        ))}
      </div>
      <div style={{ padding: '16px 10px' }}>
        <Button onClick={() => void handleLogout()}>Sign out</Button>
      </div>
      <p className="profile__version">Version {APP_VERSION}</p>
    </div>
  )
}
