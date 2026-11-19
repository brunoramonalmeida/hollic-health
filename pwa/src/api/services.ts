import { api } from './client'
import { endpoints } from './endpoints'
import type {
  ApiAddress,
  ApiAppointment,
  ApiCategory,
  ApiHighlight,
  ApiNotificationsResponse,
  ApiPartner,
  AuthSession,
  CheckoutResponse,
} from './types'

const MINI_ACCEPT = 'application/vnd.app.v2.mini+json'

export async function loginRequest(email: string, password: string) {
  return api.post<AuthSession>(
    endpoints.login,
    { email, password, remember: 1 },
    { auth: false },
  )
}

export async function logoutRequest() {
  return api.delete(endpoints.logout)
}

export async function forgotPasswordRequest(email: string) {
  return api.post<{ message?: string }>(
    endpoints.forgotPassword,
    { email },
    { auth: false },
  )
}

export async function changePasswordRequest(payload: {
  current_password: string
  password: string
  password_confirmation: string
}) {
  return api.post<{ message?: string }>(endpoints.changePassword, payload)
}

export async function updateCustomerRequest(payload: Record<string, unknown>) {
  return api.post<{ message?: string; data?: unknown }>(endpoints.updateCustomer, payload)
}

export async function fetchHighlights() {
  return api.get<ApiHighlight[]>(endpoints.highlights)
}

export async function fetchPartners() {
  return api.get<ApiPartner[]>(endpoints.partners)
}

export async function fetchNotifications() {
  return api.get<ApiNotificationsResponse>(endpoints.notifications)
}

export async function markNotificationReadRequest(id: number) {
  return api.put(endpoints.notification(id))
}

export async function fetchAppointments() {
  return api.get<ApiAppointment[]>(endpoints.appointments, { accept: MINI_ACCEPT })
}

export async function fetchAppointment(id: number | string) {
  return api.get<ApiAppointment>(endpoints.appointment(id), { accept: MINI_ACCEPT })
}

export async function cancelAppointmentRequest(id: number | string) {
  return api.delete<{ status?: boolean; message?: string }>(endpoints.appointment(id))
}

export async function createAppointmentRequest(payload: Record<string, unknown>) {
  return api.post<{ data?: ApiAppointment; message?: string }>(
    endpoints.appointments,
    payload,
    { accept: MINI_ACCEPT },
  )
}

export async function fetchCheckout() {
  return api.get<CheckoutResponse>(endpoints.checkout, { accept: MINI_ACCEPT })
}

export async function fetchCategories() {
  return api.get<{ data?: ApiCategory[] } | ApiCategory[]>(endpoints.categories)
}

export async function fetchAddresses() {
  return api.get<ApiAddress[]>(endpoints.addresses)
}

export async function updateAddressRequest(id: number | string, payload: Record<string, unknown>) {
  return api.put<{ message?: string; data?: ApiAddress }>(endpoints.address(id), payload)
}

export async function createAddressRequest(payload: Record<string, unknown>) {
  return api.post<{ message?: string; data?: ApiAddress }>(endpoints.addresses, payload)
}

export async function fetchAccreditedNetwork() {
  return api.get<unknown[]>(endpoints.accreditedNetwork)
}

export async function fetchAppointmentNeededData(attendanceTypeId: number | string) {
  const url = `${endpoints.appointmentNeededData}?att=${attendanceTypeId}`
  return api.get<Record<string, unknown>>(url)
}

export async function fetchAvailableTimes(params: Record<string, string | number>) {
  const qs = new URLSearchParams(
    Object.entries(params).map(([k, v]) => [k, String(v)]),
  ).toString()
  return api.get<unknown>(`${endpoints.availableTimes}?${qs}`)
}

export { endpoints }
