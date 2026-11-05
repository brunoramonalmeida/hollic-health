import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export function SplashPage() {
  const navigate = useNavigate()
  const { session, isHydrated } = useAuth()

  useEffect(() => {
    if (!isHydrated) return
    const t = setTimeout(() => {
      navigate(session?.token ? '/app' : '/welcome', { replace: true })
    }, 2000)
    return () => clearTimeout(t)
  }, [isHydrated, session?.token, navigate])

  return (
    <div className="page page--gradient splash">
      <img className="fade-pulse" src="/assets/images/logo_splash.svg" alt="Santé Saúde" />
    </div>
  )
}
