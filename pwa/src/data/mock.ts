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
  email: 'demo@santesaude.com.br',
  phone: '(87) 99999-1234',
  cpf: '123.456.789-00',
  birthDate: '15/03/1988',
  maritalStatus: 'Casado(a)',
  rg: '1.234.567 SSP/PE',
}

export const mockAddress: Address = {
  id: 1,
  street: 'Rua das Flores',
  number: '120',
  complement: 'Apto 302',
  neighborhood: 'Centro',
  city: 'Petrolina',
  state: 'PE',
  zipCode: '56302-000',
}

export const mockAppointments: Appointment[] = [
  {
    id: 1,
    category: 'Consulta Médica',
    holder: 'Maria Silva Santos',
    professional: 'Dr. Carlos Mendes',
    clinic: 'Clínica Santé Petrolina',
    specialty: 'Clínica Geral',
    date: '2026-08-12T09:30:00',
    status: 'marked',
    statusLabel: 'Agendado',
    services: ['Consulta clínica'],
    value: 80,
  },
  {
    id: 2,
    category: 'Exame',
    holder: 'Maria Silva Santos',
    professional: 'Lab Santé',
    clinic: 'Laboratório Santé',
    specialty: 'Laboratorial',
    date: '2026-08-18T07:00:00',
    status: 'under_analysis',
    statusLabel: 'Em análise',
    services: ['Hemograma completo', 'Glicemia'],
    value: 45,
  },
  {
    id: 3,
    category: 'Consulta Médica',
    holder: 'Maria Silva Santos',
    professional: 'Dra. Ana Paula',
    clinic: 'Clínica Santé Petrolina',
    specialty: 'Dermatologia',
    date: '2026-06-02T14:00:00',
    status: 'completed',
    statusLabel: 'Concluído',
    services: ['Consulta dermatológica'],
    value: 120,
  },
  {
    id: 4,
    category: 'Consulta Médica',
    holder: 'João Silva Santos',
    professional: 'Dr. Pedro Lima',
    clinic: 'Clínica Santé Petrolina',
    specialty: 'Pediatria',
    date: '2026-05-10T10:00:00',
    status: 'canceled',
    statusLabel: 'Cancelado',
    services: ['Consulta pediátrica'],
    value: 90,
  },
]

export const mockNotifications: NotificationItem[] = [
  {
    id: 1,
    title: 'Consulta confirmada',
    body: 'Sua consulta com Dr. Carlos Mendes foi confirmada para 12/08 às 09:30.',
    date: '2026-08-01T10:00:00',
    read: false,
  },
  {
    id: 2,
    title: 'Lembrete de pagamento',
    body: 'Sua mensalidade do plano vence em 05/08. Evite a suspensão dos benefícios.',
    date: '2026-07-28T08:30:00',
    read: false,
  },
  {
    id: 3,
    title: 'Bem-vindo à Santé!',
    body: 'Aproveite descontos em consultas e exames na rede credenciada.',
    date: '2026-07-01T12:00:00',
    read: true,
  },
]

export const mockAccredited: Accredited[] = [
  {
    id: 1,
    name: 'Clínica Santé Petrolina',
    specialty: 'Clínica Geral / Especialidades',
    city: 'Petrolina',
    state: 'PE',
    phone: '(87) 3861-0000',
    address: 'Av. Cardoso de Sá, 850',
  },
  {
    id: 2,
    name: 'Laboratório Santé',
    specialty: 'Exames Laboratoriais',
    city: 'Petrolina',
    state: 'PE',
    phone: '(87) 3862-1111',
    address: 'Rua Joaquim Nabuco, 210',
  },
  {
    id: 3,
    name: 'Centro Oftalmológico Vale',
    specialty: 'Oftalmologia',
    city: 'Juazeiro',
    state: 'BA',
    phone: '(74) 3611-2222',
    address: 'Av. José Júlio, 400',
  },
  {
    id: 4,
    name: 'Odonto Smile',
    specialty: 'Odontologia',
    city: 'Petrolina',
    state: 'PE',
    phone: '(87) 98888-3333',
    address: 'Rua 15 de Novembro, 55',
  },
]

export const mockPartners: Partner[] = [
  {
    id: 1,
    name: 'Farmácia Popular Vale',
    description: 'Descontos em medicamentos genéricos e de marca.',
    discount: 'Até 25%',
  },
  {
    id: 2,
    name: 'Ótica Visão Clara',
    description: 'Lentes e armações com condições especiais.',
    discount: 'Até 30%',
  },
  {
    id: 3,
    name: 'Academia Move Saúde',
    description: 'Planos mensais com valor reduzido para beneficiários.',
    discount: '20%',
  },
]

export const mockCharges: Charge[] = [
  {
    id: 1,
    description: 'Mensalidade Agosto/2026',
    dueDate: '2026-08-05',
    value: 89.9,
    status: 'pending',
  },
  {
    id: 2,
    description: 'Mensalidade Julho/2026',
    dueDate: '2026-07-05',
    value: 89.9,
    status: 'paid',
  },
  {
    id: 3,
    description: 'Mensalidade Junho/2026',
    dueDate: '2026-06-05',
    value: 89.9,
    status: 'paid',
  },
]

export const mockWallet = {
  modality: 'Plano Família',
  description: 'Cobertura para titular e dependentes com descontos em consultas e exames.',
  userCode: 'SS-2024-00891',
  holder: 'Maria Silva Santos',
  cpf: '123.456.789-00',
  accessionDate: '01/03/2024',
  validity: 'Vitalício (enquanto ativo)',
  dependents: [
    { name: 'João Silva Santos', kinship: 'Filho', birthDate: '10/01/2015' },
    { name: 'Pedro Silva Santos', kinship: 'Cônjuge', birthDate: '22/08/1985' },
  ],
  services: ['Consultas médicas', 'Exames laboratoriais', 'Odontologia', 'Rede credenciada'],
}

export const appointmentTypes = [
  { id: 1, name: 'Consulta Médica', icon: '🩺' },
  { id: 2, name: 'Exame', icon: '🔬' },
  { id: 3, name: 'Odontologia', icon: '🦷' },
  { id: 4, name: 'Retorno', icon: '↩️' },
]

export const specialties = [
  'Clínica Geral',
  'Cardiologia',
  'Dermatologia',
  'Ginecologia',
  'Ortopedia',
  'Pediatria',
  'Oftalmologia',
]

export const clinics = [
  'Clínica Santé Petrolina',
  'Clínica Santé Juazeiro',
  'Laboratório Santé',
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
    title: 'Consultas com até 70% off',
    subtitle: 'Agende pelo app e pague em até 3x',
  },
  {
    id: 2,
    title: 'Rede credenciada ampliada',
    subtitle: 'Novos parceiros em Petrolina e Juazeiro',
  },
  {
    id: 3,
    title: 'Carteira digital',
    subtitle: 'Apresente seu cartão Santé na hora do atendimento',
  },
]

export const WHATSAPP = '5587998115948'
export const WHATSAPP_LABEL = '(87) 9 9811-5948'
export const APP_VERSION = '0.2.16'
