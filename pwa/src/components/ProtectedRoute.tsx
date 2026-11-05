import { Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export function ProtectedRoute() {
  const { session, isHydrated } = useAuth()
  if (!isHydrated) return null
  if (!session?.token) return <Navigate to="/welcome" replace />
  return <Outlet />
}

export function GuestRoute() {
  const { session, isHydrated } = useAuth()
  if (!isHydrated) return null
  if (session?.token) return <Navigate to="/app" replace />
  return <Outlet />
}
