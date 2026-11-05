import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import AttendancePanel from '../../components/Attendance'
import LocationMap from '../../components/LocationMap'
import SignaturePanel from '../../components/Signature'
import StartPanel from '../../components/StartPanel'
import PrescriptionsPanel from '../../components/Prescriptions'
import ChatPanel from '../../components/Chat'
import { formatDateTime } from '../../dates'
import { APPOINTMENT_STATUS, statusLabel } from '../../labels'

/**
 * Read-only appointment page for the professional's own login: the visit,
 * the attendance trail and the signature. Professionals do not edit
 * appointments; they sign them.
 */
export default function AppointmentView() {
  const { id } = useParams()
  const [row, setRow] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setError('')
    api(`/appointments/${id}`).then(setRow).catch((err) => setError(err.message))
  }, [id])

  if (error) return <p className="error">{error}</p>
  if (!row) return <p className="muted">Loading…</p>

  const patient = row.dependent_name ? `${row.dependent_name} (dependent of ${row.customer_name})` : row.customer_name

  return (
    <section>
      <PageHeader
        back={{ to: '/appointments', label: 'My appointments' }}
        title={`Appointment of ${formatDateTime(row.marked_at)}`}
        hint={
          row.remote
            ? 'Join the video call at the slot time, ask the patient for the card code to start the visit, then sign it after the visit to complete it; only signed appointments enter your payout.'
            : "Start the visit with the patient's card code, then sign it after the visit to complete it; only signed appointments enter your payout."
        }
      >
        {row.on_call && <span className="badge on-call">On-call service</span>}
        {row.urgent && <span className="badge urgent">Urgent visit</span>}
        {row.remote && <span className="badge remote">Remote appointment</span>}
        <span className={`badge ${row.status}`}>{statusLabel(APPOINTMENT_STATUS, row.status)}</span>
      </PageHeader>
      <div className="grid wide">
        <div className="stack">
          <div className="panel status-panel">
            <h2>Visit</h2>
            <dl>
              <dt>Patient</dt><dd>{patient}</dd>
              <dt>When</dt><dd>{formatDateTime(row.marked_at)}</dd>
              <dt>Specialty</dt><dd>{row.specialty_name}</dd>
              <dt>Clinic</dt><dd>{row.clinic_name}</dd>
              <dt>Plan</dt>
              <dd>
                {row.modality_name || '—'}
                {row.over_limit && <small className="muted"> · over the plan limit (full price for the patient)</small>}
              </dd>
              <dt>Your fee</dt>
              <dd>
                <strong>{money(row.professional_price_cents)}</strong>
                {row.on_call && <> <span className="badge on-call">on-call rate</span></>}
                {row.urgent && <> <span className="badge urgent">urgent rate</span></>}
                {row.remote && <> <span className="badge remote">remote rate</span></>}
                {row.status !== 'realizada' && <small className="muted"> · paid once signed</small>}
              </dd>
              <dt>Booked by</dt><dd>{row.created_by_name || '—'}</dd>
            </dl>
            {row.notes && (
              <>
                <h3 className="section-title">Notes</h3>
                <pre className="notes">{row.notes}</pre>
              </>
            )}
          </div>
          <StartPanel appointment={row} onChange={setRow} />
          <SignaturePanel appointment={row} onChange={setRow} />
          <AttendancePanel appointment={row} onChange={setRow} />
          <PrescriptionsPanel appointment={row} />
          {row.remote && <ChatPanel appointment={row} />}
        </div>
        <div className="stack">
          {row.remote ? (
            <div className="card padded where remote-call">
              <div className="card-head">
                <h2>Video call</h2>
                <span className="badge remote">Remote appointment</span>
              </div>
              {row.meeting_url ? (
                <p className="address">
                  <a className="button" href={row.meeting_url} target="_blank" rel="noreferrer">Join the call</a>
                  <br />
                  <small className="muted">{row.meeting_url}</small>
                </p>
              ) : (
                <p className="muted">No meeting link yet; ask the reception.</p>
              )}
              <p className="muted">The patient joins with the same link. Ask for the virtual card code over the call to start the visit.</p>
            </div>
          ) : (
            <div className="card padded where">
              <div className="card-head">
                <h2>Where</h2>
                <span className="muted">{row.clinic_name}</span>
              </div>
              <p className="address">{row.clinic_address}</p>
              {row.clinic_phone && <p className="muted">Phone: {row.clinic_phone}</p>}
              <LocationMap latitude={row.clinic_latitude} longitude={row.clinic_longitude} address={row.clinic_address} height={220} />
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
