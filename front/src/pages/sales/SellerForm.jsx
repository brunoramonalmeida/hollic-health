import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { formatDate } from '../../dates'
import { SUBSCRIPTION_STATUS, statusLabel } from '../../labels'
import { useSession } from '../../session'

const empty = { name: '', document: '', email: '', phone: '', region_id: '', active: true, notes: '', leader_id: '' }

/** Is `candidate` below `sellerId` in the hierarchy (so it cannot lead it)? */
function isBelow(sellers, candidate, sellerId) {
  const seen = new Set()
  let current = candidate
  while (current && current.leader_id && !seen.has(current.id)) {
    seen.add(current.id)
    if (String(current.leader_id) === String(sellerId)) return true
    current = sellers.find((item) => item.id === current.leader_id)
  }
  return false
}

export default function SellerForm() {
  const { id } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { canManageSales } = useSession()
  const readOnly = !canManageSales
  const [form, setForm] = useState(empty)
  const [original, setOriginal] = useState(null)
  const [regions, setRegions] = useState([])
  const [sellers, setSellers] = useState([])
  const [sales, setSales] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setError('')
    if (!id) {
      // A new seller starts blank right away (in the region it was opened
      // from); the load below only brings the regions, so it never wipes
      // what the user types meanwhile.
      setOriginal(null)
      setForm({ ...empty, region_id: params.get('region') || '' })
      setSales([])
    }
    const requests = [api('/regions'), api('/sellers')]
    if (id) requests.push(api(`/sellers/${id}`), api(`/subscriptions?seller_id=${id}`))
    // A superseded load (another id, React's dev double mount) must not
    // overwrite what the user already typed.
    let active = true
    Promise.all(requests)
      .then(([areas, team, row, sold]) => {
        if (!active) return
        setRegions(areas)
        setSellers(team)
        if (row) {
          setOriginal(row)
          setForm({
            name: row.name,
            document: row.document,
            email: row.email,
            phone: row.phone,
            region_id: String(row.region_id),
            active: row.active,
            notes: row.notes,
            leader_id: row.leader_id ? String(row.leader_id) : '',
          })
          setSales(sold || [])
        }
      })
      .catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, [id]) // eslint-disable-line react-hooks/exhaustive-deps

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  const region = regions.find((item) => String(item.id) === form.region_id)
  const moving = original && String(original.region_id) !== form.region_id
  const teamSize = original ? original.team_size : 0
  // Possible leaders: active sellers of the same region, not itself nor anyone below it (no cycles).
  const leaders = sellers.filter(
    (item) =>
      String(item.region_id) === form.region_id &&
      String(item.id) !== id &&
      !(id && isBelow(sellers, item, id)) &&
      (item.active || String(item.id) === form.leader_id),
  )
  const team = sellers.filter((item) => id && String(item.leader_id) === id)
  const overrideLabels = region ? region.override_labels : []

  async function save(event) {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      await api(id ? `/sellers/${id}` : '/sellers', {
        method: id ? 'PUT' : 'POST',
        message: id ? 'Seller updated' : 'Seller created',
        body: {
          ...form,
          region_id: Number(form.region_id),
          leader_id: form.leader_id ? Number(form.leader_id) : null,
        },
      })
      navigate('/sellers')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/sellers', label: 'Sellers' }}
        title={id ? form.name || 'Seller' : 'New seller'}
        hint="The seller sells under the rules of its region: only the region's plans, only to customers inside its scope."
      />
      <div className="grid wide">
        <form className="panel" onSubmit={save}>
          <fieldset disabled={readOnly}>
            <legend>Seller</legend>
            <label>
              Name *
              <input value={form.name} required maxLength={120} onChange={(event) => set('name', event.target.value)} />
            </label>
            <label>
              Region *
              <select value={form.region_id} required onChange={(event) => set('region_id', event.target.value)}>
                <option value="">Select</option>
                {regions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
              {regions.length === 0 && <small className="hint">Create a region first: every seller belongs to one.</small>}
              {moving && <small className="hint">Sales already made stay in {original.region_name}; the next ones follow the new region.</small>}
            </label>
            <label>
              Document (CPF/CNPJ)
              <input value={form.document} maxLength={20} onChange={(event) => set('document', event.target.value)} />
            </label>
            <label>
              Email
              <input type="email" value={form.email} maxLength={180} onChange={(event) => set('email', event.target.value)} />
            </label>
            <label>
              Phone
              <input value={form.phone} maxLength={30} onChange={(event) => set('phone', event.target.value)} />
            </label>
            <label>
              Reports to
              <select value={form.leader_id} onChange={(event) => set('leader_id', event.target.value)}>
                <option value="">Nobody (top of its team, or sells on its own)</option>
                {leaders.map((item) => <option key={item.id} value={item.id}>{item.name}{item.leader_name ? ` — reports to ${item.leader_name}` : ''}</option>)}
              </select>
              <small className="hint">
                A seller of the same region, at any level (coordinator, regional manager…). The region pays the leaders above this seller{' '}
                {overrideLabels.length ? overrideLabels.map((label, index) => `${label} (level ${index + 1})`).join(', ') : 'no override'} on its next sales.
                {teamSize > 0 && ` Leads ${teamSize} seller${teamSize === 1 ? '' : 's'} directly.`}
              </small>
            </label>
            <label className="check">
              <input type="checkbox" checked={form.active} onChange={(event) => set('active', event.target.checked)} />
              Active (an inactive seller keeps its history but cannot sell)
            </label>
            <label>
              Notes
              <textarea value={form.notes} rows={2} onChange={(event) => set('notes', event.target.value)} />
            </label>
          </fieldset>
          {error && <p className="error">{error}</p>}
          <div className="actions">
            {!readOnly && <button type="submit" disabled={saving || regions.length === 0}>{id ? 'Save' : 'Create'}</button>}
            <Link className="button ghost" to="/sellers">{readOnly ? 'Back' : 'Cancel'}</Link>
          </div>
        </form>
        <div className="stack">
          {region && (
            <div className="card padded status-panel">
              <h2>Rules of {region.name}</h2>
              <dl>
                <dt>Commission</dt>
                <dd>{region.commission_label} of the plan fees received</dd>
                <dt>Scope</dt>
                <dd>{region.scope}</dd>
                <dt>Plans</dt>
                <dd>
                  {region.plans.length === 0 && <span className="muted">None yet</span>}
                  {region.plans.map((plan) => (
                    <div key={plan.modality_id}>
                      {plan.modality_name}
                      <small className="muted">
                        {plan.discount_percent > 0 ? ` · ${plan.discount_percent}% off · ${money(plan.value_cents)}/month` : ` · ${money(plan.plan_value_cents)}/month`}
                      </small>
                    </div>
                  ))}
                </dd>
              </dl>
            </div>
          )}
          {id && team.length > 0 && (
            <div className="card">
              <div className="card-head"><h2>Team</h2><span className="muted">{original ? `${original.organisation_size} in total, all levels` : ''}</span></div>
              <table>
                <thead><tr><th>Seller</th><th>Leads</th><th>Sales</th><th>Status</th></tr></thead>
                <tbody>
                  {team.map((row) => (
                    <tr key={row.id}>
                      <td><Link to={`/sellers/${row.id}`}>{row.name}</Link></td>
                      <td>{row.organisation_size || <span className="muted">—</span>}</td>
                      <td>{row.subscriptions_count}</td>
                      <td><span className={`badge ${row.active ? 'ativa' : 'vencida'}`}>{row.active ? 'Active' : 'Inactive'}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {id && (
            <div className="card">
              <div className="card-head"><h2>Sales</h2></div>
              <table>
                <thead>
                  <tr><th>Customer</th><th>Plan</th><th>Region</th><th>Monthly</th><th>Commission</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {sales.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <Link to={`/subscriptions/${row.id}`}>{row.customer_name}</Link>
                        <br /><small className="muted">{formatDate(row.joined_on)} – {formatDate(row.expires_on)}</small>
                      </td>
                      <td>{row.modality_name}</td>
                      <td>{row.region_name}</td>
                      <td>
                        {money(row.monthly_amount_cents)}
                        {row.discount_percent > 0 && <small className="muted"> · {row.discount_percent}% off</small>}
                      </td>
                      <td>
                        {row.commission_bp / 100}%
                        <small className="muted"> · {money(row.commission_earned_cents)} earned</small>
                      </td>
                      <td><span className={`badge ${row.status}`}>{statusLabel(SUBSCRIPTION_STATUS, row.status)}</span></td>
                    </tr>
                  ))}
                  {sales.length === 0 && <tr><td colSpan={6} className="muted">No sales yet.</td></tr>}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
