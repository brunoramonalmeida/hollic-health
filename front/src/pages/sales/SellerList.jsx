import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { useSession } from '../../session'

export default function SellerList() {
  const { canManageSales } = useSession()
  const [params, setParams] = useSearchParams()
  const regionId = params.get('region') || ''
  const status = params.get('status') || ''
  const [rows, setRows] = useState([])
  const [regions, setRegions] = useState([])
  const [error, setError] = useState('')

  async function load() {
    const query = new URLSearchParams()
    if (regionId) query.set('region_id', regionId)
    if (status) query.set('active', status === 'active' ? 'true' : 'false')
    setRows(await api(`/sellers?${query.toString()}`))
  }

  useEffect(() => {
    api('/regions').then(setRegions).catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [regionId, status]) // eslint-disable-line react-hooks/exhaustive-deps

  function filter(key, value) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  async function remove(row) {
    if (!window.confirm(`Delete seller "${row.name}"?`)) return
    setError('')
    try {
      await api(`/sellers/${row.id}`, { method: 'DELETE', message: 'Seller deleted' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  const byId = Object.fromEntries(regions.map((item) => [item.id, item]))

  return (
    <section>
      <PageHeader
        crumb="Sales / Sellers"
        title="Sellers"
        hint="Each seller belongs to one region and may only sell the plans of that region, to customers inside its scope."
      >
        <select value={regionId} aria-label="Region" onChange={(event) => filter('region', event.target.value)}>
          <option value="">All regions</option>
          {regions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <select value={status} aria-label="Status" onChange={(event) => filter('status', event.target.value)}>
          <option value="">Active and inactive</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </select>
        <span className="count">{rows.length} records</span>
        {canManageSales && (
          <Link className="button" to={regionId ? `/sellers/new?region=${regionId}` : '/sellers/new'}>New seller</Link>
        )}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Region</th>
              <th>Scope</th>
              <th>Commission</th>
              <th>Team</th>
              <th>Contact</th>
              <th>Sales</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const region = byId[row.region_id]
              return (
                <tr key={row.id}>
                  <td>
                    <strong>{row.name}</strong>
                    {row.document && <><br /><small className="muted">{row.document}</small></>}
                  </td>
                  <td><Link to={`/regions/${row.region_id}`}>{row.region_name}</Link></td>
                  <td>
                    {region ? region.scope : '—'}
                    {region && (
                      <small className="muted">
                        {' · '}
                        {region.plans.length
                          ? region.plans.map((plan) => `${plan.modality_name}${plan.discount_percent ? ` (${plan.discount_percent}% off, ${money(plan.value_cents)})` : ''}`).join(', ')
                          : 'no plans'}
                      </small>
                    )}
                  </td>
                  <td>{region ? region.commission_label : `${row.commission_bp / 100}%`}</td>
                  <td>
                    {row.team_size > 0 && <>Leads {row.team_size}{row.organisation_size > row.team_size && <small className="muted"> · {row.organisation_size} in total</small>}</>}
                    {row.team_size > 0 && row.leader_name && <br />}
                    {row.leader_name && <>Reports to {row.leader_name}</>}
                    {!row.team_size && !row.leader_name && <span className="muted">—</span>}
                  </td>
                  <td>{[row.email, row.phone].filter(Boolean).join(' · ') || <span className="muted">—</span>}</td>
                  <td>{row.subscriptions_count}</td>
                  <td><span className={`badge ${row.active ? 'ativa' : 'vencida'}`}>{row.active ? 'Active' : 'Inactive'}</span></td>
                  <td className="actions">
                    <Link className="button ghost" to={`/sellers/${row.id}`}>{canManageSales ? 'Edit' : 'View'}</Link>
                    {canManageSales && row.subscriptions_count === 0 && (
                      <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>
                    )}
                  </td>
                </tr>
              )
            })}
            {rows.length === 0 && <tr><td colSpan={9} className="muted">No sellers.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
