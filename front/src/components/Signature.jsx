import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { formatDateTime } from '../dates'
import { useSession } from '../session'
import { toast } from '../toast'
import SignatureDialog from './SignatureDialog'

/** Badge: where the appointment stands regarding the professional's signature. */
export function SignatureBadge({ appointment }) {
  if (appointment.status === 'cancelada') return null
  if (appointment.signed_at) return <span className="badge signed">Signed</span>
  if (appointment.awaiting_signature) return <span className="badge awaiting-signature">Awaiting signature</span>
  return <span className="badge none">Not signed</span>
}

/**
 * Sign / revoke buttons for the professional's own login; a status line for
 * everyone else. `onChange` receives the updated appointment.
 */
export default function SignaturePanel({ appointment, onChange, compact = false }) {
  const session = useSession()
  const [dialog, setDialog] = useState(null) // 'sign' | 'revoke'

  async function signNow(body) {
    // Errors are shown inside the dialog (silent), successes as a toast.
    const updated = await api(`/appointments/${appointment.id}/sign`, { method: 'POST', body, silent: true })
    toast.success('Appointment signed — it is now completed')
    setDialog(null)
    onChange?.(updated)
  }

  async function revoke(body) {
    const updated = await api(`/appointments/${appointment.id}/sign`, { method: 'DELETE', body, silent: true })
    toast.success('Signature revoked')
    setDialog(null)
    onChange?.(updated)
  }

  const canSign = session.canSignAppointments && appointment.can_sign
  const canRevoke = session.canSignAppointments && appointment.can_revoke_signature
  const dialogs = (
    <>
      {dialog === 'sign' && (
        <SignatureDialog
          title="Sign the appointment"
          summary={`${appointment.dependent_name || appointment.customer_name} · ${formatDateTime(appointment.marked_at)} · by signing you attest the visit happened and it enters your payout.`}
          confirmLabel="Sign appointment"
          withNotes
          onConfirm={signNow}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'revoke' && (
        <SignatureDialog
          title="Revoke your signature"
          summary="The appointment goes back to scheduled and leaves your payout."
          confirmLabel="Revoke signature"
          drawing={false}
          onConfirm={revoke}
          onClose={() => setDialog(null)}
        />
      )}
    </>
  )

  if (compact) {
    return (
      <div className="actions">
        {canSign && <button type="button" onClick={() => setDialog('sign')}>Sign</button>}
        {canRevoke && <button type="button" className="ghost" onClick={() => setDialog('revoke')}>Revoke</button>}
        {dialogs}
      </div>
    )
  }

  return (
    <div className="panel signature" data-testid="signature-panel">
      <div className="card-head" style={{ padding: 0, marginBottom: 8 }}>
        <h2>Professional's signature</h2>
        <SignatureBadge appointment={appointment} />
      </div>
      {appointment.signed_at ? (
        <>
          <p className="muted">
            Signed by {appointment.signed_by_name} on {formatDateTime(appointment.signed_at)} (password, authenticator code and hand-drawn signature). The appointment is completed and enters the payout.
          </p>
          {appointment.signature_image && <img className="signature-image" src={appointment.signature_image} alt={`Signature of ${appointment.signed_by_name}`} />}
        </>
      ) : appointment.status === 'cancelada' ? (
        <p className="muted">Cancelled appointments are not signed.</p>
      ) : appointment.awaiting_signature && !appointment.started_at ? (
        <p className="notice warn" data-testid="signature-needs-start">
          The appointment was not started: verify the patient's card code (start of the visit) before {session.canSignAppointments ? 'signing' : 'the professional can sign'}.
        </p>
      ) : appointment.awaiting_signature ? (
        <p className="muted">
          The visit {appointment.checked_out_at ? 'ended' : 'slot has passed'}; the appointment is completed once {session.canSignAppointments ? 'you sign' : 'the professional signs'} it.
        </p>
      ) : (
        <p className="muted">Only the professional completes an appointment, by signing it after the visit.</p>
      )}
      {canSign && !session.user?.totp_enabled && (
        <p className="notice warn">Set up your authenticator app in <Link to="/my-security">Security</Link> before signing.</p>
      )}
      {canSign && (
        <div className="actions">
          <button type="button" onClick={() => setDialog('sign')}>Sign appointment</button>
        </div>
      )}
      {canRevoke && (
        <div className="actions">
          <button type="button" className="ghost" onClick={() => setDialog('revoke')}>Revoke signature</button>
        </div>
      )}
      {dialogs}
    </div>
  )
}
