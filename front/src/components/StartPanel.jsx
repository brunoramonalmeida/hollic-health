import { useState } from 'react'
import { api } from '../api'
import { formatDateTime } from '../dates'
import { useSession } from '../session'

/**
 * Start of the visit: the reception or the professional types the code shown
 * on the patient's virtual card. `onChange` receives the updated appointment.
 */
export default function StartPanel({ appointment, onChange }) {
  const session = useSession()
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const ready = code.replace(/\D/g, '').length === 6

  // Not a <form>: this panel also lives inside the appointment edit form.
  async function startNow() {
    if (!ready || busy) return
    setBusy(true)
    setError('')
    try {
      const updated = await api(`/appointments/${appointment.id}/start`, {
        method: 'POST',
        body: { code },
        message: 'Card verified — appointment started',
        silent: true,
      })
      setCode('')
      onChange?.(updated)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function undo() {
    if (!window.confirm('Undo the start of this appointment?')) return
    setBusy(true)
    try {
      const updated = await api(`/appointments/${appointment.id}/start`, { method: 'DELETE', message: 'Start undone' })
      onChange?.(updated)
    } catch {
      /* toast already shown */
    } finally {
      setBusy(false)
    }
  }

  if (session.isClient) return null
  const locked = appointment.start_locked_until && new Date(appointment.start_locked_until) > new Date()

  return (
    <div className="panel start" data-testid="start-panel">
      <div className="card-head" style={{ padding: 0, marginBottom: 8 }}>
        <h2>Start of the visit</h2>
        {appointment.started_at ? <span className="badge signed">Patient verified</span> : <span className="badge none">Not started</span>}
      </div>
      {appointment.started_at ? (
        <>
          <p className="muted">
            Started on {formatDateTime(appointment.started_at)} by {appointment.started_by_name}, after the patient's card code was verified.
          </p>
          {session.isStaff && !appointment.signed_at && appointment.status !== 'realizada' && (
            <div className="actions">
              <button type="button" className="ghost" disabled={busy} onClick={undo}>Undo start</button>
            </div>
          )}
        </>
      ) : appointment.status === 'cancelada' ? (
        <p className="muted">Cancelled appointments are not started.</p>
      ) : appointment.status === 'realizada' ? (
        <p className="muted">Completed.</p>
      ) : locked ? (
        <p className="notice warn">Too many wrong codes. Locked until {formatDateTime(appointment.start_locked_until)}.</p>
      ) : appointment.can_start ? (
        <div>
          <label>
            Code on the patient's virtual card
            <input
              value={code}
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="123 456"
              maxLength={7}
              onChange={(event) => setCode(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  startNow()
                }
              }}
            />
            <small className="hint">Ask the patient to open "My virtual card"; the code changes every 30 seconds.</small>
          </label>
          {error && <p className="error">{error}</p>}
          <div className="actions">
            <button type="button" disabled={busy || !ready} onClick={startNow}>Verify card &amp; start</button>
          </div>
        </div>
      ) : (
        <p className="muted">
          {session.isProfessional && appointment.professional_id !== session.professionalId
            ? 'Only the appointment\'s professional or the reception can start it.'
            : `The appointment can be started from ${formatDateTime(appointment.check_in_opens_at)}, with the patient's card code.`}
        </p>
      )}
    </div>
  )
}
