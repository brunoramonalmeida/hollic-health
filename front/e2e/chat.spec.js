// The end-to-end encrypted chat of a remote appointment: the patient and
// the professional exchange messages and an attachment; the safety code is
// the same on both sides; the staff only sees that the chat exists.
import { expect, test } from '@playwright/test'
import {
  Api,
  createClientUser,
  createCoveredCustomer,
  createProfessional,
  createProfessionalUser,
  futureSlot,
  localIso,
  login,
} from './helpers'

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
)

test.describe('Remote appointment chat', () => {
  let ctx

  test.beforeEach(async ({ request }) => {
    const admin = await Api.asAdmin(request)
    const professional = await createProfessional(admin, { name: 'Dr Chat', remote_enabled: true })
    const professionalLogin = await createProfessionalUser(admin, professional)
    const patient = await createCoveredCustomer(admin, { name: 'Carla Patient' })
    const patientLogin = await createClientUser(admin, patient)
    const visit = await admin.post('/appointments', {
      customer_id: patient.id,
      professional_id: professional.id,
      marked_at: localIso(futureSlot(120)),
      remote: true,
    })
    ctx = { admin, professional, professionalLogin, patient, patientLogin, visit }
  })

  test('the two parties chat with an attachment; the staff cannot read', async ({ page }) => {
    // The patient opens the chat first: its browser creates the conversation key.
    await login(page, ctx.patientLogin)
    await page.goto(`/appointments/${ctx.visit.id}`)
    const chat = page.getByTestId('chat-panel')
    await expect(chat).toContainText('end-to-end encrypted')
    await expect(chat.getByPlaceholder(/Write a message/)).toBeVisible()
    await chat.getByPlaceholder(/Write a message/).fill('Doctor, the rash got worse')
    await chat.locator('input[type="file"]').setInputFiles({ name: 'rash.png', mimeType: 'image/png', buffer: PNG })
    await chat.getByRole('button', { name: 'Send' }).click()
    await expect(chat.getByTestId('chat-messages')).toContainText('Doctor, the rash got worse')
    await expect(chat.getByTestId('chat-messages')).toContainText('rash.png')
    // The server stored ciphertext only.
    const stored = await ctx.admin.request.get(`${process.env.E2E_API_URL || 'http://127.0.0.1:8000'}/appointments/${ctx.visit.id}/chat/messages`, {
      headers: { Authorization: `Bearer ${ctx.admin.bearer}` },
    })
    expect(stored.status()).toBe(403) // the staff is not a party
    await chat.getByText('Security details').click()
    const patientCode = (await chat.getByTestId('safety-code').textContent()).trim()

    // The professional (same browser, another login: its own identity key) reads it and replies.
    await login(page, ctx.professionalLogin)
    await page.goto(`/appointments/${ctx.visit.id}`)
    const theirs = page.getByTestId('chat-panel')
    await expect(theirs.getByTestId('chat-messages')).toContainText('Doctor, the rash got worse')
    await expect(theirs.getByTestId('chat-messages').locator('img[alt="rash.png"]')).toBeVisible()
    await theirs.getByPlaceholder(/Write a message/).fill('Send me another photo tomorrow, please')
    await theirs.getByRole('button', { name: 'Send' }).click()
    await expect(theirs.getByTestId('chat-messages')).toContainText('Send me another photo tomorrow')
    await theirs.getByText('Security details').click()
    expect((await theirs.getByTestId('safety-code').textContent()).trim()).toBe(patientCode)

    // Back as the patient: the reply is there.
    await login(page, ctx.patientLogin)
    await page.goto(`/appointments/${ctx.visit.id}`)
    await expect(page.getByTestId('chat-panel').getByTestId('chat-messages')).toContainText('Send me another photo tomorrow')

    // The staff sees that the chat exists, nothing else.
    await login(page, 'attendant@example.com')
    await page.goto(`/appointments/${ctx.visit.id}`)
    await expect(page.getByText('the staff cannot read them')).toBeVisible()
    await expect(page.getByTestId('chat-panel')).toHaveCount(0)
  })
})
