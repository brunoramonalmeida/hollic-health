// The professional's "My agenda": the now / next panel, the working week
// as a grid (default) and the month view.
import { expect, test } from '@playwright/test'
import {
  Api,
  book,
  cardCode,
  createClientUser,
  createCoveredCustomer,
  createProfessional,
  createProfessionalUser,
  futureSlot,
  login,
  pastSlot,
  startAppointment,
} from './helpers'

test.describe('My agenda', () => {
  let ctx

  test.beforeEach(async ({ request }) => {
    const admin = await Api.asAdmin(request)
    // Weekdays only, so the grid shows the working week.
    const professional = await admin.post('/professionals', {
      name: 'Dr Agenda',
      specialty_id: 1,
      clinic_id: 1,
      price_cents: 5000,
      working_hours: Array.from({ length: 7 }, (_, weekday) => ({ weekday, start: '00:00', end: '23:30' })),
    })
    const email = await createProfessionalUser(admin, professional)
    const patient = await createCoveredCustomer(admin, { name: 'Ana Patient' })
    const patientLogin = await createClientUser(admin, patient)
    const other = await createCoveredCustomer(admin, { name: 'Bob Patient' })
    // A visit under way (started with the card 20 minutes ago) and the next one in 3 hours.
    let running = await book(admin, { customer: patient, professional, at: pastSlot(20) })
    running = await startAppointment(admin, running, await cardCode(request, patientLogin, patient))
    const upcoming = await book(admin, { customer: other, professional, at: futureSlot(180) })
    ctx = { admin, professional, email, patient, other, running, upcoming }
  })

  test('shows the visit under way, the next one and the week grid; the month view too', async ({ page }) => {
    await login(page, ctx.email)
    await page.getByRole('link', { name: 'My agenda' }).click()
    await expect(page.getByRole('heading', { name: 'My agenda' })).toBeVisible()
    const now = page.getByTestId('now-panel')
    await expect(now).toContainText('Now')
    await expect(now).toContainText('Ana Patient')
    await expect(now).toContainText('Next')
    await expect(now).toContainText('Bob Patient')
    await expect(now).toContainText('in 2 h')
    // The week grid: both appointments as cards, the current one tagged.
    const week = page.getByTestId('agenda-week')
    await expect(week).toBeVisible()
    await expect(week.getByRole('link', { name: /Ana Patient/ })).toBeVisible()
    await expect(week.getByRole('link', { name: /Bob Patient/ })).toBeVisible()
    await expect(week.getByText('now', { exact: true })).toBeVisible()
    await expect(week.getByText('next', { exact: true })).toBeVisible()
    // Opening a card leads to the appointment page.
    await week.getByRole('link', { name: /Bob Patient/ }).click()
    await expect(page.getByRole('heading', { name: /Appointment of/ })).toBeVisible()
    await page.goBack()
    // Month view: the day carries both appointments; a click returns to that week.
    await page.getByRole('tab', { name: 'Month' }).click()
    const month = page.getByTestId('agenda-month')
    await expect(month).toBeVisible()
    await expect(month.locator('.agenda-month-day.today')).toContainText('Ana Patient')
    await expect(month.locator('.agenda-month-day.today .count')).toHaveText('2')
    await month.locator('.agenda-month-day.today').click()
    await expect(page.getByTestId('agenda-week')).toBeVisible()
    // Navigation: next week has nothing of ours, "Today" comes back.
    await page.getByRole('button', { name: 'Next ›' }).click()
    await expect(page.getByTestId('agenda-week').getByRole('link', { name: /Ana Patient/ })).toHaveCount(0)
    await page.getByRole('button', { name: 'Today' }).click()
    await expect(page.getByTestId('agenda-week').getByRole('link', { name: /Ana Patient/ })).toBeVisible()
  })

  test('is the professional\'s own page', async ({ page }) => {
    await login(page, 'attendant@example.com')
    await page.goto('/my-agenda')
    await page.waitForURL((url) => url.pathname === '/')
  })
})
