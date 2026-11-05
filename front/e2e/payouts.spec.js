// Advances, settlements in installments and receipts: the admin pays, the
// professional signs, the clinic's receipt is acknowledged.
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
  startAppointment,
  totp,
} from './helpers'

async function signedVisit(request, admin, professional, patient, patientLogin, minutesAgo) {
  const visit = await book(admin, { customer: patient, professional, at: pastSlot(minutesAgo) })
  await startAppointment(admin, visit, await cardCode(request, patientLogin, patient))
  return visit
}

test.describe('Payouts: advances, settlements and receipts', () => {
  let ctx

  test.beforeEach(async ({ request }) => {
    const admin = await Api.asAdmin(request)
    const professional = await createProfessional(admin, { name: 'Dr Payee', price_cents: 5000 })
    const professionalLogin = await createProfessionalUser(admin, professional)
    const secret = await enrollTotp(request, professionalLogin)
    const patient = await createCoveredCustomer(admin, { name: 'Eva Patient' })
    const patientLogin = await createClientUser(admin, patient)
    // Two visits started with the card and signed by the professional (password + code + drawing): 2 x R$ 50,00 earned.
    const pro = new Api(request, (await request.post(`${process.env.E2E_API_URL || 'http://127.0.0.1:8000'}/auth/token`, { data: { email: professionalLogin, password: 'saude123' } }).then((r) => r.json())).access_token)
    // A minimal 200x80 JPEG: the signature pad's output is a JPEG data URL like this one.
    const drawing = 'data:image/jpeg;base64,' + Buffer.from('FFD8FFC00011080050' + '00C8' + '03012200021101031101FFD9', 'hex').toString('base64')
    for (const minutesAgo of [150, 90]) {
      const visit = await signedVisit(request, admin, professional, patient, patientLogin, minutesAgo)
      await pro.post(`/appointments/${visit.id}/sign`, { password: 'saude123', code: totp(secret), signature_image: drawing })
    }
    ctx = { admin, professional, professionalLogin, secret, patient }
  })

  test('the admin pays an advance and settles in installments; the professional signs the receipts', async ({ page }) => {
    await login(page, 'foo.bar@example.com')
    await page.goto(`/payouts?payee=professional:${ctx.professional.id}`)
    const account = page.getByTestId('payout-account')
    await expect(account.getByTestId('balance')).toHaveText('R$ 100,00')

    // Advance of R$ 30,00.
    await page.getByRole('button', { name: 'Register advance' }).click()
    const advanceForm = page.getByTestId('advance-form')
    await advanceForm.getByLabel('Amount (R$) *').fill('30,00')
    await advanceForm.getByLabel('Method').selectOption('pix')
    await advanceForm.getByLabel('Reference').fill('PIX 0001')
    await advanceForm.getByRole('button', { name: 'Register advance' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Advance registered' })).toBeVisible()
    await expect(account.getByTestId('balance')).toHaveText('R$ 70,00')
    const advances = page.getByTestId('advances')
    await expect(advances).toContainText('R$ 30,00')
    await expect(advances).toContainText('Awaiting signature')
    const receiptNumber = (await advances.locator('tbody tr td').first().textContent()).trim()
    expect(receiptNumber).toMatch(/^REC-\d{4}-\d{6}$/)

    // Settlement in 2 installments: earned 100 − advance 30 = 70 → 35 + 35.
    await page.getByRole('button', { name: 'Create settlement' }).click()
    const settlementForm = page.getByTestId('settlement-form')
    await settlementForm.getByLabel('Installments *').fill('2')
    await settlementForm.getByRole('button', { name: 'Create settlement' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Settlement created' })).toBeVisible()
    const settlements = page.getByTestId('settlements')
    const row = settlements.locator('tbody tr').first()
    await expect(row).toContainText('Open')
    await expect(row).toContainText('R$ 100,00')
    await expect(row).toContainText('− R$ 30,00')
    await expect(row).toContainText('R$ 70,00')
    await expect(row).toContainText('0/2')
    await expect(account.getByTestId('balance')).toHaveText('R$ 70,00')

    // Pay the first installment: a receipt is issued.
    await row.getByRole('button', { name: 'Open' }).click()
    const detail = page.getByTestId('settlement-detail')
    await expect(detail).toContainText('1 of 2')
    await expect(detail.locator('tbody tr').filter({ hasText: '1 of 2' })).toContainText('R$ 35,00')
    await detail.getByRole('button', { name: 'Register payment' }).first().click()
    const paymentForm = page.getByTestId('payment-form')
    await paymentForm.getByLabel('Method').selectOption('transfer')
    await paymentForm.getByLabel('Reference').fill('TED 42')
    await paymentForm.getByRole('button', { name: 'Register payment' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Installment paid' })).toBeVisible()
    await expect(detail.locator('tbody tr').filter({ hasText: '1 of 2' })).toContainText('Awaiting signature')
    await expect(page.getByTestId('receipts').locator('tbody tr')).toHaveCount(2)
    await expect(page.getByTestId('receipts-to-confirm')).toContainText('2')

    // The professional signs both receipts from its own area.
    await page.getByRole('button', { name: 'Sign out' }).click()
    await page.waitForURL((url) => url.pathname === '/login')
    await login(page, ctx.professionalLogin)
    await expect(page.locator('.kpi').filter({ hasText: 'Receipts to sign' })).toContainText('2')
    await page.goto('/my-payouts')
    const mine = page.getByTestId('payout-account')
    await expect(mine.getByTestId('balance')).toHaveText('R$ 35,00') // the unpaid installment
    const receipts = page.getByTestId('receipts')
    await expect(receipts.getByRole('button', { name: 'Sign' })).toHaveCount(2)
    await receipts.getByRole('button', { name: 'Sign' }).first().click()
    await completeSignature(page, { secret: ctx.secret, confirm: 'Sign receipt' })
    await expect(page.getByRole('status').filter({ hasText: 'signed' })).toBeVisible()
    await expect(receipts.getByRole('button', { name: 'Sign' })).toHaveCount(1)
    await expect(receipts.locator('.badge.signed')).toHaveCount(1)

    // The digital receipt page: sign the other one there and download the PDF.
    await receipts.getByRole('link', { name: 'Receipt' }).last().click()
    await expect(page.getByTestId('receipt-confirmation')).toContainText('Not confirmed yet')
    await page.getByRole('button', { name: /Sign receipt — I received/ }).click()
    await completeSignature(page, { secret: ctx.secret, confirm: 'Sign receipt' })
    await expect(page.getByTestId('receipt-confirmation')).toContainText('Digitally signed by Dr Payee')
    await expect(page.getByTestId('receipt-signature-image')).toBeVisible()
    const download = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Download PDF' }).click()
    const file = await download
    expect(file.suggestedFilename()).toMatch(/^receipt_REC-\d{4}-\d{6}\.pdf$/)
    const path = await file.path()
    const { readFileSync } = await import('node:fs')
    const bytes = readFileSync(path)
    expect(bytes.subarray(0, 5).toString()).toBe('%PDF-')
    expect(bytes.toString('latin1')).toContain('Digitally signed by')
    expect(bytes.toString('latin1')).toContain('/DCTDecode') // the drawn signature is printed
    await page.goto('/my-payouts')
    await expect(page.getByTestId('receipts-to-confirm')).toContainText('0')
  })

  test("a clinic's receipt is acknowledged by the staff with who received the money", async ({ page }) => {
    await login(page, 'foo.bar@example.com')
    await page.goto('/payouts')
    await page.getByLabel('Payee').selectOption({ label: 'Clinic Foo — Sample/SP' })
    const account = page.getByTestId('payout-account')
    await account.getByTestId('balance').waitFor()
    await page.getByRole('button', { name: 'Register advance' }).click()
    const advanceForm = page.getByTestId('advance-form')
    await advanceForm.getByLabel('Amount (R$) *').fill('12,50')
    await advanceForm.getByLabel('Method').selectOption('cash')
    await advanceForm.getByRole('button', { name: 'Register advance' }).click()
    const advances = page.getByTestId('advances')
    const row = advances.locator('tbody tr').filter({ hasText: 'R$ 12,50' }).first()
    await expect(row).toContainText('Awaiting signature')
    await expect(row.getByRole('button', { name: 'Sign' })).toHaveCount(0) // nobody signs for a clinic
    await row.getByRole('button', { name: 'Acknowledge' }).click()
    const ackForm = page.getByTestId('ack-form')
    await ackForm.getByLabel('Received by *').fill('Maria Manager')
    await ackForm.getByLabel('Document').fill('123.456.789-00')
    await ackForm.getByRole('button', { name: 'Acknowledge' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Receipt acknowledged' })).toBeVisible()
    await expect(advances.locator('tbody tr').filter({ hasText: 'R$ 12,50' }).first()).toContainText('Acknowledged')
  })

  test('supervisors cannot open the payouts page', async ({ page }) => {
    await login(page, 'supervisor@example.com')
    await page.goto('/payouts')
    await page.waitForURL((url) => url.pathname === '/')
  })
})
