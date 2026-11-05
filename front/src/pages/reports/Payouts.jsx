import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../../api'
import { formatDate, formatDateTime } from '../../dates'
import { APPOINTMENT_STATUS, statusLabel } from '../../labels'

/**
 * Control of the payments due to professionals.
 *
 * Only completed appointments pay, each at the professional's fee
 * snapshotted when it was booked; scheduled and cancelled ones are shown for
 * control but add nothing. Period and professional/clinic come from the
 * Reports page filters; name and specialty are filtered here.
 */
export default function Payouts({ filters, downloadCsv }) {
  const [name, setName] = useState('')
  const [specialtyId, setSpecialtyId] = useState('')
  const [specialties, setSpecialties] = useState([])
  const [report, setReport] = useState(null)
  const [statement, setStatement] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api('/specialties').then(setSpecialties).catch((err) => setError(err.message))
  }, [])

  const params = new URLSearchParams({ granularity: filters.granularity, start: filters.start, end: filters.end })
  if (filters.professional_id) params.set('professional_id', filters.professional_id)
  if (filters.clinic_id) params.set('clinic_id', filters.clinic_id)
  if (name.trim()) params.set('name', name.trim())
  if (specialtyId) params.set('specialty_id', specialtyId)
  const query = params.toString()

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    // Debounce the name box so a quick typist does not fire one request per key.
    const timer = setTimeout(() => {
      api(`/reports/payouts?${query}`)
        .then((body) => active && setReport(body))
        .catch((err) => active && setError(err.message))
        .finally(() => active && setLoading(false))
    }, name ? 250 : 0)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [query]) // eslint-disable-line react-hooks/exhaustive-deps

  // The statement follows the date range; close it when the range changes.
  useEffect(() => {
    setStatement(null)
  }, [filters.start, filters.end])

  function openStatement(professionalId) {
    api(`/reports/payouts/${professionalId}?start=${filters.start}&end=${filters.end}`)
      .then(setStatement)
      .catch((err) => setError(err.message))
  }

  const periods = report ? report.periods : []
  const csvQuery = new URLSearchParams(params)
  csvQuery.delete('granularity')

  return (
    <>
      <div className="toolbar" style={{ justifyContent: 'space-between' }}>
        <div className="toolbar">
          <label className="inline">
            Name
            <input
              type="search"
              placeholder="Search professional"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="inline">
            Specialty
            <select value={specialtyId} onChange={(event) => setSpecialtyId(event.target.value)}>
              <option value="">All</option>
              {specialties.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          {(name || specialtyId) && (
            <button type="button" className="ghost" onClick={() => { setName(''); setSpecialtyId('') }}>Clear</button>
          )}
        </div>
        <button
          type="button"
          className="ghost"
          onClick={() => downloadCsv(`/reports/payouts.csv?${csvQuery.toString()}`, `payouts_${filters.start}_${filters.end}.csv`)}
        >
          Export (CSV)
        </button>
      </div>
      <p className="notice info">
        Only appointments <strong>signed by the professional</strong> (completed) are paid, each at the professional's fee in force when it was booked.
        Scheduled appointments are not due yet; cancelled ones never are.
      </p>
      {error && <p className="error">{error}</p>}
      {report && (
        <div className={`stack ${loading ? 'loading' : ''}`}>
          <div className="kpis">
            <div className="kpi"><span>Professionals</span><strong>{report.professionals_count}</strong></div>
            <div className="kpi"><span>Signed (paid)</span><strong>{report.totals.completed}</strong></div>
            <div className="kpi"><span>Scheduled (not due)</span><strong>{report.totals.scheduled}</strong></div>
            <div className="kpi"><span>Cancelled</span><strong>{report.totals.cancelled}</strong></div>
            <div className="kpi accent"><span>Earned in range</span><strong>{money(report.totals.payout_cents)}</strong></div>
            <div className="kpi">
              <span>Paid in range</span>
              <strong>{money(report.totals.paid_cents)}</strong>
              {report.totals.unconfirmed_receipts > 0 && <small>{report.totals.unconfirmed_receipts} receipts unsigned</small>}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <h2>Per professional</h2>
              <span className="muted">{formatDate(report.start)} – {formatDate(report.end)}</span>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Professional</th>
                  <th>Specialty</th>
                  <th>Clinic</th>
                  <th>Current fee</th>
                  <th>Completed</th>
                  <th>Scheduled</th>
                  <th>Cancelled</th>
                  {periods.map((period) => <th key={period.period}>{period.period}</th>)}
                  <th>Earned</th>
                  <th>Paid in range</th>
                  <th>Balance today</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {report.professionals.map((row) => (
                  <tr key={row.professional_id} className={statement && statement.professional_id === row.professional_id ? 'selected' : ''}>
                    <td><Link to={`/professionals/${row.professional_id}`}>{row.name}</Link></td>
                    <td>{row.specialty_name}</td>
                    <td>{row.clinic_name}</td>
                    <td>
                      {money(row.fee_cents)}
                      {row.on_call_fee_cents > 0 && <small className="muted"> · on-call {money(row.on_call_fee_cents)}</small>}
                      {row.urgent_fee_cents > 0 && <small className="muted"> · urgent {money(row.urgent_fee_cents)}</small>}
                      {row.remote_fee_cents > 0 && <small className="muted"> · remote {money(row.remote_fee_cents)}</small>}
                    </td>
                    <td>
                      {row.completed}
                      {row.on_call > 0 && <small className="muted"> · {row.on_call} on-call ({money(row.on_call_cents)})</small>}
                      {row.urgent > 0 && <small className="muted"> · {row.urgent} urgent ({money(row.urgent_cents)})</small>}
                      {row.remote > 0 && <small className="muted"> · {row.remote} remote ({money(row.remote_cents)})</small>}
                    </td>
                    <td className="muted">{row.scheduled}</td>
                    <td className="muted">{row.cancelled}</td>
                    {row.periods.map((period) => (
                      <td key={period.period}>{period.payout_cents ? money(period.payout_cents) : <span className="muted">—</span>}</td>
                    ))}
                    <td><strong>{money(row.payout_cents)}</strong></td>
                    <td>
                      {money(row.paid_cents)}
                      {row.unconfirmed_receipts > 0 && <small className="muted"> · {row.unconfirmed_receipts} unsigned</small>}
                    </td>
                    <td><strong>{money(row.balance_cents)}</strong>{row.advances_outstanding_cents > 0 && <small className="muted"> · adv. {money(row.advances_outstanding_cents)}</small>}</td>
                    <td className="actions">
                      <button type="button" className="ghost" onClick={() => openStatement(row.professional_id)}>Statement</button>
                      <Link className="button ghost" to={`/payouts?payee=professional:${row.professional_id}`}>Pay</Link>
                    </td>
                  </tr>
                ))}
                {report.professionals.length === 0 && (
                  <tr><td colSpan={11 + periods.length} className="muted">No professional matches the filters.</td></tr>
                )}
              </tbody>
              {report.professionals.length > 0 && (
                <tfoot>
                  <tr>
                    <th colSpan={4}>Total</th>
                    <th>{report.totals.completed}</th>
                    <th>{report.totals.scheduled}</th>
                    <th>{report.totals.cancelled}</th>
                    {periods.map((period) => <th key={period.period}>{money(period.payout_cents)}</th>)}
                    <th>{money(report.totals.payout_cents)}</th>
                    <th>{money(report.totals.paid_cents)}</th>
                    <th>{money(report.professionals.reduce((sum, row) => sum + row.balance_cents, 0))}</th>
                    <th></th>
                  </tr>
                </tfoot>
              )}
            </table>
          </div>

          {statement && (
            <div className="card">
              <div className="card-head">
                <h2>Statement · {statement.name}</h2>
                <div className="actions">
                  <span className="muted">
                    {statement.specialty_name} · {statement.clinic_name} · {formatDate(statement.start)} – {formatDate(statement.end)}
                  </span>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => downloadCsv(
                      `/reports/payouts.csv?start=${statement.start}&end=${statement.end}&professional_id=${statement.professional_id}`,
                      `payout_${statement.professional_id}_${statement.start}_${statement.end}.csv`,
                    )}
                  >
                    Export (CSV)
                  </button>
                  <button type="button" className="ghost" onClick={() => setStatement(null)}>Close</button>
                </div>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Patient</th>
                    <th>Plan</th>
                    <th>Status</th>
                    <th>Signed</th>
                    <th>Gross</th>
                    <th>Discount</th>
                    <th>Billed</th>
                    <th>Due to professional</th>
                  </tr>
                </thead>
                <tbody>
                  {statement.lines.map((line) => (
                    <tr key={line.appointment_id}>
                      <td><Link to={`/appointments/${line.appointment_id}`}>{formatDateTime(line.marked_at)}</Link></td>
                      <td>{line.patient}</td>
                      <td>
                        {line.plan || <span className="muted">—</span>}
                        {line.over_limit && <> <span className="badge over-limit" title="Booked over the plan's appointment limit: the patient paid the full price">over limit</span></>}
                        {line.on_call && <> <span className="badge on-call" title="On-call service: paid at the on-call rate">on-call</span></>}
                        {line.urgent && <> <span className="badge urgent" title="Ad-hoc urgent visit: paid at the urgent rate">urgent</span></>}
                        {line.remote && <> <span className="badge remote" title="Remote appointment (video call): paid at the remote rate">remote</span></>}
                      </td>
                      <td><span className={`badge ${line.status}`}>{statusLabel(APPOINTMENT_STATUS, line.status)}</span></td>
                      <td>{line.signed_at ? formatDateTime(line.signed_at) : <span className="muted">—</span>}</td>
                      <td>{money(line.gross_cents)}</td>
                      <td>{money(line.discount_cents)}</td>
                      <td>{money(line.amount_cents)}</td>
                      <td>{line.payout_cents ? <strong>{money(line.payout_cents)}</strong> : <span className="muted">not paid</span>}</td>
                    </tr>
                  ))}
                  {statement.lines.length === 0 && <tr><td colSpan={9} className="muted">No appointments in this range.</td></tr>}
                </tbody>
                {statement.lines.length > 0 && (
                  <tfoot>
                    <tr>
                      <th colSpan={3}>
                        {statement.completed} completed · {statement.scheduled} scheduled · {statement.cancelled} cancelled
                      </th>
                      <th colSpan={5}>Total due</th>
                      <th>{money(statement.payout_cents)}</th>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          )}
        </div>
      )}
    </>
  )
}
