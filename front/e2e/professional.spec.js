// The professional's own area: agenda, signature and payouts.
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
  formatDateTime,
  futureSlot,
  login,
  pastSlot,
  startAppointment,
} from './helpers'

test.describe('Professional role', () => {
  let ctx

  test.beforeEach(async ({ request }) => {
    const admin = await Api.asAdmin(request)
    const professional = await createProfessional(admin, { price_cents: 5000 })
    const other = await createProfessional(admin, { name: 'Someone Else' })
    const email = await createProfessionalUser(admin, professional)
    const secret = await enrollTotp(request, email)
    const patient = await createCoveredCustomer(admin, { name: 'Ana Patient' })
    const patientLogin = await createClientUser(admin, patient)
    const stranger = await createCoveredCustomer(admin, { name: 'Bob Stranger' })
    let done = await book(admin, { customer: patient, professional, at: pastSlot(90) })
    // The visit was started with the patient's card (the precondition of the signature).
    done = await startAppointment(admin, done, await cardCode(request, patientLogin, patient))
    const upcoming = await book(admin, { customer: patient, professional, at: futureSlot(180) })
    await book(admin, { customer: stranger, professional: other, at: pastSlot(90) }) // not mine
    ctx = { admin, professional, email, secret, patient, patientLogin, done, upcoming }
  })

  test('sees only its own area, never the staff pages', async ({ page }) => {
    await login(page, ctx.email)
    await expect(page.getByRole('heading', { name: `Welcome, ${ctx.professional.name}` })).toBeVisible()
    const nav = page.locator('nav, .sidebar, [class*="sidebar"]').first()
    await expect(nav.getByRole('link', { name: 'My appointments' })).toBeVisible()
    await expect(nav.getByRole('link', { name: 'My payouts' })).toBeVisible()
    await expect(nav.getByRole('link', { name: 'Customers' })).toHaveCount(0)
    await expect(nav.getByRole('link', { name: 'Reports' })).toHaveCount(0)
    await expect(page.getByText('Awaiting my signature')).toBeVisible()
    await expect(page.getByTestId('to-sign')).toContainText('Ana Patient')
    // Guarded routes send the professional home.
    for (const path of ['/customers', '/reports', '/appointments/new', '/users', '/subscriptions']) {
      await page.goto(path)
      await page.waitForURL((url) => url.pathname === '/')
    }
  })

  test('lists its own appointments with the signature state', async ({ page }) => {
    await login(page, ctx.email)
    await page.goto('/appointments')
    await expect(page.getByRole('heading', { name: 'My appointments' })).toBeVisible()
    const rows = page.locator('table tbody tr')
    await expect(rows).toHaveCount(2)
    await expect(page.getByText('Bob Stranger')).toHaveCount(0)
    const past = rows.filter({ hasText: formatDateTime(pastSlot(90)) })
    await expect(past).toContainText('Awaiting signature')
    await expect(past.getByRole('button', { name: 'Sign' })).toBeVisible()
    const future = rows.filter({ hasText: formatDateTime(futureSlot(180)) })
    await expect(future).toContainText('Not signed')
    await expect(future.getByRole('button', { name: 'Sign' })).toHaveCount(0)
    await expect(page.getByText('R$ 50,00 when signed').first()).toBeVisible()
    // The filter isolates what is waiting for the signature.
    await page.getByLabel('Status filter').selectOption('awaiting_signature')
    await expect(rows).toHaveCount(1)
    await expect(page.getByText('1 records')).toBeVisible()
  })

  test('signs an appointment, sees it paid, and can revoke', async ({ page }) => {
    await login(page, ctx.email)
    await page.goto(`/appointments/${ctx.done.id}`)
    const panel = page.getByTestId('signature-panel')
    await expect(panel).toContainText('Awaiting signature')
    await expect(page.getByText('paid once signed')).toBeVisible()
    await panel.getByRole('button', { name: 'Sign appointment' }).click()
    await completeSignature(page, { secret: ctx.secret, confirm: 'Sign appointment', notes: 'Routine follow-up' })
    await expect(page.getByRole('status').filter({ hasText: 'Appointment signed' })).toBeVisible()
    await expect(panel).toContainText('Signed by')
    await expect(panel).toContainText(ctx.professional.name)
    await expect(panel.locator('img.signature-image')).toBeVisible() // the drawing is kept
    await expect(panel.getByRole('button', { name: 'Revoke signature' })).toBeVisible()
    await expect(page.getByText('[signature] Routine follow-up')).toBeVisible()
    await expect(page.locator('.badge.realizada')).toContainText('Completed')

    // The payout now carries the fee of the signed appointment.
    await page.goto('/my-payouts')
    await expect(page.getByTestId('payout-total')).toHaveText('R$ 50,00')
    const statement = page.locator('table').last()
    await expect(statement).toContainText('Ana Patient')
    await expect(statement.locator('tbody tr').filter({ hasText: 'Completed' })).toContainText('R$ 50,00')
    // The upcoming appointment is "not due" (it falls outside the range when the test runs close to midnight).
    const scheduled = statement.locator('tbody tr').filter({ hasText: 'Scheduled' })
    if (await scheduled.count()) await expect(scheduled).toContainText('not due')

    // Revoking (password + code again) returns it to scheduled and drops it from the payout.
    await page.goto(`/appointments/${ctx.done.id}`)
    await panel.getByRole('button', { name: 'Revoke signature' }).click()
    await completeSignature(page, { secret: ctx.secret, confirm: 'Revoke signature', drawing: false })
    await expect(panel).toContainText('Awaiting signature')
    await page.goto('/my-payouts')
    await expect(page.getByTestId('payout-total')).toHaveText('R$ 0,00')
  })

  test('cannot sign before the visit', async ({ page }) => {
    await login(page, ctx.email)
    await page.goto(`/appointments/${ctx.upcoming.id}`)
    const panel = page.getByTestId('signature-panel')
    await expect(panel).toContainText('Not signed')
    await expect(panel.getByRole('button', { name: 'Sign appointment' })).toHaveCount(0)
  })

  test('signs straight from the dashboard', async ({ page }) => {
    await login(page, ctx.email)
    const card = page.getByTestId('to-sign')
    await expect(card).toContainText('Ana Patient')
    await card.getByRole('button', { name: 'Sign' }).click()
    await completeSignature(page, { secret: ctx.secret, confirm: 'Sign appointment' })
    await expect(card).toContainText('Nothing awaiting your signature')
    const month = new Date().toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })
    await expect(page.locator('.kpi').filter({ hasText: `Signed in ${month}` })).toContainText('1')
    await expect(page.locator('.kpi').filter({ hasText: 'Balance to receive' })).toContainText('R$ 50,00')
  })
})
