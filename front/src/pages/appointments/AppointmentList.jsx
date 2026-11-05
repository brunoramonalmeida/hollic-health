import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { formatDateTime } from '../../dates'
import { APPOINTMENT_STATUS, allowanceLabel, statusLabel } from '../../labels'
import AttendancePanel, { AttendanceBadge } from '../../components/Attendance'
import SignaturePanel, { SignatureBadge } from '../../components/Signature'
import { useSession } from '../../session'

const AWAITING = 'awaiting_signature'

export default function AppointmentList() {
  const session = useSession()
  const [rows, setRows] = useState([])
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  async function load() {
    setRows(await api('/appointments'))
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [])

  async function cancel(row) {
    if (!window.confirm(`Cancel the appointment of ${row.customer_name} on ${formatDateTime(row.marked_at)}?`)) return
    setError('')
    try {
      await api(`/appointments/${row.id}`, {
        method: 'PUT',
        message: 'Appointment cancelled',
        body: {
          customer_id: row.customer_id,
          dependent_id: row.dependent_id,
          professional_id: row.professional_id,
          marked_at: row.marked_at,
          status: 'cancelada',
          notes: row.notes,
          urgent: Boolean(row.urgent),
          remote: Boolean(row.remote),
        },
      })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  async function remove(row) {
    if (!window.confirm('Delete this appointment permanently?')) return
    setError('')
    try {
      await api(`/appointments/${row.id}`, { method: 'DELETE', message: 'Appointment deleted' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  const visible = status === AWAITING
    ? rows.filter((row) => row.awaiting_signature)
    : status
      ? rows.filter((row) => row.status === status)
      : rows
  const awaiting = rows.filter((row) => row.awaiting_signature).length
  const own = session.isClient || session.isProfessional

  return (
    <section>
      <PageHeader
        crumb={session.isClient ? 'My area / Appointments' : session.isProfessional ? 'My agenda / Appointments' : 'Care / Appointments'}
        title={own ? 'My appointments' : 'Appointments'}
        hint={
          session.isClient
            ? 'Your appointments and those of your dependents. Prices already include your plan discount; appointments booked over the plan\'s limit for the period pay the full price.'
            : session.isProfessional
              ? 'Your agenda. An appointment is completed — and paid — once you sign it after the visit.'
              : "Scheduled and completed appointments take a slot of the professional's agenda; completion is the professional's signature."
        }
      >
        <select aria-label="Status filter" value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All statuses</option>
          {Object.entries(APPOINTMENT_STATUS).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
          <option value={AWAITING}>Awaiting signature{awaiting ? ` (${awaiting})` : ''}</option>
        </select>
        <span className="count">{visible.length} records</span>
        {session.canBookAppointments && <Link className="button" to="/appointments/new">New appointment</Link>}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Patient</th>
              {!session.isProfessional && <th>Professional</th>}
              <th>Where</th>
              <th>Status</th>
              <th>Attendance</th>
              <th>Signature</th>
              {!session.isProfessional && <th>Discount</th>}
              <th>{session.isProfessional ? 'Your fee' : 'Total'}</th>
              {session.isStaff && <th>Booked by</th>}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={row.id}>
                <td>{formatDateTime(row.marked_at)}</td>
                <td>
                  {row.dependent_name ? (
                    <>
                      {row.dependent_name} <small className="muted">({row.customer_name})</small>
                    </>
                  ) : (
                    row.customer_name
                  )}
                </td>
                {!session.isProfessional && (
                  <td>
                    {row.professional_name} <small className="muted">{row.specialty_name}</small>
                  </td>
                )}
                <td>
                  {row.remote ? (
                    <>
                      Video call <small className="muted">({row.clinic_name})</small>
                      {row.meeting_url && row.status === 'agendada' && (
                        <>
                          <br />
                          <a className="small-link" href={row.meeting_url} target="_blank" rel="noreferrer">Join the call</a>
                        </>
                      )}
                    </>
                  ) : (
                    <>
                      {row.clinic_name}
                      {row.clinic_maps_url && (
                        <>
                          <br />
                          <a className="small-link" href={row.clinic_maps_url} target="_blank" rel="noreferrer">Directions</a>
                        </>
                      )}
                    </>
                  )}
                </td>
                <td>
                  <span className={`badge ${row.status}`}>{statusLabel(APPOINTMENT_STATUS, row.status)}</span>
                  {row.on_call && <> <span className="badge on-call" title="On-call service: on-call rates">on-call</span></>}
                  {row.urgent && <> <span className="badge urgent" title="Ad-hoc urgent visit: urgent rate">urgent</span></>}
                  {row.remote && <> <span className="badge remote" title="Remote appointment (video call): remote rates">remote</span></>}
                  {row.prescriptions_count > 0 && (
                    <> <span className="badge prescription" title={`${row.prescriptions_count} prescription file${row.prescriptions_count === 1 ? '' : 's'} attached`}>⎘ {row.prescriptions_count}</span></>
                  )}
                </td>
                <td>
                  <AttendanceBadge appointment={row} />
                  {(row.can_check_in || row.can_check_out) && (
                    <div style={{ marginTop: 6 }}>
                      <AttendancePanel appointment={row} compact onChange={() => load()} />
                    </div>
                  )}
                </td>
                <td>
                  <SignatureBadge appointment={row} />
                  {(row.can_sign || row.can_revoke_signature) && (
                    <div style={{ marginTop: 6 }}>
                      <SignaturePanel appointment={row} compact onChange={() => load()} />
                    </div>
                  )}
                </td>
                {!session.isProfessional && (
                  <td>
                    {row.over_limit ? (
                      <>
                        <span className="badge over-limit" title={`The plan's ${allowanceLabel(row.plan_limit, row.plan_limit_period)} were already booked: full price`}>Over plan limit</span>
                        {row.modality_name && <small className="muted"> ({row.modality_name})</small>}
                      </>
                    ) : row.discount_percent > 0 ? (
                      <>
                        {row.discount_percent}% <small className="muted">({row.modality_name})</small>
                      </>
                    ) : (
                      '—'
                    )}
                  </td>
                )}
                <td>
                  {session.isProfessional ? (
                    <strong>{row.status === 'realizada' ? money(row.professional_price_cents) : <span className="muted">{money(row.professional_price_cents)} when signed</span>}</strong>
                  ) : (
                    <>
                      <strong>{money(row.amount_cents)}</strong>
                      {row.discount_cents > 0 && <small className="muted"> of {money(row.gross_cents)}</small>}
                    </>
                  )}
                </td>
                {session.isStaff && <td>{row.created_by_name || '—'}</td>}
                <td className="actions">
                  <Link className="button ghost" to={`/appointments/${row.id}`}>{session.isProfessional ? 'Open' : 'Edit'}</Link>
                  {row.status === 'agendada' && session.canBookAppointments && (
                    <button type="button" className="ghost" onClick={() => cancel(row)}>Cancel</button>
                  )}
                  {session.canDeleteAppointments && (
                    <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>
                  )}
                </td>
              </tr>
            ))}
            {visible.length === 0 && <tr><td colSpan={12} className="muted">No appointments.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
