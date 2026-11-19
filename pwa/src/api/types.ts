export interface ApiUser {
  id: number
  name?: string
  email: string
  user_code?: string
  avatar_url?: string | null
  status?: string | number
  customer?: ApiCustomer
  [key: string]: unknown
}

export interface ApiCustomer {
  id: number
  name: string
  email?: string
  document?: string
  cpf?: string
  cnpj?: string
  rg?: string
  birth_date?: string
  marital_status?: string
  phone?: string
  cellphone?: string
  sus_card?: string
  contacts?: Array<{ email?: string; phone?: string; cellphone?: string }>
  addresses?: ApiAddress[]
  dependents?: Array<{ id: number; name: string; kinship?: string; birth_date?: string }>
  creditCards?: unknown[]
  contract?: ApiSubscription | null
  [key: string]: unknown
}

export interface ApiAddress {
  id: number
  street?: string
  number?: string
  complement?: string | null
  neighborhood?: string
  zipcode?: string
  zip_code?: string
  city_id?: number
  city?: { id: number; name: string; state_id?: number; state?: { id: number; name: string; uf?: string } }
  state?: { id: number; name: string; uf?: string }
  state_id?: number
  [key: string]: unknown
}

export interface AuthSession {
  token_type: string
  token: string
  expires_in?: number
  user: ApiUser
  customer: ApiCustomer | null
}

export interface ApiHighlight {
  id: number
  name: string
  display_order?: number
  image_path?: string | null
  url?: string | null
}

export interface ApiPartner {
  id: number
  name: string
  display_order?: number
  image_path?: string | null
  url?: string | null
}

export interface ApiNotification {
  id: number
  title?: string
  message?: string
  is_read?: boolean
  read_at?: string | null
  send_at?: string | null
  created_at?: string
}

export interface ApiNotificationsResponse {
  total_unread: number
  data: ApiNotification[]
}

export interface ApiAppointment {
  id: number
  amount?: string
  scheduled_at?: string
  status_label?: string
  category?: string
  holder?: string
  marked_to?: string
  clinic?: string
  patient?: string
  specialty?: string
  professional?: string
  services?: string[]
  payment_method?: string
  pay_status_label?: string
  observations?: string
  links?: { details?: string; guide?: string; payment?: string }
  meta?: {
    status?: string
    payment_status?: string
    is_cancelable?: boolean
    is_canceled?: boolean
    is_completed?: boolean
    is_editable?: boolean
  }
}

export interface ApiSubscription {
  id: number
  value?: number | string
  status?: string | number
  modality?: { id?: number; name?: string; description?: string; value?: number | string }
  paymentMethod?: { name?: string }
  creditCard?: unknown
  transactions?: ApiTransaction[]
  [key: string]: unknown
}

export interface ApiTransaction {
  id: number
  amount?: number | string
  value?: number | string
  due_date?: string
  payday?: string | null
  status?: string | number
  status_label?: string
  description?: string
  [key: string]: unknown
}

export interface ApiCategory {
  id: number
  name: string
  subtypes?: ApiCategory[]
  [key: string]: unknown
}

export interface ApiClinic {
  id: number
  name: string
  [key: string]: unknown
}

export interface ApiProfessional {
  id: number
  name: string
  photo_url?: string | null
  [key: string]: unknown
}

export interface ApiSpecialty {
  id: number
  name: string
  [key: string]: unknown
}

export interface CheckoutResponse {
  message?: string
  errors: string[]
}
