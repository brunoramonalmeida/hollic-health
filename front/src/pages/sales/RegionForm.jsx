import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, money } from '../../api'
import PageHeader from '../../components/PageHeader'
import { STATES } from '../../br'
import { useSession } from '../../session'

const empty = { name: '', description: '', commission: '0' }
const LEVEL_NAMES = ['Direct leader (level 1)', "The leader's leader (level 2)", 'Level 3', 'Level 4', 'Level 5']

/** "12.5" (percent typed by the user) -> 1250 basis points. */
export function percentToBp(value) {
  const number = Number(String(value).replace(',', '.'))
  return Number.isNaN(number) ? 0 : Math.round(number * 100)
}

/** Monthly value with the special discount, rounded half up like the API. */
function discounted(cents, percent) {
  const pct = Math.max(0, Math.min(100, Number(percent) || 0))
  return cents - Math.floor((cents * pct + 50) / 100)
}

export default function RegionForm() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { canManageSales } = useSession()
  const readOnly = !canManageSales
  const [form, setForm] = useState(empty)
  const [areas, setAreas] = useState([]) // [{state, city}]
  const [offers, setOffers] = useState([]) // [{modality_id, discount_percent}]
  const [overrides, setOverrides] = useState([]) // percent per level above the seller
  const [plans, setPlans] = useState([])
  const [sellers, setSellers] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setError('')
    if (!id) {
      // A new region starts blank right away; the load below only brings
      // the plans, so it never wipes what the user types meanwhile.
      setForm(empty)
      setAreas([{ state: '', city: '' }])
      setOffers([])
      setOverrides([])
      setSellers([])
    }
    const requests = [api('/modalities')]
    if (id) requests.push(api(`/regions/${id}`), api(`/sellers?region_id=${id}`))
    // A superseded load (another id, React's dev double mount) must not
    // overwrite what the user already typed.
    let active = true
    Promise.all(requests)
      .then(([catalog, row, people]) => {
        if (!active) return
        setPlans(catalog.filter((item) => !item.enterprise)) // enterprise plans: company agreements only, never by sellers
        if (row) {
          setForm({ name: row.name, description: row.description, commission: String(row.commission_bp / 100) })
          setAreas(row.areas.map((item) => ({ state: item.state, city: item.city })))
          setOffers(row.plans.map((item) => ({ modality_id: String(item.modality_id), discount_percent: String(item.discount_percent) })))
          setOverrides(row.override_bps.map((rate) => String(rate / 100)))
          setSellers(people || [])
        }
      })
      .catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, [id])

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  function setArea(index, field, value) {
    setAreas((current) => current.map((item, i) => (i === index ? { ...item, [field]: value } : item)))
  }

  function setOffer(index, field, value) {
    setOffers((current) => current.map((item, i) => (i === index ? { ...item, [field]: value } : item)))
  }

  const offered = new Set(offers.map((item) => item.modality_id))
  const available = plans.filter((item) => !offered.has(String(item.id)))

  async function save(event) {
    event.preventDefault()
    setError('')
    if (areas.length === 0 || areas.some((item) => !item.state)) {
      setError('The scope needs at least one area, each with a state.')
      return
    }
    if (offers.some((item) => !item.modality_id)) {
      setError('Choose the plan of every offer.')
      return
    }
    setSaving(true)
    try {
      await api(id ? `/regions/${id}` : '/regions', {
        method: id ? 'PUT' : 'POST',
        message: id ? 'Region updated' : 'Region created',
        body: {
          name: form.name,
          description: form.description,
          commission_bp: percentToBp(form.commission),
          areas: areas.map((item) => ({ state: item.state, city: item.city.trim() })),
          plans: offers.map((item) => ({ modality_id: Number(item.modality_id), discount_percent: Number(item.discount_percent || 0) })),
          override_bps: overrides.map(percentToBp),
        },
      })
      navigate('/regions')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/regions', label: 'Regions' }}
        title={id ? form.name || 'Region' : 'New region'}
        hint="Rule changes apply to the next sales: the subscriptions already sold keep the commission and discount they were sold with."
      />
      <form className="panel form-page" onSubmit={save}>
        <fieldset disabled={readOnly}>
          <legend>Region</legend>
          <div className="fields">
            <label className="span-2">
              Name *
              <input value={form.name} required maxLength={120} onChange={(event) => set('name', event.target.value)} />
            </label>
            <label>
              Seller commission (%)
              <input value={form.commission} inputMode="decimal" onChange={(event) => set('commission', event.target.value)} />
              <small className="hint">On every plan fee received from its sellers' sales (e.g. 12.5).</small>
            </label>
            <label className="span-2">
              Description
              <textarea value={form.description} rows={2} onChange={(event) => set('description', event.target.value)} />
            </label>
          </div>
        </fieldset>
        <fieldset disabled={readOnly}>
          <legend>Scope</legend>
          <p className="muted">Where its sellers may sell: the customer's address must be in one of these areas. Leave the city blank to cover the whole state.</p>
          <div className="week-editor">
            {areas.map((item, index) => (
              <div key={index} className="range">
                <select value={item.state} required aria-label="State" onChange={(event) => setArea(index, 'state', event.target.value)}>
                  <option value="">State</option>
                  {STATES.map((uf) => <option key={uf} value={uf}>{uf}</option>)}
                </select>
                <input
                  value={item.city}
                  placeholder="Whole state"
                  aria-label="City"
                  maxLength={80}
                  onChange={(event) => setArea(index, 'city', event.target.value)}
                />
                {!readOnly && areas.length > 1 && (
                  <button type="button" className="link warn-text" onClick={() => setAreas(areas.filter((_, i) => i !== index))}>
                    Remove
                  </button>
                )}
              </div>
            ))}
            {!readOnly && (
              <div>
                <button type="button" className="link" onClick={() => setAreas([...areas, { state: '', city: '' }])}>+ Add area</button>
              </div>
            )}
          </div>
        </fieldset>
        <fieldset disabled={readOnly}>
          <legend>Plans available and special discounts</legend>
          <p className="muted">Its sellers may only sell these plans. The special discount comes off the monthly fee of every installment.</p>
          <div className="week-editor">
            {offers.map((item, index) => {
              const plan = plans.find((row) => String(row.id) === item.modality_id)
              return (
                <div key={index} className="range">
                  <select value={item.modality_id} required aria-label="Plan" onChange={(event) => setOffer(index, 'modality_id', event.target.value)}>
                    <option value="">Plan</option>
                    {plans
                      .filter((row) => String(row.id) === item.modality_id || !offered.has(String(row.id)))
                      .map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
                  </select>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={item.discount_percent}
                    aria-label="Special discount (%)"
                    style={{ width: 90 }}
                    onChange={(event) => setOffer(index, 'discount_percent', event.target.value)}
                  />
                  <span>% off</span>
                  {plan && (
                    <small className="muted">
                      {money(plan.plan_value_cents)} → <strong>{money(discounted(plan.plan_value_cents, item.discount_percent))}</strong>/month
                    </small>
                  )}
                  {!readOnly && (
                    <button type="button" className="link warn-text" onClick={() => setOffers(offers.filter((_, i) => i !== index))}>
                      Remove
                    </button>
                  )}
                </div>
              )
            })}
            {!readOnly && available.length > 0 && (
              <div>
                <button type="button" className="link" onClick={() => setOffers([...offers, { modality_id: '', discount_percent: '0' }])}>
                  + Add plan
                </button>
              </div>
            )}
            {offers.length === 0 && <p className="notice info">No plans: the sellers of this region cannot sell anything yet.</p>}
          </div>
        </fieldset>
        <fieldset disabled={readOnly}>
          <legend>Team overrides by level</legend>
          <p className="muted">
            What the region pays, on top of the seller's commission, to each seller above the one who sold: the direct leader (level 1),
            the leader's leader (level 2)… Teams may have any depth; levels beyond this list earn nothing.
          </p>
          <div className="week-editor">
            {overrides.map((rate, index) => (
              <div key={index} className="range">
                <span style={{ minWidth: 200 }}>{LEVEL_NAMES[index]}</span>
                <input
                  value={rate}
                  inputMode="decimal"
                  aria-label={`Override rate level ${index + 1} (%)`}
                  style={{ width: 90 }}
                  onChange={(event) => setOverrides(overrides.map((item, i) => (i === index ? event.target.value : item)))}
                />
                <span>%</span>
                {!readOnly && index === overrides.length - 1 && (
                  <button type="button" className="link warn-text" onClick={() => setOverrides(overrides.slice(0, -1))}>Remove</button>
                )}
              </div>
            ))}
            {!readOnly && overrides.length < 5 && (
              <div>
                <button type="button" className="link" onClick={() => setOverrides([...overrides, '0'])}>+ Add level</button>
              </div>
            )}
            {overrides.length === 0 && <p className="muted">No overrides: team leaders of this region earn nothing on their teams' sales.</p>}
          </div>
        </fieldset>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          {!readOnly && <button type="submit" disabled={saving}>{id ? 'Save' : 'Create'}</button>}
          <Link className="button ghost" to="/regions">{readOnly ? 'Back' : 'Cancel'}</Link>
        </div>
      </form>
      {id && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-head">
            <h2>Sellers</h2>
            {canManageSales && <Link className="button ghost" to={`/sellers/new?region=${id}`}>New seller</Link>}
          </div>
          <table>
            <thead>
              <tr><th>Name</th><th>Contact</th><th>Sales</th><th>Status</th></tr>
            </thead>
            <tbody>
              {sellers.map((row) => (
                <tr key={row.id}>
                  <td><Link to={`/sellers/${row.id}`}>{row.name}</Link></td>
                  <td>{[row.email, row.phone].filter(Boolean).join(' · ') || <span className="muted">—</span>}</td>
                  <td>{row.subscriptions_count}</td>
                  <td><span className={`badge ${row.active ? 'ativa' : 'vencida'}`}>{row.active ? 'Active' : 'Inactive'}</span></td>
                </tr>
              ))}
              {sellers.length === 0 && <tr><td colSpan={4} className="muted">No sellers in this region.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
