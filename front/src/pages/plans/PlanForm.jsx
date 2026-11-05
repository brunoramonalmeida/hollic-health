import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fromCents, toCents } from '../../api'
import PageHeader from '../../components/PageHeader'
import { useSession } from '../../session'
import { LIMIT_PERIOD, allowanceLabel } from '../../labels'

const empty = {
  name: '',
  plan_value: '0.00',
  dependent_limit: '0',
  grace_days: '0',
  extra_dependent: '0.00',
  discount_percent: '0',
  appointment_limit: '0',
  appointment_limit_period: 'month',
  category_id: '',
}

export default function PlanForm() {
  const { id } = useParams()
  const navigate = useNavigate()
  const session = useSession()
  const readOnly = !session.canEditPricing
  const [form, setForm] = useState(empty)
  const [overrides, setOverrides] = useState([]) // [{specialty_id, percent}]
  const [specialties, setSpecialties] = useState([])
  const [categories, setCategories] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setError('')
    const requests = [api('/specialties'), api('/plan-categories')]
    if (id) requests.push(api(`/modalities/${id}`))
    Promise.all(requests)
      .then(([specs, groups, row]) => {
        setSpecialties(specs)
        setCategories(groups)
        if (row) {
          setForm({
            name: row.name,
            plan_value: fromCents(row.plan_value_cents),
            dependent_limit: String(row.dependent_limit),
            grace_days: String(row.grace_days),
            extra_dependent: fromCents(row.extra_dependent_cents),
            discount_percent: String(row.discount_percent),
            appointment_limit: String(row.appointment_limit || 0),
            appointment_limit_period: row.appointment_limit_period || 'month',
            category_id: row.category_id ? String(row.category_id) : '',
          })
          setOverrides(row.discounts.map((item) => ({ specialty_id: String(item.specialty_id), percent: String(item.percent) })))
        } else {
          setForm(empty)
          setOverrides([])
        }
      })
      .catch((err) => setError(err.message))
  }, [id])

  function set(field, value) {
    setForm((current) => ({ ...current, [field]: value }))
  }

  function setOverride(index, field, value) {
    setOverrides((current) => current.map((item, i) => (i === index ? { ...item, [field]: value } : item)))
  }

  const category = categories.find((item) => String(item.id) === form.category_id)
  // A category's waiting period replaces the plan's own.
  const categoryGrace = Boolean(category && category.grace_days !== null)
  const used = new Set(overrides.map((item) => item.specialty_id))
  const available = specialties.filter((item) => !used.has(String(item.id)))

  async function save(event) {
    event.preventDefault()
    setError('')
    if (overrides.some((item) => !item.specialty_id)) {
      setError('Choose a specialty for every override.')
      return
    }
    setSaving(true)
    try {
      const body = {
        name: form.name,
        plan_value_cents: toCents(form.plan_value),
        dependent_limit: Number(form.dependent_limit || 0),
        grace_days: Number(form.grace_days || 0),
        extra_dependent_cents: toCents(form.extra_dependent),
        discount_percent: Number(form.discount_percent || 0),
        appointment_limit: Number(form.appointment_limit || 0),
        appointment_limit_period: form.appointment_limit_period,
        category_id: form.category_id ? Number(form.category_id) : null,
      }
      const saved = await api(id ? `/modalities/${id}` : '/modalities', { method: id ? 'PUT' : 'POST', body, message: false })
      await api(`/modalities/${saved.id}/discounts`, {
        method: 'PUT',
        message: id ? 'Plan updated' : 'Plan created',
        body: { discounts: overrides.map((item) => ({ specialty_id: Number(item.specialty_id), percent: Number(item.percent || 0) })) },
      })
      navigate('/modalities')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section>
      <PageHeader
        back={{ to: '/modalities', label: 'Plans' }}
        title={id ? form.name || 'Plan' : 'New plan'}
        hint="The discount is applied to the appointment price (clinic fee + specialty + professional) when it is booked, for as many appointments per period as the plan covers; over that, the appointment pays the full price."
      />
      <form className="panel form-page" onSubmit={save}>
        <fieldset disabled={readOnly}>
          <legend>Plan</legend>
          <div className="fields">
            <label className="span-2">
              Name *
              <input value={form.name} required onChange={(event) => set('name', event.target.value)} />
            </label>
            <label>
              Monthly price
              <input value={form.plan_value} inputMode="decimal" onChange={(event) => set('plan_value', event.target.value)} />
            </label>
            <label className="span-2">
              Category
              <select value={form.category_id} onChange={(event) => set('category_id', event.target.value)}>
                <option value="">No category</option>
                {categories.map((item) => (
                  <option key={item.id} value={item.id}>{item.name}{item.enterprise ? ' (enterprise)' : ''}</option>
                ))}
              </select>
              {category && category.enterprise && (
                <small className="hint" data-testid="enterprise-hint">An enterprise plan: only available through company agreements — never sold to individuals nor by sellers.</small>
              )}
            </label>
            <label>
              Waiting period (days)
              <input type="number" min={0} value={form.grace_days} disabled={categoryGrace} onChange={(event) => set('grace_days', event.target.value)} />
              {categoryGrace && (
                <small className="hint">The {category.name} category sets it: {category.grace_days === 0 ? 'none' : `${category.grace_days} days`}.</small>
              )}
            </label>
            <label>
              Dependents included
              <input type="number" min={0} value={form.dependent_limit} onChange={(event) => set('dependent_limit', event.target.value)} />
            </label>
            <label>
              Price per extra dependent
              <input value={form.extra_dependent} inputMode="decimal" onChange={(event) => set('extra_dependent', event.target.value)} />
            </label>
          </div>
        </fieldset>
        <fieldset disabled={readOnly}>
          <legend>Appointment discount</legend>
          <div className="fields">
            <label>
              Default discount (%)
              <input type="number" min={0} max={100} value={form.discount_percent} onChange={(event) => set('discount_percent', event.target.value)} />
              <small className="hint">Applies to every specialty without an override below.</small>
            </label>
          </div>
          <div className="fields">
            <label>
              Appointments covered
              <input type="number" min={0} value={form.appointment_limit} onChange={(event) => set('appointment_limit', event.target.value)} />
              <small className="hint">0 = unlimited. Shared by the holder and the dependents.</small>
            </label>
            <label>
              Per
              <select value={form.appointment_limit_period} onChange={(event) => set('appointment_limit_period', event.target.value)}>
                {Object.entries(LIMIT_PERIOD).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <small className="hint">
                {form.appointment_limit_period === 'month'
                  ? 'Each monthly billing period of the subscription (from the join day).'
                  : 'The whole period of the subscription, from the join date to the expiration.'}
              </small>
            </label>
            <p className="notice info span-2">
              {Number(form.appointment_limit) > 0
                ? `${allowanceLabel(Number(form.appointment_limit), form.appointment_limit_period)} get the discount; the ones booked over that are accepted at the full price.`
                : 'Unlimited: every appointment gets the discount.'}
            </p>
          </div>
          <div className="week-editor" style={{ marginTop: 12 }}>
            {overrides.map((item, index) => (
              <div key={index} className="range">
                <select value={item.specialty_id} required onChange={(event) => setOverride(index, 'specialty_id', event.target.value)}>
                  <option value="">Specialty</option>
                  {specialties
                    .filter((spec) => String(spec.id) === item.specialty_id || !used.has(String(spec.id)))
                    .map((spec) => <option key={spec.id} value={spec.id}>{spec.name}</option>)}
                </select>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={item.percent}
                  required
                  style={{ width: 90 }}
                  onChange={(event) => setOverride(index, 'percent', event.target.value)}
                />
                <span>%</span>
                {!readOnly && (
                  <button type="button" className="link warn-text" onClick={() => setOverrides(overrides.filter((_, i) => i !== index))}>
                    Remove
                  </button>
                )}
              </div>
            ))}
            {!readOnly && available.length > 0 && (
              <div>
                <button type="button" className="link" onClick={() => setOverrides([...overrides, { specialty_id: '', percent: form.discount_percent }])}>
                  + Add specialty override
                </button>
              </div>
            )}
            {overrides.length === 0 && <p className="muted">No overrides: the default discount applies to every specialty.</p>}
          </div>
        </fieldset>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          {!readOnly && <button type="submit" disabled={saving}>{id ? 'Save' : 'Create'}</button>}
          <Link className="button ghost" to="/modalities">{readOnly ? 'Back' : 'Cancel'}</Link>
        </div>
      </form>
    </section>
  )
}
