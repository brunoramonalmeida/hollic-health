import { useEffect, useState } from 'react'
import { api, downloadFile, money } from '../api'
import { monthLabel } from '../labels'

function thisYear() {
  const year = new Date().getFullYear()
  return { start: `${year}-01`, end: `${year}-12` }
}

/**
 * What a company pays and what its employees pay — month by month and
 * employee by employee (by the month the fees are due) — with the payroll
 * list as CSV (one row per employee fee: the company's part, the employee's
 * part and their payments).
 *
 * `endpoint`: `/portal/statement` (the company) or `/partners/:id/statement`
 * (the staff); the CSV is the same path with `.csv`. `agreements`: for the filter.
 */
export default function CompanyStatement({ endpoint, agreements = [], onError }) {
  const [range, setRange] = useState(thisYear())
  const [agreementId, setAgreementId] = useState('')
  const [data, setData] = useState(null)

  function query() {
    const params = new URLSearchParams({ start: range.start, end: range.end })
    if (agreementId) params.set('agreement_id', agreementId)
    return params.toString()
  }

  useEffect(() => {
    if (!range.start || !range.end) return undefined
    let active = true
    api(`${endpoint}?${query()}`)
      .then((body) => active && setData(body))
      .catch((err) => active && onError && onError(err.message))
    return () => {
      active = false
    }
  }, [endpoint, range.start, range.end, agreementId]) // eslint-disable-line react-hooks/exhaustive-deps

  const totals = data ? data.totals : null
  return (
    <section data-testid="company-statement">
      <div className="toolbar" style={{ marginBottom: 12, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <label className="inline">From <input type="month" value={range.start} onChange={(event) => setRange({ ...range, start: event.target.value })} /></label>
        <label className="inline">To <input type="month" value={range.end} onChange={(event) => setRange({ ...range, end: event.target.value })} /></label>
        {agreements.length > 1 && (
          <select value={agreementId} aria-label="Agreement" onChange={(event) => setAgreementId(event.target.value)}>
            <option value="">All agreements</option>
            {agreements.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        )}
        <button type="button" className="ghost" onClick={() => downloadFile(`${endpoint}.csv?${query()}`, `statement_${range.start}_${range.end}.csv`)}>
          Export payroll list (CSV)
        </button>
      </div>
      {totals && (
        <div className="kpis">
          <div className="kpi accent"><span>The company pays</span><strong>{money(totals.company_cents)}</strong><small>its part of the fees {money(totals.company_share_cents)} + shortfall {money(totals.shortfall_cents)}</small></div>
          <div className="kpi"><span>Company: paid</span><strong>{money(totals.company_paid_cents)}</strong><small>{money(totals.company_open_cents)} open</small></div>
          <div className="kpi"><span>The employees pay</span><strong>{money(totals.employee_share_cents)}</strong><small>{totals.employees} employee{totals.employees === 1 ? '' : 's'}</small></div>
          <div className="kpi"><span>Employees: paid</span><strong>{money(totals.employee_paid_cents)}</strong><small>{money(totals.employee_open_cents)} open</small></div>
        </div>
      )}
      {data && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-head"><h2>By month</h2><span className="muted">by the month the fees are due</span></div>
          <table>
            <thead>
              <tr><th>Month</th><th>Employees</th><th>Fees</th><th>Company part</th><th>Employees' parts</th><th>Shortfall</th><th>Company: paid / open</th><th>Employees: paid / open</th></tr>
            </thead>
            <tbody>
              {data.months.filter((row) => row.installments > 0 || row.shortfall_cents > 0).map((row) => (
                <tr key={row.period}>
                  <td>{monthLabel(row.period)}</td>
                  <td>{row.employees}</td>
                  <td>{money(row.fees_cents)}</td>
                  <td><strong>{money(row.company_share_cents)}</strong></td>
                  <td>{money(row.employee_share_cents)}</td>
                  <td>{row.shortfall_cents ? money(row.shortfall_cents) : <span className="muted">—</span>}</td>
                  <td>{money(row.company_paid_cents)} <small className={row.company_open_cents ? 'warn-text' : 'muted'}>/ {money(row.company_open_cents)}</small></td>
                  <td>{money(row.employee_paid_cents)} <small className={row.employee_open_cents ? 'warn-text' : 'muted'}>/ {money(row.employee_open_cents)}</small></td>
                </tr>
              ))}
              {data.months.every((row) => row.installments === 0 && row.shortfall_cents === 0) && (
                <tr><td colSpan={8} className="muted">No fees due in these months.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      {data && data.employees.length > 0 && (
        <div className="card" style={{ marginTop: 16 }} data-testid="statement-employees">
          <div className="card-head"><h2>By employee</h2><span className="muted">what each side pays in the period</span></div>
          <table>
            <thead>
              <tr><th>Employee</th><th>Plan</th><th>Company's part now</th><th>Months</th><th>Fees</th><th>Company</th><th>Employee</th><th>Employee: paid / open</th></tr>
            </thead>
            <tbody>
              {data.employees.map((row) => (
                <tr key={row.customer_id}>
                  <td>{row.name}<br /><small className="muted">{row.document}</small></td>
                  <td>{row.plans.join(', ')}<br /><small className="muted">{row.agreements.join(', ')}</small></td>
                  <td>{row.company_share_percent ?? '—'}%</td>
                  <td>{row.months}</td>
                  <td>{money(row.fees_cents)}</td>
                  <td>{money(row.company_share_cents)}</td>
                  <td>{money(row.employee_share_cents)}</td>
                  <td>{money(row.employee_paid_cents)} <small className={row.employee_open_cents ? 'warn-text' : 'muted'}>/ {money(row.employee_open_cents)}</small></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
