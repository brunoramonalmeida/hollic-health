import type { ApiAddress, ApiAppointment, ApiCustomer, ApiNotification } from './types'
import type { Address, Appointment, AppointmentStatus, NotificationItem, User } from '../data/mock'

export function mapCustomerToUser(customer: ApiCustomer | null | undefined, emailFallback?: string): User {
  const contact = customer?.contacts?.[0]
  return {
    id: customer?.id ?? 0,
    name: customer?.name ?? 'Cliente',
    email: customer?.email || contact?.email || emailFallback || '',
    phone: contact?.cellphone || contact?.phone || customer?.cellphone || customer?.phone || '',
    cpf: customer?.cpf || customer?.document || '',
    birthDate: formatDateBr(customer?.birth_date),
    maritalStatus: customer?.marital_status || '',
    rg: customer?.rg || '',
  }
}

export function mapApiAddress(address?: ApiAddress | null): Address {
  if (!address) {
    return {
      id: 0,
      street: '',
      number: '',
      complement: '',
      neighborhood: '',
      city: '',
      state: '',
      zipCode: '',
      cityId: undefined,
      stateId: undefined,
    }
  }

  const cityId = address.city?.id ?? address.city_id
  const stateId = address.state?.id ?? address.city?.state_id ?? address.state_id
  const stateLabel =
    address.state?.uf || address.city?.state?.uf || address.state?.name || address.city?.state?.name || ''

  return {
    id: address.id,
    street: address.street || '',
    number: address.number || '',
    complement: address.complement || '',
    neighborhood: address.neighborhood || '',
    city: address.city?.name || '',
    state: stateLabel,
    zipCode: address.zip_code || address.zipcode || '',
    cityId: cityId ? Number(cityId) : undefined,
    stateId: stateId ? Number(stateId) : undefined,
  }
}

export function toAddressPayload(address: Address) {
  if (!address.cityId || !address.stateId || !address.city || !address.state) {
    throw new Error('Selecione cidade e estado para salvar o endereço.')
  }

  return {
    street: address.street,
    number: address.number,
    complement: address.complement || null,
    neighborhood: address.neighborhood,
    zip_code: address.zipCode,
    city: { value: address.cityId, label: address.city },
    state: { value: address.stateId, label: address.state },
  }
}

export function mapApiAppointment(item: ApiAppointment): Appointment {
  const status = normalizeStatus(item.meta?.status, item.status_label)
  return {
    id: item.id,
    category: item.category || 'Atendimento',
    holder: item.patient || item.holder || '',
    professional: item.professional || '',
    clinic: item.clinic || '',
    specialty: item.specialty || '',
    date: item.marked_to || item.scheduled_at || new Date().toISOString(),
    status,
    statusLabel: item.status_label || statusLabelFrom(status),
    services: item.services || [],
    value: parseAmount(item.amount),
  }
}

export function mapApiNotification(item: ApiNotification): NotificationItem {
  return {
    id: item.id,
    title: item.title || 'Notificação',
    body: item.message || '',
    date: item.send_at || item.created_at || new Date().toISOString(),
    read: Boolean(item.is_read || item.read_at),
  }
}

function parseAmount(amount?: string) {
  if (!amount) return 0
  const cleaned = amount.replace(/[^\d,.-]/g, '').replace('.', '').replace(',', '.')
  const n = Number(cleaned)
  return Number.isFinite(n) ? n : 0
}

function normalizeStatus(status?: string, label?: string): AppointmentStatus {
  const raw = (status || label || '').toLowerCase()
  if (raw.includes('cancel')) return 'canceled'
  if (raw.includes('complet') || raw.includes('conclu')) return 'completed'
  if (raw.includes('analy') || raw.includes('anális') || raw.includes('analis')) return 'under_analysis'
  return 'marked'
}

function statusLabelFrom(status: AppointmentStatus) {
  switch (status) {
    case 'canceled':
      return 'Cancelado'
    case 'completed':
      return 'Concluído'
    case 'under_analysis':
      return 'Em análise'
    default:
      return 'Agendado'
  }
}

function formatDateBr(value?: string) {
  if (!value) return ''
  if (/^\d{2}\/\d{2}\/\d{4}$/.test(value)) return value
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleDateString('pt-BR')
}
