import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { setSessionExpiredHandler } from '../api/client'
import { useAuth } from '../context/AuthContext'

/** Evita window.location no iOS PWA (abre Safari com barras por cima do app). */
export function SessionRedirect() {
  const navigate = useNavigate()
  const { logoutLocal } = useAuth()

  useEffect(() => {
    setSessionExpiredHandler(() => {
      logoutLocal()
      navigate('/login', { replace: true })
    })
    return () => setSessionExpiredHandler(null)
  }, [logoutLocal, navigate])

  return null
}
