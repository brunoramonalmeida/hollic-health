import type { AuthSession } from './types'

const SESSION_KEY = 'sante_pwa_session'

export function loadSession(): AuthSession | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY)
    if (!raw) return null
    return JSON.parse(raw) as AuthSession
  } catch {
    return null
  }
}

export function saveSession(session: AuthSession) {
  localStorage.setItem(SESSION_KEY, JSON.stringify(session))
  // limpa sessão mock antiga
  localStorage.removeItem('sante_pwa_user')
}

export function clearSession() {
  localStorage.removeItem(SESSION_KEY)
  localStorage.removeItem('sante_pwa_user')
}

export function authHeaderFromSession(session: AuthSession | null): string | null {
  if (!session?.token) return null
  return `${session.token_type || 'Bearer'} ${session.token}`
}
