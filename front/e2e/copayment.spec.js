// Co-payment of corporate plans: the company agrees a split with its
// employee in the portal and changes it later; the staff registers the
// company's part on its invoice; the employee checks its own part in
// "My payments"; the company's statement puts both sides side by side.
import { expect, test } from '@playwright/test'
import { Api, PASSWORD, createClientUser, isoDate, login, unique } from './helpers'

async function scenario(admin) {
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), 1)
  const plan = await admin.post('/modalities', { name: unique('Plan'), plan_value_cents: 10000, dependent_limit: 2, grace_days: 0 })
  const partner = await admin.post('/partners', { name: unique('Umbrella'), email: 'rh@umbrella.example.com' })
  const agreement = await admin.post('/agreements', {
    partner_id: partner.id,
    name: `${partner.name} co-pay`,
    starts_on: isoDate(start),
    min_employees: 1,
    payer: 'shared',
    company_share_percent: 60,
    plans: [{ modality_id: plan.id, discount_percent: 20, grace_days: 0 }],
  })
  const email = `${unique('hr').toLowerCase()}@umbrella.example.com`
  await admin.post('/users', { name: 'HR', email, role: 'partner', password: PASSWORD, partner_id: partner.id })
  const month = now.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
  return { plan, partner, agreement, email, month, period: isoDate(now).slice(0, 7) }
}

test.describe('Co-payment', () => {
  test('the company and the employee split the fees; each checks what it pays', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const { plan, agreement, email, month, period } = await scenario(admin)
    const employee = unique('Daniela')
    const answers = []
    page.on('dialog', (dialog) => dialog.accept(answers.length ? answers.shift() : dialog.defaultValue()))

    // The company enrolls the employee with the split they agreed: 70% by the company.
    await login(page, email)
    await page.goto(`/my-employees/new?agreement=${agreement.id}`)
    await page.getByLabel('Plan *').selectOption(String(plan.id))
    await page.getByLabel("Company's part of the fees (%)").fill('70')
    await expect(page.getByTestId('enroll-split')).toContainText('the company R$ 56,00 · the employee R$ 24,00')
    await page.getByLabel('Full name *').fill(employee)
    await page.getByLabel('CPF *').fill(String(Date.now()).slice(-11).padStart(11, '5'))
    await page.getByRole('button', { name: 'Enroll' }).click()
    await page.waitForURL(/\/my-employees\?agreement=\d+/)
    const row = page.locator('table tbody tr').filter({ hasText: employee })
    await expect(row.getByTestId('employee-split')).toContainText('company 70% R$ 56,00 · employee R$ 24,00')

    // They agree another split, from today on: the company now pays half.
    answers.push('50')
    await row.getByRole('button', { name: 'Change split' }).click()
    await expect(row.getByTestId('employee-split')).toContainText('company 50% R$ 40,00 · employee R$ 40,00')
    await page.getByRole('button', { name: 'Sign out' }).click()

    // The company pays its part of the month's invoice (the staff registers it).
    await admin.post(`/agreements/${agreement.id}/invoices/${period}/pay`, { paid_on: isoDate(new Date()) })
    const enrolled = (await admin.get(`/subscriptions?agreement_id=${agreement.id}`))[0]

    // The employee checks its own part: the company's is paid, its own is open.
    const clientEmail = await createClientUser(admin, { id: enrolled.customer_id, name: employee })
    await login(page, clientEmail)
    await page.getByRole('link', { name: 'My payments' }).click()
    const statement = page.getByTestId('employee-statement')
    const first = statement.locator('tbody tr').first()
    await expect(first).toContainText('R$ 80,00')
    await expect(first).toContainText('R$ 40,00')
    await expect(first).toContainText('Open')
    await expect(first).toContainText('paid') // the company's part, on its invoice
    await expect(statement.locator('.kpi').filter({ hasText: 'Covered by my company' })).toBeVisible()
    await page.getByRole('button', { name: 'Sign out' }).click()

    // The company's statement: both sides, and the payroll list to export.
    await login(page, email)
    await page.getByRole('link', { name: 'Statement' }).click()
    const company = page.getByTestId('company-statement')
    await expect(company.locator('tbody tr').filter({ hasText: month })).toContainText('R$ 40,00')
    await expect(company.getByTestId('statement-employees')).toContainText(employee)
    await expect(company.getByTestId('statement-employees')).toContainText('50%')
    await expect(page.getByRole('button', { name: 'Export payroll list (CSV)' })).toBeVisible()
  })
})
