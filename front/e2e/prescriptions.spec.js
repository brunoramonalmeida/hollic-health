// Medical prescriptions attached to appointments: the professional attaches
// a PDF and signs it with its (demo) ICP-Brasil certificate, the patient
// downloads the signed prescription through the signed link, an image is
// refused, a pharmacy verifies the file and its .p7s without an account.
import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import {
  Api,
  DEMO_CERTIFICATE,
  book,
  createClientUser,
  createCoveredCustomer,
  createProfessional,
  createProfessionalUser,
  login,
  pastSlot,
} from './helpers'

const PDF = Buffer.from('%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n')
const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(32)])

test.describe('Prescriptions', () => {
  let ctx

  test.beforeEach(async ({ request }) => {
    const admin = await Api.asAdmin(request)
    const professional = await createProfessional(admin, { name: 'Dr Script', document: DEMO_CERTIFICATE.cpf })
    const professionalLogin = await createProfessionalUser(admin, professional)
    const patient = await createCoveredCustomer(admin, { name: 'Paula Patient' })
    const patientLogin = await createClientUser(admin, patient)
    const visit = await book(admin, { customer: patient, professional, at: pastSlot(60) })
    ctx = { admin, professional, professionalLogin, patient, patientLogin, visit, request }
  })

  test('the professional attaches and signs a PDF; the patient downloads it; an image is refused', async ({ page }) => {
    await login(page, ctx.professionalLogin)
    await page.goto(`/appointments/${ctx.visit.id}`)
    const panel = page.getByTestId('prescriptions-panel')
    await expect(panel).toContainText('No prescription attached')
    await panel.getByLabel('Note (optional)').fill('Antibiotic, 7 days')
    await panel.locator('input[type="file"]').setInputFiles({ name: 'receita.pdf', mimeType: 'application/pdf', buffer: PDF })
    await expect(panel.getByRole('link', { name: 'receita.pdf' })).toBeVisible()
    await expect(panel).toContainText('1 of 5')
    await expect(panel).toContainText('Antibiotic, 7 days')
    await expect(panel.getByText('Not signed')).toBeVisible()
    // The content decides: a PNG named .pdf is refused by the API.
    await panel.locator('input[type="file"]').first().setInputFiles({ name: 'photo.pdf', mimeType: 'application/pdf', buffer: PNG })
    await expect(panel.getByText('only text documents are accepted')).toBeVisible()
    await expect(panel).toContainText('1 of 5')

    // The patient sees nothing until the prescription is signed.
    await login(page, ctx.patientLogin)
    await page.goto(`/appointments/${ctx.visit.id}`)
    await expect(page.getByTestId('prescriptions-panel')).toContainText('No signed prescription yet')

    // The professional signs with its certificate: a wrong password is refused, the right one signs.
    await login(page, ctx.professionalLogin)
    await page.goto(`/appointments/${ctx.visit.id}`)
    await panel.getByRole('button', { name: 'Sign' }).click()
    const dialog = page.getByTestId('sign-prescription-dialog')
    await dialog.getByLabel('Certificate file (.pfx / .p12)').setInputFiles(DEMO_CERTIFICATE.path)
    await dialog.getByLabel('Certificate password').fill('wrong')
    await dialog.getByRole('button', { name: 'Sign' }).click()
    await expect(dialog.getByText('wrong password')).toBeVisible()
    await dialog.getByLabel('Certificate password').fill(DEMO_CERTIFICATE.password)
    await dialog.getByRole('button', { name: 'Sign' }).click()
    await expect(panel.getByText('Signed · ICP-Brasil')).toBeVisible()
    await expect(panel).toContainText(`CPF ${DEMO_CERTIFICATE.cpf}`)
    await panel.getByRole('button', { name: 'Verify' }).click()
    await expect(panel.getByTestId('signature-report')).toContainText('Valid signature')

    // The patient reads it now: the listing carries signed links that work without the login.
    await login(page, ctx.patientLogin)
    await page.goto(`/appointments/${ctx.visit.id}`)
    const mine = page.getByTestId('prescriptions-panel')
    const link = mine.getByRole('link', { name: 'receita.pdf' })
    await expect(link).toBeVisible()
    await expect(mine.getByRole('button', { name: 'Remove' })).toHaveCount(0)
    await expect(mine.getByRole('button', { name: 'Attach prescription' })).toHaveCount(0)
    await expect(mine.getByRole('button', { name: 'Sign' })).toHaveCount(0)
    const href = await link.getAttribute('href')
    const response = await ctx.request.get(href)
    expect(response.status()).toBe(200)
    expect(response.headers()['content-type']).toBe('application/pdf')
    expect((await response.body()).equals(PDF)).toBeTruthy()
    const p7s = await ctx.request.get(await mine.getByRole('link', { name: 'signature (.p7s)' }).getAttribute('href'))
    expect(p7s.status()).toBe(200)

    // A pharmacy verifies both files without an account.
    await page.goto('/verify-prescription')
    await page.getByLabel('Prescription file').setInputFiles({ name: 'receita.pdf', mimeType: 'application/pdf', buffer: PDF })
    await page.getByLabel('Signature file (.p7s)').setInputFiles({ name: 'receita.p7s', mimeType: 'application/pkcs7-signature', buffer: await p7s.body() })
    await page.getByRole('button', { name: 'Verify' }).click()
    await expect(page.getByTestId('signature-report')).toContainText('Valid signature')
    await expect(page.getByTestId('professional-match')).toContainText('Dr Script')
    // The same signature on another file is invalid.
    await page.getByLabel('Prescription file').setInputFiles({ name: 'other.pdf', mimeType: 'application/pdf', buffer: Buffer.concat([PDF, Buffer.from('x')]) })
    await page.getByRole('button', { name: 'Verify' }).click()
    await expect(page.getByTestId('signature-report')).toContainText('Invalid signature')
  })

  test('cancelling the appointment deletes the files', async ({ page }) => {
    const listing = await ctx.admin.get(`/appointments/${ctx.visit.id}/prescriptions`)
    expect(listing.items).toHaveLength(0)
    await ctx.admin.request.fetch(`${process.env.E2E_API_URL || 'http://127.0.0.1:8000'}/appointments/${ctx.visit.id}/prescriptions`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${ctx.admin.bearer}` },
      multipart: { file: { name: 'receita.pdf', mimeType: 'application/pdf', buffer: PDF } },
    })
    await login(page, 'attendant@example.com')
    await page.goto('/appointments')
    const row = page.locator('tr', { hasText: ctx.patient.name }).filter({ hasText: 'Dr Script' })
    await expect(row.getByText('⎘ 1')).toBeVisible()
    page.once('dialog', (dialog) => dialog.accept())
    await row.getByRole('button', { name: 'Cancel' }).click()
    await expect(row.getByText('Cancelled', { exact: true }).first()).toBeVisible()
    const after = await ctx.admin.get(`/appointments/${ctx.visit.id}/prescriptions`)
    expect(after.items).toHaveLength(0)
  })
})
