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
 * A coordinator's team: the collaborators it leads, what each sold and
 * earned in the period, and the override the coordinator earns on their
 * sales. The API scopes the report to the signed-in seller and its team.
 */
export default function MyTeam() {
  const session = useSession()
  const [me, setMe] = useState(null)
  const [filters, setFilters] = useState(defaultFilters)
  const [report, setReport] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api('/sellers/me').then(setMe).catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    let active = true
    setLoading(true)
    api(`/reports/commissions?${new URLSearchParams(filters).toString()}`)
      .then((body) => active && setReport(body))
      .catch((err) => active && setError(err.message))
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [filters])

  function quick(kind) {
    const end = today()
    if (kind === 'thisMonth') setFilters({ granularity: 'month', start: isoDate(new Date(end.getFullYear(), end.getMonth(), 1)), end: isoDate(end) })
    if (kind === 'last8Weeks') setFilters({ granularity: 'week', start: isoDate(addDays(startOfWeek(end), -7 * 7)), end: isoDate(end) })
    if (kind === 'last6Months') setFilters(defaultFilters())
  }

  if (error && !me) return <p className="error">{error}</p>
  if (!me) return <p className="muted">Loading…</p>
  const rows = report ? report.by_seller.filter((row) => row.id !== session.sellerId) : []
  const mine = report ? report.by_seller.find((row) => row.id === session.sellerId) : null
  const member = (id) => me.organisation.find((item) => item.id === id)
  const levels = me.override_bps.map((bp, index) => `${pct(bp)} (level ${index + 1})`).join(', ') || 'nothing'
  const ordered = [...rows].sort((a, b) => (member(a.id)?.depth || 0) - (member(b.id)?.depth || 0) || a.name.localeCompare(b.name))

  return (
    <section>
      <PageHeader
        crumb="My area / Team"
        title="My team"
        hint={`You lead ${me.organisation.length} seller${me.organisation.length === 1 ? '' : 's'} in region ${me.region_name} (${me.team.length} directly). The region pays you ${levels} above the seller who sold, on every plan fee received, on top of the seller's own commission.`}
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
          <select value={filters.granularity} onChange={(event) => setFilters({ ...filters, granularity: event.target.value })}>
            <option value="week">Weekly</option>
            <option value="month">Monthly</option>
          </select>
        </label>
        <label>
          From
          <input type="date" value={filters.start} onChange={(event) => setFilters({ ...filters, start: event.target.value })} />
        </label>
        <label>
          To
          <input type="date" value={filters.end} onChange={(event) => setFilters({ ...filters, end: event.target.value })} />
        </label>
      </div>
      {error && <p className="error">{error}</p>}
      {report && (
        <div className={`stack ${loading ? 'loading' : ''}`}>
          <div className="kpis">
            <div className="kpi"><span>My organisation</span><strong>{me.organisation.length}</strong><small>{me.team.length} report to me directly</small></div>
            <div className="kpi"><span>Team sales in the period</span><strong>{report.totals.team_sold}</strong></div>
            <div className="kpi"><span>Team fees received</span><strong>{money(report.totals.team_revenue_cents)}</strong><small>{report.totals.team_payments} payments</small></div>
            <div className="kpi accent" data-testid="override-total"><span>My override</span><strong>{money(report.totals.override_cents)}</strong><small>on the organisation's fees, by level</small></div>
            {mine && <div className="kpi"><span>My own commission</span><strong>{money(mine.commission_cents)}</strong><small>total earned {money(report.totals.total_cents)}</small></div>}
          </div>
          <div className="card">
            <div className="card-head"><h2>Collaborators</h2></div>
            <table>
              <thead>
                <tr><th>Seller</th><th>Level</th><th>Reports to</th><th>Status</th><th>Sold</th><th>Cancelled</th><th>Payments</th><th>Fees received</th><th>Their commission</th><th>Paid to their leaders</th><th></th></tr>
              </thead>
              <tbody>
                {ordered.map((row) => {
                  const info = member(row.id)
                  return (
                    <tr key={row.id}>
                      <td>
                        <strong>{row.name}</strong>
                        {info && (info.email || info.phone) && <><br /><small className="muted">{[info.email, info.phone].filter(Boolean).join(' · ')}</small></>}
                      </td>
                      <td>{info ? info.depth : '—'}{row.team_size > 0 && <small className="muted"> · leads {row.organisation_size}</small>}</td>
                      <td>{row.leader_name}</td>
                      <td><span className={`badge ${row.active ? 'ativa' : 'vencida'}`}>{row.active ? 'Active' : 'Inactive'}</span></td>
                      <td>{row.sold}</td>
                      <td>{row.cancelled}</td>
                      <td>{row.payments}</td>
                      <td>{money(row.revenue_cents)}</td>
                      <td>{money(row.commission_cents)} <small className="muted">· {pct(row.commission_bp)}</small></td>
                      <td><strong>{money(row.leader_override_cents)}</strong><small className="muted"> all levels</small></td>
                      <td className="actions"><Link className="button ghost" to={`/subscriptions?seller=${row.id}`}>Sales</Link></td>
                    </tr>
                  )
                })}
                {rows.length === 0 && <tr><td colSpan={11} className="muted">No collaborators yet: the staff assigns sellers to your team.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="card">
            <div className="card-head"><h2>Per {filters.granularity}</h2></div>
            <table>
              <thead>
                <tr><th>Period</th><th>Dates</th><th>Team sales</th><th>Team payments</th><th>Team fees</th><th>My override</th><th>My commission</th><th>Total</th></tr>
              </thead>
              <tbody>
                {report.periods.map((row) => (
                  <tr key={row.period}>
                    <td>{row.period}</td>
                    <td>{formatDate(row.start)} – {formatDate(row.end)}</td>
                    <td>{row.team_sold}</td>
                    <td>{row.team_payments}</td>
                    <td>{money(row.team_revenue_cents)}</td>
                    <td><strong>{money(row.override_cents)}</strong></td>
                    <td>{money(row.commission_cents)}</td>
                    <td><strong>{money(row.total_cents)}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  )
}
