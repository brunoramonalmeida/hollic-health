import { useEffect, useState } from 'react'
import { api } from '../api'
import PageHeader from '../components/PageHeader'
import { formatDateTime } from '../dates'
import { useSession } from '../session'

/**
 * The professional's authenticator (TOTP): required, together with the
 * password and a hand-drawn signature, to sign appointments and receipts.
 */
export default function MySecurity() {
  const session = useSession()
  const [status, setStatus] = useState(null)
  const [setup, setSetup] = useState(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')

  function load() {
    return api('/auth/totp').then(setStatus).catch((err) => setError(err.message))
  }

  useEffect(() => {
    load()
  }, [])

  async function begin() {
    setError('')
    try {
      setSetup(await api('/auth/totp/setup', { method: 'POST', message: false }))
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function confirm(event) {
    event.preventDefault()
    setError('')
    try {
      setStatus(await api('/auth/totp/confirm', { method: 'POST', body: { code: code.replace(/\D/g, '') }, message: 'Authenticator confirmed — you can sign now' }))
      setSetup(null)
      setCode('')
      await session.refresh()
    } catch (err) {
      setError(err.message)
    }
  }

  if (!status) return <p className="muted">Loading…</p>
  const grouped = setup ? setup.secret.match(/.{1,4}/g).join(' ') : ''

  return (
    <section>
      <PageHeader
        crumb="My agenda / Security"
        title="Security"
        hint="Signing an appointment or a receipt asks for your password, a code from your authenticator app and your hand-drawn signature."
      />
      <div className="grid two">
        <div className="panel" data-testid="totp-status">
          <h2>Authenticator app</h2>
          {status.enabled ? (
            <p className="notice ok">Set up on {formatDateTime(status.enabled_at)}. Codes from your app are required to sign.</p>
          ) : status.pending ? (
            <p className="notice warn">Setup started but not confirmed yet. Enter a code from the app to finish.</p>
          ) : (
            <p className="notice warn">Not set up: you cannot sign until the authenticator is confirmed.</p>
          )}
          {status.sign_locked_until && new Date(status.sign_locked_until) > new Date() && (
            <p className="notice warn">Too many failed attempts: signing is locked until {formatDateTime(status.sign_locked_until)}.</p>
          )}
          {!status.enabled && !setup && (
            <div className="actions"><button type="button" onClick={begin}>{status.pending ? 'Start over' : 'Set up authenticator'}</button></div>
          )}
          {status.enabled && <p className="muted">Lost your phone? Ask an administrator to reset your authenticator.</p>}
        </div>
        {setup && (
          <form className="panel" onSubmit={confirm} data-testid="totp-setup">
            <h2>1. Add the account to your app</h2>
            <p className="muted">In Google Authenticator, Microsoft Authenticator, Authy or any TOTP app choose "enter a setup key" and type:</p>
            <p className="totp-secret" data-testid="totp-secret">{grouped}</p>
            <p className="muted">Account <strong>{setup.account}</strong>, issuer <strong>{setup.issuer}</strong>, time-based, 6 digits. Or paste this link into the app:</p>
            <code className="otpauth">{setup.otpauth_uri}</code>
            <h2 style={{ marginTop: 16 }}>2. Confirm with a code</h2>
            <label>
              Code shown by the app now
              <input inputMode="numeric" autoComplete="one-time-code" placeholder="123 456" maxLength={7} value={code} onChange={(event) => setCode(event.target.value)} required />
            </label>
            {error && <p className="error">{error}</p>}
            <div className="actions">
              <button type="submit" disabled={code.replace(/\D/g, '').length !== 6}>Confirm authenticator</button>
              <button type="button" className="ghost" onClick={() => setSetup(null)}>Cancel</button>
            </div>
          </form>
        )}
        {!setup && error && <p className="error">{error}</p>}
      </div>
    </section>
  )
}
