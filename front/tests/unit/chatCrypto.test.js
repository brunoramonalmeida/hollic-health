import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import {
  decryptBlob,
  decryptMessage,
  encryptBlob,
  encryptMessage,
  fingerprintOf,
  fromBase64,
  generateConversationKey,
  generateIdentity,
  importIdentity,
  loadOrCreateIdentity,
  memoryStore,
  safetyCode,
  toBase64,
  unwrapKey,
  wrapKeyFor,
} from '../../src/chatCrypto.js'

const fixture = JSON.parse(readFileSync(new URL('./fixtures/chat-interop.json', import.meta.url)))

test('the browser opens what the API test double (Python) produced: envelope, message, attachment, safety code', async () => {
  const me = await importIdentity(fixture.recipient_private_jwk)
  assert.equal(me.fingerprint, fixture.recipient_fingerprint)
  const key = await unwrapKey(fixture.envelope, me.privateKey)
  assert.equal(toBase64(await globalThis.crypto.subtle.exportKey('raw', key)), fixture.conversation_key_b64)
  assert.deepEqual(await decryptMessage(key, fixture.conversation_id, fixture.message), fixture.expected_payload)
  const plain = await decryptBlob(key, fixture.conversation_id, fromBase64(fixture.blob_b64), fixture.blob_iv)
  assert.equal(toBase64(plain), fixture.blob_plain_b64)
  assert.equal(await safetyCode([fixture.sender_fingerprint, fixture.recipient_fingerprint]), fixture.safety_code)
  assert.equal(await safetyCode([fixture.recipient_fingerprint, fixture.sender_fingerprint]), fixture.safety_code)
})

test('two browsers agree on a conversation key through envelopes and exchange messages', async () => {
  const patient = await generateIdentity()
  const professional = await generateIdentity()
  const key = await generateConversationKey()
  const forProfessional = await wrapKeyFor(key, professional.publicJwk)
  assert.equal(forProfessional.recipient_fingerprint, professional.fingerprint)
  const opened = await unwrapKey(forProfessional, professional.privateKey)
  const sent = await encryptMessage(key, 5, { text: 'hello', attachments: [] })
  assert.deepEqual(await decryptMessage(opened, 5, sent), { text: 'hello', attachments: [] })
  assert.ok(!sent.ciphertext.includes('hello'))
  // Bound to the conversation and to the recipient: the wrong context fails.
  await assert.rejects(decryptMessage(opened, 6, sent))
  await assert.rejects(unwrapKey(forProfessional, patient.privateKey))
  const bytes = new TextEncoder().encode('evidence')
  const { blob, iv } = await encryptBlob(key, 5, bytes)
  assert.equal(new TextDecoder().decode(await decryptBlob(opened, 5, blob, iv)), 'evidence')
  await assert.rejects(decryptBlob(opened, 5, blob, toBase64(new Uint8Array(12))))
})

test('the identity is created once per user and kept in the store', async () => {
  const store = memoryStore()
  const first = await loadOrCreateIdentity(store, 7)
  const again = await loadOrCreateIdentity(store, 7)
  assert.equal(again.fingerprint, first.fingerprint)
  const other = await loadOrCreateIdentity(store, 8)
  assert.notEqual(other.fingerprint, first.fingerprint)
  assert.equal(first.privateKey.extractable, false)
  assert.match(await fingerprintOf(first.publicJwk), /^[0-9a-f]{64}$/)
})
