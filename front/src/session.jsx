import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { api } from './api'
import { ROLE_LABEL, permissionsFor } from './permissions'

const SessionContext = createContext(null)

// Permissions live in permissions.js (pure, unit-tested); re-exported for the pages.
export { ROLE_LABEL, permissionsFor }

export function SessionProvider({ children }) {
  const [user, setUser] = useState(undefined) // undefined: loading; null: signed out

  function refresh() {
    if (!localStorage.getItem('token')) {
      setUser(null)
      return Promise.resolve(null)
    }
    return api('/auth/me')
      .then((me) => {
        setUser(me)
        return me
      })
      .catch(() => {
        setUser(null)
        return null
      })
  }

  useEffect(() => {
    refresh()
  }, [])

  const value = useMemo(() => ({ user, loading: user === undefined, refresh, ...permissionsFor(user) }), [user])
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession() {
  return useContext(SessionContext)
}

/** Renders children only when the session passes `check(session)`; otherwise sends home. */
export function RequireAccess({ check, children }) {
  const session = useSession()
  if (session.loading) return null
  if (!session.user) return <Navigate to="/login" replace />
  if (check && !check(session)) return <Navigate to="/" replace />
  return children
}
