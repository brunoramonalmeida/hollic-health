import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api'
import PageHeader from '../../components/PageHeader'
import { useSession } from '../../session'

const empty = { name: '', description: '', enterprise: false, grace_days: '' }

export default function CategoryForm() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { canEditPricing } = useSession()
  const readOnly = !canEditPricing
  const [form, setForm] = useState(empty)
  const [plans, setPlans] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setError('')
    if (!id) {
      setForm(empty)
      setPlans([])
      return undefined
    }
    let active = true
    Promise.all([api(`/plan-categories/${id}`), api('/modalities')])
      .then(([row, catalog]) => {
        if (!active) return
        setForm({ name: row.name, description: row.description, enterprise: row.enterprise, grace_days: row.grace_days === null ? '' : String(row.grace_days) })
        setPlans(catalog.filter((item) => String(item.category_id) === id))
      })
      .catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, [id])

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  async function save(event) {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      await api(id ? `/plan-categories/${id}` : '/plan-categories', {
        method: id ? 'PUT' : 'POST',
        message: id ? 'Category updated' : 'Category created',
        body: { ...form, grace_days: form.grace_days === '' ? null : Number(form.grace_days) },
      })
      navigate('/plan-categories')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/plan-categories', label: 'Plan categories' }}
        title={id ? form.name || 'Category' : 'New plan category'}
        hint="Enterprise plans are sold only through company agreements — never to individuals nor by sellers — with the special discounts and conditions of each plan and each agreement."
      />
      <div className="grid wide">
        <form className="panel" onSubmit={save}>
          <fieldset disabled={readOnly}>
            <legend>Category</legend>
            <label>
              Name *
              <input value={form.name} required maxLength={80} onChange={(event) => set('name', event.target.value)} />
            </label>
            <label>
              Description
              <textarea value={form.description} rows={2} onChange={(event) => set('description', event.target.value)} />
            </label>
            <label className="check">
              <input type="checkbox" checked={form.enterprise} onChange={(event) => set('enterprise', event.target.checked)} />
              Enterprise: its plans are only available through company agreements
            </label>
            <label>
              Waiting period of its plans (days)
              <input
                type="number"
                min={0}
                max={365}
                value={form.grace_days}
                placeholder="Each plan's own"
                onChange={(event) => set('grace_days', event.target.value)}
              />
              <small className="hint">Replaces each plan's own waiting period (0: none). Blank: each plan keeps its own. A company agreement may still negotiate its own.</small>
            </label>
          </fieldset>
          {error && <p className="error">{error}</p>}
          <div className="actions">
            {!readOnly && <button type="submit" disabled={saving}>{id ? 'Save' : 'Create'}</button>}
            <Link className="button ghost" to="/plan-categories">{readOnly ? 'Back' : 'Cancel'}</Link>
          </div>
        </form>
        {id && (
          <div className="card">
            <div className="card-head"><h2>Plans</h2></div>
            <table>
              <thead><tr><th>Plan</th><th>Waiting period</th></tr></thead>
              <tbody>
                {plans.map((plan) => (
                  <tr key={plan.id}>
                    <td><Link to={`/modalities/${plan.id}`}>{plan.name}</Link></td>
                    <td>{plan.effective_grace_days} days{plan.effective_grace_days !== plan.grace_days && <small className="muted"> (the plan's own: {plan.grace_days})</small>}</td>
                  </tr>
                ))}
                {plans.length === 0 && <tr><td colSpan={2} className="muted">No plans in this category.</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  )
}
