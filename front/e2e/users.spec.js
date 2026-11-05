// Creating professional logins.
import { expect, test } from '@playwright/test'
import { Api, PASSWORD, createProfessional, login, unique } from './helpers'

test.describe('Professional users', () => {
  test('the admin creates a professional login that can sign in', async ({ page, request }) => {
    const admin = await Api.asAdmin(request)
    const professional = await createProfessional(admin, { name: unique('Dr Login') })
    const email = `${unique('login').toLowerCase()}@example.com`

    await login(page, 'foo.bar@example.com')
    await page.goto('/users/new')
    await page.getByLabel('Name *').fill(professional.name)
    await page.getByLabel('Email *').fill(email)
    await page.getByLabel('Role *').selectOption('professional')
    const link = page.getByLabel('Professional *')
    await expect(link).toBeVisible()
    await link.selectOption({ label: `${professional.name} — ${professional.specialty_name}` })
    await expect(page.getByText('The professional signs the appointments of this agenda and sees its own payouts.')).toBeVisible()
    await page.getByLabel('Password *').fill(PASSWORD)
    await page.getByRole('button', { name: 'Create' }).click()
    await page.waitForURL((url) => url.pathname === '/users')
    const row = page.locator('table tbody tr').filter({ hasText: email })
    await expect(row).toContainText('Professional')
    await expect(row).toContainText(professional.name)

    // The new login lands on the professional's home.
    await page.getByRole('button', { name: 'Sign out' }).click()
    await page.waitForURL((url) => url.pathname === '/login')
    await login(page, email)
    await expect(page.getByRole('heading', { name: `Welcome, ${professional.name}` })).toBeVisible()
    await expect(page.getByText('Professional', { exact: true }).first()).toBeVisible()
  })

  test('a supervisor cannot create professional logins', async ({ page }) => {
    await login(page, 'supervisor@example.com')
    await page.goto('/users/new')
    // Supervisors manage the external logins only: clients and partner companies.
    await expect(page.getByLabel('Role *').locator('option')).toHaveCount(2)
    await expect(page.getByLabel('Role *').locator('option')).toHaveText(['Client', 'Partner company'])
  })
})
