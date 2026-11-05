import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money } from '../api'
import PageHeader from '../components/PageHeader'

/**
 * The seller's region: the rules every sale of the seller must follow.
 * Read-only — the region is managed by the staff; the seller is notified
 * when its rules change.
 */
export default function MyRegion() {
  const [me, setMe] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api('/sellers/me').then(setMe).catch((err) => setError(err.message))
  }, [])

  if (error) return <p className="error">{error}</p>
  if (!me) return <p className="muted">Loading…</p>
  const region = me.region

  return (
    <section>
      <PageHeader
        crumb="My area / Region"
        title={`Region ${region.name}`}
        hint="Every sale you record follows these rules: only these plans, only to customers whose address is inside the scope, at the special discount shown. Changes made by the staff apply to your next sales; the ones already recorded keep their terms."
      >
        {!me.active && <span className="badge vencida">Inactive: you cannot record new sales</span>}
        <Link className="button" to="/subscriptions/new">New sale</Link>
      </PageHeader>
      {region.description && <p className="muted">{region.description}</p>}
      {me.leader && <p className="notice info">You report to <strong>{me.leader.name}</strong>{(me.leader.email || me.leader.phone) && <> ({[me.leader.email, me.leader.phone].filter(Boolean).join(' · ')})</>}, the coordinator of your team in this region.</p>}
      {me.team.length > 0 && <p className="notice info">You lead {me.team.map((item) => item.name).join(', ')}{me.organisation.length > me.team.length && ` (${me.organisation.length} sellers in total)`}. <Link to="/my-team">My team</Link></p>}
      <div className="kpis">
        <div className="kpi accent"><span>Your commission</span><strong>{region.commission_label}</strong><small>of every plan fee received from your sales</small></div>
        <div className="kpi"><span>Areas in the scope</span><strong>{region.areas.length}</strong><small>{region.scope}</small></div>
        <div className="kpi"><span>Plans you may sell</span><strong>{region.plans.length}</strong></div>
        <div className="kpi"><span>Sellers in the region</span><strong>{region.active_sellers_count}</strong><small>active</small></div>
        <div className="kpi"><span>Team overrides</span><strong>{region.override_labels.length ? region.override_labels.join(' · ') : '—'}</strong><small>by level above the seller</small></div>
      </div>
      <div className="grid two" style={{ marginTop: 16 }}>
        <div className="card">
          <div className="card-head"><h2>Where you may sell</h2></div>
          <table>
            <thead><tr><th>State</th><th>City</th></tr></thead>
            <tbody>
              {region.areas.map((area) => (
                <tr key={area.label}>
                  <td>{area.state}</td>
                  <td>{area.city || <span className="muted">Whole state</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted" style={{ padding: '8px 16px' }}>The customer's city and state (on the customer record) decide it; register the full address.</p>
        </div>
        <div className="card">
          <div className="card-head"><h2>Plans you may sell</h2></div>
          <table>
            <thead><tr><th>Plan</th><th>List price</th><th>Special discount</th><th>Monthly fee</th></tr></thead>
            <tbody>
              {region.plans.map((plan) => (
                <tr key={plan.modality_id}>
                  <td>{plan.modality_name}</td>
                  <td>{money(plan.plan_value_cents)}</td>
                  <td>{plan.discount_percent > 0 ? `${plan.discount_percent}%` : <span className="muted">none</span>}</td>
                  <td><strong>{money(plan.value_cents)}</strong><small className="muted"> + extra dependents</small></td>
                </tr>
              ))}
              {region.plans.length === 0 && <tr><td colSpan={4} className="muted">No plans yet: ask the staff.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
