// The patient's virtual card and the start of the appointment with its code.
import { expect, test } from '@playwright/test'
import {
  Api,
  book,
  cardCode,
  createClientUser,
  createCoveredCustomer,
  createProfessional,
  createProfessionalUser,
  completeSignature,
  enrollTotp,
  login,
  pastSlot,
} from './helpers'

test.describe('Virtual card', () => {
  let ctx

  test.beforeEach(async ({ request }) => {
    const admin = await Api.asAdmin(request)
    const professional = await createProfessional(admin, { name: 'Dr Card' })
    const professionalLogin = await createProfessionalUser(admin, professional)
    const secret = await enrollTotp(request, professionalLogin)
    const patient = await createCoveredCustomer(admin, { name: 'Dana Patient' })
    const child = await admin.post(`/customers/${patient.id}/dependents`, { name: 'Dana Child', birth_date: '2018-02-20' })
    const patientLogin = await createClientUser(admin, patient)
    const visit = await book(admin, { customer: patient, professional, at: pastSlot(60) })
    ctx = { admin, professional, professionalLogin, secret, patient, child, patientLogin, visit, request }
  })

  test('the patient sees a rotating 6-digit code, for itself and for each dependent', async ({ page }) => {
    await login(page, ctx.patientLogin)
    await page.getByRole('link', { name: 'My virtual card' }).click()
    await expect(page.getByRole('heading', { name: 'My virtual card' })).toBeVisible()
    const card = page.getByTestId('virtual-card')
    await expect(card).toContainText('Dana Patient')
    await expect(card).toContainText(`SS ${String(ctx.patient.id).padStart(6, '0')} 00`)
    await expect(card).toContainText('Active')
    const shown = (await page.getByTestId('card-code').textContent()).replace(/\s/g, '')
    expect(shown).toMatch(/^\d{6}$/)
    // It is the code the API gives the patient (unless the period rolled over between the two reads).
    const viaApi = await cardCode(ctx.request, ctx.patientLogin, ctx.patient)
    const shownAgain = (await page.getByTestId('card-code').textContent()).replace(/\s/g, '')
    expect([shown, shownAgain]).toContain(viaApi)
    // The countdown runs.
    const first = Number((await page.getByTestId('card-remaining').textContent()).match(/\d+/)[0])
    await page.waitForTimeout(2100)
    const later = Number((await page.getByTestId('card-remaining').textContent()).match(/\d+/)[0])
    expect(later === first - 2 || later > first).toBeTruthy() // decreased, or a new code started
    // The dependent has its own card.
    await page.getByLabel('Card of').selectOption({ label: 'Dana Child' })
    await expect(page.getByTestId('card-patient')).toHaveText('Dana Child')
    await expect(card).toContainText(`SS ${String(ctx.patient.id).padStart(6, '0')} ${String(ctx.child.id).padStart(2, '0')}`)
  })

  test('the staff cannot see the card; the reception starts the visit with the code', async ({ page }) => {
    await login(page, 'attendant@example.com')
    const forbidden = await ctx.admin.request.get(`${process.env.E2E_API_URL || 'http://127.0.0.1:8000'}/customers/${ctx.patient.id}/card`, {
      headers: { Authorization: `Bearer ${ctx.admin.bearer}` },
    })
    expect(forbidden.status()).toBe(403)

    await page.goto(`/appointments/${ctx.visit.id}`)
    const panel = page.getByTestId('start-panel')
    await expect(panel).toContainText('Not started')
    const input = panel.getByLabel("Code on the patient's virtual card")
    // A wrong code is refused and counted.
    const real = await cardCode(ctx.request, ctx.patientLogin, ctx.patient)
    await input.fill(real === '000000' ? '111111' : '000000')
    await panel.getByRole('button', { name: 'Verify card & start' }).click()
    await expect(panel).toContainText('Wrong card code')
    await expect(panel).toContainText('Not started')
    // The right code (typed with a space, as on the card) starts the visit.
    const code = await cardCode(ctx.request, ctx.patientLogin, ctx.patient)
    await input.fill(`${code.slice(0, 3)} ${code.slice(3)}`)
    await panel.getByRole('button', { name: 'Verify card & start' }).click()
    await expect(panel).toContainText('Patient verified')
    await expect(panel).toContainText('by Att Endant')
    await expect(page.locator('.timeline li').filter({ hasText: 'Start (card verified)' })).toHaveClass(/done/)
    await expect(page.locator('.panel.attendance .badge')).toContainText('In progress')
    await expect(page.getByTestId('signature-panel')).toContainText('Awaiting signature')
    await expect(page.getByTestId('signature-needs-start')).toHaveCount(0)
  })

  test('the professional starts the visit with the code and only then can sign', async ({ page }) => {
    await login(page, ctx.professionalLogin)
    await page.goto(`/appointments/${ctx.visit.id}`)
    await expect(page.getByTestId('signature-needs-start')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Sign appointment' })).toHaveCount(0)
    const panel = page.getByTestId('start-panel')
    await panel.getByLabel("Code on the patient's virtual card").fill(await cardCode(ctx.request, ctx.patientLogin, ctx.patient))
    await panel.getByRole('button', { name: 'Verify card & start' }).click()
    await expect(panel).toContainText('Patient verified')
    await expect(panel.getByRole('button', { name: 'Undo start' })).toHaveCount(0) // only the staff undoes
    await page.getByRole('button', { name: 'Sign appointment' }).click()
    await completeSignature(page, { secret: ctx.secret, confirm: 'Sign appointment' })
    await expect(page.getByTestId('signature-panel')).toContainText('Signed by Dr Card')
    await expect(page.locator('.badge.realizada')).toContainText('Completed')
  })

  test('the patient is told when a code is used, and can issue a new card', async ({ page }) => {
    const code = await cardCode(ctx.request, ctx.patientLogin, ctx.patient)
    await ctx.admin.post(`/appointments/${ctx.visit.id}/start`, { code })
    await login(page, ctx.patientLogin)
    await page.goto('/notifications')
    await expect(page.getByText(/Appointment started at \d\d:\d\d — card verified/).first()).toBeVisible()
    await page.goto('/my-card')
    const before = (await page.getByTestId('card-code').textContent()).replace(/\s/g, '')
    page.once('dialog', (dialog) => dialog.accept())
    await page.getByRole('button', { name: 'Issue a new card' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'New card issued' })).toBeVisible()
    await expect(page.getByTestId('card-code')).toBeVisible()
    const after = (await page.getByTestId('card-code').textContent()).replace(/\s/g, '')
    expect(after).toMatch(/^\d{6}$/)
    if (after === before) test.info().annotations.push({ type: 'note', description: 'codes coincided (1 in a million)' })
  })
})
