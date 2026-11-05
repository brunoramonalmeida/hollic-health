// The partner companies' self-service portal: the company's login enrolls
// its employees in its agreement, follows the minimum of employees and its
// invoices, removes an employee who left — and reaches nothing else.
import { expect, test } from '@playwright/test'
import { Api, PASSWORD, isoDate, login, token, unique } from './helpers'

/** A company with a six-month agreement from the first day of this month (minimum 2) and a portal login. */
async function scenario(admin) {
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), 1)
  const plan = await admin.post('/modalities', { name: unique('Plan'), plan_value_cents: 10000, dependent_limit: 2, extra_dependent_cents: 1500, grace_days: 0 })
  const partner = await admin.post('/partners', { name: unique('Initech'), email: 'rh@initech.example.com' })
  const agreement = await admin.post('/agreements', {
    partner_id: partner.id,
    name: `${partner.name} 6m`,
    starts_on: isoDate(start),
    min_employees: 2,
    payer: 'company',
    plans: [{ modality_id: plan.id, discount_percent: 25, grace_days: 0 }],
  })
  const email = `${unique('hr').toLowerCase()}@initech.example.com`
  await admin.post('/users', { name: 'HR Manager', email, role: 'partner', password: PASSWORD, partner_id: partner.id })
  const month = start.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
  return { plan, partner, agreement, email, month }
}

/** A CPF unique to this run (11 digits). */
function cpf() {
  return String(Date.now()).slice(-11).padStart(11, '7')
}

test.describe('Company portal', () => {
  test('the company enrolls an employee, follows its agreement and removes the employee', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const { plan, partner, agreement, email, month } = await scenario(admin)
    const employee = unique('Ivan')

    await login(page, email)
    // The company's home: its agreement and the minimum this month, nothing of the network.
    await expect(page.getByRole('heading', { name: partner.name })).toBeVisible()
    const row = page.locator('table tbody tr').filter({ hasText: agreement.name })
    await expect(row).toContainText('0 of 2 employees')
    await expect(page.getByRole('link', { name: 'Customers' })).toHaveCount(0)
    await expect(page.getByRole('link', { name: 'Appointments' })).toHaveCount(0)

    // Enroll an employee at the negotiated price.
    await page.getByRole('link', { name: 'Enroll an employee' }).first().click()
    await page.waitForURL(/\/my-employees\/new/)
    await page.getByLabel('Agreement *').selectOption(String(agreement.id))
    await page.getByLabel('Plan *').selectOption({ label: `${plan.name} — R$ 75,00/month (25% off)` })
    await page.getByLabel('Full name *').fill(employee)
    await page.getByLabel('CPF *').fill(cpf())
    await page.getByLabel('Email').fill('ivan@initech.example.com')
    await expect(page.getByTestId('enroll-preview')).toContainText('R$ 75,00')
    await page.getByRole('button', { name: 'Enroll' }).click()
    await page.waitForURL(/\/my-employees\?agreement=\d+/)
    const line = page.locator('table tbody tr').filter({ hasText: employee })
    await expect(line).toContainText(plan.name)
    await expect(line).toContainText('R$ 75,00')
    await expect(line).toContainText('Pending payment')

    // The agreement: one employee of two this month — the shortfall is projected.
    await page.goto(`/my-agreements/${agreement.id}`)
    await expect(page.getByTestId('portal-agreement')).toContainText('1 of 2 employees')
    await expect(page.getByTestId('portal-agreement')).toContainText('1 short')
    const invoice = page.getByTestId('company-invoices').locator('tbody tr').filter({ hasText: month }).first()
    await expect(invoice).toContainText('1 / 2')
    await expect(invoice).toContainText('R$ 25,00') // the month's discount, paid back below the minimum
    await expect(invoice.getByRole('button', { name: 'Register payment' })).toHaveCount(0) // the staff registers payments

    // The employee left: removed from today.
    await page.goto('/my-employees')
    page.once('dialog', (dialog) => dialog.accept(dialog.defaultValue()))
    await line.getByRole('button', { name: 'Remove' }).click()
    await expect(line).toContainText('Cancelled')
    await expect(line.getByRole('button', { name: 'Remove' })).toHaveCount(0)

    // The network's pages are out of reach.
    await page.goto('/customers')
    await page.waitForURL((url) => url.pathname === '/')
  })

  test('the company edits its employee and manages its dependents', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const { plan, agreement, email } = await scenario(admin)
    const employee = unique('Bruna')
    const company = new Api(request, await token(request, email))
    const enrolled = await company.post('/portal/employees', { agreement_id: agreement.id, modality_id: plan.id, name: employee, document: cpf() })

    await login(page, email)
    await page.goto('/my-employees')
    const row = page.locator('table tbody tr').filter({ hasText: employee })
    await expect(row.getByTestId('employee-grace')).toContainText('None') // the agreement waives the waiting period
    await row.getByRole('link', { name: 'Edit' }).click()
    await page.waitForURL(new RegExp(`/my-employees/${enrolled.subscription_id}$`))

    // Its contact is corrected.
    const form = page.getByTestId('employee-form')
    await form.getByLabel('Phone').fill('(11) 98888-0000')
    await form.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Employee updated' })).toBeVisible()

    // Three dependents: the plan includes two, the third costs the extra price (15.00, 25% off: 11.25).
    const dependents = page.getByTestId('dependents')
    for (const [name, birth] of [['Kid One', '2016-05-05'], ['Kid Two', '2018-07-07'], ['Partner', '1990-02-02']]) {
      await dependents.getByRole('button', { name: 'Add dependent' }).click()
      const dependentForm = page.getByTestId('dependent-form')
      await dependentForm.getByLabel('Name *').fill(name)
      await dependentForm.getByLabel('Birth date *').fill(birth)
      await dependentForm.getByRole('button', { name: 'Add' }).click()
      await expect(dependents.locator('tbody tr').filter({ hasText: name })).toBeVisible()
    }
    await expect(page.locator('.status-panel')).toContainText('R$ 86,25') // (100.00 + 15.00) - 25%
    // One is corrected, another removed: the fee comes back.
    page.once('dialog', (dialog) => dialog.accept())
    await dependents.locator('tbody tr').filter({ hasText: 'Partner' }).getByRole('button', { name: 'Remove' }).click()
    await expect(dependents.locator('tbody tr').filter({ hasText: 'Partner' })).toHaveCount(0)
    await expect(page.locator('.status-panel')).toContainText('R$ 75,00')
    await dependents.locator('tbody tr').filter({ hasText: 'Kid Two' }).getByRole('button', { name: 'Edit' }).click()
    await page.getByTestId('dependent-form').getByLabel('Name *').fill('Kid Two Renamed')
    await page.getByTestId('dependent-form').getByRole('button', { name: 'Save dependent' }).click()
    await expect(dependents).toContainText('Kid Two Renamed')

    // Sign out sits in the topbar, beside the notifications.
    await page.locator('.topbar').getByRole('button', { name: 'Sign out' }).click()
    await page.waitForURL((url) => url.pathname === '/login')
  })
})
