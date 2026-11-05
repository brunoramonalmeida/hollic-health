// Corporate partnerships (B2B): a supervisor registers a partner company and
// negotiates its six-month agreement; the reception enrolls the company's
// employees at the frozen conditions, the form tells when the company is
// below its minimum (it then pays the month's discounts back) and the
// company's invoice is paid at once; the employee sees its corporate plan
// but cannot change it.
import { expect, test } from '@playwright/test'
import { Api, createClientUser, isoDate, login, unique } from './helpers'

/** The agreement runs from the first day of this month, for six months. */
function validity() {
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), 1)
  const end = new Date(start.getFullYear(), start.getMonth() + 6, 0)
  const month = start.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
  return { start: isoDate(start), end: isoDate(end), month }
}

test.describe('Corporate partnerships', () => {
  test('an agreement is negotiated, employees are enrolled and the company pays its invoice', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    // A plan with a waiting period the agreement waives, and two employees.
    const plan = await admin.post('/modalities', { name: unique('Plan'), plan_value_cents: 10000, dependent_limit: 2, grace_days: 30 })
    const first = await admin.post('/customers', { name: unique('Employee A') })
    const second = await admin.post('/customers', { name: unique('Employee B') })
    const company = unique('Acme')
    const { start, end, month } = validity()

    // The supervisor registers the company and negotiates the agreement.
    await login(page, 'supervisor@example.com')
    await page.goto('/partners/new')
    await page.getByLabel('Name *').fill(company)
    await page.getByLabel('Contact email').fill('rh@acme.example.com')
    await page.getByRole('button', { name: 'Create' }).click()
    await page.waitForURL(/\/partners\/\d+$/)
    await page.getByRole('link', { name: 'New agreement' }).click()
    await page.waitForURL(/\/agreements\/new\?partner=\d+/)
    await expect(page.getByLabel('Partner company *')).toHaveValue(/\d+/)
    await page.getByLabel('Name *').fill(`${company} agreement`)
    await page.getByLabel('Starts on *').fill(start)
    // Six months: the end follows from the start.
    await expect(page.getByTestId('contract-end')).toContainText(`until ${end.split('-').reverse().join('/')}`)
    await page.getByLabel('Minimum of employees *').fill('2')
    await page.getByLabel('Plan', { exact: true }).selectOption({ label: plan.name })
    await page.getByLabel('Discount (%)').fill('20')
    await page.getByLabel('Waiting period (days)').fill('0')
    await expect(page.locator('.offer')).toContainText('R$ 100,00 → R$ 80,00/month')
    await page.getByRole('button', { name: 'Create' }).click()
    await page.waitForURL(/\/agreements\/\d+$/)
    const agreementUrl = new URL(page.url()).pathname
    await expect(page.getByTestId('agreement-status')).toContainText('0 of 2 employees')
    await page.getByRole('button', { name: 'Sign out' }).click()

    // The reception enrolls the first employee: the negotiated price at once, the company answering for the minimum.
    await login(page, 'attendant@example.com')
    await page.goto(agreementUrl)
    await expect(page.getByRole('button', { name: 'Save' })).toHaveCount(0) // terms are the managers'
    await page.getByRole('link', { name: 'Enroll an employee' }).first().click()
    await page.waitForURL(/\/subscriptions\/new\?agreement=\d+/)
    const form = page.locator('form')
    await expect(form.getByLabel('Seller')).toBeDisabled() // never a seller's sale
    await form.getByLabel('Customer *').selectOption({ label: first.name })
    await expect(page.getByTestId('agreement-quote')).toContainText('the negotiated 20% off applies, no waiting period')
    await expect(page.getByTestId('agreement-quote')).toContainText(`1 of 2 employees in ${month} with this enrollment: 1 short`)
    await page.getByRole('button', { name: 'Create' }).click()
    await page.waitForURL(/\/subscriptions\/\d+$/)
    const firstUrl = new URL(page.url()).pathname
    await expect(page.locator('.status-panel')).toContainText(`${company} · ${company} agreement`)
    await expect(page.locator('table tbody tr').first()).toContainText('R$ 80,00')

    // The second one reaches the minimum.
    await page.goto(agreementUrl.replace(/\/agreements\/(\d+)$/, '/subscriptions/new?agreement=$1'))
    await form.getByLabel('Customer *').selectOption({ label: second.name })
    await expect(page.getByTestId('agreement-quote')).toContainText('2 of 2 employees')
    await expect(page.getByTestId('agreement-quote')).toContainText('the minimum is met')
    await page.getByRole('button', { name: 'Create' }).click()
    await page.waitForURL(/\/subscriptions\/\d+$/)
    await page.goto(firstUrl)
    await expect(page.locator('table tbody tr').first()).toContainText('R$ 80,00')

    // The company's invoice of the month is registered at once.
    await page.goto(agreementUrl)
    await expect(page.getByTestId('agreement-status')).toContainText('2 of 2 employees')
    page.once('dialog', (dialog) => dialog.accept(dialog.defaultValue()))
    const invoice = page.getByTestId('company-invoices').locator('tbody tr').filter({ hasText: month }).first()
    await invoice.getByRole('button', { name: 'Register payment' }).click()
    await expect(invoice).toContainText('Paid')
    await expect(invoice).toContainText('R$ 160,00')
    await page.getByRole('button', { name: 'Sign out' }).click()

    // The employee sees its corporate plan, paid by the company, and cannot change it.
    const email = await createClientUser(admin, first)
    await login(page, email)
    await page.goto('/subscriptions')
    await expect(page.locator('table tbody tr').first()).toContainText(`Corporate plan · ${company}`)
    await expect(page.getByRole('button', { name: 'Cancel plan' })).toHaveCount(0)
    await expect(page.getByRole('link', { name: 'Upgrade my plan' })).toHaveCount(0)
    await page.goto(firstUrl)
    await expect(page.locator('.status-panel')).toContainText('Active')
    await expect(page.locator('.status-panel')).toContainText('Paid by the company · 20% off (agreement terms)')
    await expect(page.getByText(`Corporate plan of ${company}: changes and cancellation go through your company`)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Save' })).toHaveCount(0)
  })

  test('sellers have no part in corporate agreements', async ({ page }) => {
    await login(page, 'ana.vendas@example.com')
    await expect(page.getByRole('link', { name: 'Partner companies' })).toHaveCount(0)
    await page.goto('/agreements')
    await page.waitForURL((url) => url.pathname === '/')
  })
})
