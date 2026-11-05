import { useState } from 'react'
import { api } from '../api'
import { formatDateTime } from '../dates'

export const ATTENDANCE_LABEL = {
  awaiting: 'Awaiting check-in',
  checked_in: 'Checked in',
  in_progress: 'In progress',
  checked_out: 'Attended',
  completed: 'Completed',
  no_show: 'No-show',
  cancelled: 'Cancelled',
}

/** Device position for the audit trail; resolves to {} when unavailable or denied. */
function currentPosition() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve({})
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ latitude: Number(pos.coords.latitude.toFixed(6)), longitude: Number(pos.coords.longitude.toFixed(6)) }),
      () => resolve({}),
      { timeout: 4000, maximumAge: 60000 },
    )
  })
}

export function AttendanceBadge({ appointment }) {
  return <span className={`badge att-${appointment.attendance}`}>{ATTENDANCE_LABEL[appointment.attendance] || appointment.attendance}</span>
}

/** Check-in / check-out buttons with the audit timeline; `onChange` receives the updated appointment. */
export default function AttendancePanel({ appointment, onChange, compact = false }) {
  const [busy, setBusy] = useState(false)

  async function act(kind) {
    setBusy(true)
    try {
      const position = await currentPosition()
      const updated = await api(`/appointments/${appointment.id}/${kind}`, {
        method: 'POST',
        body: position,
        message: kind === 'check-in' ? 'Check-in confirmed' : "Check-out confirmed — awaiting the professional's signature",
      })
      onChange?.(updated)
    } catch {
      /* toast already shown */
    } finally {
      setBusy(false)
    }
  }

  const buttons = (
    <>
      {appointment.can_check_in && <button type="button" disabled={busy} onClick={() => act('check-in')}>Check in</button>}
      {appointment.can_check_out && <button type="button" disabled={busy} onClick={() => act('check-out')}>Check out</button>}
    </>
  )

  if (compact) return <div className="actions">{buttons}</div>

  return (
    <div className="panel attendance">
      <div className="card-head" style={{ padding: 0, marginBottom: 8 }}>
        <h2>Attendance</h2>
        <AttendanceBadge appointment={appointment} />
      </div>
      <ol className="timeline">
        <li className="done">
          <strong>Scheduled{appointment.remote ? ' · video call' : ''}</strong>
          <small>{formatDateTime(appointment.marked_at)}{appointment.created_by_name ? ` · by ${appointment.created_by_name}` : ''}</small>
        </li>
        <li className={appointment.checked_in_at ? 'done' : ''}>
          <strong>Check-in{appointment.remote ? ' (joined the call)' : ''}</strong>
          <small>
            {appointment.checked_in_at
              ? `${formatDateTime(appointment.checked_in_at)} · by ${appointment.checked_in_by_name}`
              : appointment.attendance === 'awaiting'
                ? `Opens ${formatDateTime(appointment.check_in_opens_at)} · closes ${formatDateTime(appointment.check_in_closes_at)}`
                : '—'}
          </small>
        </li>
        <li className={appointment.started_at ? 'done' : ''}>
          <strong>Start (card verified)</strong>
          <small>
            {appointment.started_at
              ? `${formatDateTime(appointment.started_at)} · by ${appointment.started_by_name}`
              : "The patient's virtual card code is required to start"}
          </small>
        </li>
        <li className={appointment.checked_out_at ? 'done' : ''}>
          <strong>Check-out</strong>
          <small>
            {appointment.checked_out_at
              ? `${formatDateTime(appointment.checked_out_at)} · by ${appointment.checked_out_by_name}`
              : appointment.status === 'realizada'
                ? 'Signed by the professional without check-out'
                : '—'}
          </small>
        </li>
        <li className={appointment.signed_at ? 'done' : ''}>
          <strong>Signature</strong>
          <small>
            {appointment.signed_at
              ? `${formatDateTime(appointment.signed_at)} · by ${appointment.signed_by_name}`
              : appointment.awaiting_signature
                ? "Awaiting the professional's signature"
                : '—'}
          </small>
        </li>
      </ol>
      {(appointment.can_check_in || appointment.can_check_out) && <div className="actions">{buttons}</div>}
      {appointment.attendance === 'no_show' && <p className="notice warn">The patient did not check in for this appointment.</p>}
      {appointment.attendance === 'awaiting' && !appointment.can_check_in && (
        <p className="muted">Check-in becomes available two hours before the appointment.</p>
      )}
      {appointment.remote && appointment.can_check_in && appointment.meeting_url && (
        <p className="muted">
          Remote appointment: <a href={appointment.meeting_url} target="_blank" rel="noreferrer">join the call</a> and check in to confirm your presence.
        </p>
      )}
    </div>
  )
}
