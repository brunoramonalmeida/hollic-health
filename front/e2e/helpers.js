// Shared helpers: talk to the API directly to build the scenario each test
// needs (a professional working all day, a customer with an active plan,
// appointments in the past or in the future), and drive the login form.
import { createHmac } from 'node:crypto'
import { expect } from '@playwright/test'

export const API_URL = process.env.E2E_API_URL || 'http://127.0.0.1:8000'
export const PASSWORD = 'saude123'
export const ADMIN_EMAIL = 'foo.bar@example.com'

const pad = (value) => String(value).padStart(2, '0')

/** Local "YYYY-MM-DDTHH:mm" (what the API stores, no timezone). */
export function localIso(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function isoDate(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** Floor to the 30-minute grid. */
function grid(date) {
  const copy = new Date(date)
  copy.setSeconds(0, 0)
  copy.setMinutes(copy.getMinutes() - (copy.getMinutes() % 30))
  return copy
}

/** A slot that already started (signable), `minutesAgo` minutes back. */
export function pastSlot(minutesAgo = 90) {
  return grid(new Date(Date.now() - minutesAgo * 60_000))
}

/** A slot in the future (not signable). */
export function futureSlot(minutesAhead = 180) {
  return grid(new Date(Date.now() + minutesAhead * 60_000))
}

export function formatDateTime(date) {
  return `${pad(date.getDate())}/${pad(date.getMonth() + 1)}/${date.getFullYear()} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export async function token(request, email, password = PASSWORD) {
  const response = await request.post(`${API_URL}/auth/token`, { data: { email, password } })
  expect(response.ok(), await response.text()).toBeTruthy()
  return (await response.json()).access_token
}

export class Api {
  constructor(request, bearer) {
    this.request = request
    this.bearer = bearer
  }

  static async asAdmin(request) {
    return new Api(request, await token(request, ADMIN_EMAIL))
  }

  async call(method, path, data) {
    const response = await this.request.fetch(`${API_URL}${path}`, {
      method,
      data,
      headers: { Authorization: `Bearer ${this.bearer}` },
    })
    const text = await response.text()
    expect(response.ok(), `${method} ${path} -> ${response.status()} ${text}`).toBeTruthy()
    return text ? JSON.parse(text) : null
  }

  get(path) {
    return this.call('GET', path)
  }

  post(path, data) {
    return this.call('POST', path, data)
  }

  put(path, data) {
    return this.call('PUT', path, data)
  }
}

let counter = 0
export function unique(prefix) {
  counter += 1
  return `${prefix}-${Date.now()}-${counter}`
}

/** A professional working every day, all day, so slots near "now" exist. */
export async function createProfessional(api, { name, price_cents = 5000, ...extra } = {}) {
  return api.post('/professionals', {
    name: name || unique('Dr Test'),
    specialty_id: 1,
    clinic_id: 1,
    price_cents,
    working_hours: Array.from({ length: 7 }, (_, weekday) => ({ weekday, start: '00:00', end: '23:30' })),
    ...extra,
  })
}

/** The demo A1 certificate of the test PKI (see api/scripts/make_demo_certificate.py): CPF 12345678909, password saude123. */
export const DEMO_CERTIFICATE = { path: '../api/demo-ca/lorem-ipsum.pfx', cpf: '12345678909', password: 'saude123' }

export async function createProfessionalUser(api, professional, { email, name } = {}) {
  const login = email || `${unique('pro').toLowerCase()}@example.com`
  await api.post('/users', {
    name: name || professional.name,
    email: login,
    role: 'professional',
    password: PASSWORD,
    professional_id: professional.id,
  })
  return login
}

/** A customer with an active plan covering today (installments due so far are paid). */
export async function createCoveredCustomer(api, { name } = {}) {
  const customer = await api.post('/customers', { name: name || unique('Patient') })
  const plans = await api.get('/modalities')
  // An individual plan without a waiting period (enterprise plans are only for company agreements).
  let plan = plans.find((item) => !item.enterprise && (item.effective_grace_days ?? item.grace_days) === 0)
  if (!plan) {
    plan = await api.post('/modalities', { name: unique('Plan'), plan_value_cents: 9900, dependent_limit: 2, grace_days: 0, extra_dependent_cents: 1500, discount_percent: 20 })
  }
  const today = new Date()
  const joined = new Date(today)
  joined.setDate(joined.getDate() - 40)
  const expires = new Date(today)
  expires.setDate(expires.getDate() + 400)
  let subscription = await api.post('/subscriptions', {
    customer_id: customer.id,
    modality_id: plan.id,
    joined_on: isoDate(joined),
    expires_on: isoDate(expires),
  })
  for (const item of subscription.installments) {
    if (item.due_on <= isoDate(today)) {
      subscription = await api.put(`/subscriptions/${subscription.id}/installments/${item.id}`, { paid_on: item.due_on })
    }
  }
  return customer
}

export async function book(api, { customer, professional, at, status = 'agendada' }) {
  return api.post('/appointments', {
    customer_id: customer.id,
    professional_id: professional.id,
    marked_at: localIso(at),
    status,
  })
}

/** Sign in through the form and wait for the home page. */
export async function login(page, email, password = PASSWORD) {
  // A sign-out redirect may still be in flight: a navigation aborted or
  // interrupted by the SPA's own redirect to /login is harmless.
  if (!new URL(page.url(), 'http://x').pathname.endsWith('/login')) {
    await page.goto('/login').catch((err) => {
      const text = String(err.message)
      if (!text.includes('ERR_ABORTED') && !text.includes('interrupted')) throw err
    })
  }
  await page.getByLabel('Email').waitFor()
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.waitForURL((url) => url.pathname === '/')
}

/** A client login for the customer (the only login that can see the card). */
export async function createClientUser(api, customer) {
  const email = `${unique('client').toLowerCase()}@example.com`
  await api.post('/users', { name: customer.name, email, role: 'client', password: PASSWORD, customer_id: customer.id })
  return email
}

/** The code on the patient's virtual card right now, as the patient's login sees it. */
export async function cardCode(request, clientEmail, customer, dependentId = null) {
  const bearer = await token(request, clientEmail)
  const client = new Api(request, bearer)
  const path = dependentId ? `/customers/${customer.id}/dependents/${dependentId}/card` : `/customers/${customer.id}/card`
  return (await client.get(path)).code
}

/** Start the appointment through the API (the reception typing the patient's code). */
export async function startAppointment(api, appointment, code) {
  return api.post(`/appointments/${appointment.id}/start`, { code })
}

// --- the professional's strong signature -------------------------------------------------

function base32decode(text) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const char of text.replace(/=+$/, '').toUpperCase()) bits += alphabet.indexOf(char).toString(2).padStart(5, '0')
  const bytes = []
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2))
  return Buffer.from(bytes)
}

/** RFC 6238 TOTP (SHA-1, 30 s, 6 digits): the code an authenticator app would show. */
export function totp(secret, at = Date.now()) {
  const step = Math.floor(at / 1000 / 30)
  const counter = Buffer.alloc(8)
  counter.writeUInt32BE(Math.floor(step / 2 ** 32), 0)
  counter.writeUInt32BE(step >>> 0, 4)
  const digest = createHmac('sha1', base32decode(secret)).update(counter).digest()
  const offset = digest[19] & 0x0f
  const number = ((digest[offset] & 0x7f) << 24) | (digest[offset + 1] << 16) | (digest[offset + 2] << 8) | digest[offset + 3]
  return String(number % 1_000_000).padStart(6, '0')
}

/** Set up the authenticator of a professional login through the API; returns the secret. */
export async function enrollTotp(request, email) {
  const api = new Api(request, await token(request, email))
  const setup = await api.post('/auth/totp/setup', {})
  await api.post('/auth/totp/confirm', { code: totp(setup.secret) })
  return setup.secret
}

/** Draw a squiggle on the signature pad of the open dialog. */
export async function drawSignature(page, dialog) {
  const box = await dialog.locator('.signature-pad canvas').boundingBox()
  await page.mouse.move(box.x + 30, box.y + box.height / 2)
  await page.mouse.down()
  for (let i = 1; i <= 24; i++) {
    await page.mouse.move(box.x + 30 + i * 14, box.y + box.height / 2 + Math.sin(i / 2) * 30)
  }
  await page.mouse.up()
  await expect(dialog.getByTestId('signature-pad-state')).toHaveText('Signature drawn')
}

/** Complete the signature dialog: password, authenticator code, drawing (unless revoking), confirm. */
export async function completeSignature(page, { secret, password = PASSWORD, confirm, drawing = true, notes }) {
  const dialog = page.getByTestId('signature-dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('Your password').fill(password)
  await dialog.getByLabel('Authenticator code').fill(totp(secret))
  if (drawing) await drawSignature(page, dialog)
  if (notes) await dialog.getByLabel('Note (optional)').fill(notes)
  await dialog.getByRole('button', { name: confirm }).click()
  await expect(dialog).toHaveCount(0)
}
