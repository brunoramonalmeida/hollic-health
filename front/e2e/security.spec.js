// The professional's authenticator setup and the automatic monthly closing.
import { expect, test } from '@playwright/test'
import { API_URL, Api, createProfessional, createProfessionalUser, login, totp } from './helpers'

test.describe('Authenticator setup', () => {
  test('a professional enrols its authenticator through the UI and can then sign', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const professional = await createProfessional(admin, { name: 'Dr Secure' })
    const email = await createProfessionalUser(admin, professional)
    await login(page, email)
    // Without an authenticator the home shows the warning and signing is blocked.
    await expect(page.getByTestId('totp-banner')).toBeVisible()
    await page.getByTestId('totp-banner').getByRole('link', { name: 'Set it up in Security' }).click()
    await expect(page.getByTestId('totp-status')).toContainText('Not set up')
    await page.getByRole('button', { name: 'Set up authenticator' }).click()
    const setup = page.getByTestId('totp-setup')
    const secret = (await setup.getByTestId('totp-secret').textContent()).replace(/\s/g, '')
    expect(secret).toMatch(/^[A-Z2-7]{32}$/)
    await expect(setup).toContainText(email)
    await expect(setup.locator('code.otpauth')).toContainText('otpauth://totp/')
    // A wrong code is refused; the right one (computed like the app does) confirms it.
    const wrong = totp(secret) === '000000' ? '111111' : '000000'
    await setup.getByLabel('Code shown by the app now').fill(wrong)
    await setup.getByRole('button', { name: 'Confirm authenticator' }).click()
    await expect(setup).toContainText('does not match')
    await setup.getByLabel('Code shown by the app now').fill(totp(secret))
    await setup.getByRole('button', { name: 'Confirm authenticator' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Authenticator confirmed' })).toBeVisible()
    await expect(page.getByTestId('totp-status')).toContainText('Set up on')
    await page.goto('/')
    await expect(page.getByTestId('totp-banner')).toHaveCount(0)
    // Already set up: starting over is refused by the API (an administrator resets it).
    const bearer = (await request.post(`${API_URL}/auth/token`, { data: { email, password: 'saude123' } }).then((r) => r.json())).access_token
    const again = await request.post(`${API_URL}/auth/totp/setup`, { headers: { Authorization: `Bearer ${bearer}` } })
    expect(again.status()).toBe(409)
  })
})

test.describe('Monthly closing', () => {
  test('the admin closes the previous month by hand and the worker settles every payee', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const closings = await admin.get('/payouts/closings')
    const now = new Date()
    const first = new Date(now.getFullYear(), now.getMonth(), 1)
    first.setDate(0)
    const previous = `${first.getFullYear()}-${String(first.getMonth() + 1).padStart(2, '0')}`
    test.skip(closings.some((c) => c.period === previous), 'previous month already closed in this database')

    await login(page, 'foo.bar@example.com')
    await page.goto('/payouts')
    const card = page.getByTestId('closings')
    page.once('dialog', (dialog) => dialog.accept())
    await card.getByRole('button', { name: `Close ${previous} now` }).click()
    await expect(page.getByRole('status').filter({ hasText: 'queued' })).toBeVisible()
    await expect(card.locator('tbody tr').filter({ hasText: previous })).toContainText('Scheduled')
    // The worker relays and consumes the jobs: close -> one settle job per payee -> done.
    // Each pass handles up to 100 jobs and the emails of the specs run before
    // this one are queued ahead of the closing: pass until it is done.
    for (let pass = 0; pass < 10; pass += 1) {
      await admin.post('/queue/run', {})
      const closing = (await admin.get('/payouts/closings')).find((c) => c.period === previous)
      if (closing && closing.status === 'done') break
    }
    await page.reload()
    const row = card.locator('tbody tr').filter({ hasText: previous })
    await expect(row).toContainText('Done')
    await expect(row).toContainText('By Foo Bar')
    const done = (await admin.get('/payouts/closings')).find((c) => c.period === previous)
    expect(done.payees_total).toBeGreaterThan(0)
    expect(done.settlements_created + done.payees_skipped).toBe(done.payees_total)
    await expect(card.getByRole('button', { name: `Close ${previous} now` })).toHaveCount(0)
  })
})
