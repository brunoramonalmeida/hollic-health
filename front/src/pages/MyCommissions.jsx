import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../api'
import PageHeader from '../components/PageHeader'
import { addDays, formatDate, isoDate, startOfWeek, today } from '../dates'
import { useSession } from '../session'

const pct = (bp) => `${(bp / 100).toLocaleString('pt-BR', { maximumFractionDigits: 2 })}%`

function defaultFilters() {
  const end = today()
  return { granularity: 'month', start: isoDate(new Date(end.getFullYear(), end.getMonth() - 5, 1)), end: isoDate(end) }
}

/**
 * The seller's own commission report: the portfolio as it stands today, the
 * commission earned per period on the fees received from its sales, and the
 * statement (every installment paid) backing the payment of the commission.
 * The API scopes everything to the signed-in seller.
 */
export default function MyCommissions() {
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
    Promise.all([api(`/reports/commissions?${query}`), api(`/reports/commissions/${session.sellerId}?start=${filters.start}&end=${filters.end}`)])
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
  }, [filters, session.sellerId])

  function set(field, value) {
    setFilters((current) => ({ ...current, [field]: value }))
  }

  function quick(kind) {
    const end = today()
    if (kind === 'thisMonth') setFilters({ granularity: 'month', start: isoDate(new Date(end.getFullYear(), end.getMonth(), 1)), end: isoDate(end) })
    if (kind === 'last8Weeks') setFilters({ granularity: 'week', start: isoDate(addDays(startOfWeek(end), -7 * 7)), end: isoDate(end) })
    if (kind === 'last6Months') setFilters(defaultFilters())
  }

  const portfolio = report ? report.portfolio : null
  const mine = report ? report.by_seller[0] : null

  return (
    <section>
      <PageHeader
        crumb="My area / Commissions"
        title="My commissions"
        hint="Your commission is earned as your customers pay each installment, at the rate of your region when the plan was sold. The statement lists every payment received in the period."
      >
        <div className="actions">
          <button type="button" className="ghost" onClick={() => quick('thisMonth')}>This month</button>
          <button type="button" className="ghost" onClick={() => quick('last8Weeks')}>Last 8 weeks</button>
          <button type="button" className="ghost" onClick={() => quick('last6Months')}>Last 6 months</button>
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
      </div>
      {error && <p className="error">{error}</p>}
      {report && (
        <div className={`stack ${loading ? 'loading' : ''}`}>
          <h2 className="section-title">In the period</h2>
          <div className="kpis">
            <div className="kpi"><span>Plans sold</span><strong>{report.totals.sold}</strong></div>
            <div className="kpi"><span>Cancelled</span><strong>{report.totals.cancelled}</strong></div>
            <div className="kpi"><span>Payments received</span><strong>{report.totals.payments}</strong><small>{money(report.totals.revenue_cents)}</small></div>
            <div className="kpi"><span>Special discounts granted</span><strong>{money(report.totals.discount_cents)}</strong></div>
            <div className="kpi accent" data-testid="commission-total"><span>Commission earned</span><strong>{money(report.totals.commission_cents)}</strong>{mine && <small>current rate {pct(mine.commission_bp)}</small>}</div>
            {session.user.seller_team_size > 0 && (
              <>
                <div className="kpi accent" data-testid="override-total"><span>Override on my team</span><strong>{money(report.totals.override_cents)}</strong><small>{report.totals.team_payments} team payments · {money(report.totals.team_revenue_cents)}</small></div>
                <div className="kpi accent"><span>Total earned</span><strong>{money(report.totals.total_cents)}</strong><small><Link to="/my-team">My team</Link></small></div>
              </>
            )}
          </div>
          <h2 className="section-title">My portfolio today</h2>
          <div className="kpis">
            <div className="kpi"><span>Customers</span><strong>{portfolio.customers}</strong><small>registered by you or sold to by you</small></div>
            <div className="kpi"><span>Active plans</span><strong>{portfolio.active}</strong><small>{money(portfolio.monthly_cents)} a month</small></div>
            <div className={`kpi ${portfolio.pending ? 'danger' : ''}`}><span>Awaiting first payment</span><strong>{portfolio.pending}</strong></div>
            <div className={`kpi ${portfolio.overdue ? 'danger' : ''}`}><span>Overdue plans</span><strong>{portfolio.overdue}</strong><small>{portfolio.installments_overdue} installments overdue</small></div>
            <div className="kpi"><span>Due in 7 days</span><strong>{portfolio.installments_due_week}</strong><small>installments to remind</small></div>
            <div className="kpi"><span>Expired / cancelled</span><strong>{portfolio.expired + portfolio.cancelled}</strong></div>
          </div>
          <div className="card">
            <div className="card-head"><h2>Per {filters.granularity}</h2></div>
            <table>
              <thead>
                <tr><th>Period</th><th>Dates</th><th>Sold</th><th>Cancelled</th><th>Payments</th><th>Fees received</th><th>Commission</th>{session.user.seller_team_size > 0 && <><th>Override</th><th>Total</th></>}</tr>
              </thead>
              <tbody>
                {report.periods.map((row) => (
                  <tr key={row.period}>
                    <td>{row.period}</td>
                    <td>{formatDate(row.start)} – {formatDate(row.end)}</td>
                    <td>{row.sold}</td>
                    <td>{row.cancelled}</td>
                    <td>{row.payments}</td>
                    <td>{money(row.revenue_cents)}</td>
                    <td><strong>{money(row.commission_cents)}</strong></td>
                    {session.user.seller_team_size > 0 && <><td>{money(row.override_cents)}</td><td><strong>{money(row.total_cents)}</strong></td></>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {statement && (
            <div className="card">
              <div className="card-head"><h2>Statement · {formatDate(statement.start)} – {formatDate(statement.end)}</h2></div>
              <table>
                <thead>
                  <tr><th>Paid on</th><th>Customer</th><th>Plan</th><th>Installment</th><th>Sold by</th><th>Paid</th><th>Rate</th><th>Earned</th></tr>
                </thead>
                <tbody>
                  {statement.lines.map((line) => (
                    <tr key={line.installment_id}>
                      <td>{formatDate(line.paid_on)}</td>
                      <td><Link to={`/subscriptions/${line.subscription_id}`}>{line.customer_name}</Link></td>
                      <td>{line.modality_name}{line.discount_percent > 0 && <small className="muted"> · {line.discount_percent}% off</small>}</td>
                      <td>#{line.sequence}</td>
                      <td>{line.kind === 'override' ? <>{line.sold_by} <small className="muted">· override level {line.level}</small></> : 'me'}</td>
                      <td>{money(line.paid_cents)}</td>
                      <td>{pct(line.commission_bp)}</td>
                      <td><strong>{money(line.commission_cents)}</strong></td>
                    </tr>
                  ))}
                  {statement.lines.length === 0 && <tr><td colSpan={8} className="muted">No payments received in this period.</td></tr>}
                </tbody>
                {statement.lines.length > 0 && (
                  <tfoot>
                    <tr>
                      <td colSpan={5}><strong>{statement.payments} payments</strong></td>
                      <td><strong>{money(statement.revenue_cents)}</strong></td>
                      <td></td>
                      <td><strong>{money(statement.total_cents)}</strong>{statement.override_cents > 0 && <small className="muted"> · {money(statement.commission_cents)} commission + {money(statement.override_cents)} override</small>}</td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
