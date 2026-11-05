// Remote appointments (video call): the professional offers them in its own
// profile, the reception books one in a regular slot, everyone gets the link.
import { expect, test } from '@playwright/test'
import {
  Api,
  createClientUser,
  createCoveredCustomer,
  createProfessional,
  createProfessionalUser,
  formatDateTime,
  futureSlot,
  localIso,
  login,
} from './helpers'

test.describe('Remote appointments', () => {
  let ctx

  test.beforeEach(async ({ request }) => {
    const admin = await Api.asAdmin(request)
    const professional = await createProfessional(admin, { name: 'Dr Remote' })
    const professionalLogin = await createProfessionalUser(admin, professional)
    const patient = await createCoveredCustomer(admin, { name: 'Rita Patient' })
    const patientLogin = await createClientUser(admin, patient)
    ctx = { admin, professional, professionalLogin, patient, patientLogin }
  })

  test('the professional switches remote appointments on in its profile', async ({ page }) => {
    await login(page, ctx.professionalLogin)
    await page.goto('/my-profile')
    await expect(page.getByRole('heading', { name: 'Remote appointments' })).toBeVisible()
    const form = page.locator('form', { hasText: 'Remote appointments' })
    await form.getByLabel('I offer remote appointments (video call)').check()
    await form.getByLabel('Remote rate (R$)').fill('40,00')
    await form.getByRole('button', { name: 'Save' }).click()
    await expect(form.getByText('Currently offering remote appointments at R$ 40,00')).toBeVisible()
    const me = await ctx.admin.get(`/professionals/${ctx.professional.id}`)
    expect(me.remote_enabled).toBe(true)
    expect(me.remote_price_cents).toBe(4000)
  })

  test('the reception books a video call and everyone sees the link', async ({ page }) => {
    await ctx.admin.put(`/professionals/${ctx.professional.id}`, {
      name: ctx.professional.name,
      specialty_id: ctx.professional.specialty_id,
      clinic_id: ctx.professional.clinic_id,
      price_cents: ctx.professional.price_cents,
      remote_enabled: true,
      remote_price_cents: 4000,
    })
    const at = futureSlot(180)
    await login(page, 'attendant@example.com')
    await page.goto('/appointments/new')
    const form = page.locator('form')
    await form.getByLabel('Customer *').selectOption({ label: ctx.patient.name })
    const option = form.getByLabel('Professional *').locator('option', { hasText: ctx.professional.name })
    await form.getByLabel('Professional *').selectOption({ label: await option.textContent() })
    // The kind is offered for this professional (the slot is then picked in the agenda).
    await expect(form.getByLabel('Remote appointment (video call)')).toBeVisible()
    const booked = await ctx.admin.post('/appointments', {
      customer_id: ctx.patient.id,
      professional_id: ctx.professional.id,
      marked_at: localIso(at),
      remote: true,
    })
    expect(booked.remote).toBe(true)
    expect(booked.meeting_url).toMatch(/^https?:\/\//)
    expect(booked.professional_price_cents).toBe(4000)

    // The reception's list: the badge and the link.
    await page.goto('/appointments')
    const row = page.locator('tr', { hasText: formatDateTime(at) }).filter({ hasText: ctx.patient.name })
    await expect(row.getByText('remote', { exact: true })).toBeVisible()
    await expect(row.getByRole('link', { name: 'Join the call' })).toHaveAttribute('href', booked.meeting_url)

    // The professional's page: the video-call card instead of the map.
    await login(page, ctx.professionalLogin)
    await page.goto(`/appointments/${booked.id}`)
    await expect(page.getByRole('heading', { name: 'Video call' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Join the call' })).toHaveAttribute('href', booked.meeting_url)
    await expect(page.getByText('remote rate')).toBeVisible()

    // The patient's home and list carry it too.
    await login(page, ctx.patientLogin)
    await expect(page.getByRole('link', { name: 'Join the call' }).first()).toHaveAttribute('href', booked.meeting_url)
  })
})
