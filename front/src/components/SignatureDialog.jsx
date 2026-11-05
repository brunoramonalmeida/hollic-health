import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import SignaturePad from './SignaturePad'
import { useSession } from '../session'

/**
 * The professional's strong signature: account password, authenticator
 * code and a hand-drawn signature (omitted when `drawing` is false, e.g. to
 * revoke). `onConfirm(body)` receives `{ password, code, signature_image?, notes? }`
 * and should return a promise; the dialog shows the error it throws.
 */
export default function SignatureDialog({ title, summary, confirmLabel = 'Sign', drawing = true, withNotes = false, onConfirm, onClose }) {
  const session = useSession()
  const pad = useRef(null)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [notes, setNotes] = useState('')
  const [drawn, setDrawn] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const enabled = session.user?.totp_enabled
  const ready = password.length > 0 && code.replace(/\D/g, '').length === 6 && (!drawing || drawn)

  async function submit(event) {
    event.preventDefault()
    if (!ready || busy) return
    setBusy(true)
    setError('')
    try {
      const body = { password, code: code.replace(/\D/g, ''), notes }
      if (drawing) body.signature_image = pad.current.toJpeg()
      await onConfirm(body)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="signature-dialog-title" data-testid="signature-dialog">
      <form className="dialog" onSubmit={submit}>
        <h2 id="signature-dialog-title">{title}</h2>
        {summary && <p className="muted">{summary}</p>}
        {!enabled ? (
          <p className="notice warn">
            Your authenticator app is not set up yet, so you cannot sign. <Link to="/my-security">Set it up now</Link>.
          </p>
        ) : (
          <>
            <div className="fields two">
              <label>
                Your password
                <input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
              </label>
              <label>
                Authenticator code
                <input inputMode="numeric" autoComplete="one-time-code" placeholder="123 456" maxLength={7} value={code} onChange={(event) => setCode(event.target.value)} required />
              </label>
            </div>
            {drawing && (
              <div className="field">
                <span className="field-label">Your signature</span>
                <SignaturePad ref={pad} onChange={setDrawn} />
              </div>
            )}
            {withNotes && (
              <label>
                Note (optional)
                <textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
              </label>
            )}
          </>
        )}
        {error && <p className="error">{error}</p>}
        <div className="actions">
          {enabled && <button type="submit" disabled={!ready || busy}>{confirmLabel}</button>}
          <button type="button" className="ghost" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </div>
  )
}
