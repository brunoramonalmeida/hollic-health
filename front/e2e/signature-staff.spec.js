// The staff side of the signature: nobody types "completed", the check-out
// leaves the appointment awaiting signature, and the signature is visible.
import { expect, test } from '@playwright/test'
import {
  API_URL,
  Api,
  book,
  cardCode,
  createClientUser,
  createCoveredCustomer,
  createProfessional,
  createProfessionalUser,
  enrollTotp,
  login,
  pastSlot,
  startAppointment,
  token,
  totp,
} from './helpers'

test.describe('Signature as seen by the staff', () => {
  let ctx

  test.beforeEach(async ({ request }) => {
    const admin = await Api.asAdmin(request)
    const professional = await createProfessional(admin, { name: 'Dr Signer' })
    const email = await createProfessionalUser(admin, professional)
    const secret = await enrollTotp(request, email)
    const patient = await createCoveredCustomer(admin, { name: 'Carla Patient' })
    const patientLogin = await createClientUser(admin, patient)
    const visit = await book(admin, { customer: patient, professional, at: pastSlot(60) })
    ctx = { admin, professional, email, secret, patient, patientLogin, visit, request }
  })

  test('the edit form never offers "completed" and shows the signature state', async ({ page }) => {
    await login(page, 'foo.bar@example.com')
    await page.goto(`/appointments/${ctx.visit.id}`)
    const status = page.getByLabel('Status')
    await expect(status).toHaveValue('agendada')
    await expect(status.locator('option[value="realizada"]')).toHaveAttribute('disabled', '')
    await expect(page.getByText("An appointment is completed by the professional's signature after the visit.")).toBeVisible()
    await expect(page.getByTestId('signature-panel')).toContainText('Awaiting signature')

    // The visit is started with the patient's card, the professional signs (through the API);
    // the staff sees it, and the status is now completed and frozen.
    await startAppointment(ctx.admin, ctx.visit, await cardCode(ctx.request, ctx.patientLogin, ctx.patient))
    const bearer = await token(ctx.request, ctx.email)
    const drawing = 'data:image/jpeg;base64,' + Buffer.from('FFD8FFC00011080050' + '00C8' + '03012200021101031101FFD9', 'hex').toString('base64')
    const signed = await ctx.request.post(`${API_URL}/appointments/${ctx.visit.id}/sign`, {
      headers: { Authorization: `Bearer ${bearer}` },
      data: { password: 'saude123', code: totp(ctx.secret), signature_image: drawing },
    })
    expect(signed.ok(), await signed.text()).toBeTruthy()
    await page.reload()
    await expect(page.getByTestId('signature-panel')).toContainText('Signed by Dr Signer')
    await expect(page.getByLabel('Status')).toHaveValue('realizada')
    await expect(page.getByText('Signed by the professional: only the notes can change, or the appointment can be cancelled.')).toBeVisible()
    await expect(page.locator('.timeline li').filter({ hasText: 'Signature' })).toHaveClass(/done/)
  })

  test('the reception check-out leaves the appointment awaiting signature', async ({ page }) => {
    await login(page, 'attendant@example.com')
    await page.goto('/appointments')
    const row = page.locator('table tbody tr').filter({ hasText: 'Carla Patient' }).first()
    await row.getByRole('button', { name: 'Check in' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Check-in confirmed' })).toBeVisible()
    await row.getByRole('button', { name: 'Check out' }).click()
    await expect(page.getByRole('status').filter({ hasText: "awaiting the professional's signature" })).toBeVisible()
    await expect(row.locator('.badge.agendada')).toContainText('Scheduled')
    await expect(row).toContainText('Awaiting signature')
    await expect(row.getByRole('button', { name: 'Sign' })).toHaveCount(0) // the attendant cannot sign
    // Without the start (card verified) the professional cannot sign either.
    await page.goto(`/appointments/${ctx.visit.id}`)
    await expect(page.getByTestId('signature-needs-start')).toBeVisible()
    // The dashboard counts it for the staff.
    await page.goto('/')
    const kpi = page.locator('.kpi').filter({ hasText: 'Awaiting signature' })
    await expect(kpi).toBeVisible()
    expect(Number((await kpi.locator('strong').textContent()).trim())).toBeGreaterThanOrEqual(1)
  })
})
