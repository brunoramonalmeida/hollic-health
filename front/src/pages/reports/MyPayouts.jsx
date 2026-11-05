import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import PayoutAccount from '../../components/PayoutAccount'
import { addDays, formatDate, formatDateTime, isoDate, startOfWeek, today } from '../../dates'
import { APPOINTMENT_STATUS, statusLabel } from '../../labels'
import { useSession } from '../../session'
import { toast } from '../../toast'

const API_URL = import.meta.env.VITE_API_URL || '/api'

async function downloadCsv(path, filename) {
  const response = await fetch(`${API_URL}${path}`, { headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } })
  if (!response.ok) {
    toast.error('Could not export the file.')
    return
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
  toast.success('File exported')
}

function defaultFilters() {
  const end = today()
  return { granularity: 'month', start: isoDate(new Date(end.getFullYear(), end.getMonth() - 2, 1)), end: isoDate(end) }
}

/**
 * The professional's own payout: what the signed appointments of the period
 * pay, per week/month, with the statement that backs it. The API scopes the
 * report to the signed-in professional.
 */
export default function MyPayouts() {
  const session = useSession()
  const [filters, setFilters] = useState(defaultFilters)
  const [report, setReport] = useState(null)
  const [statement, setStatement] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    const query = new URLSearchParams(filters).toString()
    Promise.all([api(`/reports/payouts?${query}`), api(`/reports/payouts/${session.professionalId}?start=${filters.start}&end=${filters.end}`)])
      .then(([summary, lines]) => {
        if (!active) return
        setReport(summary)
        setStatement(lines)
      })
      .catch((err) => active && setError(err.message))
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [filters, session.professionalId])

  function set(field, value) {
    setFilters((current) => ({ ...current, [field]: value }))
  }

  function quick(kind) {
    const end = today()
    if (kind === 'thisWeek') setFilters({ granularity: 'week', start: isoDate(startOfWeek(end)), end: isoDate(end) })
    if (kind === 'thisMonth') setFilters({ granularity: 'month', start: isoDate(new Date(end.getFullYear(), end.getMonth(), 1)), end: isoDate(end) })
    if (kind === 'last8Weeks') setFilters({ granularity: 'week', start: isoDate(addDays(startOfWeek(end), -7 * 7)), end: isoDate(end) })
    if (kind === 'last3Months') setFilters(defaultFilters())
  }

  const me = report && report.professionals[0]

  return (
    <section>
      <PageHeader
        crumb="My agenda / Payouts"
        title="My payouts"
        hint="What your signed appointments pay, the advances and settlements of your account and the receipts to sign. Scheduled appointments are not due yet; cancelled ones never are."
      >
        <div className="actions">
          <button type="button" className="ghost" onClick={() => quick('thisWeek')}>This week</button>
          <button type="button" className="ghost" onClick={() => quick('thisMonth')}>This month</button>
          <button type="button" className="ghost" onClick={() => quick('last8Weeks')}>Last 8 weeks</button>
          <button type="button" className="ghost" onClick={() => quick('last3Months')}>Last 3 months</button>
        </div>
      </PageHeader>
      <div className="panel filters">
        <label>
          Granularity
          <select value={filters.granularity} onChange={(event) => set('granularity', event.target.value)}>
            <option value="week">Weekly</option>
            <option value="month">Monthly</option>
          </select>
        </label>
        <label>
          From
          <input type="date" value={filters.start} onChange={(event) => set('start', event.target.value)} />
        </label>
        <label>
          To
          <input type="date" value={filters.end} onChange={(event) => set('end', event.target.value)} />
        </label>
        <label>
          Export
          <button
            type="button"
            className="ghost"
            onClick={() => downloadCsv(`/reports/payouts.csv?start=${filters.start}&end=${filters.end}`, `my_payouts_${filters.start}_${filters.end}.csv`)}
          >
            Statement (CSV)
          </button>
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      <h2 className="section-title">My account</h2>
      <PayoutAccount payeeType="professional" payeeId={session.professionalId} />
      <h2 className="section-title">Earnings in the period</h2>
      {report && me && (
        <div className={`stack ${loading ? 'loading' : ''}`}>
          <p className="muted">
            {me.name} · {me.specialty_name} · {me.clinic_name} · current fee {money(me.fee_cents)} per appointment (each appointment pays the fee in force when it was booked)
          </p>
          <div className="kpis">
            <div className="kpi accent"><span>Due to you</span><strong data-testid="payout-total">{money(me.payout_cents)}</strong></div>
            <div className="kpi"><span>Signed (paid)</span><strong>{me.completed}</strong></div>
            <div className="kpi"><span>Scheduled (not due)</span><strong>{me.scheduled}</strong></div>
            <div className="kpi"><span>Cancelled</span><strong>{me.cancelled}</strong></div>
            <div className="kpi">
              <span>Average per appointment</span>
              <strong>{me.completed ? money(me.payout_cents / me.completed) : '—'}</strong>
            </div>
            <div className="kpi"><span>Period</span><strong>{formatDate(report.start)} – {formatDate(report.end)}</strong></div>
          </div>
          <div className="card">
            <div className="card-head"><h2>Per {filters.granularity}</h2></div>
            <table>
              <thead>
                <tr>
                  <th>Period</th>
                  <th>Dates</th>
                  <th>Signed</th>
                  <th>Scheduled</th>
                  <th>Cancelled</th>
                  <th>Due to you</th>
                </tr>
              </thead>
              <tbody>
                {me.periods.map((row) => (
                  <tr key={row.period}>
                    <td>{row.period}</td>
                    <td>{formatDate(row.start)} – {formatDate(row.end)}</td>
                    <td>{row.completed}</td>
                    <td className="muted">{row.scheduled}</td>
                    <td className="muted">{row.cancelled}</td>
                    <td><strong>{money(row.payout_cents)}</strong></td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th colSpan={2}>Total</th>
                  <th>{me.completed}</th>
                  <th>{me.scheduled}</th>
                  <th>{me.cancelled}</th>
                  <th>{money(me.payout_cents)}</th>
                </tr>
              </tfoot>
            </table>
          </div>
          {statement && (
            <div className="card">
              <div className="card-head">
                <h2>Statement</h2>
                <span className="muted">{statement.lines.length} appointments in the period</span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Patient</th>
                    <th>Plan</th>
                    <th>Status</th>
                    <th>Signed</th>
                    <th>Due to you</th>
                  </tr>
                </thead>
                <tbody>
                  {statement.lines.map((line) => (
                    <tr key={line.appointment_id}>
                      <td><Link to={`/appointments/${line.appointment_id}`}>{formatDateTime(line.marked_at)}</Link></td>
                      <td>{line.patient}</td>
                      <td>{line.plan || <span className="muted">—</span>}</td>
                      <td><span className={`badge ${line.status}`}>{statusLabel(APPOINTMENT_STATUS, line.status)}</span></td>
                      <td>{line.signed_at ? formatDateTime(line.signed_at) : <span className="muted">—</span>}</td>
                      <td>{line.payout_cents ? <strong>{money(line.payout_cents)}</strong> : <span className="muted">not due</span>}</td>
                    </tr>
                  ))}
                  {statement.lines.length === 0 && <tr><td colSpan={6} className="muted">No appointments in this period.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
