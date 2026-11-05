import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../api'
import PageHeader from '../../components/PageHeader'
import PayoutAccount from '../../components/PayoutAccount'
import { formatDate, formatDateTime } from '../../dates'

const CLOSING_STATUS = { scheduled: 'Scheduled', dispatched: 'Running', done: 'Done' }

function previousPeriod() {
  const now = new Date()
  const first = new Date(now.getFullYear(), now.getMonth(), 1)
  first.setDate(0)
  return `${first.getFullYear()}-${String(first.getMonth() + 1).padStart(2, '0')}`
}

/** The automatic monthly closings (and the button to run the previous month by hand). */
function Closings() {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')

  function load() {
    return api('/payouts/closings').then(setRows).catch((err) => setError(err.message))
  }

  useEffect(() => {
    load()
  }, [])

  async function closeNow() {
    const period = previousPeriod()
    if (!window.confirm(`Close ${period} now? Every professional and clinic with signed appointments up to the end of that month gets a settlement.`)) return
    try {
      await api('/payouts/closings', { method: 'POST', body: { period }, message: `Closing of ${period} queued — the worker settles each payee` })
      await load()
    } catch {
      /* toast already shown */
    }
  }

  const previous = previousPeriod()
  const alreadyClosed = rows?.some((row) => row.period === previous)
  return (
    <div className="card" data-testid="closings">
      <div className="card-head">
        <h2>Monthly closings</h2>
        <div className="actions">
          <span className="muted">Automatic on the closing day; one settlement per payee, through the job queue.</span>
          {rows && !alreadyClosed && <button type="button" className="ghost" onClick={closeNow}>Close {previous} now</button>}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      <table>
        <thead>
          <tr>
            <th>Period</th>
            <th>Status</th>
            <th>Payees</th>
            <th>Settlements</th>
            <th>Nothing to settle</th>
            <th>Origin</th>
            <th>Finished</th>
          </tr>
        </thead>
        <tbody>
          {(rows || []).map((row) => (
            <tr key={row.id}>
              <td><strong>{row.period}</strong> <small className="muted">{formatDate(row.period_start)} – {formatDate(row.period_end)}</small></td>
              <td><span className={`badge closing-${row.status}`}>{CLOSING_STATUS[row.status] || row.status}</span></td>
              <td>{row.payees_total}</td>
              <td>{row.settlements_created}</td>
              <td>{row.payees_skipped}</td>
              <td>{row.automatic ? 'Automatic' : `By ${row.triggered_by_name}`}</td>
              <td>{row.finished_at ? formatDateTime(row.finished_at) : <span className="muted">—</span>}</td>
            </tr>
          ))}
          {rows && rows.length === 0 && <tr><td colSpan={7} className="muted">No closing yet. The worker closes the previous month on the closing day.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Administrators pay professionals and clinics here: pick the payee, see the
 * account, register advances, create settlements, pay installments and
 * acknowledge clinic receipts.
 */
export default function PayoutsAdmin() {
  const [params, setParams] = useSearchParams()
  const [payees, setPayees] = useState([])
  const [error, setError] = useState('')
  const selected = params.get('payee') || ''

  useEffect(() => {
    api('/payouts/payees').then(setPayees).catch((err) => setError(err.message))
  }, [])

  const [payeeType, payeeId] = selected ? selected.split(':') : [null, null]
  const payee = payees.find((item) => `${item.payee_type}:${item.payee_id}` === selected)

  return (
    <section>
      <PageHeader
        crumb="Finance / Payouts"
        title="Payouts"
        hint="Advances, settlements in installments and receipts for professionals and clinics. The professional signs each receipt; a clinic's receipt is acknowledged with who received the money."
      >
        <label className="inline">
          Payee
          <select aria-label="Payee" value={selected} onChange={(event) => setParams(event.target.value ? { payee: event.target.value } : {})}>
            <option value="">Select a professional or clinic</option>
            <optgroup label="Professionals">
              {payees.filter((item) => item.payee_type === 'professional').map((item) => (
                <option key={`p${item.payee_id}`} value={`professional:${item.payee_id}`}>{item.name} — {item.detail}{item.has_login ? '' : ' (no login)'}</option>
              ))}
            </optgroup>
            <optgroup label="Clinics">
              {payees.filter((item) => item.payee_type === 'clinic').map((item) => (
                <option key={`c${item.payee_id}`} value={`clinic:${item.payee_id}`}>{item.name} — {item.detail}</option>
              ))}
            </optgroup>
          </select>
        </label>
      </PageHeader>
      {error && <p className="error">{error}</p>}
      {payee && !payee.has_login && payee.payee_type === 'professional' && (
        <p className="notice warn">This professional has no login yet, so nobody can sign its receipts. Create a professional user first.</p>
      )}
      {payeeId ? (
        <PayoutAccount key={selected} payeeType={payeeType} payeeId={Number(payeeId)} manage />
      ) : (
        <>
          <Closings />
          <p className="muted" style={{ marginTop: 12 }}>Choose a payee to see its account.</p>
        </>
      )}
    </section>
  )
}
