export type AppointmentStatus = 'marked' | 'under_analysis' | 'completed' | 'canceled'

export interface User {
  id: number
  name: string
  email: string
  phone: string
  cpf: string
  birthDate: string
  maritalStatus: string
  rg: string
}

export interface Address {
  id: number
  street: string
  number: string
  complement: string
  neighborhood: string
  city: string
  state: string
  zipCode: string
  cityId?: number
  stateId?: number
}

export interface Appointment {
  id: number
  category: string
  holder: string
  professional: string
  clinic: string
  specialty: string
  date: string
  status: AppointmentStatus
  statusLabel: string
  services: string[]
  value: number
}

export interface NotificationItem {
  id: number
  title: string
  body: string
  date: string
  read: boolean
}

export interface Accredited {
  id: number
  name: string
  specialty: string
  city: string
  state: string
  phone: string
  address: string
}

export interface Partner {
  id: number
  name: string
  description: string
  discount: string
}

export interface Charge {
  id: number
  description: string
  dueDate: string
  value: number
  status: 'paid' | 'pending' | 'overdue'
}

export const DEMO_CREDENTIALS = {
  email: '',
  password: '',
}


export const mockUser: User = {
  id: 1,
  name: 'Maria Silva Santos',
  email: 'demo@hollic-health.example',
  phone: '(87) 99999-1234',
  cpf: '123.456.789-00',
  birthDate: '15/03/1988',
  maritalStatus: 'Married',
  rg: '1.234.567 SSP/PE',
}

export const mockAddress: Address = {
  id: 1,
  street: 'Flower Street',
  number: '120',
  complement: 'Apt 302',
  neighborhood: 'Downtown',
  city: 'Petrolina',
  state: 'PE',
  zipCode: '56302-000',
}

export const mockAppointments: Appointment[] = [
  {
    id: 1,
    category: 'Medical visit',
    holder: 'Maria Silva Santos',
    professional: 'Dr. Carlos Mendes',
    clinic: 'Hollic Clinic Petrolina',
    specialty: 'General practice',
    date: '2026-08-12T09:30:00',
    status: 'marked',
    statusLabel: 'Scheduled',
    services: ['Clinical visit'],
    value: 80,
  },
  {
    id: 2,
    category: 'Lab test',
    holder: 'Maria Silva Santos',
    professional: 'Lab Hollic',
    clinic: 'Hollic Lab',
    specialty: 'Laboratory',
    date: '2026-08-18T07:00:00',
    status: 'under_analysis',
    statusLabel: 'Under review',
    services: ['Complete blood count', 'Blood glucose'],
    value: 45,
  },
  {
    id: 3,
    category: 'Medical visit',
    holder: 'Maria Silva Santos',
    professional: 'Dra. Ana Paula',
    clinic: 'Hollic Clinic Petrolina',
    specialty: 'Dermatology',
    date: '2026-06-02T14:00:00',
    status: 'completed',
    statusLabel: 'Completed',
    services: ['Dermatology visit'],
    value: 120,
  },
  {
    id: 4,
    category: 'Medical visit',
    holder: 'João Silva Santos',
    professional: 'Dr. Pedro Lima',
    clinic: 'Hollic Clinic Petrolina',
    specialty: 'Pediatrics',
    date: '2026-05-10T10:00:00',
    status: 'canceled',
    statusLabel: 'Cancelled',
    services: ['Pediatric visit'],
    value: 90,
  },
]

export const mockNotifications: NotificationItem[] = [
  {
    id: 1,
    title: 'Appointment confirmed',
    body: 'Your appointment with Dr. Carlos Mendes is confirmed for 08/12 at 09:30.',
    date: '2026-08-01T10:00:00',
    read: false,
  },
  {
    id: 2,
    title: 'Payment reminder',
    body: 'Your plan fee is due on 08/05. Pay it to keep your benefits active.',
    date: '2026-07-28T08:30:00',
    read: false,
  },
  {
    id: 3,
    title: 'Welcome to Hollic!',
    body: 'Enjoy discounts on appointments and tests across the accredited network.',
    date: '2026-07-01T12:00:00',
    read: true,
  },
]

export const mockAccredited: Accredited[] = [
  {
    id: 1,
    name: 'Hollic Clinic Petrolina',
    specialty: 'General practice / Specialties',
    city: 'Petrolina',
    state: 'PE',
    phone: '(87) 3861-0000',
    address: 'Av. Cardoso de Sá, 850',
  },
  {
    id: 2,
    name: 'Hollic Lab',
    specialty: 'Laboratory tests',
    city: 'Petrolina',
    state: 'PE',
    phone: '(87) 3862-1111',
    address: 'Rua Joaquim Nabuco, 210',
  },
  {
    id: 3,
    name: 'Vale Eye Center',
    specialty: 'Ophthalmology',
    city: 'Juazeiro',
    state: 'BA',
    phone: '(74) 3611-2222',
    address: 'Av. José Júlio, 400',
  },
  {
    id: 4,
    name: 'Odonto Smile',
    specialty: 'Dentistry',
    city: 'Petrolina',
    state: 'PE',
    phone: '(87) 98888-3333',
    address: 'Rua 15 de Novembro, 55',
  },
]

export const mockPartners: Partner[] = [
  {
    id: 1,
    name: 'Vale Community Pharmacy',
    description: 'Discounts on generic and brand-name medicines.',
    discount: 'Up to 25%',
  },
  {
    id: 2,
    name: 'Clear Vision Optical',
    description: 'Lenses and frames on special terms.',
    discount: 'Up to 30%',
  },
  {
    id: 3,
    name: 'Move Health Gym',
    description: 'Monthly plans at a reduced price for members.',
    discount: '20%',
  },
]

export const mockCharges: Charge[] = [
  {
    id: 1,
    description: 'August 2026 fee',
    dueDate: '2026-08-05',
    value: 89.9,
    status: 'pending',
  },
  {
    id: 2,
    description: 'July 2026 fee',
    dueDate: '2026-07-05',
    value: 89.9,
    status: 'paid',
  },
  {
    id: 3,
    description: 'June 2026 fee',
    dueDate: '2026-06-05',
    value: 89.9,
    status: 'paid',
  },
]

export const mockWallet = {
  modality: 'Family plan',
  description: 'Coverage for the holder and dependents, with discounts on appointments and tests.',
  userCode: 'SS-2024-00891',
  holder: 'Maria Silva Santos',
  cpf: '123.456.789-00',
  accessionDate: '01/03/2024',
  validity: 'Lifetime (while active)',
  dependents: [
    { name: 'João Silva Santos', kinship: 'Child', birthDate: '01/10/2015' },
    { name: 'Pedro Silva Santos', kinship: 'Spouse', birthDate: '08/22/1985' },
  ],
  services: ['Medical visits', 'Lab tests', 'Dentistry', 'Accredited network'],
}

export const appointmentTypes = [
  { id: 1, name: 'Medical visit', icon: '🩺' },
  { id: 2, name: 'Lab test', icon: '🔬' },
  { id: 3, name: 'Dentistry', icon: '🦷' },
  { id: 4, name: 'Follow-up', icon: '↩️' },
]

export const specialties = [
  'General practice',
  'Cardiology',
  'Dermatology',
  'Gynecology',
  'Orthopedics',
  'Pediatrics',
  'Ophthalmology',
]

export const clinics = [
  'Hollic Clinic Petrolina',
  'Hollic Clinic Juazeiro',
  'Hollic Lab',
]

export const professionals = [
  'Dr. Carlos Mendes',
  'Dra. Ana Paula',
  'Dr. Pedro Lima',
  'Dra. Fernanda Souza',
]

export const availableTimes = [
  '08:00',
  '08:30',
  '09:00',
  '09:30',
  '10:00',
  '14:00',
  '14:30',
  '15:00',
  '16:00',
]

export const banners = [
  {
    id: 1,
    title: 'Appointments up to 70% off',
    subtitle: 'Book in the app and pay in up to 3 installments',
  },
  {
    id: 2,
    title: 'A wider accredited network',
    subtitle: 'New partners in Petrolina and Juazeiro',
  },
  {
    id: 3,
    title: 'Digital card',
    subtitle: 'Show your Hollic card at the visit',
  },
]

export const WHATSAPP = '5587998115948'
export const WHATSAPP_LABEL = '(87) 9 9811-5948'
export const APP_VERSION = '0.2.16'
