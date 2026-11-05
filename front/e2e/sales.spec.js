// Sellers and sales regions: the reception records a seller's sale and the
// seller's region bounds it (plans available, special discount, scope);
// managers set regions and sellers up; clients never see the commission.
import { expect, test } from '@playwright/test'
import { Api, login, unique } from './helpers'

/** A plan, a region selling it with 10% off in one municipality, a seller there and two customers. */
async function scenario(admin) {
  const city = unique('Vila')
  const plan = await admin.post('/modalities', { name: unique('Plan'), plan_value_cents: 10000, dependent_limit: 2, grace_days: 0 })
  const other = await admin.post('/modalities', { name: unique('Other plan'), plan_value_cents: 20000, dependent_limit: 2, grace_days: 0 })
  const region = await admin.post('/regions', {
    name: unique('Region'),
    commission_bp: 1250,
    areas: [{ state: 'RJ', city }],
    plans: [{ modality_id: plan.id, discount_percent: 10 }],
  })
  const seller = await admin.post('/sellers', { name: unique('Seller'), region_id: region.id })
  const inside = await admin.post('/customers', { name: unique('Inside'), city, state: 'RJ' })
  const outside = await admin.post('/customers', { name: unique('Outside'), city: 'Rio de Janeiro', state: 'RJ' })
  return { city, plan, other, region, seller, inside, outside }
}

test.describe('Sellers and sales regions', () => {
  test('the reception records a seller sale, bound by the region of the seller', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const { plan, other, region, seller, inside, outside } = await scenario(admin)

    await login(page, 'attendant@example.com')
    await page.goto('/subscriptions/new')
    const form = page.locator('form')
    await form.getByLabel('Customer *').selectOption({ label: outside.name })
    await form.getByLabel('Seller').selectOption({ label: seller.name })
    // Only the plans of the seller's region can be picked.
    await expect(form.getByLabel('Plan *').locator('option', { hasText: other.name })).toHaveJSProperty('disabled', true)
    await form.getByLabel('Plan *').selectOption({ label: `${plan.name} — 10% off in ${region.name}` })

    // Outside the scope: the form says why and does not let it be created.
    await expect(page.locator('.notice.warn')).toContainText(`is outside region ${region.name}`)
    await expect(page.getByRole('button', { name: 'Create' })).toBeDisabled()

    // Inside the scope: the special discount and the commission are shown.
    await form.getByLabel('Customer *').selectOption({ label: inside.name })
    await expect(page.locator('.notice.warn')).toHaveCount(0)
    await expect(page.locator('.notice.info')).toContainText('R$ 90,00 (10% off R$ 100,00)')
    await expect(page.locator('.notice.info')).toContainText('seller commission 12.5%')
    await page.getByRole('button', { name: 'Create' }).click()
    await page.waitForURL(/\/subscriptions\/\d+$/)
    const status = page.locator('.status-panel')
    await expect(status).toContainText(`${seller.name} · ${region.name}`)
    await expect(status).toContainText('10% special discount · 12.5% commission')
    await expect(page.locator('table tbody tr').first()).toContainText('R$ 90,00')
  })

  test('a supervisor sets up a region and its seller; an attendant only reads them', async ({ page }) => {
    const name = unique('South')
    await login(page, 'supervisor@example.com')
    await page.goto('/regions/new')
    await page.getByLabel('Name *').fill(name)
    await page.getByLabel('Seller commission (%)').fill('7,5')
    await page.getByLabel('State').first().selectOption('RS')
    await page.getByRole('button', { name: '+ Add area' }).click()
    await page.getByLabel('State').nth(1).selectOption('PR')
    await page.getByLabel('City').nth(1).fill('Curitiba')
    await page.getByRole('button', { name: 'Create' }).click()
    await page.waitForURL((url) => url.pathname === '/regions')
    const row = page.locator('table tbody tr').filter({ hasText: name })
    await expect(row).toContainText('Curitiba/PR, RS')
    await expect(row).toContainText('7.5%')

    const seller = unique('Rep')
    await page.goto('/sellers/new')
    await page.getByLabel('Name *').fill(seller)
    await page.getByLabel('Region *').selectOption({ label: name })
    await expect(page.getByText(`Rules of ${name}`)).toBeVisible()
    await page.getByRole('button', { name: 'Create' }).click()
    await page.waitForURL((url) => url.pathname === '/sellers')
    await expect(page.locator('table tbody tr').filter({ hasText: seller })).toContainText(name)

    await page.getByRole('button', { name: 'Sign out' }).click()
    await page.waitForURL((url) => url.pathname === '/login')
    await login(page, 'attendant@example.com')
    await page.goto('/regions')
    await expect(page.getByRole('link', { name: 'New region' })).toHaveCount(0)
    await page.locator('table tbody tr').filter({ hasText: name }).getByRole('link', { name: 'View' }).click()
    await expect(page.getByRole('button', { name: 'Save' })).toHaveCount(0)
  })

  test('a customer with a plan is upgraded to another one, which replaces it', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const { plan, inside } = await scenario(admin)
    const premium = await admin.post('/modalities', { name: unique('Premium'), plan_value_cents: 20000, dependent_limit: 4, grace_days: 0 })
    const today = new Date()
    const iso = (date) => date.toISOString().slice(0, 10)
    const joined = new Date(today); joined.setDate(joined.getDate() - 40)
    const expires = new Date(today); expires.setDate(expires.getDate() + 300)
    const current = await admin.post('/subscriptions', { customer_id: inside.id, modality_id: plan.id, joined_on: iso(joined), expires_on: iso(expires) })
    await admin.put(`/subscriptions/${current.id}/installments/${current.installments[0].id}`, { paid_on: current.installments[0].due_on })

    await login(page, 'attendant@example.com')
    await page.goto('/subscriptions/new')
    const form = page.locator('form')
    await form.getByLabel('Customer *').selectOption({ label: inside.name })
    const notice = page.getByTestId('upgrade-notice')
    await expect(notice).toContainText(`${inside.name} already has ${plan.name}`)
    await form.getByLabel('Plan *').selectOption({ label: premium.name })
    await expect(page.getByRole('button', { name: 'Upgrade plan' })).toBeDisabled() // until the upgrade is confirmed
    await page.getByLabel('Upgrade the current plan').check()
    const start = new Date(today); start.setDate(start.getDate() + 10)
    const end = new Date(start); end.setFullYear(end.getFullYear() + 1)
    await form.locator('label:has-text("Joined on *") input').fill(iso(start))
    await form.locator('label:has-text("Expires on *") input').fill(iso(end))
    await page.getByRole('button', { name: 'Upgrade plan' }).click()
    await page.waitForURL(/\/subscriptions\/\d+$/)
    await expect(page.locator('.status-panel')).toContainText('Replaced the previous plan')
    const previous = await admin.get(`/subscriptions/${current.id}`)
    const dayBefore = new Date(start); dayBefore.setDate(dayBefore.getDate() - 1)
    expect(previous.expires_on).toBe(iso(dayBefore))
    expect(previous.installments_paid).toBe(1)
    await page.goto(`/subscriptions/${current.id}`)
    await expect(page.locator('.status-panel')).toContainText('Replaced by a new plan')
  })

  test('the client sees who sold the plan and the discount, never the commission', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const { plan, seller, inside } = await scenario(admin)
    const today = new Date().toISOString().slice(0, 10)
    const next = new Date(Date.now() + 364 * 86_400_000).toISOString().slice(0, 10)
    const sold = await admin.post('/subscriptions', { customer_id: inside.id, modality_id: plan.id, joined_on: today, expires_on: next, seller_id: seller.id })
    const email = `${unique('client').toLowerCase()}@example.com`
    await admin.post('/users', { name: inside.name, email, role: 'client', password: 'saude123', customer_id: inside.id })

    await login(page, email)
    await page.goto(`/subscriptions/${sold.id}`)
    const status = page.locator('.status-panel')
    await expect(status).toContainText(seller.name)
    await expect(status).toContainText('10% special discount')
    await expect(status).not.toContainText('commission')
    await expect(page.locator('form').getByLabel('Seller')).toHaveCount(0)
  })
})
