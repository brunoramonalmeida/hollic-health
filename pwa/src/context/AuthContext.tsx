import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { ApiError } from '../api/client'
import { mapApiAddress, mapApiAppointment, mapApiNotification, mapCustomerToUser } from '../api/mappers'
import {
  changePasswordRequest,
  fetchAddresses,
  fetchAppointments,
  fetchNotifications,
  loginRequest,
  logoutRequest,
  markNotificationReadRequest,
  updateCustomerRequest,
} from '../api/services'
import { clearSession, loadSession, saveSession } from '../api/session'
import type { AuthSession } from '../api/types'
import type { Address, Appointment, NotificationItem, User } from '../data/mock'

interface AuthContextValue {
  user: User | null
  session: AuthSession | null
  address: Address
  appointments: Appointment[]
  notifications: NotificationItem[]
  unreadCount: number
  isHydrated: boolean
  loadingAppointments: boolean
  loadingNotifications: boolean
  login: (email: string, password: string) => Promise<{ ok: boolean; message?: string }>
  logout: () => Promise<void>
  logoutLocal: () => void
  refreshAppointments: () => Promise<void>
  refreshNotifications: () => Promise<void>
  refreshAddresses: () => Promise<void>
  updateUser: (data: Partial<User>) => Promise<{ ok: boolean; message?: string }>
  updateAddress: (data: Partial<Address>) => void
  addAppointment: (appointment: Appointment) => void
  markNotificationRead: (id: number) => Promise<void>
  changePassword: (current: string, next: string, confirm: string) => Promise<{ ok: boolean; message?: string }>
}

const AuthContext = createContext<AuthContextValue | null>(null)

const emptyAddress: Address = {
  id: 0,
  street: '',
  number: '',
  complement: '',
  neighborhood: '',
  city: '',
  state: '',
  zipCode: '',
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(() => loadSession())
  const [user, setUser] = useState<User | null>(() => {
    const s = loadSession()
    return s ? mapCustomerToUser(s.customer, s.user?.email) : null
  })
  const [address, setAddress] = useState<Address>(() => {
    const s = loadSession()
    return mapApiAddress(s?.customer?.addresses?.[0])
  })
  const [appointments, setAppointments] = useState<Appointment[]>([])
  const [notifications, setNotifications] = useState<NotificationItem[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const [isHydrated, setIsHydrated] = useState(false)
  const [loadingAppointments, setLoadingAppointments] = useState(false)
  const [loadingNotifications, setLoadingNotifications] = useState(false)

  const clearAuth = useCallback(() => {
    clearSession()
    setSession(null)
    setUser(null)
    setAddress(emptyAddress)
    setAppointments([])
    setNotifications([])
    setUnreadCount(0)
  }, [])

  useEffect(() => {
    setIsHydrated(true)
  }, [])

  const logoutLocal = clearAuth

  const refreshAppointments = useCallback(async () => {
    if (!loadSession()?.token) return
    setLoadingAppointments(true)
    try {
      const data = await fetchAppointments()
      setAppointments((Array.isArray(data) ? data : []).map(mapApiAppointment))
    } catch {
      // keep the current list if this refresh fails
    } finally {
      setLoadingAppointments(false)
    }
  }, [])

  const refreshNotifications = useCallback(async () => {
    if (!loadSession()?.token) return
    setLoadingNotifications(true)
    try {
      const data = await fetchNotifications()
      setNotifications((data.data || []).map(mapApiNotification))
      setUnreadCount(data.total_unread || 0)
    } catch {
      // ignore
    } finally {
      setLoadingNotifications(false)
    }
  }, [])

  const refreshAddresses = useCallback(async () => {
    if (!loadSession()?.token) return
    try {
      const data = await fetchAddresses()
      const list = Array.isArray(data) ? data : []
      if (list[0]) setAddress(mapApiAddress(list[0]))
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    if (!session?.token) return
    void refreshAppointments()
    void refreshNotifications()
    void refreshAddresses()
  }, [session?.token, refreshAppointments, refreshNotifications, refreshAddresses])

  const login = useCallback(async (email: string, password: string) => {
    try {
      const data = await loginRequest(email, password)
      const nextSession: AuthSession = {
        token_type: data.token_type || 'Bearer',
        token: data.token,
        expires_in: data.expires_in,
        user: data.user,
        customer: data.customer,
      }
      saveSession(nextSession)
      setSession(nextSession)
      setUser(mapCustomerToUser(data.customer, data.user?.email || email))
      setAddress(mapApiAddress(data.customer?.addresses?.[0]))
      return { ok: true }
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : 'Could not sign in. Please try again.'
      return { ok: false, message }
    }
  }, [])

  const logout = useCallback(async () => {
    try {
      await logoutRequest()
    } catch {
      // clear local state even if the API call fails
    } finally {
      clearAuth()
    }
  }, [clearAuth])

  const updateUser = useCallback(async (data: Partial<User>) => {
    try {
      const payload: Record<string, unknown> = {}
      if (data.name !== undefined) payload.name = data.name
      if (data.email !== undefined) payload.email = data.email
      if (data.cpf !== undefined) payload.cpf = data.cpf
      if (data.rg !== undefined) payload.rg = data.rg
      if (data.maritalStatus !== undefined) payload.marital_status = data.maritalStatus
      if (data.birthDate !== undefined) payload.birth_date = data.birthDate
      if (data.phone !== undefined) payload.cellphone = data.phone

      await updateCustomerRequest(payload)
      setUser((prev) => (prev ? { ...prev, ...data } : prev))

      const current = loadSession()
      if (current?.customer) {
        const next = {
          ...current,
          customer: {
            ...current.customer,
            name: data.name ?? current.customer.name,
            email: data.email ?? current.customer.email,
            cpf: data.cpf ?? current.customer.cpf,
            rg: data.rg ?? current.customer.rg,
            marital_status: data.maritalStatus ?? current.customer.marital_status,
            birth_date: data.birthDate ?? current.customer.birth_date,
            cellphone: data.phone ?? current.customer.cellphone,
          },
        }
        saveSession(next)
        setSession(next)
      }

      return { ok: true }
    } catch (error) {
      const message = error instanceof ApiError ? error.message : 'Could not update your details.'
      return { ok: false, message }
    }
  }, [])

  const updateAddress = useCallback((data: Partial<Address>) => {
    setAddress((prev) => ({ ...prev, ...data }))
  }, [])

  const addAppointment = useCallback((appointment: Appointment) => {
    setAppointments((prev) => [appointment, ...prev])
  }, [])

  const markNotificationRead = useCallback(async (id: number) => {
    setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)))
    setUnreadCount((c) => Math.max(0, c - 1))
    try {
      await markNotificationReadRequest(id)
    } catch {
      // UI was already updated optimistically
    }
  }, [])

  const changePassword = useCallback(async (current: string, next: string, confirm: string) => {
    try {
      await changePasswordRequest({
        current_password: current,
        password: next,
        password_confirmation: confirm,
      })
      return { ok: true }
    } catch (error) {
      const message = error instanceof ApiError ? error.message : 'Could not change the password.'
      return { ok: false, message }
    }
  }, [])

  const value = useMemo(
    () => ({
      user,
      session,
      address,
      appointments,
      notifications,
      unreadCount,
      isHydrated,
      loadingAppointments,
      loadingNotifications,
      login,
      logout,
      logoutLocal,
      refreshAppointments,
      refreshNotifications,
      refreshAddresses,
      updateUser,
      updateAddress,
      addAppointment,
      markNotificationRead,
      changePassword,
    }),
    [
      user,
      session,
      address,
      appointments,
      notifications,
      unreadCount,
      isHydrated,
      loadingAppointments,
      loadingNotifications,
      login,
      logout,
      logoutLocal,
      refreshAppointments,
      refreshNotifications,
      refreshAddresses,
      updateUser,
      updateAddress,
      addAppointment,
      markNotificationRead,
      changePassword,
    ],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
