import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../../api'
import { formatDate } from '../../dates'

const pct = (bp) => `${(bp / 100).toLocaleString('pt-BR', { maximumFractionDigits: 2 })}%`

function Totals({ row }) {
  return (
    <>
      <td>{row.sold}</td>
      <td>{row.cancelled}</td>
      <td>{row.payments}</td>
      <td>{money(row.revenue_cents)}</td>
      <td>{money(row.discount_cents)}</td>
      <td>{money(row.commission_cents)}</td>
      <td>{row.override_cents ? money(row.override_cents) : <span className="muted">—</span>}</td>
      <td><strong>{money(row.total_cents)}</strong></td>
    </>
  )
}

const TOTAL_HEADERS = ['Sold', 'Cancelled', 'Payments', 'Fees received', 'Special discounts', 'Commission', 'Override', 'Total']

/**
 * Plans sold by sellers and the commission due: each installment paid (by
 * payment date) on a seller's sale pays the commission snapshotted on the
 * sale. Period comes from the Reports page; region and seller are filtered here.
 */
export default function Commissions({ filters }) {
  const [regionId, setRegionId] = useState('')
  const [sellerId, setSellerId] = useState('')
  const [regions, setRegions] = useState([])
  const [sellers, setSellers] = useState([])
  const [report, setReport] = useState(null)
  const [statement, setStatement] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    Promise.all([api('/regions'), api('/sellers')])
      .then(([areas, team]) => {
        setRegions(areas)
        setSellers(team)
      })
      .catch((err) => setError(err.message))
  }, [])

  const params = new URLSearchParams({ granularity: filters.granularity, start: filters.start, end: filters.end })
  if (regionId) params.set('region_id', regionId)
  if (sellerId) params.set('seller_id', sellerId)
  const query = params.toString()

  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    api(`/reports/commissions?${query}`)
      .then((body) => active && setReport(body))
      .catch((err) => active && setError(err.message))
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [query])

  useEffect(() => {
    setStatement(null)
  }, [filters.start, filters.end])

  function openStatement(id) {
    api(`/reports/commissions/${id}?start=${filters.start}&end=${filters.end}`)
      .then(setStatement)
      .catch((err) => setError(err.message))
  }

  const team = regionId ? sellers.filter((item) => String(item.region_id) === regionId) : sellers

  return (
    <>
      <div className="toolbar">
        <label className="inline">
          Region
          <select value={regionId} onChange={(event) => { setRegionId(event.target.value); setSellerId('') }}>
            <option value="">All</option>
            {regions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <label className="inline">
          Seller
          <select value={sellerId} onChange={(event) => setSellerId(event.target.value)}>
            <option value="">All</option>
            {team.map((item) => <option key={item.id} value={item.id}>{item.name}{item.active ? '' : ' (inactive)'}</option>)}
          </select>
        </label>
      </div>
      <p className="notice info">
        Commission is due on the plan fees <strong>received</strong> from each seller's sales (by payment date), at the rate of the
        region when the plan was sold; a team leader's <strong>override</strong> likewise, at its own rate, on its collaborators' sales.
        Both are the company's cost, snapshotted on each sale. Sales made directly (no seller) are not included.
      </p>
      {error && <p className="error">{error}</p>}
      {report && (
        <div className={`stack ${loading ? 'loading' : ''}`}>
          <div className="kpis">
            <div className="kpi"><span>Plans sold</span><strong>{report.totals.sold}</strong></div>
            <div className="kpi"><span>Cancelled</span><strong>{report.totals.cancelled}</strong></div>
            <div className="kpi"><span>Payments</span><strong>{report.totals.payments}</strong></div>
            <div className="kpi"><span>Fees received</span><strong>{money(report.totals.revenue_cents)}</strong></div>
            <div className="kpi"><span>Special discounts</span><strong>{money(report.totals.discount_cents)}</strong></div>
            <div className="kpi"><span>Commission due</span><strong>{money(report.totals.commission_cents)}</strong></div>
            <div className="kpi"><span>Override due to leaders</span><strong>{money(report.totals.override_cents)}</strong><small>{report.totals.team_payments} payments of team sales</small></div>
            <div className="kpi accent"><span>Total due</span><strong>{money(report.totals.total_cents)}</strong></div>
          </div>
          <div className="card">
            <div className="card-head"><h2>Per {filters.granularity}</h2></div>
            <table>
              <thead>
                <tr><th>Period</th><th>Dates</th>{TOTAL_HEADERS.map((label) => <th key={label}>{label}</th>)}</tr>
              </thead>
              <tbody>
                {report.periods.map((row) => (
                  <tr key={row.period}>
                    <td>{row.period}</td>
                    <td>{formatDate(row.start)} – {formatDate(row.end)}</td>
                    <Totals row={row} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card">
            <div className="card-head"><h2>By seller</h2></div>
            <table>
              <thead>
                <tr><th>Seller</th><th>Region</th><th>Team</th>{TOTAL_HEADERS.map((label) => <th key={label}>{label}</th>)}<th></th></tr>
              </thead>
              <tbody>
                {report.by_seller.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <Link to={`/sellers/${row.id}`}>{row.name}</Link>
                      {!row.active && <small className="muted"> · inactive</small>}
                    </td>
                    <td>{row.region_name} <small className="muted">· {pct(row.commission_bp)} now</small></td>
                    <td>
                      {row.team_size > 0 && <>Leads {row.organisation_size} <small className="muted">· {row.team_sold} team sales</small></>}
                      {row.team_size > 0 && row.leader_name && <br />}
                      {row.leader_name && <>Reports to {row.leader_name}</>}
                      {!row.team_size && !row.leader_name && <span className="muted">—</span>}
                    </td>
                    <Totals row={row} />
                    <td className="actions">
                      <button type="button" className="ghost" onClick={() => openStatement(row.id)}>Statement</button>
                    </td>
                  </tr>
                ))}
                {report.by_seller.length === 0 && <tr><td colSpan={12} className="muted">No sellers.</td></tr>}
              </tbody>
            </table>
          </div>
          {statement && (
            <div className="card">
              <div className="card-head">
                <h2>
                  Statement · {statement.seller_name} <small className="muted">({statement.region_name}) · {formatDate(statement.start)} – {formatDate(statement.end)}</small>
                </h2>
                <button type="button" className="ghost" onClick={() => setStatement(null)}>Close</button>
              </div>
              <table>
                <thead>
                  <tr><th>Paid on</th><th>Customer</th><th>Plan</th><th>Installment</th><th>Sold by</th><th>Region of the sale</th><th>Paid</th><th>Rate</th><th>Earned</th></tr>
                </thead>
                <tbody>
                  {statement.lines.map((line) => (
                    <tr key={line.installment_id}>
                      <td>{formatDate(line.paid_on)}</td>
                      <td><Link to={`/subscriptions/${line.subscription_id}`}>{line.customer_name}</Link></td>
                      <td>{line.modality_name}{line.discount_percent > 0 && <small className="muted"> · {line.discount_percent}% off</small>}</td>
                      <td>#{line.sequence}</td>
                      <td>{line.sold_by}{line.kind === 'override' && <small className="muted"> · override level {line.level}</small>}</td>
                      <td>{line.region_name || '—'}</td>
                      <td>{money(line.paid_cents)}</td>
                      <td>{pct(line.commission_bp)}</td>
                      <td><strong>{money(line.commission_cents)}</strong></td>
                    </tr>
                  ))}
                  {statement.lines.length === 0 && <tr><td colSpan={9} className="muted">No payments in this range.</td></tr>}
                </tbody>
                {statement.lines.length > 0 && (
                  <tfoot>
                    <tr>
                      <td colSpan={6}><strong>{statement.payments} payments</strong></td>
                      <td><strong>{money(statement.revenue_cents)}</strong></td>
                      <td></td>
                      <td><strong>{money(statement.total_cents)}</strong>{statement.override_cents > 0 && <small className="muted"> · {money(statement.commission_cents)} + {money(statement.override_cents)} override</small>}</td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          )}
          <div className="card">
            <div className="card-head"><h2>By region of the sale</h2></div>
            <table>
              <thead>
                <tr><th>Region</th><th>Sellers</th>{TOTAL_HEADERS.map((label) => <th key={label}>{label}</th>)}</tr>
              </thead>
              <tbody>
                {report.by_region.map((row) => (
                  <tr key={row.id}>
                    <td><Link to={`/regions/${row.id}`}>{row.name}</Link> <small className="muted">· {pct(row.commission_bp)} now</small></td>
                    <td>{row.sellers_count}</td>
                    <Totals row={row} />
                  </tr>
                ))}
                {report.by_region.length === 0 && <tr><td colSpan={10} className="muted">No regions.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
