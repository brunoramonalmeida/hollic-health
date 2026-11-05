import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { useSession } from '../../session'

export default function RegionList() {
  const { canManageSales } = useSession()
  const [rows, setRows] = useState([])
  const [error, setError] = useState('')

  async function load() {
    setRows(await api('/regions'))
  }

  useEffect(() => {
    load().catch((err) => setError(err.message))
  }, [])

  async function remove(row) {
    if (!window.confirm(`Delete region "${row.name}"?`)) return
    setError('')
    try {
      await api(`/regions/${row.id}`, { method: 'DELETE', message: 'Region deleted' })
      await load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section>
      <PageHeader
        crumb="Sales / Regions"
        title="Sales regions"
        hint="Every seller belongs to a region. The region sets the sellers' commission, where they may sell (the customer's address) and which plans they may sell, with its special discount on the monthly fee."
      >
        <span className="count">{rows.length} records</span>
        {canManageSales && <Link className="button" to="/regions/new">New region</Link>}
      </PageHeader>
      {error && <p className="error">{error}</p>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Scope</th>
              <th>Plans available</th>
              <th>Commission</th>
              <th>Sellers</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <strong>{row.name}</strong>
                  {row.description && <><br /><small className="muted">{row.description}</small></>}
                </td>
                <td>{row.scope}</td>
                <td>
                  {row.plans.length === 0 && <span className="muted">None: its sellers cannot sell</span>}
                  {row.plans.map((plan) => (
                    <div key={plan.modality_id}>
                      {plan.modality_name}
                      <small className="muted">
                        {plan.discount_percent > 0
                          ? ` · ${plan.discount_percent}% off · ${money(plan.value_cents)}/month`
                          : ` · list price ${money(plan.plan_value_cents)}/month`}
                      </small>
                    </div>
                  ))}
                </td>
                <td>
                  {row.commission_label}
                  {row.override_labels.length > 0 && <><br /><small className="muted">overrides {row.override_labels.join(' · ')}</small></>}
                </td>
                <td>
                  <Link to={`/sellers?region=${row.id}`}>
                    {row.active_sellers_count} active
                  </Link>
                  {row.sellers_count > row.active_sellers_count && (
                    <small className="muted"> · {row.sellers_count - row.active_sellers_count} inactive</small>
                  )}
                </td>
                <td className="actions">
                  <Link className="button ghost" to={`/regions/${row.id}`}>{canManageSales ? 'Edit' : 'View'}</Link>
                  {canManageSales && <button type="button" className="warn" onClick={() => remove(row)}>Delete</button>}
                </td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={6} className="muted">No regions yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
