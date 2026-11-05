// Plan categories and enterprise plans: a supervisor creates an enterprise
// category with its own waiting period; its plans are only offered through
// company agreements — the reception cannot pick one for an individual, but
// can enroll an employee in it — and clients never see them.
import { expect, test } from '@playwright/test'
import { Api, createClientUser, isoDate, login, unique } from './helpers'

test.describe('Plan categories', () => {
  test('an enterprise plan is only offered through a company agreement', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const name = unique('Enterprise')

    // The supervisor creates the enterprise category: no waiting period for its plans.
    await login(page, 'supervisor@example.com')
    await page.goto('/plan-categories/new')
    await page.getByLabel('Name *').fill(name)
    await page.getByLabel('Enterprise: its plans are only available through company agreements').check()
    await page.getByLabel('Waiting period of its plans (days)').fill('0')
    await page.getByRole('button', { name: 'Create' }).click()
    await page.waitForURL((url) => url.pathname === '/plan-categories')
    const row = page.locator('table tbody tr').filter({ hasText: name })
    await expect(row).toContainText('Enterprise: company agreements only')
    await expect(row).toContainText('none')
    await page.getByRole('button', { name: 'Sign out' }).click()

    // An enterprise plan (its own 30 days are replaced by the category's none) and an agreement offering it.
    const category = (await admin.get('/plan-categories')).find((item) => item.name === name)
    const plan = await admin.post('/modalities', { name: unique('Plan Corp'), plan_value_cents: 10000, dependent_limit: 2, grace_days: 30, discount_percent: 40, category_id: category.id })
    expect(plan.effective_grace_days).toBe(0)
    const now = new Date()
    const partner = await admin.post('/partners', { name: unique('Acme') })
    const agreement = await admin.post('/agreements', {
      partner_id: partner.id,
      name: `${partner.name} 6m`,
      starts_on: isoDate(new Date(now.getFullYear(), now.getMonth(), 1)),
      min_employees: 1,
      payer: 'company',
      plans: [{ modality_id: plan.id, discount_percent: 10 }],
    })
    const employee = await admin.post('/customers', { name: unique('Employee') })

    // The reception cannot sell it to an individual…
    await login(page, 'attendant@example.com')
    await page.goto('/subscriptions/new')
    const form = page.locator('form')
    await form.getByLabel('Customer *').selectOption({ label: employee.name })
    const option = form.getByLabel('Plan *').locator('option', { hasText: plan.name })
    await expect(option).toHaveJSProperty('disabled', true)
    await expect(option).toContainText('enterprise plan: company agreements only')
    // …but enrolls the employee in it through the company's agreement, with no waiting period.
    await form.getByLabel('Corporate agreement').selectOption(String(agreement.id))
    await expect(option).toHaveJSProperty('disabled', false)
    await form.getByLabel('Plan *').selectOption(String(plan.id))
    await page.getByRole('button', { name: 'Create' }).click()
    await page.waitForURL(/\/subscriptions\/\d+$/)
    await expect(page.locator('.status-panel')).toContainText(`Usable from${isoDate(now).split('-').reverse().join('/')}`)
    await page.getByRole('button', { name: 'Sign out' }).click()

    // Clients never see enterprise plans when they subscribe on their own.
    const holder = await admin.post('/customers', { name: unique('Holder') })
    await login(page, await createClientUser(admin, holder))
    await page.goto('/subscriptions/new')
    await expect(page.getByLabel('Plan *').locator('option', { hasText: plan.name })).toHaveCount(0)
  })
})
