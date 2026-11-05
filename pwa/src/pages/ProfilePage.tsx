import { useNavigate } from 'react-router-dom'
import { Button } from '../components/Button'
import { useAuth } from '../context/AuthContext'
import { APP_VERSION, WHATSAPP } from '../data/mock'

const items = [
  {
    label: 'Meus dados',
    icon: '/assets/icons/user_profile_icon.svg',
    to: '/app/perfil/dados',
  },
  {
    label: 'Endereço',
    icon: '/assets/icons/address_profile_icon.svg',
    to: '/app/perfil/endereco',
  },
  {
    label: 'Fale com o suporte',
    icon: '/assets/icons/whatsapp_profile_icon.svg',
    action: 'whatsapp' as const,
  },
  {
    label: 'Alterar Senha',
    icon: '/assets/icons/password_profile_icon.svg',
    to: '/app/perfil/senha',
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
        <Button onClick={() => void handleLogout()}>Sair do aplicativo</Button>
      </div>
      <p className="profile__version">Versão {APP_VERSION}</p>
    </div>
  )
}
