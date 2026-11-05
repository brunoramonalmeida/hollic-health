// The seller's own area: the login (lost at once when deactivated),
// registering a customer and recording a sale bound by the region, the
// change requests the customer confirms, the commission report, the
// account, and the areas a seller never reaches.
import { expect, test } from '@playwright/test'
import { Api, PASSWORD, login, unique } from './helpers'

/** A region selling one plan at 10% off in one city, a seller there with a login. */
async function sellerScenario(admin) {
  const city = unique('Vila')
  const plan = await admin.post('/modalities', { name: unique('Plan'), plan_value_cents: 10000, dependent_limit: 2, grace_days: 0 })
  const region = await admin.post('/regions', {
    name: unique('Region'),
    commission_bp: 1000,
    areas: [{ state: 'SP', city }],
    plans: [{ modality_id: plan.id, discount_percent: 10 }],
  })
  const seller = await admin.post('/sellers', { name: unique('Seller'), region_id: region.id })
  const email = `${unique('seller').toLowerCase()}@example.com`
  await admin.post('/users', { name: seller.name, email, role: 'seller', password: PASSWORD, seller_id: seller.id })
  return { city, plan, region, seller, email }
}

test.describe('Seller area', () => {
  test('a seller registers a customer, records a sale inside its region and sees its commission', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const { city, plan, region, seller, email } = await sellerScenario(admin)
    const customer = unique('Holder')

    await login(page, email)
    await expect(page.getByRole('heading', { name: `Welcome, ${seller.name}` })).toBeVisible()
    await expect(page.getByText(`You sell in region ${region.name} at 10% commission`)).toBeVisible()

    // Registers a customer (it joins the portfolio) and sells from its record.
    await page.goto('/customers/new')
    await page.getByLabel('Name *').fill(customer)
    await page.getByLabel('Municipality').fill(city)
    await page.locator('label:has-text("State") select').first().selectOption('SP')
    await page.getByRole('button', { name: 'Create' }).click()
    await page.waitForURL(/\/customers\/\d+$/)
    await page.getByRole('link', { name: 'Sell a plan' }).click()
    await page.waitForURL(/\/subscriptions\/new\?customer=\d+/)
    const form = page.locator('form')
    await expect(form.getByLabel('Customer *')).toHaveValue(/\d+/)
    await expect(form.getByLabel('Seller')).toHaveCount(0) // sells as itself
    await form.getByLabel('Plan *').selectOption({ label: `${plan.name} — 10% off in ${region.name}` })
    await expect(page.locator('.notice.info').last()).toContainText('R$ 90,00 (10% off R$ 100,00) · seller commission 10%')
    await page.getByRole('button', { name: 'Record sale' }).click()
    await page.waitForURL(/\/subscriptions\/\d+$/)
    await expect(page.locator('.status-panel')).toContainText(`${seller.name} · ${region.name}`)
    // Follows the sale, does not change it.
    await expect(page.getByRole('button', { name: 'Save' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Cancel plan' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Register payment' })).toHaveCount(0)
    const sale = Number(new URL(page.url()).pathname.split('/').pop())

    // The staff registers the first payment: the commission shows up.
    const sold = await admin.get(`/subscriptions/${sale}`)
    await admin.put(`/subscriptions/${sale}/installments/${sold.installments[0].id}`, { paid_on: sold.installments[0].due_on })
    await page.goto('/my-commissions')
    await page.getByRole('button', { name: 'This month' }).click()
    await expect(page.getByTestId('commission-total')).toContainText('R$ 9,00')
    await expect(page.locator('table').last()).toContainText(customer)
    await page.goto('/notifications')
    await expect(page.getByText(`Sale recorded: ${customer} — ${plan.name}`)).toBeVisible()
    await expect(page.getByText(`Commission earned: R$ 9,00 — ${customer}, installment 1`)).toBeVisible()
  })

  test('a seller cannot sell outside its region and never reaches the operations area', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const { plan, region, email } = await sellerScenario(admin)

    await login(page, email)
    for (const path of ['/appointments', '/regions', '/sellers', '/reports', '/users', '/professionals', '/payouts']) {
      await page.goto(path)
      await page.waitForURL((url) => url.pathname === '/')
    }
    await expect(page.locator('.sidebar')).not.toContainText('Appointments')

    await page.goto('/customers/new')
    await page.getByLabel('Name *').fill(unique('Outsider'))
    await page.getByLabel('Municipality').fill('Rio de Janeiro')
    await page.locator('label:has-text("State") select').first().selectOption('RJ')
    await page.getByRole('button', { name: 'Create' }).click()
    await page.waitForURL(/\/customers\/\d+$/)
    await page.getByRole('link', { name: 'Sell a plan' }).click()
    await page.locator('form').getByLabel('Plan *').selectOption({ label: `${plan.name} — 10% off in ${region.name}` })
    await expect(page.locator('.notice.warn')).toContainText(`is outside region ${region.name}`)
    await expect(page.getByRole('button', { name: 'Record sale' })).toBeDisabled()
  })

  test('a seller edits or cancels a sale only through a request the customer confirms', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const { city, plan, seller, email } = await sellerScenario(admin)
    // A customer registered by the staff: the seller sells to anyone inside its region.
    const holder = await admin.post('/customers', { name: unique('Holder'), city, state: 'SP' })
    const today = new Date().toISOString().slice(0, 10)
    const next = new Date(Date.now() + 364 * 86_400_000).toISOString().slice(0, 10)
    const sold = await admin.post('/subscriptions', { customer_id: holder.id, modality_id: plan.id, joined_on: today, expires_on: next, seller_id: seller.id })
    const clientEmail = `${unique('client').toLowerCase()}@example.com`
    await admin.post('/users', { name: holder.name, email: clientEmail, role: 'client', password: PASSWORD, customer_id: holder.id })
    page.on('dialog', (dialog) => dialog.accept(dialog.type() === 'prompt' ? (dialog.defaultValue() || 'shorter period') : undefined))

    await login(page, email)
    await page.goto(`/subscriptions/${sold.id}`)
    await expect(page.locator('.status-panel')).toContainText('Sold by') // the record is loaded
    const shorter = new Date(Date.now() + 180 * 86_400_000).toISOString().slice(0, 10)
    await page.locator('label:has-text("Expires on *") input').fill(shorter)
    await page.getByRole('button', { name: 'Request change' }).click()
    const banner = page.getByTestId('pending-change')
    await expect(banner).toContainText("awaiting the customer's confirmation")
    await expect(banner).toContainText('reason: shorter period')
    await expect(page.locator('label:has-text("Expires on *") input')).toBeDisabled()
    expect((await admin.get(`/subscriptions/${sold.id}`)).expires_on).toBe(next) // nothing changed yet

    await page.getByRole('button', { name: 'Sign out' }).click()
    await page.waitForURL((url) => url.pathname === '/login')
    await login(page, clientEmail)
    await page.goto('/subscriptions')
    await expect(page.locator('table')).toContainText('Change awaiting your confirmation')
    await page.goto(`/subscriptions/${sold.id}`)
    await page.getByTestId('pending-change').getByRole('button', { name: 'Confirm', exact: true }).click()
    await expect(page.getByText('Change confirmed and applied')).toBeVisible()
    await expect(page.getByTestId('pending-change')).toHaveCount(0)
    expect((await admin.get(`/subscriptions/${sold.id}`)).expires_on).toBe(shorter)
  })

  test('a deactivated seller is signed out at once and cannot sign in again', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const { seller, email } = await sellerScenario(admin)
    await login(page, email)
    await admin.put(`/sellers/${seller.id}`, { name: seller.name, region_id: seller.region_id, active: false })
    // The first request answers 401 and the app reloads the login page, aborting this navigation.
    await page.goto('/subscriptions').catch(() => {})
    await page.waitForURL((url) => url.pathname === '/login')
    await expect(page.locator('.error').first()).toContainText('Your seller access was deactivated')
    await page.getByLabel('Email').fill(email)
    await page.getByLabel('Password').fill(PASSWORD)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.locator('.error').first()).toContainText('deactivated')
    expect(new URL(page.url()).pathname).toBe('/login')
  })

  test('a coordinator follows its team and the override it earns on their sales', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const { city, plan, region, seller: leader, email } = await sellerScenario(admin)
    // The region pays 2.5% to the direct leader (level 1).
    await admin.put(`/regions/${region.id}`, { name: region.name, commission_bp: region.commission_bp, areas: region.areas, plans: region.plans, override_bps: [250] })
    const collaborator = await admin.post('/sellers', { name: unique('Collab'), region_id: region.id, leader_id: leader.id })
    const holder = await admin.post('/customers', { name: unique('Holder'), city, state: 'SP' })
    const today = new Date().toISOString().slice(0, 10)
    const next = new Date(Date.now() + 364 * 86_400_000).toISOString().slice(0, 10)
    const sold = await admin.post('/subscriptions', { customer_id: holder.id, modality_id: plan.id, joined_on: today, expires_on: next, seller_id: collaborator.id })
    expect(sold.overrides.map((item) => [item.level, item.seller_id, item.rate_bp])).toEqual([[1, leader.id, 250]])
    await admin.put(`/subscriptions/${sold.id}/installments/${sold.installments[0].id}`, { paid_on: sold.installments[0].due_on })

    await login(page, email)
    await expect(page.locator('.kpis')).toContainText('My team')
    await page.getByRole('link', { name: 'My team' }).first().click()
    await page.waitForURL('/my-team')
    await page.getByRole('button', { name: 'This month' }).click()
    await expect(page.getByTestId('override-total')).toContainText('R$ 2,25') // 2.5% of R$ 90,00
    const row = page.locator('table').first().locator('tbody tr').filter({ hasText: collaborator.name })
    await expect(row).toContainText('R$ 90,00')
    await expect(row).toContainText('R$ 9,00') // the collaborator's own commission
    await expect(row).toContainText('R$ 2,25')
    // The team's sale is visible to the coordinator, read-only.
    await row.getByRole('link', { name: 'Sales' }).click()
    await expect(page.locator('table')).toContainText(collaborator.name)
    await page.goto(`/subscriptions/${sold.id}`)
    await expect(page.locator('.status-panel')).toContainText(collaborator.name)
    await expect(page.getByRole('button', { name: 'Request change' })).toHaveCount(0)
    await page.goto('/notifications')
    await expect(page.getByText(`Override earned: R$ 2,25 — ${holder.name}, sold by ${collaborator.name}`)).toBeVisible()
  })

  test('a seller manages its account: contact data and password', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const { email } = await sellerScenario(admin)

    await login(page, email)
    await page.goto('/my-account')
    await page.getByLabel('Phone').fill('(11) 98888-7777')
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByText('Contact data updated')).toBeVisible()
    await page.getByLabel('Current password *').fill(PASSWORD)
    await page.getByLabel(/^New password/).fill('changed123')
    await page.getByLabel('Confirm the new password *').fill('changed123')
    await page.getByRole('button', { name: 'Change password' }).click()
    await expect(page.getByText('Password changed')).toBeVisible()

    await page.getByRole('button', { name: 'Sign out' }).click()
    await page.waitForURL((url) => url.pathname === '/login')
    await login(page, email, 'changed123')
    await page.goto('/my-region')
    await expect(page.getByRole('heading', { name: /^Region / })).toBeVisible()
  })
})
