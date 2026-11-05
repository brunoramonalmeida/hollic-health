import { useEffect, useState } from 'react'
import { api, money } from '../api'
import { formatDate } from '../dates'
import { OWN_PART_STATUS, statusLabel } from '../labels'

const OWN_BADGE = { paid: 'paid', open: 'open', overdue: 'overdue', none: 'none' }

function thisYear() {
  const year = new Date().getFullYear()
  return { start: `${year}-01`, end: `${year}-12` }
}

/**
 * A customer's own payments: its part of every fee due in the months — all
 * of it for an individual plan, its share in a corporate co-payment, nothing
 * when its company pays — what its company paid, and what is still open.
 * `customerId`: the staff reading a customer's (a client reads its own).
 */
export default function EmployeeStatement({ customerId, onError }) {
  const [range, setRange] = useState(thisYear())
  const [data, setData] = useState(null)

  useEffect(() => {
    if (!range.start || !range.end) return undefined
    const params = new URLSearchParams({ start: range.start, end: range.end })
    if (customerId) params.set('customer_id', customerId)
    let active = true
    api(`/reports/employee-statement?${params.toString()}`)
      .then((body) => active && setData(body))
      .catch((err) => active && onError && onError(err.message))
    return () => {
      active = false
    }
  }, [customerId, range.start, range.end]) // eslint-disable-line react-hooks/exhaustive-deps

  const totals = data ? data.totals : null
  const corporate = data ? data.lines.some((line) => line.partner_name) : false
  return (
    <section data-testid="employee-statement">
      <div className="toolbar" style={{ marginBottom: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
        <label className="inline">From <input type="month" value={range.start} onChange={(event) => setRange({ ...range, start: event.target.value })} /></label>
        <label className="inline">To <input type="month" value={range.end} onChange={(event) => setRange({ ...range, end: event.target.value })} /></label>
      </div>
      {totals && (
        <div className="kpis">
          <div className="kpi accent"><span>{corporate ? 'My part' : 'My fees'}</span><strong>{money(totals.own_cents)}</strong><small>of {money(totals.fees_cents)} in fees</small></div>
          <div className="kpi"><span>Paid</span><strong>{money(totals.own_paid_cents)}</strong></div>
          <div className={`kpi ${totals.own_overdue_cents > 0 ? 'danger' : ''}`}><span>Open</span><strong>{money(totals.own_open_cents)}</strong><small>{money(totals.own_overdue_cents)} overdue</small></div>
          {corporate && <div className="kpi"><span>Covered by my company</span><strong>{money(totals.company_cents)}</strong><small>its part of the fees</small></div>}
        </div>
      )}
      {data && (
        <div className="card" style={{ marginTop: 16 }}>
          <table>
            <thead>
              <tr><th>Due</th><th>Plan</th><th>Period</th><th>Fee</th>{corporate && <th>Company part</th>}<th>My part</th><th>Status</th></tr>
            </thead>
            <tbody>
              {data.lines.map((line) => (
                <tr key={`${line.subscription_id}-${line.sequence}`}>
                  <td>{formatDate(line.due_on)}</td>
                  <td>
                    {line.modality_name}
                    {line.partner_name && <><br /><small className="muted">{line.partner_name} · {line.agreement_name}</small></>}
                  </td>
                  <td>{formatDate(line.period_start)} – {formatDate(line.period_end)}</td>
                  <td>{money(line.amount_cents)}</td>
                  {corporate && (
                    <td>
                      {line.company_cents ? money(line.company_cents) : <span className="muted">—</span>}
                      {line.company_cents > 0 && <><br /><small className="muted">{line.company_paid_on ? `paid ${formatDate(line.company_paid_on)}` : 'open'}</small></>}
                    </td>
                  )}
                  <td>
                    <strong>{line.own_cents ? money(line.own_cents) : '—'}</strong>
                    {line.own_paid_on && <><br /><small className="muted">paid {formatDate(line.own_paid_on)}</small></>}
                  </td>
                  <td><span className={`badge ${OWN_BADGE[line.own_status]}`}>{statusLabel(OWN_PART_STATUS, line.own_status)}</span></td>
                </tr>
              ))}
              {data.lines.length === 0 && <tr><td colSpan={corporate ? 7 : 6} className="muted">No fees due in these months.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
