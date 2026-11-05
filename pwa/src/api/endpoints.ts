export const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') || '/api'

export const APP_V1 = `${API_BASE}/app/v1`
export const API_ROOT = API_BASE

export const endpoints = {
  login: `${APP_V1}/login`,
  logout: `${APP_V1}/logout`,
  forgotPassword: `${API_ROOT}/forgot-password`,
  me: `${APP_V1}/user`,
  updateUser: `${APP_V1}/user/update`,
  changePassword: `${APP_V1}/user/change-password`,
  updateCustomer: `${APP_V1}/update`,
  highlights: `${APP_V1}/highlights`,
  highlightImage: (id: number | string) => `${API_ROOT}/highlights/${id}/image`,
  partners: `${APP_V1}/partners`,
  partnerImage: (id: number | string) => `${API_ROOT}/partners/${id}/image`,
  notifications: `${APP_V1}/notifications`,
  notification: (id: number | string) => `${APP_V1}/notifications/${id}`,
  appointments: `${APP_V1}/appointments`,
  appointment: (id: number | string) => `${APP_V1}/appointments/${id}`,
  appointmentNeededData: `${APP_V1}/appointments/needed-data`,
  checkout: `${APP_V1}/checkout`,
  categories: `${APP_V1}/categories`,
  clinics: `${APP_V1}/clinics`,
  professionals: `${APP_V1}/professionals`,
  professionalAvailableDays: (id: number | string) =>
    `${APP_V1}/professionals/${id}/available-days`,
  specialties: `${APP_V1}/specialties`,
  services: `${APP_V1}/services`,
  paymentMethods: `${APP_V1}/payment-methods`,
  subscriptions: `${APP_V1}/subscriptions`,
  santeCard: `${APP_V1}/sante-card`,
  addresses: `${APP_V1}/addresses`,
  address: (id: number | string) => `${APP_V1}/addresses/${id}`,
  accreditedNetwork: `${APP_V1}/accredited-network`,
  states: `${APP_V1}/states`,
  cities: `${APP_V1}/cities`,
  city: (id: number | string) => `${APP_V1}/city/${id}`,
  dependents: `${APP_V1}/dependents`,
  creditCards: `${APP_V1}/credit-cards`,
  availableTimes: `${API_ROOT}/v1/attendances/schedules/available-times`,
} as const
